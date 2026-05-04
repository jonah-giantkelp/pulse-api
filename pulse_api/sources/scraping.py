"""Shared web-scraping utilities for sources that lack a public API.

Provides direct_fetch (browser-style HTTP GET), Jina reader proxy for
markdown conversion (website scraper only), and OpenAI web_search for
discovering artist pages on DICE / Bandsintown / etc.
"""

import json
import logging
import re

import httpx

from pulse_api.config import settings

logger = logging.getLogger(__name__)

JINA_PREFIX = "https://r.jina.ai/"


# ─────────────────────────────────────────────
# Direct fetch (primary path for all scrapers)
# ─────────────────────────────────────────────


BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
}


async def direct_fetch(
    url: str,
    *,
    source_name: str = "scraper",
    timeout: float = 15.0,
) -> httpx.Response:
    """Fetch a URL directly (no proxy) with a browser User-Agent."""
    logger.debug("%s: fetching %s", source_name, url)
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=timeout, headers=BROWSER_HEADERS
    ) as client:
        return await client.get(url)


# ─────────────────────────────────────────────
# Jina reader (used only by the website scraper)
# ─────────────────────────────────────────────


async def jina_fetch_md(url: str, *, source_name: str = "scraper") -> str:
    """Fetch a URL via r.jina.ai and return markdown."""
    jina_url = f"{JINA_PREFIX}{url}"
    logger.debug("%s: Jina fetch %s", source_name, url)
    headers = {
        "Accept": "text/plain",
        "X-Return-Format": "markdown",
    }
    key = settings.jina_api_key
    if key:
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(jina_url, headers=headers)
    resp.raise_for_status()
    return resp.text


# ─────────────────────────────────────────────
# Web search via OpenAI web_search (giantkelp-ai)
# ─────────────────────────────────────────────


def _extract_url_from_ai_response(text: str, site: str, needle: str) -> str | None:
    """Pull the first URL matching site/needle from an AI text response."""
    for m in re.finditer(r'https?://[^\s<>"\')\]]+', text):
        url = m.group(0).rstrip(".,;:")
        if site in url and needle in url:
            clean = url.split("?")[0] if "?" in url else url
            return clean
    return None


async def search_url(
    query: str,
    site: str,
    *,
    path_pattern: str = "",
    source_name: str = "scraper",
) -> str | None:
    """Search for *query* on *site* via OpenAI web_search, return first matching URL.

    *path_pattern* narrows matches, e.g. ``"/artist/"`` or ``"/a/"``.
    """
    full_query = f"{query} site:{site}"
    needle = path_pattern or site

    try:
        from giantkelp_ai import AIAgent

        agent = AIAgent(provider="openai", agent_name="slug_search")
        ai_query = (
            f"Find the exact URL for the artist \"{query}\" on {site}. "
            f"The URL must contain \"{needle}\". "
            f"Return ONLY the URL, nothing else."
        )
        result = agent.web_search(
            query=ai_query,
            scope="fast",
            max_results=5,
        )
        text = result if isinstance(result, str) else str(result)
        url = _extract_url_from_ai_response(text, site, needle)
        if url:
            logger.info("%s: web search %r → %s", source_name, query, url)
            return url
        logger.info("%s: web search %r → no matching URL", source_name, query)
    except Exception as e:
        logger.warning("%s: web search failed for %r: %s", source_name, query, e)

    return None


# ─────────────────────────────────────────────
# JSON-LD extraction
# ─────────────────────────────────────────────


def extract_json_ld(html: str) -> list[dict]:
    """Extract all JSON-LD blocks from an HTML page."""
    results = []
    marker = '<script type="application/ld+json">'
    start = 0
    while True:
        idx = html.find(marker, start)
        if idx == -1:
            break
        idx += len(marker)
        end_idx = html.find("</script>", idx)
        if end_idx == -1:
            break
        try:
            data = json.loads(html[idx:end_idx])
            if isinstance(data, list):
                results.extend(data)
            else:
                results.append(data)
        except json.JSONDecodeError:
            pass
        start = end_idx
    return results


# ─────────────────────────────────────────────
# Markdown helpers
# ─────────────────────────────────────────────


def trim_to_section(md: str, *needles: str) -> str:
    """Return everything from the first heading containing any *needle*."""
    lower_needles = [n.lower() for n in needles]
    lines = md.split("\n")
    for i, line in enumerate(lines):
        if line.startswith("#"):
            heading_lower = line.lower()
            if any(n in heading_lower for n in lower_needles):
                return "\n".join(lines[i:])
    return md


def extract_md_links(md: str) -> list[tuple[str, str]]:
    """Return all ``(text, url)`` pairs from markdown ``[text](url)`` links."""
    return re.findall(r'\[([^\]]+)\]\((https?://[^)]+)\)', md)
