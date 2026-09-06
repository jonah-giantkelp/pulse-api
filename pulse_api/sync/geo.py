"""Canonical trackable cities + per-source geo extraction from raw_data.

Not wired into the sync pipeline yet — consumed by scripts/geo_backfill_dryrun.py
to test extraction quality against existing events before anything ships.

Design constraints:
- Only the cities in CITIES are linked into the trackable structure; any other
  city keeps whatever raw string the source gave us.
- No external geocoding. Matching is exact (case-insensitive) against a small
  hand-maintained alias list.
"""

# ─────────────────────────────────────────────
# Canonical city list
# ─────────────────────────────────────────────

# aliases are matched lowercase, exact (no substring matching — that's what
# fingerprint.normalise_city does for dedup, deliberately looser than this).
CITIES = [
    {"key": "london", "display": "London", "country": "GB",
     "aliases": ["london", "greater london"]},
    {"key": "paris", "display": "Paris", "country": "FR",
     "aliases": ["paris"]},
    {"key": "amsterdam", "display": "Amsterdam", "country": "NL",
     "aliases": ["amsterdam"]},
    {"key": "marseille", "display": "Marseille", "country": "FR",
     "aliases": ["marseille", "marseilles"]},
    {"key": "brussels", "display": "Brussels", "country": "BE",
     "aliases": ["brussels", "bruxelles", "brussel"]},
    {"key": "berlin", "display": "Berlin", "country": "DE",
     "aliases": ["berlin"]},
    {"key": "new-york", "display": "New York", "country": "US",
     "aliases": ["new york", "new york city", "nyc", "brooklyn",
                 "manhattan", "queens", "bronx", "the bronx"]},
    {"key": "brighton", "display": "Brighton", "country": "GB",
     "aliases": ["brighton", "brighton and hove", "hove"]},
    {"key": "barcelona", "display": "Barcelona", "country": "ES",
     "aliases": ["barcelona"]},
    {"key": "manchester", "display": "Manchester", "country": "GB",
     "aliases": ["manchester", "salford"]},
]

_ALIAS_TO_CITY = {
    alias: city
    for city in CITIES
    for alias in city["aliases"]
}


def canonical_city(raw_city: str | None) -> dict | None:
    """Return the CITIES entry a raw city string maps to, or None."""
    if not raw_city:
        return None
    return _ALIAS_TO_CITY.get(raw_city.strip().lower())


def resolve_pref_city(name: str | None) -> str | None:
    """Resolve user input (any alias, any casing) to a canonical display
    name, or None if it isn't a trackable city."""
    canon = canonical_city(name)
    return canon["display"] if canon else None


def location_filter_clause(
    cities: list[str] | None, countries: list[str] | None
) -> str | None:
    """Build a PostgREST `or=` clause for a user's tracked cities/countries.

    Canonical cities carry a country guard — the name matches AND the row's
    country is either that city's country or NULL (unknown). This keeps
    namesakes out (a Manchester/US row exists: Bonnaroo). Pref strings that
    aren't canonical fall back to a bare exact city match, preserving legacy
    behaviour. Returns None when there is nothing to filter on.
    """
    by_country: dict[str, set[str]] = {}
    loose: list[str] = []
    for name in cities or []:
        canon = canonical_city(name)
        if canon:
            by_country.setdefault(canon["country"], set()).add(canon["display"])
        else:
            loose.append(name)

    parts = []
    for ctry, names in sorted(by_country.items()):
        csv = ",".join(f'"{n}"' for n in sorted(names))
        parts.append(f"and(city.in.({csv}),or(country.eq.{ctry},country.is.null))")
    if loose:
        csv = ",".join(f'"{n}"' for n in loose)
        parts.append(f"city.in.({csv})")
    if countries:
        parts.append(f"country.in.({','.join(countries)})")

    return ",".join(parts) if parts else None


# ─────────────────────────────────────────────
# Country-name / timezone → ISO alpha-2
# ─────────────────────────────────────────────

# Bandsintown gives English country names in "City, Country" location strings.
_COUNTRY_NAME_TO_ISO = {
    "united kingdom": "GB", "uk": "GB", "great britain": "GB",
    "england": "GB", "scotland": "GB", "wales": "GB",
    "northern ireland": "GB",
    "ireland": "IE", "france": "FR", "germany": "DE",
    "netherlands": "NL", "the netherlands": "NL", "belgium": "BE",
    "spain": "ES", "portugal": "PT", "italy": "IT",
    "switzerland": "CH", "austria": "AT", "denmark": "DK",
    "sweden": "SE", "norway": "NO", "finland": "FI",
    "poland": "PL", "czech republic": "CZ", "czechia": "CZ",
    "greece": "GR", "croatia": "HR", "hungary": "HU",
    "romania": "RO", "turkey": "TR", "türkiye": "TR",
    "united states": "US", "usa": "US", "united states of america": "US",
    "canada": "CA", "mexico": "MX", "brazil": "BR",
    "argentina": "AR", "chile": "CL", "colombia": "CO", "peru": "PE",
    "australia": "AU", "new zealand": "NZ", "japan": "JP",
    "south korea": "KR", "china": "CN", "india": "IN",
    "singapore": "SG", "thailand": "TH", "indonesia": "ID",
    "south africa": "ZA", "morocco": "MA", "israel": "IL",
    "iceland": "IS", "luxembourg": "LU", "serbia": "RS",
    "bulgaria": "BG", "slovakia": "SK", "slovenia": "SI",
    "lithuania": "LT", "latvia": "LV", "estonia": "EE",
    "malta": "MT", "cyprus": "CY", "georgia": "GE",
    "albania": "AL", "bosnia and herzegovina": "BA",
    "montenegro": "ME", "north macedonia": "MK",
    "paraguay": "PY", "kazakhstan": "KZ",
    # Bandsintown occasionally spells the state out in full
    "arizona": "US", "california": "US", "new york": "US", "texas": "US",
    "florida": "US", "illinois": "US", "colorado": "US", "georgia": "US",
    "washington": "US", "oregon": "US", "michigan": "US", "ohio": "US",
    "massachusetts": "US", "pennsylvania": "US", "tennessee": "US",
}

# Bandsintown writes US/Canadian locations as "City, ST" with no country —
# a bare state/province code where UK events get "United Kingdom". These sets
# don't overlap, so a 2-letter token resolves unambiguously.
_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
_CA_PROVINCES = {"ON", "QC", "BC", "AB", "MB", "SK", "NS", "NB", "NL", "PE", "YT", "NT", "NU"}

# Fallback: unambiguous single-country timezones (Bandsintown always sends one).
_TZ_TO_ISO = {
    "Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Paris": "FR",
    "Europe/Berlin": "DE", "Europe/Amsterdam": "NL", "Europe/Brussels": "BE",
    "Europe/Madrid": "ES", "Europe/Lisbon": "PT", "Europe/Rome": "IT",
    "Europe/Zurich": "CH", "Europe/Vienna": "AT", "Europe/Copenhagen": "DK",
    "Europe/Stockholm": "SE", "Europe/Oslo": "NO", "Europe/Helsinki": "FI",
    "Europe/Warsaw": "PL", "Europe/Prague": "CZ", "Europe/Athens": "GR",
    "Europe/Zagreb": "HR", "Europe/Budapest": "HU", "Europe/Bucharest": "RO",
    "Europe/Istanbul": "TR", "Asia/Tokyo": "JP", "Asia/Seoul": "KR",
    "Asia/Singapore": "SG", "Asia/Bangkok": "TH", "Australia/Sydney": "AU",
    "Australia/Melbourne": "AU", "Pacific/Auckland": "NZ",
}

_ALPHA3_TO_ALPHA2 = None  # populated lazily from adapters to avoid duplication


def _alpha3_to_alpha2(code: str) -> str | None:
    global _ALPHA3_TO_ALPHA2
    if _ALPHA3_TO_ALPHA2 is None:
        from pulse_api.sync.adapters import _ALPHA3_TO_ALPHA2 as m
        _ALPHA3_TO_ALPHA2 = m
    return _ALPHA3_TO_ALPHA2.get(code)


# ─────────────────────────────────────────────
# Per-source extraction from raw_data
# ─────────────────────────────────────────────


def extract_geo(source: str, raw: dict) -> tuple[str | None, str | None]:
    """Extract (city, iso-alpha-2 country) from a source's raw_data.

    Returns (None, None) when the raw shape carries no geo (concerts_tracker)
    or the fields are absent. Never guesses.
    """
    if not isinstance(raw, dict):
        return None, None

    if source == "ra":
        venue = raw.get("venue") or {}
        area = (venue.get("area") or {}).get("name")
        if area and area.lower() in ("all", "global", "worldwide"):
            area = None
        iso = (venue.get("country") or {}).get("isoCode")
        if iso and len(iso) == 3:
            iso = _alpha3_to_alpha2(iso)
        return area, iso

    if source == "dice":
        # Profile-section shape: venues[0].city.{name, country_code}
        venues = raw.get("venues") or []
        if venues and isinstance(venues[0], dict):
            city_obj = venues[0].get("city") or {}
            if isinstance(city_obj, dict):
                return city_obj.get("name"), city_obj.get("country_code")
        # JSON-LD shape: location.address.{addressLocality, addressCountry}
        loc = raw.get("location") or {}
        if isinstance(loc, dict):
            addr = loc.get("address") or {}
            if isinstance(addr, dict):
                city = addr.get("addressLocality")
                country = addr.get("addressCountry")
                if isinstance(country, dict):
                    country = country.get("name")
                if country and len(country) == 3:
                    country = _alpha3_to_alpha2(country) or country
                if country and len(country) != 2:
                    country = _COUNTRY_NAME_TO_ISO.get(country.lower())
                return city, country
        return None, None

    if source == "ticketmaster":
        venues = (raw.get("_embedded") or {}).get("venues") or []
        if venues and isinstance(venues[0], dict):
            v = venues[0]
            city = (v.get("city") or {}).get("name")
            country = (v.get("country") or {}).get("countryCode")
            return city, country
        return None, None

    if source == "bandsintown":
        location = raw.get("location") or ""
        city, country = None, None
        if "," in location:
            city_part, country_part = location.rsplit(",", 1)
            city = city_part.strip() or None
            token = country_part.strip()
            country = _COUNTRY_NAME_TO_ISO.get(token.lower())
            if not country and token.upper() in _US_STATES:
                country = "US"
            elif not country and token.upper() in _CA_PROVINCES:
                country = "CA"
        elif location:
            city = location.strip()
        if not country:
            country = _TZ_TO_ISO.get(raw.get("timezone") or "")
        return city, country

    if source == "skiddle":
        venue = raw.get("venue") or {}
        return venue.get("town"), venue.get("country")

    if source in ("twitter", "instagram", "social_ai"):
        # raw_data is the distilled gig dict — city/country set by the AI,
        # country already ISO alpha-2 (or None).
        country = raw.get("country")
        if country and len(country) != 2:
            country = None
        return raw.get("city"), country

    # concerts_tracker (raw is just {"venue_id": ...}), website, unknown
    return raw.get("city"), None


def propose_geo(
    source: str,
    raw: dict,
    stored_city: str | None,
    stored_country: str | None,
) -> tuple[str, str | None, str]:
    """Compute what (city, country) a backfill would write for one event.

    Returns (city, country, reason). Rules, in order:
    - city: extracted from raw if present, else keep stored.
      If the result maps to a canonical city, use its display name.
    - country: extracted from raw if present; else stamped from the canonical
      city's country; else keep stored (which may be a suspect GB default).
    """
    ext_city, ext_country = extract_geo(source, raw)

    # City: the stored value has usually been through AI cleanup and is the
    # better string — raw extraction only fills gaps or routes to a canonical
    # name via the alias list. Never let a raw platform string ("Chicago, IL",
    # "Greater - Manchester") overwrite a clean stored city.
    stored_clean = stored_city if stored_city and stored_city != "Unknown" else None
    canon = canonical_city(stored_clean) or canonical_city(ext_city)

    # Homonym guard: if the source explicitly says a different country than
    # the canonical city's (Manchester TN, Brighton NY…), this is NOT the
    # tracked city — keep the raw name unlinked and trust the raw country.
    if canon and ext_country and ext_country != canon["country"]:
        return stored_clean or ext_city or "Unknown", ext_country, "homonym"

    # Same guard using the stored country when raw has no geo (concerts_tracker):
    # a specific stored country that isn't GB was set deliberately at ingest —
    # a conflict with the canonical city's country means it's a namesake
    # (Bonnaroo's Manchester/US), not ours. GB doesn't count: it was the
    # column default and is exactly the value we're trying to clean up.
    if (
        canon
        and not ext_country
        and stored_country
        and stored_country not in ("GB", canon["country"])
    ):
        return stored_clean or "Unknown", stored_country, "homonym"

    if canon:
        city = canon["display"]
    else:
        city = stored_clean or ext_city or "Unknown"

    if ext_country:
        country, reason = ext_country, "raw"
    elif canon:
        country, reason = canon["country"], "city-stamp"
    else:
        country, reason = stored_country, "kept"

    return city, country, reason
