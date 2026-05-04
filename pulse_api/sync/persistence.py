"""Low-level DB write primitives used by the sync pipeline.

Each function is idempotent and swallows exceptions where appropriate,
because a failed write here should not stop the rest of an artist's sync.

Higher-level orchestration (the full upsert flow with dedup/AI) lives in
`sync.orchestrator`.
"""

import logging
import re

from pulse_api.db import supabase

logger = logging.getLogger(__name__)


def link_artist_to_event(
    event_id: str,
    artist_id: str,
    billing: str | None = None,
    source: str | None = None,
):
    """Insert into event_artists junction table (idempotent)."""
    try:
        supabase.table("event_artists").upsert(
            {
                "event_id": event_id,
                "artist_id": artist_id,
                "billing": billing,
                "source": source,
            },
            on_conflict="event_id,artist_id",
        ).execute()
    except Exception:
        pass


def extract_price(
    source: str,
    raw: dict,
) -> tuple[float | None, float | None, str | None]:
    """Return (price_min, price_max, currency) from raw_data."""
    if source == "dice":
        p = raw.get("price", {})
        if not isinstance(p, dict):
            return (None, None, None)
        cents = p.get("amount") or p.get("amount_from")
        if cents and isinstance(cents, (int, float)) and cents > 0:
            val = round(cents / 100, 2)
            return (val, val, p.get("currency"))
        return (None, None, p.get("currency"))

    if source == "skiddle":
        tp = raw.get("ticketpricing") or {}
        if not isinstance(tp, dict):
            return (None, None, None)
        lo, hi = tp.get("minPrice"), tp.get("maxPrice")
        cur = raw.get("currency", "GBP")
        if lo == 0 and hi == 0:
            return (None, None, cur)
        return (lo or None, hi or None, cur)

    if source == "ra":
        cost_str = (raw.get("cost") or "").strip()
        if not cost_str or cost_str.lower() in ("tba", "0", "free"):
            if cost_str == "0" or cost_str.lower() == "free":
                return (0, 0, "GBP")
            return (None, None, None)
        # Parse free-text like "£12", "£12.50", "8 - 10", "$12.50 + Conc"
        cur = "GBP"
        if "€" in cost_str:
            cur = "EUR"
        elif "$" in cost_str:
            cur = "USD"
        numbers = re.findall(r"[\d]+(?:\.[\d]+)?", cost_str)
        if not numbers:
            return (None, None, None)
        vals = [float(n) for n in numbers]
        return (min(vals), max(vals), cur)

    if source == "bandsintown":
        if raw.get("isFree"):
            return (0, 0, "GBP")
        return (None, None, None)

    return (None, None, None)


def store_external_id(
    event_id: str,
    source: str,
    external_id: str,
    ticket_url: str | None = None,
    raw_data: dict | None = None,
):
    """Store an external platform event ID with optional pricing (idempotent)."""
    if not external_id:
        return
    price_min, price_max, currency = extract_price(source, raw_data or {})
    row = {
        "event_id": event_id,
        "source": source,
        "external_id": external_id,
        "ticket_url": ticket_url,
    }
    if price_min is not None:
        row["price_min"] = price_min
    if price_max is not None:
        row["price_max"] = price_max
    if currency:
        row["currency"] = currency
    try:
        supabase.table("event_external_ids").upsert(
            row,
            on_conflict="source,external_id",
        ).execute()
    except Exception:
        pass


def store_event_images(event_id: str, images: list[dict]):
    """Store images associated with an event (skips duplicates)."""
    if not images:
        return

    # Check which image URLs already exist for this event
    try:
        existing = (
            supabase.table("event_images")
            .select("image_url")
            .eq("event_id", event_id)
            .execute()
        )
        existing_urls = {row["image_url"] for row in existing.data}
    except Exception:
        existing_urls = set()

    rows = [
        {
            "event_id": event_id,
            "image_url": img["image_url"],
            "source_post_id": img.get("source_post_id"),
            "image_type": img.get("image_type", "poster"),
        }
        for img in images
        if img["image_url"] not in existing_urls
    ]
    if not rows:
        return
    try:
        supabase.table("event_images").insert(rows).execute()
    except Exception:
        pass


def upsert_posts(posts: list[dict]):
    """Upsert social posts by (platform, post_id)."""
    if not posts:
        return
    supabase.table("social_posts").upsert(
        posts, on_conflict="platform,post_id"
    ).execute()
