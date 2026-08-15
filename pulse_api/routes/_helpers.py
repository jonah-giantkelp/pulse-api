"""Shared helpers used across route blueprints.

Anything used by more than one blueprint lives here. Single-blueprint
helpers stay in their own route module.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import g

from pulse_api.db import supabase


def upcoming_cutoff() -> str:
    """Start of today in London, as an ISO timestamp for date filters.

    Events count as upcoming until the day ends, not until their start
    time passes — tonight's gig stays on the events page all day.
    """
    return (
        datetime.now(ZoneInfo("Europe/London"))
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )


def run_async(coro):
    """Run an async function from sync Flask context."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# PostgREST builds GETs with IN filters in the querystring, so passing hundreds
# of UUIDs overflows the URL length and the server returns "Bad Request".
# Chunk the IN clause into small-enough batches and concatenate the results.
_IN_CHUNK_SIZE = 100


def chunked_in(table: str, column: str, values: list, select: str) -> list[dict]:
    """Run a batched `.select(...).in_(column, values)` query in chunks,
    concatenating the rows. Safe for large `values` lists that would
    otherwise blow past PostgREST's URL length limit."""
    if not values:
        return []
    rows: list[dict] = []
    for i in range(0, len(values), _IN_CHUNK_SIZE):
        chunk = values[i : i + _IN_CHUNK_SIZE]
        resp = (
            supabase.table(table)
            .select(select)
            .in_(column, chunk)
            .execute()
        )
        rows.extend(resp.data or [])
    return rows


def attach_ticket_links(events: list[dict]) -> None:
    """Attach ticket_links (with pricing) from event_external_ids."""
    event_ids = [e["id"] for e in events]
    if not event_ids:
        return
    rows_data = chunked_in(
        "event_external_ids",
        "event_id",
        event_ids,
        "event_id, source, ticket_url, price_min, price_max, currency",
    )
    links_map: dict[str, list] = {}
    for r in rows_data:
        if not r.get("ticket_url"):
            continue
        link = {
            "source": r["source"],
            "url": r["ticket_url"],
        }
        if r.get("price_min") is not None:
            link["price_min"] = float(r["price_min"])
        if r.get("price_max") is not None:
            link["price_max"] = float(r["price_max"])
        if r.get("currency"):
            link["currency"] = r["currency"]
        links_map.setdefault(r["event_id"], []).append(link)
    for event in events:
        event["ticket_links"] = links_map.get(event["id"], [])


def attach_lineup(events: list[dict]) -> None:
    """Attach the `lineup` column from `events` onto each row.

    The `event_with_artist` view uses `select e.*` so its column list is
    frozen and doesn't include `lineup` (added in migration 012). Rather
    than rebuilding the view, fetch the column directly here.
    """
    event_ids = [e["id"] for e in events]
    if not event_ids:
        return
    rows_data = chunked_in("events", "id", event_ids, "id, lineup")
    lineup_map = {r["id"]: r.get("lineup") for r in rows_data}
    for event in events:
        event["lineup"] = lineup_map.get(event["id"])


def attach_social_posts(events: list[dict]) -> None:
    """Attach the social post that originally surfaced each event.

    Uses the source_post_id from raw_data (the post the distiller extracted
    the event from), NOT the accumulated source_post_ids array which
    collects every post that ever mentioned the event.
    """
    post_ids_needed = []
    for e in events:
        raw = e.get("raw_data") or {}
        pid = raw.get("source_post_id")
        if pid:
            post_ids_needed.append(pid)
    if not post_ids_needed:
        return
    unique_ids = list(set(post_ids_needed))
    rows_data = chunked_in(
        "social_posts",
        "post_id",
        unique_ids,
        "post_id, platform, caption, media_url, posted_at",
    )
    post_map = {r["post_id"]: r for r in rows_data}
    for event in events:
        raw = event.get("raw_data") or {}
        pid = raw.get("source_post_id")
        if pid and pid in post_map:
            event["social_post"] = post_map[pid]


def get_user_location_prefs() -> tuple[list[str], list[str]]:
    """Return (default_cities, default_countries) for the current user.

    Falls back to (['London'], []) when no prefs row exists — keeps legacy
    users (pre-trigger) on the same default as fresh signups.
    """
    result = (
        supabase.table("user_email_preferences")
        .select("default_cities, default_countries")
        .eq("user_id", g.user_id)
        .execute()
    )
    if not result.data:
        return ["London"], []
    row = result.data[0]
    return (
        list(row.get("default_cities") or []),
        list(row.get("default_countries") or []),
    )


def apply_user_location_filter(
    query,
    scope: str | None,
    countries_override: list[str] | None = None,
):
    """Apply the user's default_cities/default_countries filter to `query`.

    The client toggle is:
      * scope == "all"        → no filter (return everything).
      * countries_override     → filter by those country codes only,
                                ignoring the user's stored prefs.
      * otherwise             → filter by the user's prefs (city ∈ defaults
                                OR country ∈ defaults). If the user has no
                                prefs at all, return as-is (no filter).

    Mirrors the OR-across-columns logic in email_digest._apply_location_filter.
    """
    if scope == "all":
        return query

    if countries_override:
        return query.in_("country", countries_override)

    cities, countries = get_user_location_prefs()
    if cities and not countries:
        return query.in_("city", cities)
    if countries and not cities:
        return query.in_("country", countries)
    if cities and countries:
        city_csv = ",".join(cities)
        country_csv = ",".join(countries)
        return query.or_(f"city.in.({city_csv}),country.in.({country_csv})")
    return query


def enrich_events(events: list[dict]) -> None:
    """Extract useful fields from raw_data and strip it from the response."""
    for event in events:
        raw = event.get("raw_data") or {}
        source = event.get("source", "")
        detail: dict = {}

        # Lineup is a first-class column populated during sync (longest across
        # every source that contributed to this event). Fall back to the
        # per-source extraction below for events synced before the column was
        # introduced.
        lineup_col = event.get("lineup")
        if isinstance(lineup_col, list) and lineup_col:
            detail["lineup"] = lineup_col

        if source == "dice":
            about = raw.get("about") or {}
            desc = about.get("description") or ""
            if desc:
                detail["description"] = desc
            # Fallback for legacy events without the lineup column populated
            if "lineup" not in detail:
                lineup = raw.get("summary_lineup")
                if isinstance(lineup, dict):
                    names = [
                        a.get("name") for a in (lineup.get("top_artists") or [])
                        if isinstance(a, dict) and a.get("name")
                    ]
                    if names:
                        detail["lineup"] = names
                elif isinstance(lineup, list):
                    detail["lineup"] = lineup
            # Age restriction
            for h in (about.get("highlights") or []):
                if h.get("type") == "age_restriction":
                    detail["age_restriction"] = h.get("title")
                    break
            # Status (on-sale, sold-out, etc.)
            status = raw.get("status")
            if status:
                detail["status"] = status
            # Doors time from venue
            venues = raw.get("venues") or []
            if venues and isinstance(venues[0], dict):
                doors = venues[0].get("doors_open_date")
                if doors:
                    detail["doors_open"] = doors
            # Venue address
            if venues and isinstance(venues[0], dict):
                addr = venues[0].get("address")
                if addr:
                    detail["venue_address"] = addr

        elif source == "skiddle":
            desc = raw.get("description") or ""
            if desc:
                detail["description"] = desc
            times = raw.get("openingtimes") or {}
            if times.get("doorsopen"):
                detail["doors_open"] = times["doorsopen"]
            if times.get("lastentry"):
                detail["last_entry"] = times["lastentry"]
            if times.get("doorsclose"):
                detail["doors_close"] = times["doorsclose"]
            minage = raw.get("minage")
            if minage and minage != "0":
                detail["age_restriction"] = f"{minage}+"
            venue = raw.get("venue") or {}
            if venue.get("address"):
                addr_parts = [
                    p.strip() for p in [
                        venue.get("address"),
                        venue.get("town"),
                        venue.get("postcode"),
                    ] if p and p.strip()
                ]
                detail["venue_address"] = ", ".join(addr_parts)
            if venue.get("type"):
                detail["venue_type"] = venue["type"]
            if raw.get("cancelled") == "1":
                detail["status"] = "cancelled"

        elif source == "ticketmaster":
            info = raw.get("info") or raw.get("pleaseNote") or ""
            if info:
                detail["description"] = info
            dates = raw.get("dates") or {}
            start = dates.get("start") or {}
            if start.get("localTime"):
                detail["doors_open"] = start["localTime"]
            status = (dates.get("status") or {}).get("code")
            if status:
                detail["status"] = status
            # Venue address from _embedded
            embedded_venues = (raw.get("_embedded") or {}).get("venues") or []
            if embedded_venues:
                v = embedded_venues[0]
                addr_parts = [
                    p for p in [
                        (v.get("address") or {}).get("line1"),
                        (v.get("city") or {}).get("name"),
                        (v.get("postalCode")),
                    ] if p
                ]
                if addr_parts:
                    detail["venue_address"] = ", ".join(addr_parts)
            # Genre
            for c in raw.get("classifications") or []:
                genre = (c.get("genre") or {}).get("name")
                if genre and genre != "Undefined":
                    detail["genre"] = genre
                    break

        elif source == "ra":
            # RA lineup from artists array (fallback when _canonical_lineup
            # isn't set — e.g. events synced before that field existed)
            if "lineup" not in detail:
                artists = raw.get("artists") or []
                if artists:
                    detail["lineup"] = [a.get("name") for a in artists if a.get("name")]
            start_time = raw.get("startTime")
            if start_time:
                # Extract HH:MM from ISO
                try:
                    detail["doors_open"] = start_time.split("T")[1][:5]
                except (IndexError, AttributeError):
                    pass

        elif source == "bandsintown":
            if raw.get("isFree"):
                detail["status"] = "free"
            if raw.get("streamingEvent"):
                detail["status"] = "streaming"

        elif source in ("twitter", "instagram", "social_ai"):
            # The raw_data IS the social context — keep it accessible
            if raw.get("title"):
                detail["description"] = raw.get("title")

        # Strip tracked artists from the full lineup — they're already
        # rendered as structured chips/headliners on the event card via
        # event.artists, no need to repeat them in the "full lineup" section.
        lineup = detail.get("lineup")
        if lineup:
            tracked_names = {
                (a.get("name") or "").strip().casefold()
                for a in (event.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            }
            if tracked_names:
                detail["lineup"] = [
                    n for n in lineup
                    if n and n.strip().casefold() not in tracked_names
                ]

        if detail:
            event["detail"] = detail

        # Drop raw_data from the response — it's bulky and internal
        event.pop("raw_data", None)


def attach_extras(events: list[dict]) -> None:
    """Run all event enrichments, with the independent attach_* queries in
    parallel threads (each writes its own keys onto the event dicts).
    enrich_events runs after — it reads the lineup attach_lineup adds.

    Keeping the response fast matters beyond UX: the iOS app's pull-to-refresh
    task gets torn down by SwiftUI if the request is still in flight when the
    view updates, surfacing a spurious "cancelled" error.
    """
    if not events:
        return
    attachers = (
        attach_event_images,
        attach_ticket_links,
        attach_lineup,
        attach_social_posts,
    )
    with ThreadPoolExecutor(max_workers=len(attachers)) as pool:
        futures = [pool.submit(fn, events) for fn in attachers]
        for future in futures:
            future.result()
    enrich_events(events)


def attach_event_images(events: list[dict]) -> None:
    """Attach `images` array from event_images table onto each event."""
    event_ids = [e["id"] for e in events]
    if not event_ids:
        return
    image_rows_data = chunked_in(
        "event_images",
        "event_id",
        event_ids,
        "event_id, image_url, image_type",
    )
    image_map: dict[str, list] = {}
    for img in image_rows_data:
        image_map.setdefault(img["event_id"], []).append({
            "image_url": img["image_url"],
            "image_type": img["image_type"],
        })
    for event in events:
        event["images"] = image_map.get(event["id"], [])
