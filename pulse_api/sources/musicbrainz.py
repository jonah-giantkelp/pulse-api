"""MusicBrainz API client for artist search and platform URL extraction."""

import asyncio
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://musicbrainz.org/ws/2"
USER_AGENT = "PulseAPI/1.0 (pulse-api)"

# Minimum interval between requests (MusicBrainz enforces 1 req/sec)
_MIN_INTERVAL = 1.1
_last_request_time = 0.0

# Map MusicBrainz URL relation types to our platform columns.
# MusicBrainz uses the URL itself (not a relation-type label) to
# identify platforms, so we pattern-match on the target URL.
URL_PATTERNS = {
    r"open\.spotify\.com/artist/([a-zA-Z0-9]+)": "spotify_id",
    r"instagram\.com/([^/?#]+)": "instagram_handle",
    r"twitter\.com/([^/?#]+)": "twitter_handle",
    r"x\.com/([^/?#]+)": "twitter_handle",
    r"ra\.co/dj/([^/?#]+)": "ra_slug",
    r"soundcloud\.com/([^/?#]+)": "soundcloud_slug",
    r"bandcamp\.com": "bandcamp_url",
    r"facebook\.com/([^/?#]+)": "facebook_slug",
    r"youtube\.com/(?:channel|c|@)([^/?#]+)": "youtube_id",
    r"discogs\.com/artist/(\d+)": "discogs_id",
}


# Retry policy: MusicBrainz occasionally times out or returns 503 under load.
# Retry transient failures with exponential backoff before surfacing a 500.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0  # seconds; doubles each attempt
_RETRY_STATUS = {502, 503, 504}


async def _rate_limited_get(
    client: httpx.AsyncClient,
    url: str,
    **kwargs,
) -> httpx.Response:
    """GET with rate limiting and retries for transient failures.

    Respects MusicBrainz's 1-req/sec policy via _MIN_INTERVAL. Retries on
    network timeouts, connection errors, and 5xx responses (502/503/504)
    with exponential backoff.
    """
    global _last_request_time

    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        elapsed = time.monotonic() - _last_request_time
        if elapsed < _MIN_INTERVAL:
            await asyncio.sleep(_MIN_INTERVAL - elapsed)

        _last_request_time = time.monotonic()
        try:
            resp = await client.get(url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                logger.warning(
                    "MusicBrainz GET %s failed after %d attempts: %s",
                    url, attempt, exc,
                )
                raise
            backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
            logger.info(
                "MusicBrainz GET %s attempt %d/%d failed (%s); retrying in %.1fs",
                url, attempt, _MAX_ATTEMPTS, exc, backoff,
            )
            await asyncio.sleep(backoff)
            continue

        if resp.status_code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS:
            backoff = _BACKOFF_BASE * (2 ** (attempt - 1))
            logger.info(
                "MusicBrainz GET %s returned %d on attempt %d/%d; retrying in %.1fs",
                url, resp.status_code, attempt, _MAX_ATTEMPTS, backoff,
            )
            await asyncio.sleep(backoff)
            continue

        resp.raise_for_status()
        return resp

    # Defensive: loop should always return or raise above.
    assert last_exc is not None
    raise last_exc


async def search_artists(query: str, limit: int = 10) -> list[dict]:
    """Search MusicBrainz for artists matching a query string.

    Returns a list of candidates with name, disambiguation, country,
    tags, and MusicBrainz ID.
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    ) as client:
        resp = await _rate_limited_get(
            client,
            f"{BASE_URL}/artist",
            params={"query": query, "fmt": "json", "limit": limit},
        )
        data = resp.json()

    artists = data.get("artists", [])
    results = []
    for a in artists:
        tags = [t["name"] for t in a.get("tags", [])] if a.get("tags") else []
        results.append({
            "musicbrainz_id": a["id"],
            "name": a["name"],
            "disambiguation": a.get("disambiguation", ""),
            "type": a.get("type", ""),
            "country": a.get("country", ""),
            "score": a.get("score", 0),
            "tags": tags,
            "life_span": a.get("life-span", {}),
        })
    return results


async def get_artist_details(mbid: str) -> dict:
    """Fetch full artist details including URL relations, genres, and tags.

    Returns artist metadata plus a dict of extracted platform identifiers
    parsed from the URL relations.
    """
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=15,
    ) as client:
        resp = await _rate_limited_get(
            client,
            f"{BASE_URL}/artist/{mbid}",
            params={"inc": "url-rels+genres+tags", "fmt": "json"},
        )
        data = resp.json()

    # Extract genres and tags
    genres = [g["name"] for g in data.get("genres", [])]
    tags = [t["name"] for t in data.get("tags", [])]

    # Extract platform identifiers from URL relations
    platform_ids = {}
    platform_urls = {}

    for rel in data.get("relations", []):
        if rel.get("type") == "image":
            # Some artists have an image relation — grab it
            url = rel.get("url", {}).get("resource", "")
            if url:
                platform_ids["image_url"] = url
            continue

        url = rel.get("url", {}).get("resource", "")
        if not url:
            continue

        for pattern, key in URL_PATTERNS.items():
            match = re.search(pattern, url)
            if match:
                value = match.group(1) if match.lastindex else url
                platform_ids[key] = value
                platform_urls[key] = url
                break

    return {
        "musicbrainz_id": data["id"],
        "name": data["name"],
        "disambiguation": data.get("disambiguation", ""),
        "type": data.get("type", ""),
        "country": data.get("country", ""),
        "genres": genres,
        "tags": tags,
        "life_span": data.get("life-span", {}),
        "platform_ids": platform_ids,
        "platform_urls": platform_urls,
    }


async def get_artist_image(mbid: str, platform_ids: dict) -> str | None:
    """Try to get an artist image.

    Priority:
    1. Image URL from MusicBrainz relations (rare)
    2. Spotify artist image (if spotify_id was found in relations)
    3. None
    """
    # Check if MusicBrainz had a direct image
    if platform_ids.get("image_url"):
        return platform_ids["image_url"]

    # Try Spotify if we have an ID from MusicBrainz relations
    if platform_ids.get("spotify_id"):
        try:
            from pulse_api.sources.spotify import SpotifySource

            source = SpotifySource()
            details = await source.get_artist_details(platform_ids["spotify_id"])
            return details.get("image_url")
        except Exception:
            logger.debug("Spotify image fetch failed", exc_info=True)

    return None
