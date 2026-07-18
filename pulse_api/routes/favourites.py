"""Favourite events — /me/favourites."""

from flask import Blueprint, g, jsonify

from pulse_api.auth import require_auth
from pulse_api.db import supabase
from pulse_api.routes._helpers import (
    attach_event_images,
    attach_lineup,
    attach_social_posts,
    attach_ticket_links,
    enrich_events,
)

favourites_bp = Blueprint("favourites", __name__)


@favourites_bp.get("/me/favourites")
@require_auth
def list_favourites():
    """List the user's favourited events, soonest first.

    Includes past events (a favourite is a bookmark, not a feed) and ignores
    the user's location filter — you favourited it, you see it.
    """
    favs = (
        supabase.table("user_event_favourites")
        .select("event_id")
        .eq("user_id", g.user_id)
        .execute()
    )
    event_ids = [f["event_id"] for f in favs.data]
    if not event_ids:
        return jsonify([])

    events = (
        supabase.table("event_with_artist")
        .select("*")
        .in_("id", event_ids)
        .order("date", desc=False)
        .execute()
    )

    attach_event_images(events.data)
    attach_ticket_links(events.data)
    attach_lineup(events.data)
    attach_social_posts(events.data)
    enrich_events(events.data)

    return jsonify(events.data)


@favourites_bp.post("/me/favourites/<event_id>")
@require_auth
def add_favourite(event_id):
    """Favourite an event. Idempotent — favouriting twice is a no-op."""
    supabase.table("user_event_favourites").upsert(
        {"user_id": g.user_id, "event_id": event_id},
        on_conflict="user_id,event_id",
    ).execute()
    return jsonify({"status": "favourited"})


@favourites_bp.delete("/me/favourites/<event_id>")
@require_auth
def remove_favourite(event_id):
    """Unfavourite an event. Idempotent."""
    (
        supabase.table("user_event_favourites")
        .delete()
        .eq("user_id", g.user_id)
        .eq("event_id", event_id)
        .execute()
    )
    return jsonify({"status": "unfavourited"})
