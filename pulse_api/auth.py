import logging
import time
from functools import wraps

import httpx
from flask import request, jsonify, g
from supabase import create_client

from pulse_api.config import settings

logger = logging.getLogger(__name__)

# Reuse a single Supabase client for auth
print(f"[AUTH INIT] Supabase URL: {settings.supabase_url}")
print(f"[AUTH INIT] Supabase key: {settings.supabase_key[:20]}...")
_supabase = create_client(settings.supabase_url, settings.supabase_key)

# --- Account approval -------------------------------------------------------
# New accounts are gated until approved (user_approvals table). Cached
# per-process: approved users for 10 min, pending for 30 s so a flip to
# approved takes effect quickly.

APPROVAL_NOTIFY_EMAIL = "jonah@giantkelp.xyz"

# Paths a signed-in but unapproved user may still use: checking their own
# status, and deleting their account.
_APPROVAL_EXEMPT = {("GET", "/me/access"), ("DELETE", "/me/account")}

_approval_cache: dict[str, tuple[bool, float]] = {}


def _notify_pending_signup(email: str | None) -> None:
    """Best-effort email so a pending signup doesn't sit unnoticed."""
    if not settings.postmark_server_token:
        return
    try:
        httpx.post(
            "https://api.postmarkapp.com/email",
            headers={
                "Accept": "application/json",
                "X-Postmark-Server-Token": settings.postmark_server_token,
            },
            json={
                "From": settings.postmark_from_email,
                "To": APPROVAL_NOTIFY_EMAIL,
                "Subject": f"Pulse signup awaiting approval: {email or 'unknown'}",
                "TextBody": (
                    f"New Pulse account pending approval.\n\nEmail: {email}\n\n"
                    "Approve it by setting approved=true on their row in the "
                    "user_approvals table in Supabase."
                ),
                "MessageStream": "outbound",
            },
            timeout=10,
        )
    except Exception as e:
        logger.warning("[APPROVAL] Notify email failed: %s", str(e)[:120])


def is_approved(user_id: str, email: str | None = None) -> bool:
    """Check (and cache) whether a user is approved. Unknown users get a
    pending row created so they show up for review."""
    now = time.time()
    cached = _approval_cache.get(user_id)
    if cached and now < cached[1]:
        return cached[0]

    rows = (
        _supabase.table("user_approvals")
        .select("approved")
        .eq("user_id", user_id)
        .execute()
        .data
    )
    if rows:
        approved = bool(rows[0]["approved"])
    else:
        approved = False
        _supabase.table("user_approvals").insert({"user_id": user_id}).execute()
        logger.info("[APPROVAL] New pending account %s (%s)", user_id, email)
        _notify_pending_signup(email)

    _approval_cache[user_id] = (approved, now + (600 if approved else 30))
    return approved


def require_auth(f):
    """Verify Supabase JWT and set g.user_id."""

    @wraps(f)
    def decorated(*args, **kwargs):
        logger.info(
            "[AUTH] %s %s — headers: %s",
            request.method,
            request.path,
            dict(request.headers),
        )

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            logger.warning(
                "[AUTH] Rejected: missing/malformed Authorization header: %r",
                auth_header[:50],
            )
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header.removeprefix("Bearer ").strip()
        logger.info("[AUTH] Token (first 40 chars): %s...", token[:40])
        logger.info("[AUTH] Token length: %d", len(token))

        try:
            user_resp = _supabase.auth.get_user(token)
            logger.info(
                "[AUTH] Supabase get_user succeeded — user_id=%s, email=%s",
                user_resp.user.id,
                getattr(user_resp.user, "email", "?"),
            )
            g.user_id = user_resp.user.id
            g.user_email = getattr(user_resp.user, "email", None)
        except Exception as e:
            logger.error(
                "[AUTH] Supabase get_user FAILED — type=%s, detail=%s",
                type(e).__name__,
                str(e)[:200],
            )
            return jsonify({"error": "Invalid or expired token", "detail": str(e)}), 401

        if (request.method, request.path) not in _APPROVAL_EXEMPT:
            if not is_approved(g.user_id, g.user_email):
                return jsonify({
                    "error": "Account pending approval",
                    "code": "pending_approval",
                }), 403

        return f(*args, **kwargs)

    return decorated
