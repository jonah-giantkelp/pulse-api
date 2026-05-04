import logging
from functools import wraps

from flask import request, jsonify, g
from supabase import create_client

from pulse_api.config import settings

logger = logging.getLogger(__name__)

# Reuse a single Supabase client for auth
print(f"[AUTH INIT] Supabase URL: {settings.supabase_url}")
print(f"[AUTH INIT] Supabase key: {settings.supabase_key[:20]}...")
_supabase = create_client(settings.supabase_url, settings.supabase_key)


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
        except Exception as e:
            logger.error(
                "[AUTH] Supabase get_user FAILED — type=%s, detail=%s",
                type(e).__name__,
                str(e)[:200],
            )
            return jsonify({"error": "Invalid or expired token", "detail": str(e)}), 401

        return f(*args, **kwargs)

    return decorated
