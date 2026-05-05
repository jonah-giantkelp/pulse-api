import json
import logging

from giantkelp_ai import AIAgent

from pulse_api.db import supabase

logger = logging.getLogger(__name__)

DISTILL_PROMPT = """You are extracting live event information from social media posts by the musical artist "{artist_name}".

Your job is to find every upcoming gig, concert, festival set, DJ set, tour date, or live appearance mentioned in these posts that we DON'T already know about.

We already have these events on record for this artist — DO NOT extract any of these again:
{known_events}

Posts:
{posts}

Return a JSON object:
{{
    "summary": "<one sentence about what events you found>",
    "gig_mentions": [
        {{
            "source_post_id": "<post_id of the post this event was found in>",
            "event_name": "<the event's real name — see naming rules below>",
            "artist_billing": "<one of: headline, support, b2b, dj_set, live, festival_slot>",
            "date": "<YYYY-MM-DD if exact, YYYY-MM if only month known (e.g. 'December' → '2026-12'), or YYYY if only year. Always resolve what you can: 'NYE 2025' → '2025-12-31', 'this summer' → '2026-07'. Use null only if truly unknowable>",
            "date_precision": "<one of: exact, month, season, unknown>",
            "time": "<HH:MM in 24h format, or null>",
            "venue_name": "<exact venue name as written, or null>",
            "city": "<city name, or null>",
            "country": "<ISO 3166-1 alpha-2 code, e.g. 'GB', 'DE', 'NL'. Infer from city/venue. Use null if unknown>",
            "ticket_url": "<URL to tickets if mentioned in the post, or null>",
            "confidence": "<one of: high, medium, low>",
            "has_event_image": <true if the source post contains a poster, flyer, or lineup image for this event, false otherwise>
        }}
    ]
}}

EVENT NAMING RULES (follow strictly):
- Festival / branded event / club night / series: use the event's own name.
  Examples: "Dekmantel 2026", "Boiler Room London", "fabric presents: Floating Points", "Houghton Festival"
- Solo show / headline gig: use "{artist_name} at {{Venue}}" format.
  Examples: "Floating Points at Fabric", "Four Tet at Printworks"
- Support / B2B / lineup slot: use the event name with the artist's role in artist_billing.
  Examples: event_name="Drumcode Halloween", artist_billing="dj_set"
- If neither a named event nor venue is known, use the raw descriptive text from the post.

CONFIDENCE RULES:
- high: explicit event announcement with date and venue clearly stated
- medium: event is clearly referenced but missing ONE of: exact date, venue (e.g. "Corsica Studios in March" or "March 20th, venue TBA")
- low: vague or teaser-style reference with no exact date AND no venue (e.g. "back in London in December", "all nighter coming soon"). These are NOT actionable yet.

Rules:
- Extract EVERY event mentioned, even if details are partial
- Include festival appearances, DJ sets, residencies, not just headline gigs
- If a post mentions a ticket link or pre-sale, that's an event — extract the URL
- Retweets/reposts of event announcements count
- IMPORTANT: Always resolve the city for festivals/branded events using your knowledge (e.g. Lentekabinet → Amsterdam/NL, Dekmantel → Amsterdam/NL, Houghton → Norfolk/GB). Do NOT leave city null for well-known festivals.
- If no events are found, return an empty gig_mentions list
- For has_event_image: set true only if the post clearly contains a poster, flyer, or lineup graphic (not just a random photo)
"""

ENRICH_IMAGE_PROMPT = """Look at this image from a social media post by the musical artist "{artist_name}".
It appears to relate to an event: {event_text}

Extract any event details visible in the image (concert posters, flyer graphics, etc.):
- Date (in YYYY-MM-DD format)
- Venue name
- City

Return a JSON object:
{{
    "date": "<YYYY-MM-DD or null>",
    "date_precision": "exact",
    "venue_name": "<venue name or null>",
    "city": "<city or null>"
}}

Only return details you can clearly read from the image. Use null for anything unclear."""

ENRICH_QUOTED_PROMPT = """You are extracting event details from a quoted/retweeted post related to the musical artist "{artist_name}".

The original post mentioned this event but lacked a date: {event_text}

Quoted/retweeted post text:
{quoted_text}

Return a JSON object:
{{
    "date": "<YYYY-MM-DD or null>",
    "date_precision": "exact",
    "venue_name": "<venue name or null>",
    "city": "<city or null>"
}}

Only return details clearly stated in the text. Use null for anything unclear."""

WEB_ENRICH_PROMPT = """You searched the web for details about this event by the artist "{artist_name}":
- Event: {event_name}
- Known venue: {venue_name}
- Known city: {city}
- Known date: {date}
- Date precision: {date_precision}

Search results:
{search_results}

Based on the search results, fill in any missing or imprecise details for this specific event.
Only use information that clearly refers to the same event.

Return a JSON object:
{{
    "date": "<YYYY-MM-DD or null — only update if you found a more precise date>",
    "date_precision": "<exact, month, season, or unknown>",
    "time": "<HH:MM or null>",
    "venue_name": "<venue name or null — only update if currently null>",
    "city": "<city or null — only update if currently null>",
    "country": "<ISO 3166-1 alpha-2 or null>",
    "ticket_url": "<URL to tickets or null>"
}}

Only return details you are confident about. Use null for anything uncertain."""


async def distill_posts(
    artist_id: str,
    artist_name: str,
    posts: list[dict],
    known_events: list[dict] | None = None,
    model: str | None = None,
) -> dict:
    """Distill social media posts into structured event mentions.

    *known_events* is a lightweight list of events already on record
    (title, date, venue) so the AI can skip duplicates cheaply.
    """
    if not posts:
        return {
            "summary": "No recent posts to analyse.",
            "gig_mentions": [],
            "has_london_relevance": False,
        }

    formatted_posts = "\n\n".join(
        f"[{p.get('platform', '?')} - {p.get('posted_at', '?')} - post_id:{p.get('post_id', '?')}]\n"
        f"{p.get('caption', '(no caption)')}"
        for p in posts
    )

    if known_events:
        formatted_known = "\n".join(
            f"- {e['title']} | {e.get('date', '?')} | {e.get('venue', '?')}"
            for e in known_events
        )
    else:
        formatted_known = "(none)"

    prompt = DISTILL_PROMPT.format(
        artist_name=artist_name,
        posts=formatted_posts,
        known_events=formatted_known,
    )

    agent = AIAgent(provider="openai")
    response = agent.smart_completion(
        user_prompt=prompt,
        json_output=True,
    )

    result = json.loads(response) if isinstance(response, str) else response
    gigs = result.get("gig_mentions", [])
    logger.info(
        "    🧠 AI distiller: %d gig(s) found — %s",
        len(gigs), result.get("summary", "")[:80],
    )

    # Store summary
    post_ids_result = (
        supabase.table("social_posts")
        .select("id")
        .eq("artist_id", artist_id)
        .order("created_at", desc=True)
        .limit(len(posts))
        .execute()
    )
    source_post_ids = [r["id"] for r in post_ids_result.data]

    supabase.table("social_summaries").insert(
        {
            "artist_id": artist_id,
            "summary": result.get("summary", ""),
            "date": "today",
            "model_used": model or "default",
            "source_post_ids": source_post_ids,
        }
    ).execute()

    return result


async def enrich_dateless_gigs(
    artist_name: str,
    gig_mentions: list[dict],
    posts_by_id: dict[str, dict],
) -> list[dict]:
    """Try to fill in missing dates by checking tweet images and quoted tweets.

    Args:
        artist_name: Name of the artist.
        gig_mentions: List of gig dicts from distill_posts (only dateless ones).
        posts_by_id: Mapping of post_id -> full post dict (with media_url, raw_data).

    Returns:
        The same gig_mentions list, mutated in-place with any newly found details.
    """
    agent = AIAgent(provider="openai")

    for gig in gig_mentions:
        if gig.get("date"):
            continue

        source_post = posts_by_id.get(gig.get("source_post_id", ""))
        if not source_post:
            continue

        raw = source_post.get("raw_data") or {}
        event_text = gig.get("event_name") or gig.get("text", "unknown event")

        # --- 1. Check quoted tweet text ---
        quoted = raw.get("quoted_tweet") or raw.get("retweeted_tweet") or {}
        quoted_text = quoted.get("text") or quoted.get("full_text")
        if quoted_text:
            try:
                resp = agent.fast_completion(
                    user_prompt=ENRICH_QUOTED_PROMPT.format(
                        artist_name=artist_name,
                        event_text=event_text,
                        quoted_text=quoted_text,
                    ),
                    json_output=True,
                )
                enriched = json.loads(resp) if isinstance(resp, str) else resp
                if enriched.get("date"):
                    gig["date"] = enriched["date"]
                    gig["date_precision"] = enriched.get("date_precision", "exact")
                    gig["venue_name"] = gig.get("venue_name") or enriched.get("venue_name")
                    gig["city"] = gig.get("city") or enriched.get("city")
                    logger.info(
                        "        🔗 Quoted tweet → date: %s for %s",
                        enriched["date"], event_text[:40],
                    )
                    continue
            except Exception as e:
                logger.debug("Quoted tweet enrichment failed: %s", e)

        # --- 2. Check image via vision ---
        media_url = source_post.get("media_url")
        if not media_url:
            media_urls = raw.get("media_url", [])
            if isinstance(media_urls, list) and media_urls:
                media_url = media_urls[0]

        if media_url:
            try:
                resp = agent.image_completion(
                    user_prompt=ENRICH_IMAGE_PROMPT.format(
                        artist_name=artist_name,
                        event_text=event_text,
                    ),
                    image=media_url,
                    file_path=False,
                    json_output=True,
                )
                enriched = json.loads(resp) if isinstance(resp, str) else resp
                if enriched.get("date"):
                    gig["date"] = enriched["date"]
                    gig["date_precision"] = enriched.get("date_precision", "exact")
                    gig["venue_name"] = gig.get("venue_name") or enriched.get("venue_name")
                    gig["city"] = gig.get("city") or enriched.get("city")
                    logger.info(
                        "        🖼️  Image → date: %s for %s",
                        enriched["date"], event_text[:40],
                    )
                    continue
            except Exception as e:
                logger.debug("Image enrichment failed: %s", e)

    return gig_mentions


async def web_enrich_event(
    artist_name: str,
    gig: dict,
) -> dict:
    """Use web search to fill in missing event details.

    Triggered for medium/high confidence events that are missing
    exact dates or venue information.

    Returns a dict of fields to merge into the gig.
    """
    event_name = gig.get("event_name") or gig.get("text", "unknown event")
    venue = gig.get("venue_name") or ""
    city = gig.get("city") or ""
    date = gig.get("date") or ""
    date_precision = gig.get("date_precision", "unknown")

    # Build a search query from what we know
    # For festivals, lead with the event name — it's the better search key
    billing = gig.get("artist_billing", "")
    is_festival = billing in ("festival_slot",) or any(
        kw in (event_name or "").lower()
        for kw in ("festival", "fest ", "kabinet", "carnival", "gala")
    )

    if is_festival and event_name:
        query_parts = [f'"{event_name}"']
        if date:
            query_parts.append(date)
        query_parts.append("tickets location")
    else:
        query_parts = [f'"{artist_name}"']
        if venue:
            query_parts.append(f'"{venue}"')
        if city:
            query_parts.append(city)
        if date:
            query_parts.append(date)
        elif event_name:
            query_parts.append(event_name)
        query_parts.append("tickets 2026")

    search_query = " ".join(query_parts)
    logger.info("        🔎 Web search: %s", search_query[:80])

    agent = AIAgent(provider="openai")
    search_results = agent.web_search(
        query=search_query,
        scope="fast",
        max_results=5,
        search_context_size="medium",
    )

    # Parse search results with AI
    resp = agent.fast_completion(
        user_prompt=WEB_ENRICH_PROMPT.format(
            artist_name=artist_name,
            event_name=event_name,
            venue_name=venue or "unknown",
            city=city or "unknown",
            date=date or "unknown",
            date_precision=date_precision,
            search_results=search_results,
        ),
        json_output=True,
    )

    enriched = json.loads(resp) if isinstance(resp, str) else resp

    # Only return non-null fields that improve on what we have
    updates = {}
    if enriched.get("date") and date_precision != "exact":
        updates["date"] = enriched["date"]
        updates["date_precision"] = enriched.get("date_precision", "exact")
    if enriched.get("time") and not gig.get("time"):
        updates["time"] = enriched["time"]
    if enriched.get("venue_name") and not venue:
        updates["venue_name"] = enriched["venue_name"]
    if enriched.get("city") and not city:
        updates["city"] = enriched["city"]
    if enriched.get("country") and not gig.get("country"):
        updates["country"] = enriched["country"]
    if enriched.get("ticket_url") and not gig.get("ticket_url"):
        updates["ticket_url"] = enriched["ticket_url"]

    if updates:
        logger.info("        ✅ Web enriched: %s", ", ".join(updates.keys()))
    else:
        logger.info("        ⚪ Web enrichment: no new details")

    return updates
