"""Source-shape → DB-row converters.

Each `EventResult` / `PostResult` from a source adapter gets turned into
the dict shape we'll upsert into Postgres. This is the only place that
should know about both the source SDK shape and the DB column names.
"""


_ALPHA3_TO_ALPHA2 = {
    "ALB": "AL", "BEL": "BE", "DEU": "DE", "ESP": "ES", "FRA": "FR",
    "GBR": "GB", "HRV": "HR", "ITA": "IT", "NLD": "NL", "NOR": "NO",
    "PRT": "PT", "TUR": "TR", "USA": "US", "JPN": "JP", "AUT": "AT",
    "CHE": "CH", "SWE": "SE", "DNK": "DK", "FIN": "FI", "IRL": "IE",
    "POL": "PL", "CZE": "CZ", "ROU": "RO", "GRC": "GR", "SRB": "RS",
    "HUN": "HU", "BGR": "BG", "SVN": "SI", "SVK": "SK", "LTU": "LT",
    "LVA": "LV", "EST": "EE", "CYP": "CY", "MLT": "MT", "LUX": "LU",
    "ISL": "IS", "MNE": "ME", "MKD": "MK", "BIH": "BA", "GEO": "GE",
    "AUS": "AU", "NZL": "NZ", "CAN": "CA", "MEX": "MX", "BRA": "BR",
    "ARG": "AR", "COL": "CO", "CHL": "CL", "ZAF": "ZA", "MAR": "MA",
    "EGY": "EG", "KOR": "KR", "THA": "TH", "IDN": "ID", "MYS": "MY",
    "SGP": "SG", "IND": "IN", "CHN": "CN", "TWN": "TW", "PHL": "PH",
}


def event_to_row(artist_id, e, city) -> dict | None:
    """Convert a source EventResult into an `events` table row dict.

    Returns None if the event has no usable date — `events.date` is
    NOT NULL so we can't persist anything else.
    """
    raw = e.raw_data or {}
    # Extract country from raw_data if the source provided it
    venue_country = (raw.get("venue", {}) or {}).get("country", {}) or {}
    country_code = venue_country.get("isoCode") if isinstance(venue_country, dict) else None
    # Convert alpha-3 → alpha-2 if needed
    if country_code and len(country_code) == 3:
        country_code = _ALPHA3_TO_ALPHA2.get(country_code, country_code[:2])
    # Normalise empty/blank date strings. The `date` column is NOT NULL, so
    # an event without a usable date can't be persisted — skip it entirely.
    raw_date = e.date
    if isinstance(raw_date, str):
        raw_date = raw_date.strip() or None
    if not raw_date:
        return None
    d = {
        "artist_id": artist_id,
        "title": e.title or "Untitled Event",
        "date": raw_date,
        "venue": e.venue,
        "city": e.city,  # keep None if unknown — AI geo will fill it
        "source": e.source,
        "source_id": e.source_id,
        "ticket_url": e.ticket_url,
        "raw_data": raw,
    }
    if country_code:
        d["country"] = country_code
    if e.image_url:
        d["_image_url"] = e.image_url
    return d


def post_to_row(artist_id, p) -> dict:
    """Convert a source PostResult into a `social_posts` row dict."""
    return {
        "artist_id": artist_id,
        "platform": p.platform,
        "post_id": p.post_id,
        "caption": p.caption,
        "media_url": p.media_url,
        "posted_at": p.posted_at,
        "raw_data": p.raw_data,
    }
