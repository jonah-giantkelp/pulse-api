"""Email sending — daily digest delivery + HTML/text templates.

Public API: `send_daily_digests` from `digest`. Template builders are
re-exported for tests but are otherwise an internal detail of `digest`.
"""

from pulse_api.mailer.digest import send_daily_digests
from pulse_api.mailer.template import build_digest_html, build_digest_text

__all__ = [
    "send_daily_digests",
    "build_digest_html",
    "build_digest_text",
]
