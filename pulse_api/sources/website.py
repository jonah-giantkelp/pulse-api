import json
import logging

import httpx
from giantkelp_ai import AIAgent

from pulse_api.ai import metrics
from pulse_api.config import settings
from pulse_api.sources.base import EventResult

logger = logging.getLogger(__name__)

JINA_BASE = "https://r.jina.ai"


FIND_TOUR_PAGE_PROMPT = """You are looking at the homepage of the musical artist "{artist_name}".

Page URL: {url}
Page content:
{content}

Look at the navigation links and page content. Is there a dedicated page for tour dates,
live shows, gigs, events, or concerts? If so, return the full URL. If the current page
already contains tour/gig listings, return the current URL.

Return a JSON object:
{{
    "tour_page_url": "<full URL of the tour/gigs page, or the current URL if dates are already here>",
    "has_dates_on_this_page": true | false
}}
"""

EXTRACT_PROMPT = """You are analysing an artist's website content for upcoming live performances.

Artist: {artist_name}
Page URL: {url}

Page content:
{content}

Extract any upcoming gigs, concerts, tours, or festival appearances. Return a JSON object:
{{
    "events": [
        {{
            "title": "<event name>",
            "date": "<date in ISO format if possible, otherwise as written>",
            "venue": "<venue name or null>",
            "city": "<city or null>",
            "ticket_url": "<ticket link or null>"
        }}
    ]
}}

Include all events regardless of location.
If no events are found, return {{"events": []}}.
"""


async def _jina_fetch(url: str) -> str | None:
    """Fetch a URL via r.jina.ai and return clean markdown."""
    headers = {
        "Accept": "application/json",
        "X-Return-Format": "markdown",
    }
    key = settings.jina_api_key
    if key:
        headers["Authorization"] = f"Bearer {key}"

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{JINA_BASE}/{url}",
                headers=headers,
            )
            if resp.status_code != 200:
                logger.warning("website: Jina fetch returned %d for %s", resp.status_code, url)
                return None
            data = resp.json()
            content = data.get("data", {}).get("content", "")
            return content[:10000] if content else None
    except Exception as e:
        logger.warning("website: Jina fetch failed: %s", e)
        return None


async def scrape_artist_website(
    website_url: str,
    artist_name: str,
    city: str | None = None,
) -> list[EventResult]:
    """Scrape an artist's website using AI to find and extract gig info."""

    # Step 1: Fetch the homepage via Jina
    homepage_content = await _jina_fetch(website_url)
    if not homepage_content:
        return []

    # Step 2: Ask AI to find the tour/gigs page
    agent = AIAgent(provider="openai", agent_name="website_scraper")

    find_prompt = FIND_TOUR_PAGE_PROMPT.format(
        artist_name=artist_name,
        url=website_url,
        content=homepage_content,
    )
    find_response = agent.fast_completion(user_prompt=find_prompt, json_output=True)
    metrics.record(
        "tour-find", "fast",
        input_chars=len(find_prompt),
        output_chars=metrics.response_chars(find_response),
    )
    find_result = json.loads(find_response) if isinstance(find_response, str) else find_response

    tour_page_url = find_result.get("tour_page_url", website_url)
    has_dates_here = find_result.get("has_dates_on_this_page", False)

    # Step 3: If the tour page is different from the homepage, fetch it
    if has_dates_here:
        tour_content = homepage_content
    else:
        tour_content = await _jina_fetch(tour_page_url)
        if not tour_content:
            # Fall back to homepage content
            tour_content = homepage_content
            tour_page_url = website_url

    # Step 4: Extract events from the tour page
    extract_prompt = EXTRACT_PROMPT.format(
        artist_name=artist_name,
        url=tour_page_url,
        content=tour_content,
    )
    extract_response = agent.fast_completion(user_prompt=extract_prompt, json_output=True)
    metrics.record(
        "web-extract", "fast",
        input_chars=len(extract_prompt),
        output_chars=metrics.response_chars(extract_response),
    )
    extract_result = json.loads(extract_response) if isinstance(extract_response, str) else extract_response

    events = extract_result.get("events", [])
    return [
        EventResult(
            source="website",
            source_id=f"{website_url}:{e.get('date', '')}:{e.get('title', '')}",
            title=e.get("title", ""),
            date=e.get("date", ""),
            venue=e.get("venue"),
            city=e.get("city"),
            ticket_url=e.get("ticket_url"),
            raw_data=e,
        )
        for e in events
    ]
