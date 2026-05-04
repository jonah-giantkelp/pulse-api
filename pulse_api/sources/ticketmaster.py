import logging

import httpx

from pulse_api.config import settings
from pulse_api.sources.base import EventSource, SearchResult, EventResult

logger = logging.getLogger(__name__)

BASE_URL = "https://app.ticketmaster.com/discovery/v2"


class TicketmasterSource(EventSource):
    def __init__(self):
        self.api_key = settings.ticketmaster_api_key
        if not self.api_key:
            logger.warning("Ticketmaster: API key missing")

    def _params(self, **kwargs) -> dict:
        return {"apikey": self.api_key, **kwargs}

    async def search_artist(self, name: str) -> list[SearchResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/attractions",
                params=self._params(keyword=name, classificationName="music"),
            )
            resp.raise_for_status()
            data = resp.json()

        attractions = data.get("_embedded", {}).get("attractions", [])
        logger.info("Ticketmaster: search %r → %d attraction(s)", name, len(attractions))
        return [
            SearchResult(
                platform="ticketmaster",
                platform_id=a["id"],
                name=a["name"],
                url=a.get("url"),
                image_url=(a["images"][0]["url"] if a.get("images") else None),
                extra={
                    "upcoming_events": a.get("upcomingEvents", {}),
                    "classifications": [
                        c.get("genre", {}).get("name")
                        for c in a.get("classifications", [])
                    ],
                },
            )
            for a in attractions
        ]

    async def get_events(
        self, artist_id: str, city: str | None = None
    ) -> list[EventResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/events",
                params=self._params(
                    attractionId=artist_id,
                    countryCode="GB",
                    classificationName="music",
                    sort="date,asc",
                ),
            )
            resp.raise_for_status()
            data = resp.json()

        events = data.get("_embedded", {}).get("events", [])
        logger.info("Ticketmaster: %d event(s) for %s", len(events), artist_id)
        results = []
        for e in events:
            venue_info = (
                e.get("_embedded", {}).get("venues", [{}])[0]
                if e.get("_embedded", {}).get("venues")
                else {}
            )
            results.append(
                EventResult(
                    source="ticketmaster",
                    source_id=e["id"],
                    title=e["name"],
                    date=e.get("dates", {}).get("start", {}).get("dateTime", ""),
                    venue=venue_info.get("name"),
                    city=venue_info.get("city", {}).get("name", city),
                    ticket_url=e.get("url"),
                    raw_data=e,
                    image_url=(
                        e["images"][0]["url"] if e.get("images") else None
                    ),
                )
            )
        return results
