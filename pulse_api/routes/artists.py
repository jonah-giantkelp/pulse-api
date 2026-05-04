"""Artist endpoints — search, add, resolve, fetch, list-tracked, untrack."""

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

artists_bp = Blueprint("artists", __name__)


@artists_bp.get("/artists/search")
@require_auth
def search_artists():
    """Live search for artists via MusicBrainz.

    Returns candidates with name, disambiguation, country, tags, and
    image URL for the frontend to display as the user types.
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    limit = min(int(request.args.get("limit", 8)), 20)

    candidates = run_async(mb_search_artists(q, limit=limit))

    # Enrich top candidates with images (from Spotify via MusicBrainz
    # relations).  Only fetch details for the top 5 to stay within
    # MusicBrainz rate limits.
    async def _enrich():
        for c in candidates[:5]:
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

    return jsonify(candidates)


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
    """List artists the current user is tracking."""
    subs = (
        supabase.table("user_artists")
        .select("artist_id, city, notify, created_at, artists(*)")
        .eq("user_id", g.user_id)
        .execute()
    )
    return jsonify(subs.data)


@artists_bp.delete("/me/artists/<artist_id>")
@require_auth
def untrack_artist(artist_id):
    """Stop tracking an artist."""
    supabase.table("user_artists").delete().eq(
        "user_id", g.user_id
    ).eq("artist_id", artist_id).execute()
    return jsonify({"status": "untracked"})
