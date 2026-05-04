import logging

import httpx
from datetime import datetime

from pulse_api.sources.base import EventSource, SearchResult, EventResult

logger = logging.getLogger(__name__)

GRAPHQL_URL = "https://ra.co/graphql"

HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://ra.co/events",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

ARTIST_SEARCH_QUERY = """
query SEARCH($searchTerm: String!) {
    search(searchTerm: $searchTerm, limit: 10, indices: [ARTIST]) {
        id
        value
        contentUrl
        imageUrl
        searchType
    }
}
"""

ARTIST_BY_SLUG_QUERY = """
query GET_ARTIST($slug: String!) {
    artist(slug: $slug) {
        id
        name
    }
}
"""

ARTIST_EVENTS_QUERY = """
query GET_ARTIST($id: ID!) {
    artist(id: $id) {
        id
        name
        upcomingEventsCount
        events(type: LATEST, limit: 50) {
            id
            date
            startTime
            title
            cost
            contentUrl
            flyerFront
            venue {
                id
                name
                area {
                    name
                }
                country {
                    isoCode
                    name
                }
            }
            artists {
                id
                name
            }
        }
    }
}
"""


class RASource(EventSource):
    async def search_artist(self, name: str) -> list[SearchResult]:
        payload = {
            "operationName": "SEARCH",
            "variables": {"searchTerm": name},
            "query": ARTIST_SEARCH_QUERY,
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(GRAPHQL_URL, json=payload, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()

        if data.get("errors"):
            logger.warning("RA: GraphQL errors: %s", data["errors"])

        artists = data.get("data", {}).get("search") or []
        logger.info("RA: search %r → %d artist(s)", name, len(artists))

        results = []
        for a in artists:
            if not isinstance(a, dict) or a.get("searchType") != "ARTIST":
                continue
            content_url = a.get("contentUrl", "")
            # Extract slug from contentUrl like "/dj/hunee"
            slug = content_url.rstrip("/").split("/")[-1] if content_url else ""
            results.append(
                SearchResult(
                    platform="ra",
                    platform_id=slug or str(a.get("id", "")),
                    name=a.get("value", ""),
                    url=f"https://ra.co{content_url}" if content_url else None,
                    image_url=a.get("imageUrl"),
                    extra={
                        "numeric_id": str(a.get("id", "")),
                        "content_url": content_url,
                    },
                )
            )
        return results

    async def get_events(
        self, artist_id_or_slug: str, city: str | None = None
    ) -> list[EventResult]:
        """Get upcoming events for an artist via RA GraphQL.

        *artist_id_or_slug* can be a numeric ID (e.g. "4561") or a slug
        (e.g. "hunee"). If it's a slug, we first resolve the numeric ID
        via a search query.
        """
        numeric_id = artist_id_or_slug

        # If stored as slug, resolve to numeric ID first
        if not artist_id_or_slug.isdigit():
            numeric_id = await self._resolve_numeric_id(artist_id_or_slug)
            if not numeric_id:
                logger.warning("RA: could not resolve slug %r", artist_id_or_slug)
                return []

        return await self._get_artist_events(numeric_id, city)

    async def _resolve_numeric_id(self, slug: str) -> str | None:
        """Resolve an RA slug to its numeric ID via GraphQL."""
        logger.debug("RA: resolving slug %r", slug)
        payload = {
            "operationName": "GET_ARTIST",
            "variables": {"slug": slug},
            "query": ARTIST_BY_SLUG_QUERY,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    GRAPHQL_URL, json=payload, headers=HEADERS
                )
                resp.raise_for_status()
                data = resp.json()

            artist = data.get("data", {}).get("artist")
            if artist and artist.get("id"):
                logger.info(
                    "RA: slug %r → ID %s (%s)",
                    slug, artist["id"], artist.get("name"),
                )
                return str(artist["id"])
        except Exception as e:
            logger.warning("RA: slug lookup failed for %r: %s", slug, e)

        # Fallback: try search
        results = await self.search_artist(slug)
        for r in results:
            if r.extra and r.extra.get("content_url", "").endswith(f"/{slug}"):
                return r.extra["numeric_id"]
        if results and results[0].extra:
            return results[0].extra.get("numeric_id")
        return None

    async def _get_artist_events(
        self, numeric_id: str, city: str | None = None
    ) -> list[EventResult]:
        """Query RA GraphQL for an artist's upcoming events by numeric ID."""
        payload = {
            "operationName": "GET_ARTIST",
            "variables": {"id": numeric_id},
            "query": ARTIST_EVENTS_QUERY,
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(GRAPHQL_URL, json=payload, headers=HEADERS)
            resp.raise_for_status()
            data = resp.json()

        if data.get("errors"):
            logger.warning("RA: GraphQL errors for ID %s: %s", numeric_id, data["errors"])
            return []

        artist_data = data.get("data", {}).get("artist")
        if not artist_data:
            logger.warning("RA: no artist data for ID %s", numeric_id)
            return []

        events = artist_data.get("events", [])
        logger.info(
            "RA: %d event(s) for %s (ID %s)",
            len(events), artist_data.get("name"), numeric_id,
        )

        results = []
        now = datetime.utcnow()

        for event in events:
            # Filter out past events (LATEST should be upcoming, but be safe)
            event_date = event.get("date", "")
            if event_date:
                try:
                    from dateutil import parser as dateparser
                    dt = dateparser.parse(event_date)
                    if dt and dt < now:
                        continue
                except (ValueError, ImportError):
                    pass

            venue = event.get("venue", {}) or {}
            area = venue.get("area", {}) or {}
            country = venue.get("country", {}) or {}
            area_name = area.get("name")
            if area_name and area_name.lower() in ("all", "global", "worldwide"):
                area_name = None
            results.append(
                EventResult(
                    source="ra",
                    source_id=str(event.get("id", "")),
                    title=event.get("title", ""),
                    date=event_date,
                    venue=venue.get("name"),
                    city=area_name,
                    ticket_url=(
                        f"https://ra.co{event['contentUrl']}"
                        if event.get("contentUrl")
                        else None
                    ),
                    raw_data=event,
                    image_url=event.get("flyerFront"),
                )
            )

        return results
