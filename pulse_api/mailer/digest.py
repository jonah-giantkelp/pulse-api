"""Daily email digest sender using Postmark.

Queries for new events added since the last digest for each user,
builds branded HTML, and sends via Postmark's API.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx

from pulse_api.config import settings
from pulse_api.db import supabase
from pulse_api.mailer.template import build_digest_html, build_digest_text

logger = logging.getLogger(__name__)

POSTMARK_API_URL = "https://api.postmarkapp.com/email"


async def _send_postmark_email(
    to: str,
    subject: str,
    html_body: str,
    text_body: str,
) -> dict:
    """Send a single email via Postmark's API."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            POSTMARK_API_URL,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": settings.postmark_server_token,
            },
            json={
                "From": settings.postmark_from_email,
                "To": to,
                "Subject": subject,
                "HtmlBody": html_body,
                "TextBody": text_body,
                "MessageStream": "outbound",
            },
        )
        resp.raise_for_status()
        return resp.json()


def _get_users_with_digest_enabled() -> list[dict]:
    """Get all users who have digest enabled with their email and location prefs."""
    result = (
        supabase.table("user_email_preferences")
        .select("user_id, email, default_cities, default_countries")
        .eq("digest_enabled", True)
        .execute()
    )
    return result.data


def _get_last_digest_time(user_id: str) -> datetime:
    """Get the last time we sent a digest to this user.

    Falls back to 24 hours ago if no prior digest exists.
    """
    result = (
        supabase.table("email_digest_log")
        .select("sent_at")
        .eq("user_id", user_id)
        .order("sent_at", desc=True)
        .limit(1)
        .execute()
    )
    if result.data:
        return datetime.fromisoformat(
            result.data[0]["sent_at"].replace("Z", "+00:00")
        )
    return datetime.now(timezone.utc) - timedelta(hours=24)


def _get_new_events_for_user(
    user_id: str,
    since: datetime,
    default_cities: list[str] | None = None,
    default_countries: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """Get events created since `since` for artists the user tracks.

    Only returns upcoming events (date >= now).
    Filters by the user's default_cities/default_countries if set.
    Returns (events, cities_label).
    """
    cities = default_cities or []
    countries = default_countries or []

    # Get user's tracked artist IDs
    subs = (
        supabase.table("user_artists")
        .select("artist_id")
        .eq("user_id", user_id)
        .execute()
    )
    artist_ids = [s["artist_id"] for s in subs.data]
    if not artist_ids:
        return [], cities

    # Get all event links for the user's artists
    links = (
        supabase.table("event_artists")
        .select("event_id, artist_id, billing, created_at, artists(name, image_url)")
        .in_("artist_id", artist_ids)
        .execute()
    )
    if not links.data:
        return [], cities

    since_iso = since.isoformat()

    # Build event → artists map, and track which events have new artist
    # announcements (event_artists.created_at >= since)
    event_artist_map: dict[str, list] = {}
    event_ids_new_link: set[str] = set()
    all_event_ids: set[str] = set()
    for link in links.data:
        eid = link["event_id"]
        all_event_ids.add(eid)
        event_artist_map.setdefault(eid, []).append({
            "artist_id": link["artist_id"],
            "billing": link.get("billing"),
            **(link.get("artists") or {}),
        })
        # Track events where a new artist was linked since last digest
        if link.get("created_at", "") >= since_iso:
            event_ids_new_link.add(eid)

    # Fetch upcoming events (optionally filtered by location) that are either:
    # 1. Newly created events (events.created_at >= since), OR
    # 2. Existing events with a new artist announcement (event_artists.created_at >= since)
    #
    # Supabase doesn't support OR across tables in a single query,
    # so we fetch both sets and merge.

    def _apply_location_filter(query):
        """Apply city/country filters if the user has location preferences."""
        if cities and not countries:
            query = query.in_("city", cities)
        elif countries and not cities:
            query = query.in_("country", countries)
        elif cities and countries:
            # PostgREST OR: city in (...) or country in (...)
            city_csv = ",".join(cities)
            country_csv = ",".join(countries)
            query = query.or_(
                f"city.in.({city_csv}),country.in.({country_csv})"
            )
        return query

    # Set 1: new events
    q1 = (
        supabase.table("events")
        .select("*")
        .in_("id", list(all_event_ids))
        .gte("date", "now()")
        .gte("created_at", since_iso)
    )
    q1 = _apply_location_filter(q1)
    new_events = q1.order("date", desc=False).execute()

    # Set 2: existing events with new artist links
    new_announcement_events = []
    # Only query if there are event IDs with new links that aren't already
    # covered by new events
    new_event_ids = {e["id"] for e in new_events.data}
    extra_ids = event_ids_new_link - new_event_ids
    if extra_ids:
        q2 = (
            supabase.table("events")
            .select("*")
            .in_("id", list(extra_ids))
            .gte("date", "now()")
        )
        q2 = _apply_location_filter(q2)
        extra = q2.order("date", desc=False).execute()
        new_announcement_events = extra.data

    # Merge and deduplicate
    seen_ids: set[str] = set()
    all_events: list[dict] = []
    for event in new_events.data + new_announcement_events:
        if event["id"] not in seen_ids:
            seen_ids.add(event["id"])
            all_events.append(event)

    # Sort by date
    all_events.sort(key=lambda e: e.get("date", ""))

    # Fetch ticket URLs from external IDs (some sources store better URLs there)
    if all_events:
        fetched_ids = [e["id"] for e in all_events]
        ext_ids = (
            supabase.table("event_external_ids")
            .select("event_id, source, ticket_url")
            .in_("event_id", fetched_ids)
            .execute()
        )
        # Prefer external ticket URLs (often more direct)
        ext_ticket_map: dict[str, str] = {}
        for ext in ext_ids.data:
            if ext.get("ticket_url"):
                ext_ticket_map.setdefault(ext["event_id"], ext["ticket_url"])

        for event in all_events:
            event["artists"] = event_artist_map.get(event["id"], [])
            # Use external ticket URL if available, else fall back to event's own
            if event["id"] in ext_ticket_map and not event.get("ticket_url"):
                event["ticket_url"] = ext_ticket_map[event["id"]]

    return all_events, cities


def _log_digest_sent(user_id: str, event_count: int) -> None:
    """Record that we sent a digest to this user."""
    supabase.table("email_digest_log").insert({
        "user_id": user_id,
        "events_sent": event_count,
    }).execute()


async def send_daily_digests() -> dict:
    """Send the daily digest email to all opted-in users.

    Returns a summary of what was sent.
    """
    users = _get_users_with_digest_enabled()
    logger.info("[DIGEST] Found %d users with digest enabled", len(users))

    results = {
        "users_processed": 0,
        "emails_sent": 0,
        "errors": [],
    }

    for user in users:
        user_id = user["user_id"]
        email = user["email"]

        try:
            since = _get_last_digest_time(user_id)
            events, cities_label = _get_new_events_for_user(
                user_id,
                since,
                default_cities=user.get("default_cities") or [],
                default_countries=user.get("default_countries") or [],
            )

            logger.info(
                "[DIGEST] User %s (%s): %d new events since %s",
                user_id, email, len(events), since.isoformat(),
            )

            # Only send if there are new events
            if not events:
                results["users_processed"] += 1
                continue

            # Build a display label from the user's tracked locations
            city_display = ", ".join(cities_label) if cities_label else None
            html = build_digest_html(events, email, city_display)
            text = build_digest_text(events)
            subject = f"Pulse \u00b7 {len(events)} new event{'s' if len(events) != 1 else ''} for your artists"

            await _send_postmark_email(
                to=email,
                subject=subject,
                html_body=html,
                text_body=text,
            )

            _log_digest_sent(user_id, len(events))
            results["emails_sent"] += 1
            logger.info("[DIGEST] Sent to %s (%d events)", email, len(events))

        except Exception as e:
            logger.error(
                "[DIGEST] Failed for user %s (%s): %s",
                user_id, email, str(e),
            )
            results["errors"].append({
                "user_id": user_id,
                "error": str(e),
            })

        results["users_processed"] += 1

    logger.info(
        "[DIGEST] Complete — %d sent, %d errors",
        results["emails_sent"], len(results["errors"]),
    )
    return results
