import logging

import httpx

from pulse_api.config import settings
from pulse_api.sources.base import EventSource, SearchResult, EventResult

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "concerts-artists-events-tracker.p.rapidapi.com"

class ConcertsTrackerSource(EventSource):
    def __init__(self):
        if not settings.rapidapi_concerts_key:
            logger.warning("Concerts Tracker: RapidAPI key missing")
        self.headers = {
            "X-RapidAPI-Key": settings.rapidapi_concerts_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        }

    async def search_artist(self, name: str) -> list[SearchResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://{RAPIDAPI_HOST}/search",
                headers=self.headers,
                params={"keyword": name, "types": "artist"},
            )
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, list):
            artists = data
        elif isinstance(data, dict):
            artists = (
                data.get("artists")
                or data.get("results")
                or data.get("data")
                or []
            )
        else:
            artists = []
        if isinstance(artists, dict):
            artists = [artists]
        logger.info(
            "Concerts Tracker: search %r → %d result(s)",
            name, len(artists) if isinstance(artists, list) else 1,
        )

        return [
            SearchResult(
                platform="concerts_tracker",
                platform_id=str(a.get("id", a.get("artist_id", ""))),
                name=a.get("name", ""),
                url=a.get("url"),
                image_url=a.get("image_url") or a.get("image"),
                followers=a.get("tracker_count"),
                extra={
                    "genre": a.get("genre"),
                    "type": a.get("type"),
                    "on_tour": a.get("on_tour"),
                    "verified": a.get("verified"),
                },
            )
            for a in artists
            if isinstance(a, dict) and a.get("name")
        ]

    async def get_events(
        self, artist_id: str, city: str | None = None
    ) -> list[EventResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://{RAPIDAPI_HOST}/artist/events",
                headers=self.headers,
                params={"artist_id": artist_id},
            )
            resp.raise_for_status()
            data = resp.json()

        if isinstance(data, list):
            events = data
        elif isinstance(data, dict):
            events = (
                data.get("events")
                or data.get("results")
                or data.get("data")
                or []
            )
        else:
            events = []
        if isinstance(events, dict):
            events = [events]
        logger.info("Concerts Tracker: %d event(s) for %s", len(events), artist_id)

        results = []
        for e in events:
            if not isinstance(e, dict):
                continue
            event_city = (
                e.get("city")
                or e.get("venue", {}).get("city", "")
                if isinstance(e.get("venue"), dict)
                else ""
            )

            venue_name = (
                e.get("venue", {}).get("name", "")
                if isinstance(e.get("venue"), dict)
                else e.get("venue_name", e.get("venue", ""))
            )

            results.append(
                EventResult(
                    source="concerts_tracker",
                    source_id=str(e.get("id", e.get("event_id", ""))),
                    title=e.get("name") or e.get("title") or "Untitled Event",
                    date=e.get("date", e.get("starts_at", "")),
                    venue=str(venue_name) if venue_name else None,
                    city=str(event_city) if event_city else city,
                    ticket_url=e.get("url") or e.get("ticket_url"),
                    raw_data=e,
                    image_url=e.get("image_url") or e.get("image"),
                )
            )
        return results

    async def search_events_by_city(
        self,
        city: str = "",
        genre: str | None = None,
        page: int = 1,
    ) -> list[EventResult]:
        """Search for all events in a city — not tied to a specific artist."""
        params = {
            "city": city,
            "types": "event",
            "sort": "date",
            "page": str(page),
        }
        if genre:
            params["genre"] = genre

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://{RAPIDAPI_HOST}/search",
                headers=self.headers,
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

        events = data if isinstance(data, list) else data.get("results", data.get("data", []))
        if isinstance(events, dict):
            events = [events]

        return [
            EventResult(
                source="concerts_tracker",
                source_id=str(e.get("id", e.get("event_id", ""))),
                title=e.get("name", e.get("title", "")),
                date=e.get("date", e.get("starts_at", "")),
                venue=(
                    e.get("venue", {}).get("name", "")
                    if isinstance(e.get("venue"), dict)
                    else e.get("venue_name", e.get("venue", ""))
                ),
                city=city,
                ticket_url=e.get("url") or e.get("ticket_url"),
                raw_data=e,
            )
            for e in events
            if isinstance(e, dict)
        ]
