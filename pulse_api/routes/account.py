"""Account endpoints — push token registration and account deletion."""

import logging

from flask import Blueprint, g, jsonify, request

from pulse_api.auth import require_auth
from pulse_api.db import supabase

logger = logging.getLogger(__name__)

account_bp = Blueprint("account", __name__)


@account_bp.post("/me/push-token")
@require_auth
def register_push_token():
    """Register this device for push notifications. Idempotent."""
    body = request.get_json() or {}
    token = (body.get("device_token") or "").strip()
    if not token:
        return jsonify({"error": "device_token is required"}), 400

    supabase.table("push_tokens").upsert(
        {
            "user_id": g.user_id,
            "device_token": token,
            "platform": body.get("platform", "ios"),
            "updated_at": "now()",
        },
        on_conflict="user_id,device_token",
    ).execute()
    return jsonify({"status": "registered"})


@account_bp.delete("/me/push-token/<token>")
@require_auth
def remove_push_token(token):
    """Deregister a device. Idempotent."""
    supabase.table("push_tokens").delete().eq("user_id", g.user_id).eq(
        "device_token", token
    ).execute()
    return jsonify({"status": "removed"})


@account_bp.delete("/me/account")
@require_auth
def delete_account():
    """Delete the user's account: all their rows, then the auth user itself."""
    user_id = g.user_id
    for table in (
        "push_tokens",
        "user_event_favourites",
        "user_artists",
        "email_digest_log",
        "user_email_preferences",
    ):
        try:
            supabase.table(table).delete().eq("user_id", user_id).execute()
        except Exception as e:
            logger.warning("[ACCOUNT] Cleanup of %s failed for %s: %s", table, user_id, e)

    supabase.auth.admin.delete_user(user_id)
    logger.info("[ACCOUNT] Deleted user %s", user_id)
    return jsonify({"status": "deleted"})
