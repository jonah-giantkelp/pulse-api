"""Bandsintown source — direct fetch + structured extraction.

Bandsintown is a Next.js app that embeds its state in a
``<script>window.__data={...}</script>`` blob. We pull that JSON out
and read the canonical events list from:

    window.__data.artistView.body.events.upcomingEvents.events[]

Each item already has clean fields (`id`, `venueName`, `location`,
`eventUrl`, `startsAt`, `timezone`) — no markdown scraping, no HTML
anchor parsing, no proxy.
"""

import json
import logging
import re
from urllib.parse import quote

from dateutil import parser as dateparser

from pulse_api.sources.base import EventSource, SearchResult, EventResult
from pulse_api.sources.scraping import direct_fetch, search_url

logger = logging.getLogger(__name__)

_BIT_SLUG_RE = re.compile(r"bandsintown\.com/a/([^/?#]+)")


class BandsintownSource(EventSource):
    async def search_artist(self, name: str) -> list[SearchResult]:
        """Find a Bandsintown artist slug via web search."""
        url = await search_url(
            name,
            "bandsintown.com",
            path_pattern="/a/",
            source_name="Bandsintown",
        )
        if not url:
            logger.info("Bandsintown: search %r → no results", name)
            return []

        m = _BIT_SLUG_RE.search(url)
        if not m:
            logger.info("Bandsintown: search %r → URL didn't match slug pattern", name)
            return []

        slug = m.group(1)
        logger.info("Bandsintown: search %r → slug %s", name, slug)
        return [
            SearchResult(
                platform="bandsintown",
                platform_id=slug,
                name=name,
                url=f"https://www.bandsintown.com/a/{slug}",
            )
        ]

    async def get_events(
        self, artist_name: str, city: str | None = None
    ) -> list[EventResult]:
        """Fetch the artist page and extract events from window.__data.

        *artist_name* here is the ``bandsintown_name`` DB field — which
        is typically ``"<numeric_id>-<slug>"`` (e.g. ``5063310-palms-trax``).
        """
        slug = quote(artist_name.replace(" ", "-"), safe="-")
        url = f"https://www.bandsintown.com/a/{slug}"

        events = await self._fetch_via_next_data(url)
        if events is None:
            logger.warning("Bandsintown: parse failed for %s", artist_name)
            return []

        logger.info("Bandsintown: %d event(s) for %s", len(events), artist_name)
        return events

    async def _fetch_via_next_data(self, url: str) -> list[EventResult] | None:
        """Return parsed events, or None if parsing failed.

        An empty list means "page loaded fine, artist has no upcoming events"
        — that's a definitive answer.
        """
        try:
            resp = await direct_fetch(url, source_name="Bandsintown")
        except Exception as e:
            logger.warning("Bandsintown: fetch failed: %s", e)
            return None

        # Bandsintown sometimes returns 404 even for valid artists, but
        # still embeds window.__data with artistView — so we don't bail
        # on non-200 alone. We try to parse regardless.
        data = _extract_window_data(resp.text)
        if not data:
            logger.debug("Bandsintown: window.__data not found on page")
            return None

        try:
            raw_events = (
                data["artistView"]["body"]["events"]
                ["upcomingEvents"]["events"]
            )
        except (KeyError, TypeError):
            logger.debug("Bandsintown: upcomingEvents.events path missing")
            return None

        results = []
        for e in raw_events:
            if not isinstance(e, dict):
                continue
            event_id = str(e.get("id") or "")
            if not event_id:
                continue

            starts_at = e.get("startsAt")  # ISO without timezone
            timezone = e.get("timezone")  # e.g. "Europe/London"
            iso_date = _combine_date_tz(starts_at, timezone)
            if not iso_date:
                continue

            venue = e.get("venueName") or None
            # "City, Country" → take the city part
            location = e.get("location") or ""
            city_val = location.split(",", 1)[0].strip() if location else None

            ticket_url = (e.get("eventUrl") or "").split("?")[0] or None
            title_artist = (
                (data.get("artistView") or {})
                .get("body", {}).get("topSection", {}).get("name")
                or ""
            )
            title = (
                f"{title_artist} at {venue}" if title_artist and venue
                else (venue or title_artist or "Live show")
            )

            results.append(
                EventResult(
                    source="bandsintown",
                    source_id=event_id,
                    title=title,
                    date=iso_date,
                    venue=venue,
                    city=city_val,
                    ticket_url=ticket_url,
                    raw_data=e,
                )
            )

        return results


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────


def _extract_window_data(html: str) -> dict | None:
    """Pull the ``window.__data = {...}`` JSON blob out of the HTML.

    Uses brace-balanced scanning because the blob contains embedded
    strings with curly braces that a naive regex would choke on.
    """
    marker = "window.__data="
    start = html.find(marker)
    if start == -1:
        return None
    i = start + len(marker)
    depth = 0
    in_str = False
    esc = False
    for j in range(i, len(html)):
        c = html[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i : j + 1])
                except json.JSONDecodeError as e:
                    logger.warning("Bandsintown: __data JSON decode failed: %s", e)
                    return None
    return None


def _combine_date_tz(iso_local: str | None, tz_name: str | None) -> str | None:
    """Turn a naive local ISO string + IANA tz into a full ISO-8601 timestamp."""
    if not iso_local:
        return None
    if not tz_name:
        return iso_local
    try:
        from zoneinfo import ZoneInfo
        dt = dateparser.parse(iso_local)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.isoformat()
    except Exception:
        return iso_local
