"""Fingerprint primitives for event deduplication.

Two fingerprint kinds:
  * strict   — venue + city + date  (safe for cross-artist linking)
  * loose    — city + date          (same-artist only)

Plus the normalisers feeding them. No internal pulse_api deps.
"""

import hashlib
import re

from dateutil import parser as dateparser


def date_bucket(date: str | None) -> str | None:
    """Extract YYYY-MM-DD from a date string."""
    if not date:
        return None
    try:
        dt = dateparser.parse(date)
        return dt.strftime("%Y-%m-%d") if dt else date[:10]
    except (ValueError, OverflowError):
        return date[:10]


def normalise_city(city: str | None) -> str:
    """Normalise a city string for fingerprinting.

    Handles full addresses (DICE returns "Straker's Rd, London SE15 3UA, UK")
    by extracting known city names, then falling back to the raw string.
    """
    if not city:
        return "unknown"
    city_lower = city.lower().strip()

    # Try to extract a known city name from a full address
    _KNOWN_CITIES = [
        "london", "berlin", "amsterdam", "paris", "new york", "barcelona",
        "ibiza", "rome", "marseille", "lisbon", "tokyo", "manchester",
        "bristol", "brighton", "leeds", "glasgow", "dublin", "copenhagen",
        "munich", "hamburg", "prague", "brussels", "zagreb", "tisno",
        "wimborne",
    ]
    for known in _KNOWN_CITIES:
        if known in city_lower:
            return known

    # Fall back to first comma-separated part, lowered
    return city_lower.split(",")[0].strip()


def normalise_venue(venue: str | None) -> str:
    """Normalise a venue name for fingerprinting.

    Strips punctuation, whitespace, and common suffixes so cross-source
    variations like "DC-10" / "DC10" / "dc 10" all collapse to the same
    value.
    """
    if not venue:
        return "unknown"
    v = venue.lower().strip()
    # Strip common punctuation that differs across sources
    v = re.sub(r"[^a-z0-9 ]", "", v)
    # Collapse whitespace
    v = re.sub(r"\s+", " ", v).strip()
    return v or "unknown"


def make_fingerprint(
    artist_id: str,
    venue: str | None,
    date: str | None,
    city: str | None = None,
) -> str | None:
    """Strict fingerprint: venue + city + date.

    Returns None when venue is missing/unknown — two events without
    venues should NOT be assumed identical just because they share a
    city and date.  Use make_fingerprint_loose for same-artist dedup
    where that assumption is safe.
    """
    bucket = date_bucket(date)
    if not bucket:
        return None

    venue_norm = normalise_venue(venue)
    if venue_norm == "unknown":
        return None

    city_norm = normalise_city(city)
    raw = f"{venue_norm}|{city_norm}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_fingerprint_loose(date: str | None, city: str | None = None) -> str | None:
    """Loose fingerprint: city + date only.

    Safe for same-artist dedup (an artist almost never has two
    different gigs in the same city on the same day).  Must NOT be
    used for cross-artist matching — that's how Armand gets linked
    to Cross The Tracks.
    """
    bucket = date_bucket(date)
    if not bucket:
        return None

    city_norm = normalise_city(city)
    raw = f"{city_norm}|{bucket}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
