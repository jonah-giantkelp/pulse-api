"""In-app notification feed — /me/notifications.

Rows are written by the digest job (one per user+event announcement);
this blueprint only reads them and flips read_at.
"""

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify

from pulse_api.auth import require_auth
from pulse_api.db import supabase
from pulse_api.routes._helpers import attach_extras

notifications_bp = Blueprint("notifications", __name__)


def _clean_ts(raw: str | None) -> str | None:
    """Strip microseconds — the app's ISO8601 decoder can't parse 6-digit
    fractional seconds."""
    if not raw:
        return None
    try:
        return (
            datetime.fromisoformat(raw.replace("Z", "+00:00"))
            .replace(microsecond=0)
            .isoformat()
        )
    except ValueError:
        return raw


@notifications_bp.get("/me/notifications")
@require_auth
def list_notifications():
    """The user's notifications, newest first, each with its full event."""
    rows = (
        supabase.table("user_notifications")
        .select("id, event_id, created_at, read_at")
        .eq("user_id", g.user_id)
        .order("created_at", desc=True)
        .limit(200)
        .execute()
        .data
    )
    if not rows:
        return jsonify([])

    events = (
        supabase.table("event_with_artist")
        .select("*")
        .in_("id", [r["event_id"] for r in rows])
        .execute()
        .data
    )
    attach_extras(events)
    event_map = {e["id"]: e for e in events}

    return jsonify([
        {
            "id": r["id"],
            "created_at": _clean_ts(r["created_at"]),
            "read_at": _clean_ts(r["read_at"]),
            "event": event_map[r["event_id"]],
        }
        for r in rows
        if r["event_id"] in event_map  # event since deleted → drop the row
    ])


@notifications_bp.post("/me/notifications/read")
@require_auth
def mark_all_read():
    """Mark every unread notification as read."""
    (
        supabase.table("user_notifications")
        .update({"read_at": datetime.now(timezone.utc).isoformat()})
        .eq("user_id", g.user_id)
        .is_("read_at", "null")
        .execute()
    )
    return jsonify({"status": "read"})
