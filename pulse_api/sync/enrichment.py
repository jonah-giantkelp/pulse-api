"""Enrichment helpers — AI calls + per-source field extraction.

This module groups the value-add transforms that sit between raw source
results and the dedup/persistence steps:

  * `extract_lineup`            — pull artist names out of source raw_data
  * `normalise_title`           — for fuzzy name-similarity grouping
  * `parse_date_hint`           — coerce free-text dates to ISO
  * `ai_fill_geo`               — batch-fill missing city/country
  * `ai_clean_titles_and_cities` — strip platform junk from titles & addresses
  * `ai_merge_decision`         — adjudicate ambiguous same-event clusters
"""

import json
import logging
import re
from datetime import datetime, timezone

from dateutil import parser as dateparser

from pulse_api.sync.fingerprint import date_bucket

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Lineup extraction
# ─────────────────────────────────────────────

# Tie-break ranking when two sources tie on lineup length.
# Lower number = preferred. RA tends to have full festival lineups with IDs,
# Ticketmaster has structured attraction data, DICE often truncates to 5.
LINEUP_SOURCE_RANK = {
    "ra": 0,
    "ticketmaster": 1,
    "skiddle": 2,
    "bandsintown": 3,
    "dice": 4,
}


def extract_lineup(source: str, raw_data: dict | None) -> list[str]:
    """Pull the artist-name list out of a source's raw_data.

    Each ticketing platform parks its lineup in a different place — this is the
    one spot that knows the per-source paths so the merge step can compare
    contender lineup sizes apples-to-apples.
    """
    if not raw_data or not isinstance(raw_data, dict):
        return []

    def _names_from(items):
        if not isinstance(items, list):
            return []
        out = []
        for a in items:
            if isinstance(a, dict):
                n = a.get("name")
                if n:
                    out.append(n)
            elif isinstance(a, str) and a:
                out.append(a)
        return out

    if source == "ra":
        return _names_from(raw_data.get("artists"))

    if source == "dice":
        # Prefer a full lineup field if DICE exposes one; fall back to the
        # 5-artist summary card. (Field name varies — check both seen shapes.)
        for key in ("lineups", "lineup", "full_lineup"):
            names = _names_from(raw_data.get(key))
            if names:
                return names
        summary = raw_data.get("summary_lineup")
        if isinstance(summary, dict):
            return _names_from(summary.get("top_artists"))
        return []

    if source == "ticketmaster":
        embedded = raw_data.get("_embedded") or {}
        return _names_from(embedded.get("attractions"))

    if source in ("skiddle", "bandsintown"):
        return _names_from(raw_data.get("artists"))

    return []


# ─────────────────────────────────────────────
# Title / date hint helpers
# ─────────────────────────────────────────────


def normalise_title(title: str) -> str:
    """Rough normalisation for name-similarity grouping."""
    t = title.lower().strip()
    # Strip year suffixes, punctuation, extra whitespace
    t = re.sub(r"[''`]", "", t)
    t = re.sub(r"\s*20\d{2}\s*$", "", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


_NYE_RE = re.compile(r"(?i)\bNYE\b\s*(\d{4})?")
_YEAR_ONLY_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def parse_date_hint(hint: str | None) -> str | None:
    """Try to coerce a fuzzy date hint into an ISO-8601 timestamp string.

    Returns None if the hint is unparseable so callers can skip the event.
    """
    if not hint:
        return None

    hint = hint.strip()

    # Handle "NYE 2025" / "NYE" explicitly
    m = _NYE_RE.search(hint)
    if m:
        year = int(m.group(1)) if m.group(1) else datetime.now(timezone.utc).year
        return f"{year}-12-31T23:59:00Z"

    # Bare year like "2025" → Jan 1 of that year (rough placeholder)
    if _YEAR_ONLY_RE.match(hint):
        return f"{hint}-01-01T00:00:00Z"

    # Year-month like "2026-12" → 1st of that month
    if _YEAR_MONTH_RE.match(hint):
        return f"{hint}-01T00:00:00Z"

    try:
        dt = dateparser.parse(hint, fuzzy=True)
        return dt.isoformat()
    except (ValueError, OverflowError):
        return None


# ─────────────────────────────────────────────
# AI: fill missing city/country
# ─────────────────────────────────────────────

_GEO_PROMPT = """Given these music events, identify the city and country for each one.
Use the event title, venue name, and any reference events on the same date as clues.

Events to geo-locate:
{events}
{references}
Return a JSON object with exactly this structure:
{{
    "geo": [
        {{
            "city": "<city name or 'Unknown'>",
            "country": "<ISO 3166-1 alpha-2 code, or null if unknown>",
            "country_confidence": "high" | "medium" | "low"
        }}
    ]
}}

The "geo" array MUST contain exactly {count} entries, one per event above, in the same order.

Rules:
- Use the most well-known city name (e.g. "London" not "Greater London")
- If two events share the same date and similar title, they are probably the same event in the same city
- If you cannot determine the city, use "Unknown"
- For country: only return a code if you have direct evidence (named city, venue you recognise, or matching reference event). Otherwise return null.
- country_confidence is "high" only when the country is unambiguous from the venue/city. If you're guessing from weak signals or generic clues, use "medium" or "low".
- Do NOT default to any particular country when uncertain — return null with low confidence.
"""


async def ai_fill_geo(events: list[dict], all_events: list[dict] | None = None):
    """Batch-fill missing city/country on events using a fast AI call.

    *all_events* is the full list including events that already have cities,
    so the AI can cross-reference same-date events for dedup hints.
    """
    lines = []
    for i, e in enumerate(events):
        lines.append(
            f"{i + 1}. \"{e.get('title', '?')}\" at \"{e.get('venue', '?')}\" on {date_bucket(e.get('date')) or '?'}"
        )

    # Build reference lines from events that DO have a city on the same dates
    ref_lines = []
    if all_events:
        missing_dates = {date_bucket(e.get("date")) for e in events}
        for e in all_events:
            if e.get("city") and date_bucket(e.get("date")) in missing_dates:
                ref_lines.append(
                    f"- \"{e.get('title', '?')}\" at \"{e.get('venue', '?')}\" "
                    f"on {date_bucket(e.get('date')) or '?'} → {e['city']}, {e.get('country', '?')}"
                )

    references = ""
    if ref_lines:
        references = (
            "\nReference events (already geo-located, same dates — use as hints):\n"
            + "\n".join(ref_lines)
            + "\n"
        )

    try:
        from giantkelp_ai import AIAgent

        agent = AIAgent(provider="openai")
        resp = agent.fast_completion(
            user_prompt=_GEO_PROMPT.format(
                events="\n".join(lines),
                references=references,
                count=len(events),
            ),
            json_output=True,
        )
        raw = json.loads(resp) if isinstance(resp, str) else resp
        geo_list = raw.get("geo", []) if isinstance(raw, dict) else raw

        country_skipped = 0
        for i, geo in enumerate(geo_list):
            if i >= len(events):
                break
            if not isinstance(geo, dict):
                continue
            if geo.get("city"):
                events[i]["city"] = geo["city"]
            # Only write country when the model is confident — anything else
            # historically biased toward GB and polluted the column.
            country = geo.get("country")
            confidence = (geo.get("country_confidence") or "").lower()
            if country and confidence == "high":
                events[i]["country"] = country
            elif country:
                country_skipped += 1

        filled = sum(1 for e in events if e.get("city"))
        logger.info(
            "    🌍 AI geo filled %d/%d events (skipped %d low-confidence countries)",
            filled, len(events), country_skipped,
        )
    except Exception as e:
        logger.warning("    ⚠️  AI geo failed: %s", str(e)[:60])

    # Safety net: city is NOT NULL in DB
    for e in events:
        if not e.get("city"):
            e["city"] = "Unknown"


# ─────────────────────────────────────────────
# AI: title + city cleanup
# ─────────────────────────────────────────────

_CLEAN_PROMPT = """Clean up these music event titles and cities.

Events:
{events}

Return a JSON object:
{{
    "cleaned": [
        {{"title": "<cleaned title>", "city": "<cleaned city>"}}
    ]
}}

The "cleaned" array MUST have exactly {count} entries, one per event, in the same order.

Title rules:
- Remove platform-specific suffixes like "| DAY 2 - Saturday", "| FULL PASS", "- Tickets", ticket IDs
- Keep the core event name, year, and any meaningful qualifier (e.g. "Opening Party" is meaningful)
- Do NOT shorten names that are already clean — "Rainbow Disco Club 2026" stays as-is
- "Spring Attitude Festival 2026 | FULL PASS" → "Spring Attitude Festival 2026"
- "Spring Attitude Festival 2026 | DAY 2 - Saturday" → "Spring Attitude Festival 2026 - Day 2"
- "53rav8-circoloco-ibiza-opening-party-27th-apr-dc-10-ibiza-tickets" → "Circoloco Ibiza - Opening Party"

City rules:
- Return just the city name, no addresses or postcodes
- "Straker's Rd, London SE15 3UA, UK" → "London"
- "41 Rue Jobin, 13003 Marseille, France" → "Marseille"
- "BH21 5NA, Wimborne, Dorset, England, United Kingdom" → "Wimborne"
- If already clean (e.g. "London", "Ibiza"), return as-is
"""


def _needs_title_clean(title: str) -> bool:
    """Check if a title likely has platform junk."""
    if len(title) > 30:
        return True
    if "|" in title or " - Tickets" in title:
        return True
    return False


def _needs_city_clean(city: str | None) -> bool:
    """Check if a city has address junk (punctuation beyond periods in acronyms)."""
    if not city:
        return False
    # Clean if it has commas, digits, or postcodes
    if "," in city or re.search(r"\d", city):
        return True
    return False


def ai_clean_titles_and_cities(events: list[dict]):
    """Batch-clean titles >30 chars and cities with address junk."""
    indices_to_clean = []
    for i, e in enumerate(events):
        if _needs_title_clean(e.get("title", "")) or _needs_city_clean(e.get("city")):
            indices_to_clean.append(i)

    if not indices_to_clean:
        return

    lines = []
    for i in indices_to_clean:
        e = events[i]
        lines.append(f"{len(lines) + 1}. title: \"{e.get('title', '')}\" | city: \"{e.get('city', '')}\"")

    try:
        from giantkelp_ai import AIAgent

        agent = AIAgent(provider="openai")
        resp = agent.fast_completion(
            user_prompt=_CLEAN_PROMPT.format(
                events="\n".join(lines),
                count=len(indices_to_clean),
            ),
            json_output=True,
        )
        raw = json.loads(resp) if isinstance(resp, str) else resp
        cleaned = raw.get("cleaned", []) if isinstance(raw, dict) else raw

        applied = 0
        for j, c in enumerate(cleaned):
            if j >= len(indices_to_clean):
                break
            if not isinstance(c, dict):
                continue
            idx = indices_to_clean[j]
            if c.get("title"):
                old_title = events[idx]["title"]
                events[idx]["title"] = c["title"]
                if c["title"] != old_title:
                    applied += 1
            if c.get("city"):
                events[idx]["city"] = c["city"]

        logger.info("    ✨ AI cleaned %d title(s) / %d checked", applied, len(indices_to_clean))
    except Exception as e:
        logger.warning("    ⚠️  AI title cleanup failed: %s", str(e)[:60])


# ─────────────────────────────────────────────
# AI: same-event merge adjudication
# ─────────────────────────────────────────────

_MERGE_PROMPT = """You are deduplicating music events for the same artist from different ticketing platforms.

Events that might be duplicates (grouped by similar name):
{groups}

For each group, decide: are these the SAME real-world event listed on different platforms, or genuinely different events?

Return a JSON object:
{{
    "groups": [
        {{
            "ids": [<list of event numbers that are the same event>],
            "same_event": true | false,
            "reason": "<brief explanation>"
        }}
    ]
}}

Rules:
- Same festival/event name + same or adjacent dates = same event (even if venue name differs slightly)
- "Festival X | DAY 1" and "Festival X | DAY 2" are DIFFERENT events
- "Festival X | FULL PASS" and "Festival X | DAY 2" are DIFFERENT events
- A headline show and a festival on the same date in the same city are DIFFERENT events
- Slight title variations like "GALA'26" vs "GALA '26" = same event
"""


def ai_merge_decision(
    events: list[dict],
    clusters: list[list[int]],
) -> list[list[int]]:
    """Ask AI which similar-named events are actually the same."""
    # Format the groups for the prompt
    group_lines = []
    event_num = 1
    idx_to_num: dict[int, int] = {}
    for ci, indices in enumerate(clusters):
        group_lines.append(f"\nGroup {ci + 1}:")
        for idx in indices:
            e = events[idx]
            idx_to_num[idx] = event_num
            group_lines.append(
                f"  #{event_num}. \"{e.get('title', '?')}\" | "
                f"{date_bucket(e.get('date')) or '?'} | "
                f"{e.get('venue', '?')} | {e.get('city', '?')} | "
                f"source: {e.get('source')}"
            )
            event_num += 1

    try:
        from giantkelp_ai import AIAgent

        agent = AIAgent(provider="openai")
        resp = agent.fast_completion(
            user_prompt=_MERGE_PROMPT.format(groups="\n".join(group_lines)),
            json_output=True,
        )
        raw = json.loads(resp) if isinstance(resp, str) else resp
        ai_groups = raw.get("groups", []) if isinstance(raw, dict) else raw

        # Convert AI event numbers back to list indices
        num_to_idx = {v: k for k, v in idx_to_num.items()}
        merges = []
        for g in ai_groups:
            if not isinstance(g, dict) or not g.get("same_event"):
                continue
            ids = g.get("ids", [])
            real_indices = [num_to_idx[n] for n in ids if n in num_to_idx]
            if len(real_indices) >= 2:
                logger.info("    🔗 AI merge: %s", g.get("reason", "")[:60])
                merges.append(real_indices)
        return merges
    except Exception as e:
        logger.warning("    ⚠️  AI merge decision failed: %s", str(e)[:60])
        return []
