import logging

import httpx

from pulse_api.config import settings
from pulse_api.sources.base import EventSource, SearchResult, EventResult

logger = logging.getLogger(__name__)

BASE_URL = "https://www.skiddle.com/api/v1"

class SkiddleSource(EventSource):
    def __init__(self):
        self.api_key = settings.skiddle_api_key
        if not self.api_key:
            logger.warning("Skiddle: API key missing")

    def _params(self, **kwargs) -> dict:
        return {"api_key": self.api_key, **kwargs}

    async def search_artist(self, name: str) -> list[SearchResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/artists",
                params=self._params(name=name),
            )
            resp.raise_for_status()
            data = resp.json()

        artists = data.get("results", [])
        logger.info("Skiddle: search %r → %d artist(s)", name, len(artists))
        return [
            SearchResult(
                platform="skiddle",
                platform_id=str(a.get("id", a.get("artistid", ""))),
                name=a.get("name", ""),
                url=a.get("link"),
                image_url=a.get("imageurl"),
                extra={
                    "description": a.get("description"),
                    "spotify_url": a.get("spotifyartisturl"),
                },
            )
            for a in artists
        ]

    async def get_events(
        self, artist_id: str, city: str | None = None
    ) -> list[EventResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{BASE_URL}/events/search",
                params=self._params(
                    a=artist_id,
                    eventcode="LIVE",
                ),
            )
            resp.raise_for_status()
            data = resp.json()

        events = data.get("results", [])
        logger.info("Skiddle: %d event(s) for %s", len(events), artist_id)
        return [
            EventResult(
                source="skiddle",
                source_id=str(e.get("id", "")),
                title=e.get("eventname", ""),
                date=e.get("date", ""),
                venue=e.get("venue", {}).get("name"),
                city=e.get("venue", {}).get("town", city),
                ticket_url=e.get("link"),
                raw_data=e,
                image_url=e.get("largeimageurl") or e.get("imageurl"),
            )
            for e in events
        ]
