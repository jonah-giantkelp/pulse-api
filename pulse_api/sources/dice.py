"""DICE source — direct fetch + structured extraction.

DICE is a Next.js app. The artist page embeds its full state in
``<script id="__NEXT_DATA__">`` JSON. Upcoming events live at:

    props.pageProps.initialProfile.sections[*].events[]

Each item has rich, typed fields (``dates.event_start_date``, ``venues[0]``
with city + country, ``images.landscape``, ``perm_name`` for the URL).

Falls back to JSON-LD extraction if the Next.js shape ever changes.
"""

import json
import logging
import re

from bs4 import BeautifulSoup

from pulse_api.sources.base import EventSource, SearchResult, EventResult
from pulse_api.sources.scraping import (
    direct_fetch,
    extract_json_ld,
    search_url,
)

logger = logging.getLogger(__name__)

_DICE_ARTIST_RE = re.compile(r"dice\.fm/artist/([^/?#]+)")


class DiceSource(EventSource):
    async def search_artist(self, name: str) -> list[SearchResult]:
        """Find a DICE artist slug via web search."""
        url = await search_url(
            name,
            "dice.fm",
            path_pattern="/artist/",
            source_name="DICE",
        )
        if not url:
            logger.info("DICE: search %r → no results", name)
            return []

        m = _DICE_ARTIST_RE.search(url)
        if not m:
            logger.info("DICE: search %r → URL didn't match artist pattern", name)
            return []

        slug = m.group(1)
        logger.info("DICE: search %r → slug %s", name, slug)
        return [
            SearchResult(
                platform="dice",
                platform_id=slug,
                name=name,
                url=f"https://dice.fm/artist/{slug}",
            )
        ]

    async def get_events(
        self, artist_slug: str, city: str | None = None
    ) -> list[EventResult]:
        """Extract events for a DICE artist page.

        Strategy order:
          1. direct fetch → __NEXT_DATA__ (rich, structured, authoritative)
          2. direct fetch → JSON-LD (next featured event only)
        """
        url = f"https://dice.fm/artist/{artist_slug}"

        # --- Primary: Next.js __NEXT_DATA__ ---
        results = await self._extract_from_next_data(url)
        if results is not None:
            logger.info("DICE: %d event(s) via __NEXT_DATA__ for %s", len(results), artist_slug)
            return results

        # --- Fallback: JSON-LD on direct fetch ---
        results = await self._extract_from_json_ld(url, city)
        if results:
            logger.info("DICE: %d event(s) via JSON-LD for %s", len(results), artist_slug)
        else:
            logger.info("DICE: 0 events for %s", artist_slug)
        return results

    async def _extract_from_next_data(self, url: str) -> list[EventResult] | None:
        """Return parsed events, or None if parsing failed (caller can fall back).

        An empty list means "page loaded fine, artist has no upcoming events"
        — that's a definitive answer, not a reason to retry via Jina.
        """
        try:
            resp = await direct_fetch(url, source_name="DICE")
        except Exception as e:
            logger.warning("DICE: fetch failed: %s", e)
            return None
        if resp.status_code != 200:
            logger.warning("DICE: fetch returned %d", resp.status_code)
            return None

        soup = BeautifulSoup(resp.text, "lxml")
        tag = soup.find("script", id="__NEXT_DATA__")
        if not tag or not tag.string:
            logger.debug("DICE: __NEXT_DATA__ script not found")
            return None
        try:
            data = json.loads(tag.string)
        except json.JSONDecodeError as e:
            logger.warning("DICE: __NEXT_DATA__ JSON decode failed: %s", e)
            return None

        try:
            sections = data["props"]["pageProps"]["initialProfile"]["sections"]
        except (KeyError, TypeError):
            logger.debug("DICE: initialProfile.sections path missing")
            return None

        results: list[EventResult] = []
        for section in sections or []:
            # A section with type in {'PAST', 'past'} would be past events.
            # Upcoming sections have type=None or title "Upcoming events".
            if (section.get("type") or "").lower() == "past":
                continue
            for ev in section.get("events") or []:
                if not isinstance(ev, dict):
                    continue
                parsed = _dice_event_to_result(ev)
                if parsed is not None:
                    results.append(parsed)
        return results

    async def _extract_from_json_ld(
        self, url: str, city: str
    ) -> list[EventResult]:
        """JSON-LD extraction via direct fetch."""
        try:
            resp = await direct_fetch(url, source_name="DICE")
        except Exception as e:
            logger.warning("DICE: fetch failed: %s", e)
            return []
        if resp.status_code != 200:
            return []

        json_ld = extract_json_ld(resp.text)

        event_items = []
        for item in json_ld:
            if not isinstance(item, dict):
                continue
            item_type = item.get("@type", "")
            if item_type in ("MusicEvent", "Event", "Festival", "DanceEvent"):
                event_items.append(item)
            for key in ("event", "events", "subEvent", "subEvents"):
                nested = item.get(key, [])
                if isinstance(nested, dict):
                    nested = [nested]
                if isinstance(nested, list):
                    for n in nested:
                        if isinstance(n, dict):
                            event_items.append(n)

        if not event_items:
            return []

        results = []
        for item in event_items:
            location = item.get("location") or {}
            if isinstance(location, str):
                venue_name, event_city = location, ""
            else:
                venue_name = location.get("name", "")
                address = location.get("address") or {}
                event_city = (
                    address if isinstance(address, str)
                    else address.get("addressLocality", "")
                )

            image = item.get("image")
            if isinstance(image, str):
                image_url = image
            elif isinstance(image, dict):
                image_url = image.get("url")
            elif isinstance(image, list) and image:
                first = image[0]
                image_url = (
                    first if isinstance(first, str)
                    else first.get("url") if isinstance(first, dict) else None
                )
            else:
                image_url = None

            offers = item.get("offers")
            if isinstance(offers, list) and offers:
                offers = offers[0]
            offers_url = offers.get("url") if isinstance(offers, dict) else None

            results.append(
                EventResult(
                    source="dice",
                    source_id=(
                        item.get("url", "").rstrip("/").split("/")[-1]
                        or item.get("name", "")
                    ),
                    title=item.get("name", ""),
                    date=item.get("startDate", ""),
                    venue=venue_name or None,
                    city=event_city or None,
                    ticket_url=item.get("url") or offers_url,
                    raw_data=item,
                    image_url=image_url,
                )
            )
        return results



# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _dice_event_to_result(ev: dict) -> EventResult | None:
    """Convert a Next.js profile-section event dict to an EventResult."""
    ev_id = str(ev.get("id") or "")
    name = (ev.get("name") or "").strip()
    if not ev_id or not name:
        return None

    dates = ev.get("dates") or {}
    iso_date = dates.get("event_start_date") or ""
    if not iso_date:
        return None

    venues = ev.get("venues") or []
    venue_obj = venues[0] if venues and isinstance(venues[0], dict) else {}
    venue = venue_obj.get("name") or None
    city_obj = venue_obj.get("city") or {}
    city = city_obj.get("name") if isinstance(city_obj, dict) else None

    images = ev.get("images") or {}
    image_url = None
    if isinstance(images, dict):
        image_url = (
            images.get("landscape")
            or images.get("square")
            or images.get("portrait")
        )

    perm = ev.get("perm_name")
    ticket_url = f"https://dice.fm/event/{perm}" if perm else None

    return EventResult(
        source="dice",
        source_id=ev_id,
        title=name,
        date=iso_date,
        venue=venue,
        city=city,
        ticket_url=ticket_url,
        raw_data=ev,
        image_url=image_url,
    )
