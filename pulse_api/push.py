"""APNs push sender — HTTP/2 with token-based (p8) auth.

Configured via env:
    APNS_KEY_ID       key ID of the .p8 auth key
    APNS_TEAM_ID      Apple developer team ID
    APNS_PRIVATE_KEY  contents of the .p8 file (\\n-escaped newlines OK)
    APNS_BUNDLE_ID    app bundle id (default xyz.giantkelp.pulse)
    APNS_USE_SANDBOX  "true" (default) for development builds

If unconfigured, sends are skipped with a log line — never an error.
"""

import logging
import os
import time

import httpx

from pulse_api.db import supabase

logger = logging.getLogger(__name__)

APNS_KEY_ID = os.environ.get("APNS_KEY_ID", "")
APNS_TEAM_ID = os.environ.get("APNS_TEAM_ID", "")
APNS_PRIVATE_KEY = os.environ.get("APNS_PRIVATE_KEY", "").replace("\\n", "\n")
APNS_BUNDLE_ID = os.environ.get("APNS_BUNDLE_ID", "giantkelp.pulse-app")
APNS_USE_SANDBOX = os.environ.get("APNS_USE_SANDBOX", "true").lower() == "true"

_jwt_cache: tuple[str, float] | None = None


def configured() -> bool:
    return bool(APNS_KEY_ID and APNS_TEAM_ID and APNS_PRIVATE_KEY)


def _auth_token() -> str:
    """ES256 provider token, cached ~40 minutes (Apple allows 20-60)."""
    global _jwt_cache
    import jwt

    now = time.time()
    if _jwt_cache and now - _jwt_cache[1] < 2400:
        return _jwt_cache[0]
    token = jwt.encode(
        {"iss": APNS_TEAM_ID, "iat": int(now)},
        APNS_PRIVATE_KEY,
        algorithm="ES256",
        headers={"kid": APNS_KEY_ID},
    )
    _jwt_cache = (token, now)
    return token


async def send_push_to_user(
    user_id: str, title: str, body: str, results: list | None = None
) -> int:
    """Send an alert push to every registered device for a user.

    Returns the number of devices reached. Dead tokens are pruned.
    Pass a list as `results` to collect per-token APNs responses
    (used by the /push-test admin endpoint).
    """
    if not configured():
        logger.warning("[PUSH] APNs not configured — skipping push for %s", user_id)
        return 0

    tokens = (
        supabase.table("push_tokens")
        .select("device_token")
        .eq("user_id", user_id)
        .execute()
    )
    if not tokens.data:
        logger.info("[PUSH] No registered devices for %s", user_id)
        return 0

    host = "api.sandbox.push.apple.com" if APNS_USE_SANDBOX else "api.push.apple.com"
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}
    headers = {
        "authorization": f"bearer {_auth_token()}",
        "apns-topic": APNS_BUNDLE_ID,
        "apns-push-type": "alert",
        "apns-priority": "10",
    }

    sent = 0
    async with httpx.AsyncClient(http2=True, timeout=10) as client:
        for row in tokens.data:
            token = row["device_token"]
            try:
                resp = await client.post(
                    f"https://{host}/3/device/{token}",
                    json=payload,
                    headers=headers,
                )
                if results is not None:
                    results.append({
                        "token": token[:10],
                        "status": resp.status_code,
                        "response": resp.text[:200],
                    })
                if resp.status_code == 200:
                    sent += 1
                elif resp.status_code in (400, 410) and "BadDeviceToken" in resp.text:
                    logger.info("[PUSH] Pruning dead token for %s", user_id)
                    supabase.table("push_tokens").delete().eq(
                        "device_token", token
                    ).execute()
                else:
                    logger.warning(
                        "[PUSH] APNs %s for %s: %s",
                        resp.status_code, user_id, resp.text[:120],
                    )
            except Exception as e:
                if results is not None:
                    results.append({"token": token[:10], "error": str(e)[:200]})
                logger.warning("[PUSH] Send failed for %s: %s", user_id, str(e)[:120])
    return sent
