"""Admin / cron endpoints — sync trigger, digest trigger, health check.

`/sync` and `/digest` are gated behind `X-API-Key`; `/health` is open.
"""

from flask import Blueprint, jsonify, request

from pulse_api.routes._helpers import run_async
from pulse_api.mailer import send_daily_digests
from pulse_api.sync import run_daily_sync

admin_bp = Blueprint("admin", __name__)


@admin_bp.post("/sync")
def trigger_sync():
    """Trigger a full sync. Intended for cron / internal use."""
    api_key = request.headers.get("X-API-Key", "")
    from pulse_api.config import settings

    if not api_key or api_key != settings.sync_api_key:
        return jsonify({"error": "Unauthorized"}), 401

    run_async(run_daily_sync())
    return jsonify({"status": "sync complete"})


@admin_bp.post("/digest")
def trigger_digest():
    """Trigger the daily email digest. Intended for cron / internal use.

    Call this after /sync completes so new events are available.
    Authenticated via X-API-Key (same as /sync).
    """
    api_key = request.headers.get("X-API-Key", "")
    from pulse_api.config import settings

    if not api_key or api_key != settings.sync_api_key:
        return jsonify({"error": "Unauthorized"}), 401

    result = run_async(send_daily_digests())
    return jsonify({"status": "digest complete", **result})


@admin_bp.post("/push-test")
def push_test():
    """Send a test push to one user's devices and return APNs diagnostics.

    Body: {"email": "<user email>"}. Authenticated via X-API-Key.
    """
    api_key = request.headers.get("X-API-Key", "")
    from pulse_api.config import settings

    if not api_key or api_key != settings.sync_api_key:
        return jsonify({"error": "Unauthorized"}), 401

    from pulse_api import push
    from pulse_api.db import supabase

    payload = request.get_json(silent=True) or {}
    email = payload.get("email")
    if not email:
        return jsonify({"error": "email required"}), 400
    rows = (
        supabase.table("user_email_preferences")
        .select("user_id")
        .eq("email", email)
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return jsonify({"error": f"no user with email {email}"}), 404

    results: list = []
    sent = run_async(
        push.send_push_to_user(
            rows[0]["user_id"], "PULSE", "Test push — hello from /push-test", results,
            artist_images=payload.get("artist_images"),
        )
    )
    return jsonify({
        "configured": push.configured(),
        "sandbox": push.APNS_USE_SANDBOX,
        "bundle_id": push.APNS_BUNDLE_ID,
        "sent": sent,
        "results": results,
    })


@admin_bp.get("/health")
def health():
    return jsonify({"status": "ok"})
