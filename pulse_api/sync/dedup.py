"""Event deduplication — both in-memory (per-batch) and DB-level.

Two layers:

  * `batch_dedup`              — in-memory pass over a single artist's batch:
                                 strict fingerprint, exact name+date,
                                 fuzzy name → AI adjudication.
  * `deduplicate_event`        — pre-write check against existing rows in
                                 `events` / `event_external_ids`. Matches
                                 by external_id, strict fp, then loose fp.
  * `filter_known_external_ids` — early skip: drop events whose
                                 (source, source_id) pair is already
                                 indexed, while still linking the
                                 current artist to the existing row.
"""

import logging
from collections import defaultdict
from difflib import SequenceMatcher

from pulse_api.db import supabase
from pulse_api.sync.enrichment import (
    LINEUP_SOURCE_RANK,
    ai_merge_decision,
    extract_lineup,
    normalise_title,
)
from pulse_api.sync.persistence import link_artist_to_event, store_external_id

logger = logging.getLogger(__name__)

TICKETING_SOURCES = {
    "ticketmaster", "bandsintown", "skiddle",
    "dice", "ra", "concerts_tracker",
}


# ─────────────────────────────────────────────
# Existing-row lookups
# ─────────────────────────────────────────────


def find_by_external_id(source: str, external_id: str) -> dict | None:
    """Look up an existing event by its platform-specific event ID."""
    try:
        result = (
            supabase.table("event_external_ids")
            .select("event_id")
            .eq("source", source)
            .eq("external_id", external_id)
            .limit(1)
            .execute()
        )
        if not result.data:
            return None
        event_id = result.data[0]["event_id"]
        event = (
            supabase.table("events")
            .select("id, source, ticket_url, title, raw_data, source_id, artist_id")
            .eq("id", event_id)
            .limit(1)
            .execute()
        )
        return event.data[0] if event.data else None
    except Exception:
        return None


def find_by_fingerprint(fp: str) -> dict | None:
    """Look up an existing event by strict fingerprint (venue+city+date)."""
    try:
        result = (
            supabase.table("events")
            .select("id, source, ticket_url, title, raw_data, source_id, artist_id")
            .eq("fingerprint", fp)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception:
        return None


def find_by_fingerprint_loose(fp_loose: str, artist_id: str) -> dict | None:
    """Look up an existing event by loose fingerprint (city+date) for the same artist."""
    try:
        result = (
            supabase.table("events")
            .select("id, source, ticket_url, title, raw_data, source_id, artist_id")
            .eq("fingerprint_loose", fp_loose)
            .eq("artist_id", artist_id)
            .limit(1)
            .execute()
        )
        return result.data[0] if result.data else None
    except Exception:
        return None


# ─────────────────────────────────────────────
# Early skip: known external IDs
# ─────────────────────────────────────────────


def filter_known_external_ids(events: list[dict]) -> list[dict]:
    """Batch-check which events already exist by (source, external_id).

    Returns only the events that are NOT yet in event_external_ids,
    so downstream AI steps (geo fill, title clean, merge) are skipped
    for already-known events.

    Side effect: for each event we filter out as already-known, link
    the current artist to the existing event row in event_artists. This
    keeps multi-artist festivals (e.g. GALA) discoverable when the same
    event is surfaced by a second artist's sync — without this, the
    early-skip would prevent deduplicate_event from ever doing the link.
    """
    if not events:
        return events

    # Build lookup keys — only events with a source_id can be checked
    keyed = [
        (e.get("source", ""), e.get("source_id", ""))
        for e in events
    ]
    # Collect unique source_ids that are non-empty
    source_ids_to_check = list({
        sid for _, sid in keyed if sid
    })
    if not source_ids_to_check:
        return events

    # Single batch query — pull event_id too so we can link the artist
    # to any already-known events we're about to filter out.
    try:
        result = (
            supabase.table("event_external_ids")
            .select("source,external_id,event_id")
            .in_("external_id", source_ids_to_check)
            .execute()
        )
        known: dict[tuple[str, str], str] = {
            (row["source"], row["external_id"]): row["event_id"]
            for row in result.data
        }
    except Exception:
        return events  # on error, skip filtering — let downstream handle it

    if not known:
        return events

    kept: list[dict] = []
    for e, (src, sid) in zip(events, keyed):
        if not sid or (src, sid) not in known:
            kept.append(e)
            continue
        # Already known — make sure the current artist is linked to it.
        event_id = known[(src, sid)]
        artist_id = e.get("artist_id")
        if event_id and artist_id:
            link_artist_to_event(
                event_id,
                artist_id,
                billing=e.get("artist_billing"),
                source=src,
            )
    return kept


# ─────────────────────────────────────────────
# Per-event DB dedup
# ─────────────────────────────────────────────


def deduplicate_event(event: dict) -> dict | None:
    """Check for existing events via external IDs then fingerprints.

    Priority:
    1. Exact match on (source, source_id) in event_external_ids
    2. Strict fingerprint (venue+city+date) — safe for cross-artist linking
    3. Loose fingerprint (city+date) — same-artist only

    Returns the event for upsert, or None if it was merged into an existing one.
    """
    source = event.get("source", "")
    source_id = event.get("source_id", "")

    # --- 1. Check external IDs first (most reliable) ---
    existing = find_by_external_id(source, source_id) if source_id else None
    match_tier = "external_id"

    # --- 2. Strict fingerprint (venue+city+date) ---
    if not existing:
        fp = event.get("fingerprint")
        if fp:
            existing = find_by_fingerprint(fp)
            match_tier = "strict"

    # --- 3. Loose fingerprint (city+date, same artist only) ---
    if not existing:
        fp_loose = event.get("fingerprint_loose")
        if fp_loose:
            existing = find_by_fingerprint_loose(
                fp_loose, event.get("artist_id", ""),
            )
            match_tier = "loose"

    if not existing:
        return event

    # --- Match found — merge ---
    same_artist = existing.get("artist_id") == event.get("artist_id")

    existing_is_ticketing = existing["source"] in TICKETING_SOURCES
    new_is_ticketing = source in TICKETING_SOURCES

    # Always store the new source's external ID on the existing event
    store_external_id(
        existing["id"], source, source_id, event.get("ticket_url"),
        raw_data=event.get("raw_data"),
    )

    if not same_artist:
        # Only allow cross-artist linking from strict or external_id matches.
        # Loose fingerprint (city+date) is NOT specific enough — that's how
        # Armand gets linked to Cross The Tracks.
        if match_tier == "loose":
            logger.info(
                "    ⚠️  Loose match but different artist — skipping link: "
                "'%s' vs '%s'",
                event.get("title", "?")[:40], existing["title"][:40],
            )
            return event  # treat as a new event

        link_artist_to_event(
            existing["id"],
            event["artist_id"],
            billing=event.get("artist_billing"),
            source=source,
        )
        if new_is_ticketing and not existing_is_ticketing:
            try:
                supabase.table("events").update({
                    "title": event.get("title") or existing["title"],
                    "ticket_url": event.get("ticket_url") or existing.get("ticket_url"),
                    "source": source,
                }).eq("id", existing["id"]).execute()
            except Exception:
                pass
        logger.info("    🔗 Multi-artist: linked to existing '%s'", existing["title"][:50])
        return None

    # Same artist, cross-source dedup
    if existing_is_ticketing and not new_is_ticketing:
        merged_raw = existing.get("raw_data") or {}
        merged_raw["social_ai_context"] = event.get("raw_data")
        # Link the social post to the existing event
        new_post_ids = event.get("source_post_ids") or []
        existing_post_ids = existing.get("source_post_ids") or []
        merged_post_ids = list(dict.fromkeys(existing_post_ids + new_post_ids))
        try:
            supabase.table("events").update({
                "raw_data": merged_raw,
                "source_post_ids": merged_post_ids if merged_post_ids else None,
            }).eq("id", existing["id"]).execute()
        except Exception:
            pass
        logger.info("    🔗 Merged into existing: %s (%s)", existing["title"][:50], existing["source"])
        return None

    if not existing_is_ticketing and new_is_ticketing:
        event["source_id"] = existing["source_id"]
        event["source"] = existing["source"]
        if existing.get("raw_data", {}).get("social_ai_context"):
            raw = event.get("raw_data") or {}
            raw["social_ai_context"] = existing["raw_data"]["social_ai_context"]
            event["raw_data"] = raw
        logger.info("    🔗 Upgrading event with ticketing data: %s", event["title"][:50])
        return event

    # Both are ticketing sources for the same artist — skip the duplicate
    logger.info("    🔗 Duplicate skipped (same artist, cross-source): %s", event["title"][:50])
    return None


# ─────────────────────────────────────────────
# In-memory batch dedup
# ─────────────────────────────────────────────


def _score(e: dict) -> int:
    s = 0
    if e.get("source") in TICKETING_SOURCES:
        s += 10
    if e.get("ticket_url"):
        s += 5
    if e.get("venue"):
        s += 2
    if e.get("city"):
        s += 1
    return s


def _merge_group(group: list[dict]) -> tuple[dict, list[dict]]:
    """Pick winner from a group, return (winner, all_ext_ids).

    Side effect: stamps `lineup` on the winner with the longest lineup found
    across any group member. Without this, picking the winner by ticket-url
    score throws away richer lineup data on the loser (e.g. RA's 100+ festival
    lineup discarded in favour of DICE's 5).
    """
    group.sort(key=_score, reverse=True)
    winner = group[0]
    ids = []

    # Pick the longest lineup across the group; ties broken by source rank.
    best_lineup: list[str] = []
    best_rank = 999
    best_source = None
    for e in group:
        names = extract_lineup(e.get("source", ""), e.get("raw_data"))
        if not names:
            continue
        rank = LINEUP_SOURCE_RANK.get(e.get("source", ""), 99)
        if (
            len(names) > len(best_lineup)
            or (len(names) == len(best_lineup) and rank < best_rank)
        ):
            best_lineup = names
            best_rank = rank
            best_source = e.get("source")

    if best_lineup:
        winner["lineup"] = best_lineup
        if best_source != winner.get("source"):
            logger.info(
                "    🎭 Lineup: %d artists from %s (winner is %s)",
                len(best_lineup), best_source, winner.get("source"),
            )

    for e in group:
        ids.append({
            "source": e.get("source"),
            "external_id": e.get("source_id"),
            "ticket_url": e.get("ticket_url"),
            "raw_data": e.get("raw_data"),
        })
        if e is not winner:
            logger.info(
                "    🔗 Batch dedup: %s/%s → merged into %s/%s",
                e.get("source"), e.get("source_id", "?")[:30],
                winner.get("source"), winner.get("source_id", "?")[:30],
            )
    return winner, ids


def batch_dedup(events: list[dict]) -> tuple[list[dict], dict[str, list[dict]]]:
    """In-memory dedup within a batch before DB checks.

    Three passes:
    1. Loose fingerprint (city+date) — free, instant
    2. Exact normalised name + same date — auto-merge regardless of city
    3. Fuzzy name similarity → AI adjudication for ambiguous pairs

    Returns (unique_events, extra_ids_map) where extra_ids_map maps
    fingerprint → list of {source, source_id, ticket_url} for the dupes.
    """
    # --- Pass 1: loose fingerprint (city+date) ---
    # Within a single artist's batch, city+date is safe for dedup
    fp_groups: dict[str | None, list[dict]] = {}
    no_fp = []
    for e in events:
        fp = e.get("fingerprint_loose") or e.get("fingerprint")
        if not fp:
            no_fp.append(e)
        else:
            fp_groups.setdefault(fp, []).append(e)

    after_fp = list(no_fp)
    extra_ids: dict[str, list[dict]] = {}

    for fp, group in fp_groups.items():
        if len(group) == 1:
            after_fp.append(group[0])
            continue
        winner, ids = _merge_group(group)
        after_fp.append(winner)
        extra_ids[fp] = ids

    # --- Pass 2: exact name + same date auto-merge (regardless of city) ---
    name_date_groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    for i, e in enumerate(after_fp):
        norm = normalise_title(e.get("title", ""))
        date_b = (e.get("date") or "")[:10]
        if norm and date_b:
            name_date_groups[(norm, date_b)].append(i)

    merged_pass2 = set()
    for key, indices in name_date_groups.items():
        if len(indices) < 2:
            continue
        group = [after_fp[i] for i in indices]
        winner, ids = _merge_group(group)
        fp = winner.get("fingerprint") or winner.get("source_id", "")
        extra_ids[fp] = ids
        winner_idx = None
        for i in indices:
            if after_fp[i] is winner:
                winner_idx = i
                break
        for i in indices:
            if i != winner_idx:
                merged_pass2.add(i)

    if merged_pass2:
        logger.info("    ✅ Same-name+date auto-merged %d event(s)", len(merged_pass2))
        after_fp = [e for i, e in enumerate(after_fp) if i not in merged_pass2]

    # --- Pass 3: fuzzy name grouping → AI ---
    # Compare all pairs for name similarity, cluster potential dupes
    norms = [normalise_title(e.get("title", "")) for e in after_fp]

    # Union-Find to cluster similar events
    parent = list(range(len(after_fp)))

    def _find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a, b):
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(after_fp)):
        if not norms[i]:
            continue
        for j in range(i + 1, len(after_fp)):
            if not norms[j]:
                continue
            ratio = SequenceMatcher(None, norms[i], norms[j]).ratio()
            if ratio >= 0.6:
                _union(i, j)

    # Build clusters from union-find
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(after_fp)):
        clusters[_find(i)].append(i)

    ambiguous_clusters = [
        indices for indices in clusters.values()
        if len(indices) >= 2
    ]

    if ambiguous_clusters:
        logger.info("    🤔 %d name-similar group(s) — asking AI...", len(ambiguous_clusters))
        ai_merges = ai_merge_decision(after_fp, ambiguous_clusters)

        # Apply AI merge decisions (process in reverse so indices stay valid)
        merged_away = set()
        for merge_indices in ai_merges:
            if len(merge_indices) < 2:
                continue
            group = [after_fp[i] for i in merge_indices if i not in merged_away]
            if len(group) < 2:
                continue
            winner, ids = _merge_group(group)
            fp = winner.get("fingerprint") or winner.get("source_id", "")
            extra_ids[fp] = ids
            # Mark losers for removal
            winner_idx = None
            for i in merge_indices:
                if i not in merged_away and after_fp[i] is winner:
                    winner_idx = i
                    break
            for i in merge_indices:
                if i != winner_idx and i not in merged_away:
                    merged_away.add(i)

        if merged_away:
            after_fp = [e for i, e in enumerate(after_fp) if i not in merged_away]

    return after_fp, extra_ids
