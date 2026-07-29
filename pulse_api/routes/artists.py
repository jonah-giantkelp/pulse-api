"""Artist endpoints — search, add, resolve, fetch, list-tracked, untrack."""

import logging

from flask import Blueprint, g, jsonify, request

from pulse_api.routes._helpers import run_async
from pulse_api.ai.resolver import (
    resolve_artist,
    resolve_artist_from_mbid,
    update_resolution,
)
from pulse_api.auth import require_auth
from pulse_api.db import supabase
from pulse_api.sources.musicbrainz import (
    get_artist_details as mb_get_artist_details,
    get_artist_image,
    search_artists as mb_search_artists,
)
from pulse_api.sync.orchestrator import sync_events_for_artist, upsert_events

logger = logging.getLogger(__name__)

artists_bp = Blueprint("artists", __name__)

# Hard cap on MusicBrainz search results — each candidate can cost an extra
# rate-limited (~1.1s) detail fetch, so this bounds worst-case latency.
MB_SEARCH_LIMIT = 3


def _escape_like(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so user input matches literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _local_row_to_card(row: dict) -> dict:
    """Project an `artists` row into the shared search-card shape.

    Mirrors the MusicBrainz shape so the client can merge results from both
    endpoints by `musicbrainz_id` (or fall back to `artist_id` for local-only
    rows).
    """
    return {
        "source": "local",
        "artist_id": row.get("id"),
        "musicbrainz_id": row.get("musicbrainz_id"),
        "name": row.get("name") or "",
        "disambiguation": "",
        "type": "",
        "country": "",
        "score": 100,
        "tags": [],
        "genres": row.get("genres") or [],
        "life_span": {},
        "image_url": row.get("image_url"),
    }


@artists_bp.get("/artists/search/local")
@require_auth
def search_artists_local():
    """Fast DB-only search of artists already in our system.

    Returns rows in the same shape as /artists/search/musicbrainz so the
    client can merge results, deduping by musicbrainz_id where present.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    limit = min(int(request.args.get("limit", 8)), 20)

    # Case-insensitive prefix-and-substring match on name. Keep it tight —
    # this endpoint runs on every keystroke.
    rows = (
        supabase.table("artists")
        .select("id, name, musicbrainz_id, image_url, genres")
        .ilike("name", f"%{q}%")
        .limit(limit)
        .execute()
    )
    return jsonify([_local_row_to_card(r) for r in rows.data or []])


@artists_bp.get("/artists/search/musicbrainz")
@require_auth
def search_artists_musicbrainz():
    """Live search for artists via MusicBrainz, with image enrichment.

    Exact (case-insensitive) name matches from our own DB are returned
    first, with MusicBrainz results appended after (deduped by
    musicbrainz_id). Slower than the local endpoint — clients should fire
    both in parallel and render local results immediately while MB results
    stream in.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    limit = min(int(request.args.get("limit", MB_SEARCH_LIMIT)), MB_SEARCH_LIMIT)

    # Artists already in our DB whose name matches the query exactly
    # (case-insensitive) lead the response; MusicBrainz results follow.
    exact_rows = (
        supabase.table("artists")
        .select("id, name, musicbrainz_id, image_url, genres")
        .ilike("name", _escape_like(q))
        .limit(5)
        .execute()
    )
    exact_cards = [_local_row_to_card(r) for r in exact_rows.data or []]
    exact_mbids = {c["musicbrainz_id"] for c in exact_cards if c["musicbrainz_id"]}

    candidates = run_async(mb_search_artists(q, limit=limit))
    candidates = [c for c in candidates if c["musicbrainz_id"] not in exact_mbids]

    # Reuse stored image/genres for candidates already in the DB; only hit
    # MusicBrainz for details on genuinely new artists, since each detail
    # fetch costs ~1s of rate-limit wait.
    known: dict[str, dict] = {}
    mbids = [c["musicbrainz_id"] for c in candidates]
    if mbids:
        known_rows = (
            supabase.table("artists")
            .select("musicbrainz_id, image_url, genres")
            .in_("musicbrainz_id", mbids)
            .execute()
        )
        known = {r["musicbrainz_id"]: r for r in known_rows.data or []}

    async def _enrich():
        for c in candidates:
            row = known.get(c["musicbrainz_id"])
            if row:
                c["image_url"] = row.get("image_url")
                c["genres"] = row.get("genres") or c.get("tags", [])
                continue
            try:
                details = await mb_get_artist_details(c["musicbrainz_id"])
                c["image_url"] = await get_artist_image(
                    c["musicbrainz_id"],
                    details["platform_ids"],
                )
                c["genres"] = details["genres"] or c.get("tags", [])
            except Exception:
                c["image_url"] = None

    run_async(_enrich())

    # Tag every result with its origin so the client can merge cleanly.
    for c in candidates:
        c["source"] = "musicbrainz"

    return jsonify(exact_cards + candidates)


@artists_bp.get("/artists/search")
@require_auth
def search_artists():
    """Deprecated: use /artists/search/local + /artists/search/musicbrainz.

    Kept as a back-compat alias that returns MusicBrainz results only,
    matching the legacy single-endpoint behaviour. iOS clients should
    migrate to the split endpoints for instant local results.
    """
    logger.info("[DEPRECATED] /artists/search hit — client should migrate to split endpoints")
    return search_artists_musicbrainz()


@artists_bp.post("/artists")
@require_auth
def add_artist():
    """Add a new artist and subscribe the current user.

    Accepts either:
    - { "musicbrainz_id": "..." } — preferred, from the search endpoint
    - { "name": "...", "hint": "..." } — legacy fallback
    """
    body = request.get_json()
    mbid = body.get("musicbrainz_id")
    name = body.get("name")
    city = body.get("city")

    if not mbid and not name:
        return jsonify({"error": "musicbrainz_id or name is required"}), 400

    # Check if artist already exists (by MBID or name)
    if mbid:
        existing = (
            supabase.table("artists")
            .select("id")
            .eq("musicbrainz_id", mbid)
            .execute()
        )
    else:
        existing = (
            supabase.table("artists")
            .select("id")
            .ilike("name", name)
            .execute()
        )

    if existing.data:
        artist_id = existing.data[0]["id"]
        supabase.table("user_artists").upsert(
            {"user_id": g.user_id, "artist_id": artist_id, "city": city},
            on_conflict="user_id,artist_id",
        ).execute()
        return jsonify({
            "artist_id": artist_id,
            "status": "existing",
            "message": "Artist already exists, subscribed.",
        })

    # Resolve new artist
    if mbid:
        result = run_async(resolve_artist_from_mbid(mbid))
    else:
        hint = body.get("hint")
        result = run_async(resolve_artist(name, disambiguator=hint))

    # Subscribe user
    supabase.table("user_artists").insert(
        {"user_id": g.user_id, "artist_id": result["artist_id"], "city": city}
    ).execute()

    # Initial event sync so the artist arrives with gigs (and an accurate
    # active pill) instead of waiting for the nightly sync. Best-effort —
    # a source failure shouldn't fail the add.
    try:
        artist_row = (
            supabase.table("artists")
            .select("*")
            .eq("id", result["artist_id"])
            .execute()
        ).data[0]
        events = run_async(sync_events_for_artist(artist_row))
        upsert_events(events)
        result["initial_events"] = len(events)
        logger.info("Initial sync for %s: %d event(s)", artist_row["name"], len(events))
    except Exception:
        logger.exception("Initial event sync failed for %s", result["artist_id"])

    return jsonify(result), 201


@artists_bp.post("/artists/<artist_id>/resolve")
@require_auth
def resolve_platform(artist_id):
    """Manually resolve an ambiguous platform match."""
    body = request.get_json()
    platform = body.get("platform")
    platform_id = body.get("platform_id")
    if not platform or not platform_id:
        return jsonify({"error": "platform and platform_id are required"}), 400

    run_async(update_resolution(artist_id, platform, platform_id))
    return jsonify({"status": "resolved"})


@artists_bp.get("/artists/<artist_id>")
@require_auth
def get_artist(artist_id):
    """Get artist details."""
    result = supabase.table("artists").select("*").eq("id", artist_id).execute()
    if not result.data:
        return jsonify({"error": "Artist not found"}), 404
    return jsonify(result.data[0])


@artists_bp.get("/artists/<artist_id>/resolutions")
@require_auth
def get_resolutions(artist_id):
    """Get resolution audit trail for an artist."""
    result = (
        supabase.table("artist_resolutions")
        .select("*")
        .eq("artist_id", artist_id)
        .order("created_at", desc=True)
        .execute()
    )
    return jsonify(result.data)


@artists_bp.get("/me/artists")
@require_auth
def list_my_artists():
    """List artists the current user is tracking.

    Each row carries `has_upcoming` — whether the artist has at least one
    future event — which drives the app's ACTIVE/INACTIVE pill (the
    artists.active column is a sync kill-switch, not a liveness signal).
    """
    subs = (
        supabase.table("user_artists")
        .select("artist_id, city, notify, created_at, artists(*)")
        .eq("user_id", g.user_id)
        .execute()
    )

    artist_ids = [s["artist_id"] for s in subs.data]
    upcoming: set[str] = set()
    if artist_ids:
        rows = (
            supabase.table("event_artists")
            .select("artist_id, events!inner(date)")
            .in_("artist_id", artist_ids)
            .gte("events.date", "now()")
            .execute()
        )
        upcoming = {r["artist_id"] for r in rows.data}

    for s in subs.data:
        s["has_upcoming"] = s["artist_id"] in upcoming
    return jsonify(subs.data)


@artists_bp.delete("/me/artists/<artist_id>")
@require_auth
def untrack_artist(artist_id):
    """Stop tracking an artist."""
    supabase.table("user_artists").delete().eq(
        "user_id", g.user_id
    ).eq("artist_id", artist_id).execute()
    return jsonify({"status": "untracked"})
