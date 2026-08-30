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
from pulse_api.push import send_push_to_user

logger = logging.getLogger(__name__)

POSTMARK_API_URL = "https://api.postmarkapp.com/email"

# event_artists links created within this window after the user tracks an
# artist are treated as the initial backfill (the add-artist sync discovering
# events that already existed), not as new announcements.
BACKFILL_GRACE = timedelta(hours=1)


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def _ranked_artist_names(events: list[dict]) -> list[str]:
    """Distinct tracked-artist names across the events, most events first.

    Tiebreak: alphabetical (deterministic so reruns produce the same copy).
    """
    counts: dict[str, int] = {}
    for e in events:
        for a in (e.get("artists") or []):
            name = (a.get("name") or "").strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def _pick_subject(events: list[dict]) -> str:
    """Build a digest subject that leads with the top tracked artist."""
    n = len(events)
    names = _ranked_artist_names(events)

    if not names:
        # No tracked-artist names attached — fall back to a generic count.
        return f"{n} new show{'s' if n != 1 else ''} for your artists"

    if n == 1:
        return f"{names[0]} just announced a show"
    return f"{names[0]} + {n - 1} more new date{'s' if n - 1 != 1 else ''}"


def _ranked_artist_images(events: list[dict]) -> list[str]:
    """Image URLs for the top-ranked artists (same order as the push copy),
    skipping artists without an image. Capped at 3 — the app stacks at most
    three avatars."""
    images: dict[str, str] = {}
    for e in events:
        for a in (e.get("artists") or []):
            name = (a.get("name") or "").strip()
            if name and a.get("image_url") and name not in images:
                images[name] = a["image_url"]
    return [images[n] for n in _ranked_artist_names(events) if n in images][:3]


def _pick_push_copy(events: list[dict]) -> str:
    """Push notification body led by the announcing artists.

    1 artist  → "X announced a new event near you"
    2 artists → "X & Y announced new events near you"
    3+        → "X, Y and n more announced new events near you"
    """
    names = _ranked_artist_names(events)
    n_events = len(events)

    if not names:
        if n_events == 1:
            return "A new event was announced near you"
        return f"{n_events} new events announced near you"

    if len(names) == 1:
        what = "a new event" if n_events == 1 else "new events"
        return f"{names[0]} announced {what} near you"
    if len(names) == 2:
        return f"{names[0]} & {names[1]} announced new events near you"
    return f"{names[0]}, {names[1]} and {len(names) - 2} more announced new events near you"


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
    """Users with the newsletter and/or push notifications enabled."""
    result = (
        supabase.table("user_email_preferences")
        .select("user_id, email, recipients, digest_enabled, push_enabled, "
                "default_cities, default_countries")
        .or_("digest_enabled.eq.true,push_enabled.eq.true")
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

    # Get user's tracked artist IDs, plus when each was tracked — needed to
    # tell a genuine lineup announcement apart from a fresh add's backfill.
    subs = (
        supabase.table("user_artists")
        .select("artist_id, created_at")
        .eq("user_id", user_id)
        .execute()
    )
    artist_ids = [s["artist_id"] for s in subs.data]
    tracked_at = {s["artist_id"]: _parse_ts(s["created_at"]) for s in subs.data}
    if not artist_ids:
        return [], cities

    since_iso = since.isoformat()

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

    # Narrow by date first (small result set), then intersect with the user's
    # tracked artists via event_artists. Filtering events by a huge IN list of
    # artist-derived event IDs blows up the request URL and trips PostgREST's
    # 400 "JSON could not be generated" / "Bad Request" responses.

    # Set 1: newly created upcoming events.
    q1 = (
        supabase.table("events")
        .select("*")
        .gte("date", "now()")
        .gte("created_at", since_iso)
    )
    q1 = _apply_location_filter(q1)
    new_events_rows = q1.order("date", desc=False).execute().data

    # Set 2: existing upcoming events that got a new artist link since `since`.
    # Narrow event_artists by created_at + tracked artists first (small set),
    # then look the events up by ID.
    new_link_rows = (
        supabase.table("event_artists")
        .select("event_id, artist_id, created_at")
        .in_("artist_id", artist_ids)
        .gte("created_at", since_iso)
        .execute()
        .data
    )
    # Only links for artists the user was already tracking count as
    # announcements. When a freshly added artist turns up on events that were
    # already in the DB, that's the initial backfill — not news.
    new_link_event_ids = {
        r["event_id"]
        for r in new_link_rows
        if r["artist_id"] in tracked_at
        and _parse_ts(r["created_at"]) > tracked_at[r["artist_id"]] + BACKFILL_GRACE
    }
    new_link_event_ids -= {e["id"] for e in new_events_rows}

    new_announcement_events: list[dict] = []
    if new_link_event_ids:
        q2 = (
            supabase.table("events")
            .select("*")
            .in_("id", list(new_link_event_ids))
            .gte("date", "now()")
        )
        q2 = _apply_location_filter(q2)
        new_announcement_events = q2.order("date", desc=False).execute().data

    # Merge and deduplicate
    candidates: list[dict] = []
    seen_ids: set[str] = set()
    for event in new_events_rows + new_announcement_events:
        if event["id"] not in seen_ids:
            seen_ids.add(event["id"])
            candidates.append(event)

    if not candidates:
        return [], cities

    # Intersect with event_artists to confirm each event has at least one
    # tracked artist and to build the per-event artist display map.
    candidate_ids = [e["id"] for e in candidates]
    links = (
        supabase.table("event_artists")
        .select("event_id, artist_id, created_at, billing, artists(name, image_url)")
        .in_("event_id", candidate_ids)
        .in_("artist_id", artist_ids)
        .execute()
        .data
    )

    event_artist_map: dict[str, list] = {}
    for link in links:
        eid = link["event_id"]
        event_artist_map.setdefault(eid, []).append({
            "artist_id": link["artist_id"],
            "billing": link.get("billing"),
            **(link.get("artists") or {}),
        })

    # Set-2 events the user could already see via an older tracked-artist
    # link were announced (or backfilled) before this window — a further
    # artist joining the lineup isn't a new event for them.
    already_visible = {
        link["event_id"] for link in links if _parse_ts(link["created_at"]) < since
    }

    all_events = [
        e for e in candidates
        if e["id"] in event_artist_map
        and not (e["id"] in new_link_event_ids and e["id"] in already_visible)
    ]
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


def _record_notifications(user_id: str, events: list[dict]) -> None:
    """Write in-app notification rows for this batch of new events.

    Idempotent (unique user_id+event_id) and best-effort — the feed missing
    a row must never block the email/push send.
    """
    rows = [{"user_id": user_id, "event_id": e["id"]} for e in events]
    try:
        supabase.table("user_notifications").upsert(
            rows, on_conflict="user_id,event_id", ignore_duplicates=True
        ).execute()
    except Exception as e:
        logger.warning(
            "[DIGEST] Failed to record notifications for %s: %s",
            user_id, str(e)[:120],
        )


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
        recipients = user.get("recipients") or (
            [user["email"]] if user.get("email") else []
        )

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
                user_id, recipients, len(events), since.isoformat(),
            )

            # Only send if there are new events
            if not events:
                results["users_processed"] += 1
                continue

            _record_notifications(user_id, events)

            if user.get("digest_enabled") and recipients:
                # Build a display label from the user's tracked locations
                city_display = ", ".join(cities_label) if cities_label else None
                text = build_digest_text(events)
                subject = _pick_subject(events)

                for recipient in recipients:
                    html = build_digest_html(events, recipient, city_display)
                    await _send_postmark_email(
                        to=recipient,
                        subject=subject,
                        html_body=html,
                        text_body=text,
                    )
                    results["emails_sent"] += 1
                    logger.info("[DIGEST] Sent to %s (%d events)", recipient, len(events))

            if user.get("push_enabled"):
                pushed = await send_push_to_user(
                    user_id,
                    "PULSE",
                    _pick_push_copy(events),
                    artist_images=_ranked_artist_images(events),
                )
                if pushed:
                    logger.info("[DIGEST] Pushed to %d device(s) for %s", pushed, user_id)

            _log_digest_sent(user_id, len(events))

        except Exception as e:
            logger.error(
                "[DIGEST] Failed for user %s: %s",
                user_id, str(e),
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
