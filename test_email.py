"""Test the email digest by sending all events added today for a given user.

Usage:
    python test_email.py <user_id> <email>
    python test_email.py                      # uses DEV_USER_ID and TEST_EMAIL from .env

Requires POSTMARK_SERVER_TOKEN to be set in .env.
Requires the user to have tracked artists with events in the DB.
Does NOT require the user_email_preferences table to exist.
"""

import asyncio
import sys
from datetime import datetime, timezone, time

from pulse_api.config import settings
from pulse_api.db import supabase
from pulse_api.mailer import build_digest_html, build_digest_text
from pulse_api.mailer.digest import _send_postmark_email


def get_todays_events(
    user_id: str, since: datetime | None = None
) -> tuple[list[dict], str]:
    """Get events created on/after `since` (default: start of today UTC)
    for a user's tracked artists."""
    # Get user's tracked artist IDs
    subs = (
        supabase.table("user_artists")
        .select("artist_id, city")
        .eq("user_id", user_id)
        .execute()
    )
    artist_ids = [s["artist_id"] for s in subs.data]
    if not artist_ids:
        print(f"No tracked artists for user {user_id}")
        return [], None

    user_city = subs.data[0].get("city")
    print(f"Found {len(artist_ids)} tracked artist(s) (city: {user_city or 'all'})")

    # Default: start of today (UTC); override via `since` arg
    if since is None:
        since = datetime.combine(
            datetime.now(timezone.utc).date(),
            time.min,
            tzinfo=timezone.utc,
        )
    today_start = since.isoformat()

    print(f"Fetching events created since {today_start}" + (f" in {user_city}" if user_city else ""))

    # Query events first with the narrow filters (upcoming + created today).
    # Filtering by artist would blow up the URL length with a huge IN clause,
    # so we narrow by date first then intersect with the user's artists.
    query = (
        supabase.table("events")
        .select("*")
        .gte("date", "now()")
        .gte("created_at", today_start)
    )
    if user_city:
        query = query.ilike("city", user_city)
    events = query.order("date", desc=False).execute()
    if not events.data:
        print("No matching events created today")
        return [], user_city

    # Look up which of these events are linked to the user's tracked artists
    fetched_ids = [e["id"] for e in events.data]
    links = (
        supabase.table("event_artists")
        .select("event_id, artist_id, billing, artists(name, image_url)")
        .in_("event_id", fetched_ids)
        .in_("artist_id", artist_ids)
        .execute()
    )
    if not links.data:
        print("No tracked artists linked to today's new events")
        return [], user_city

    # Build event → artists map
    event_artist_map: dict[str, list] = {}
    for link in links.data:
        eid = link["event_id"]
        event_artist_map.setdefault(eid, []).append({
            "artist_id": link["artist_id"],
            "billing": link.get("billing"),
            **(link.get("artists") or {}),
        })

    # Keep only events that have at least one tracked artist
    events.data = [e for e in events.data if e["id"] in event_artist_map]
    for event in events.data:
        event["artists"] = event_artist_map.get(event["id"], [])

    # Fetch ticket URLs from external IDs
    if events.data:
        fetched_ids = [e["id"] for e in events.data]
        ext_ids = (
            supabase.table("event_external_ids")
            .select("event_id, source, ticket_url")
            .in_("event_id", fetched_ids)
            .execute()
        )
        ext_ticket_map: dict[str, str] = {}
        for ext in ext_ids.data:
            if ext.get("ticket_url"):
                ext_ticket_map.setdefault(ext["event_id"], ext["ticket_url"])

        for event in events.data:
            if event["id"] in ext_ticket_map and not event.get("ticket_url"):
                event["ticket_url"] = ext_ticket_map[event["id"]]

    return events.data, user_city


async def send_test_digest(
    user_id: str, email: str, since: datetime | None = None
):
    """Build and send a test digest email."""
    print(f"\n{'=' * 50}")
    print(f"PULSE EMAIL DIGEST TEST")
    print(f"{'=' * 50}")
    print(f"User:  {user_id}")
    print(f"Email: {email}")
    if since:
        print(f"Since: {since.isoformat()}")
    print()

    if not settings.postmark_server_token:
        print("ERROR: POSTMARK_SERVER_TOKEN not set in .env")
        sys.exit(1)

    events, city = get_todays_events(user_id, since=since)
    print(f"\n{len(events)} event(s) in {city}\n")

    if not events:
        print("No events to send. Sending empty digest for template preview...")

    for e in events:
        artists = ", ".join(a.get("name", "?") for a in e.get("artists", []))
        print(f"  📅 {e.get('date', '?')[:10]}  {e.get('title', '?')}")
        print(f"     {artists} @ {e.get('venue', '?')}, {e.get('city', '?')}")
        if e.get("ticket_url"):
            print(f"     🎫 {e['ticket_url'][:60]}")
        print()

    html = build_digest_html(events, email, city)
    text = build_digest_text(events)

    count = len(events)
    subject = f"[TEST] Pulse · {count} new event{'s' if count != 1 else ''} for your artists"

    print(f"\nSending via Postmark to {email}...")
    try:
        result = await _send_postmark_email(
            to=email,
            subject=subject,
            html_body=html,
            text_body=text,
        )
        print(f"✅ Sent! MessageID: {result.get('MessageID', '?')}")
        print(f"   To: {result.get('To', '?')}")
        print(f"   SubmittedAt: {result.get('SubmittedAt', '?')}")
    except Exception as e:
        print(f"❌ Send failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Pull out --since YYYY-MM-DD[THH:MM] if provided
    argv = sys.argv[1:]
    since_val: datetime | None = None
    if "--since" in argv:
        idx = argv.index("--since")
        try:
            raw = argv[idx + 1]
        except IndexError:
            print("ERROR: --since requires a value (YYYY-MM-DD or ISO datetime)")
            sys.exit(1)
        # Accept a plain date or a full ISO datetime
        try:
            if "T" in raw:
                since_val = datetime.fromisoformat(raw)
            else:
                since_val = datetime.combine(
                    datetime.fromisoformat(raw).date(),
                    time.min,
                )
            if since_val.tzinfo is None:
                since_val = since_val.replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"ERROR: could not parse --since value '{raw}'")
            sys.exit(1)
        # Remove the flag + value so positional parsing below still works
        argv = argv[:idx] + argv[idx + 2:]

    if len(argv) >= 2:
        user_id = argv[0]
        email = argv[1]
    elif len(argv) == 1:
        user_id = argv[0]
        email = settings.test_email
    else:
        user_id = settings.dev_user_id
        email = settings.test_email

    if not email:
        print("ERROR: No email provided and TEST_EMAIL not set in .env")
        sys.exit(1)

    asyncio.run(send_test_digest(user_id, email, since=since_val))
