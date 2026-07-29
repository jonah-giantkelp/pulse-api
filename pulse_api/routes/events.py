"""Event listing endpoints — /me/events and /artists/<id>/events."""

from flask import Blueprint, g, jsonify, request

from pulse_api.routes._helpers import (
    apply_user_location_filter,
    attach_extras,
)
from pulse_api.auth import require_auth
from pulse_api.db import supabase

events_bp = Blueprint("events", __name__)


def _parse_csv(value: str | None) -> list[str] | None:
    """Parse a comma-separated query param into a list, or None if empty."""
    if not value:
        return None
    items = [v.strip().upper() for v in value.split(",") if v.strip()]
    return items or None


@events_bp.get("/me/events")
@require_auth
def list_my_events():
    """List upcoming events for all artists the user tracks.

    Uses the event_with_artist view which joins events → event_artists →
    artists and groups by event, so each row already has an `artists` JSON
    array embedded.
    """
    subs = (
        supabase.table("user_artists")
        .select("artist_id")
        .eq("user_id", g.user_id)
        .execute()
    )
    artist_ids = [s["artist_id"] for s in subs.data]
    if not artist_ids:
        return jsonify([])

    # Location scope toggle: default is to filter by the user's
    # default_cities/default_countries (which default to {'London'} on
    # signup). Pass ?scope=all to bypass and see every location, or
    # ?countries=GB,FR to override with specific country codes.
    scope = request.args.get("scope")
    countries_override = _parse_csv(request.args.get("countries"))

    query = (
        supabase.table("event_with_artist")
        .select("*")
        .in_("artist_id", artist_ids)
        .gte("date", "now()")
    )
    query = apply_user_location_filter(query, scope, countries_override)
    events = query.order("date", desc=False).execute()

    # Dedupe — the same event can appear for multiple tracked artists
    seen = set()
    unique = []
    for event in events.data:
        if event["id"] not in seen:
            seen.add(event["id"])
            unique.append(event)

    attach_extras(unique)

    return jsonify(unique)


@events_bp.get("/artists/<artist_id>/events")
@require_auth
def list_artist_events(artist_id):
    """List upcoming events for a specific artist via the view.

    Defaults to filtering by the user's default_cities/default_countries;
    pass ?scope=all to see every location, or ?countries=GB,FR to override.
    """
    scope = request.args.get("scope")
    countries_override = _parse_csv(request.args.get("countries"))

    query = (
        supabase.table("event_with_artist")
        .select("*")
        .eq("artist_id", artist_id)
        .gte("date", "now()")
    )
    query = apply_user_location_filter(query, scope, countries_override)
    events = query.order("date", desc=False).execute()

    attach_extras(events.data)

    return jsonify(events.data)
