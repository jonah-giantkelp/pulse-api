import asyncio
import json
import logging

from giantkelp_ai import AIAgent

from pulse_api.ai import metrics

logger = logging.getLogger(__name__)

from pulse_api.sources.spotify import SpotifySource
from pulse_api.sources.ticketmaster import TicketmasterSource
from pulse_api.sources.bandsintown import BandsintownSource
from pulse_api.sources.instagram import InstagramSource
from pulse_api.sources.twitter import TwitterSource
from pulse_api.sources.skiddle import SkiddleSource
from pulse_api.sources.concerts import ConcertsTrackerSource
from pulse_api.sources.ra import RASource
from pulse_api.sources.dice import DiceSource
from pulse_api.sources.base import SearchResult
from pulse_api.sources.musicbrainz import get_artist_details as mb_get_artist_details
from pulse_api.db import supabase

PLATFORMS = {
    "spotify": SpotifySource,
    "ticketmaster": TicketmasterSource,
    "bandsintown": BandsintownSource,
    "instagram": InstagramSource,
    "twitter": TwitterSource,
    "skiddle": SkiddleSource,
    "concerts_tracker": ConcertsTrackerSource,
    "ra": RASource,
    "dice": DiceSource,
}

RESOLUTION_PROMPT = """You are matching the musical artist "{artist_name}" across multiple platforms.
{disambiguator}

For each platform below, identify which result (if any) is the correct match for this artist.
Use cross-referencing signals: genre consistency, follower scale, bio content, linked socials,
profile images, and naming patterns.

{platform_results}

Return a JSON object with this exact structure:
{{
    "matches": {{
        "<platform>": {{
            "platform_id": "<id of best match or null>",
            "confidence": "high" | "medium" | "low",
            "reasoning": "<brief explanation>"
        }}
    }}
}}

Rules:
- If a result has an EXACT name match (case-insensitive), that is almost certainly correct — set confidence to "high"
- If there is one clear match, set confidence to "high"
- If there are multiple plausible matches, set confidence to "medium" and pick the most likely
- If no results seem to match, set platform_id to null and confidence to "low"
- Consider that smaller/niche artists may not be on every platform
- For unusual or distinctive artist names (e.g. "Hunee", "Antal", "Objekt"), an exact name match is very strong evidence
- Do NOT return null just because there are also non-matching results — focus on the best match
"""


def _format_results(
    results: dict[str, list[SearchResult]],
) -> str:
    sections = []
    for platform, items in results.items():
        if not items:
            sections.append(f"\n## {platform}\nNo results found.")
            continue

        # Filter out empty/private results to reduce noise
        filtered = [
            r for r in items
            if r.platform_id
            and not (r.extra and r.extra.get("is_private"))
        ]
        if not filtered:
            sections.append(f"\n## {platform}\nNo usable results (all empty or private).")
            continue

        lines = [f"\n## {platform}"]
        for i, r in enumerate(filtered[:10]):
            parts = [f"  - ID: {r.platform_id}", f"    Name: {r.name}"]
            if r.followers is not None:
                parts.append(f"    Followers: {r.followers}")
            if r.genres:
                parts.append(f"    Genres: {', '.join(r.genres)}")
            if r.bio:
                parts.append(f"    Bio: {r.bio[:200]}")
            if r.url:
                parts.append(f"    URL: {r.url}")
            if r.extra:
                for k, v in r.extra.items():
                    if v:
                        parts.append(f"    {k}: {v}")
            lines.append(f"  Result {i + 1}:\n" + "\n".join(parts))
        sections.append("\n".join(lines))

    return "\n".join(sections)


async def search_all_platforms(artist_name: str) -> dict[str, list[SearchResult]]:
    results = {}

    async def _search(platform: str, source_cls):
        try:
            source = source_cls()
            found = await source.search_artist(artist_name)
            results[platform] = found
            if found:
                top = found[0]
                print(f"  ✅ {platform}: {len(found)} results — top: \"{top.name}\" (ID: {top.platform_id})")
            else:
                print(f"  ⚪ {platform}: 0 results")
        except Exception as e:
            logger.error("Platform %s search failed", platform, exc_info=True)
            err = str(e).split("\n")[0][:80]
            print(f"  ❌ {platform}: {err}")
            results[platform] = []

    print(f"\n🔍 Searching all platforms for \"{artist_name}\"...")
    await asyncio.gather(
        *[_search(p, cls) for p, cls in PLATFORMS.items()]
    )
    return results


async def resolve_artist(
    artist_name: str,
    disambiguator: str | None = None,
) -> dict:
    """Resolve an artist across all platforms using AI matching."""
    # Step 1: fan out searches
    search_results = await search_all_platforms(artist_name)

    # Step 2: AI matching
    print(f"\n🤖 Asking AI to match \"{artist_name}\" across platforms...")
    disamb_text = f"Additional context: {disambiguator}" if disambiguator else ""
    prompt = RESOLUTION_PROMPT.format(
        artist_name=artist_name,
        disambiguator=disamb_text,
        platform_results=_format_results(search_results),
    )

    agent = AIAgent(provider="openai", agent_name="artist_resolver")
    response = agent.smart_completion(
        user_prompt=prompt,
        json_output=True,
    )
    metrics.record(
        "resolution", "smart",
        input_chars=len(prompt),
        output_chars=metrics.response_chars(response),
    )

    ai_matches = (json.loads(response) if isinstance(response, str) else response)["matches"]

    # Step 3: determine statuses and what needs review
    matches = {}
    needs_review = []

    print(f"\n📋 Resolution results:")
    for platform, match in ai_matches.items():
        pid = match.get("platform_id")
        confidence = match.get("confidence", "low")

        if pid is None:
            status = "not_found"
            icon = "⚫"
        elif confidence in ("high", "medium"):
            status = "resolved"
            icon = "✅" if confidence == "high" else "🔵"
        else:
            status = "ambiguous"
            icon = "🟡"
            needs_review.append(platform)

        print(f"  {icon} {platform}: {pid or 'not found'} ({confidence}) — {match.get('reasoning', '')[:60]}")

        matches[platform] = {
            "platform_id": pid,
            "confidence": confidence,
            "reasoning": match.get("reasoning", ""),
            "status": status,
            "candidates": [
                {
                    "platform_id": r.platform_id,
                    "name": r.name,
                    "url": r.url,
                    "followers": r.followers,
                    "bio": r.bio,
                }
                for r in search_results.get(platform, [])
            ],
        }

    # Step 4: create artist record with high-confidence matches
    artist_data = {"name": artist_name, "active": True}

    platform_to_column = {
        "spotify": "spotify_id",
        "ticketmaster": "ticketmaster_id",
        "bandsintown": "bandsintown_name",
        "instagram": "instagram_handle",
        "twitter": "twitter_handle",
        "skiddle": "skiddle_id",
        "concerts_tracker": "concerts_tracker_id",
        "ra": "ra_id",
        "dice": "dice_slug",
    }

    for platform, match in matches.items():
        col = platform_to_column.get(platform)
        if col and match["status"] == "resolved":
            value = match["platform_id"]
            if platform in ("instagram", "twitter"):
                for r in search_results.get(platform, []):
                    if r.platform_id == value and r.extra and r.extra.get("username"):
                        value = r.extra["username"]
                        break
            artist_data[col] = value

    # Enrich with Spotify metadata if resolved
    if matches.get("spotify", {}).get("status") == "resolved":
        spotify_id = matches["spotify"]["platform_id"]
        try:
            source = SpotifySource()
            details = await source.get_artist_details(spotify_id)
            artist_data["genres"] = details.get("genres", [])
            artist_data["image_url"] = details.get("image_url")
            if details.get("website_url"):
                artist_data["website_url"] = details["website_url"]
            print(f"\n🎵 Spotify enrichment: genres={details.get('genres', [])}, image={'yes' if details.get('image_url') else 'no'}")
        except Exception as e:
            print(f"\n⚠️  Spotify enrichment failed: {e}")

    # Fallback images: RA, then Instagram profile pic
    if not artist_data.get("image_url"):
        ra_results = search_results.get("ra", [])
        if ra_results and ra_results[0].image_url:
            artist_data["image_url"] = ra_results[0].image_url
            print(f"\n🖼️  Image from RA: {ra_results[0].image_url[:60]}...")

    if not artist_data.get("image_url"):
        ig_results = search_results.get("instagram", [])
        if ig_results and ig_results[0].image_url:
            artist_data["image_url"] = ig_results[0].image_url
            print(f"\n🖼️  Image from Instagram: {ig_results[0].image_url[:60]}...")

    # Insert artist
    result = supabase.table("artists").insert(artist_data).execute()
    artist_id = result.data[0]["id"]
    print(f"\n💾 Artist saved: {artist_id}")

    # Step 5: store resolution audit trail
    resolution_rows = [
        {
            "artist_id": artist_id,
            "platform": platform,
            "status": match["status"],
            "confidence": match["confidence"],
            "candidates": match["candidates"],
            "resolved_by": "ai",
            "resolved_at": "now()",
        }
        for platform, match in matches.items()
    ]
    supabase.table("artist_resolutions").insert(resolution_rows).execute()

    if needs_review:
        print(f"\n⚠️  Needs manual review: {', '.join(needs_review)}")
    else:
        print(f"\n🎉 All platforms resolved!")

    return {
        "artist_id": artist_id,
        "matches": matches,
        "needs_review": needs_review,
    }


# Mapping from MusicBrainz relation keys to artist table columns
_MB_TO_COLUMN = {
    "spotify_id": "spotify_id",
    "instagram_handle": "instagram_handle",
    "twitter_handle": "twitter_handle",
    "ra_slug": "ra_id",
}

# Platforms that MusicBrainz can NOT provide — always search these
_SEARCH_ONLY_PLATFORMS = {
    "ticketmaster",
    "bandsintown",
    "skiddle",
    "concerts_tracker",
    "dice",
}

# MB key → platform name in PLATFORMS dict (for fallback search)
_MB_KEY_TO_PLATFORM = {
    "spotify_id": "spotify",
    "ra_slug": "ra",
    "instagram_handle": "instagram",
    "twitter_handle": "twitter",
}


async def resolve_artist_from_mbid(mbid: str) -> dict:
    """Resolve an artist using their MusicBrainz ID as anchor.

    1. Fetch MusicBrainz details + URL relations
    2. Seed platform IDs directly from relations (Spotify, socials, RA)
    3. Search remaining platforms (event sources) with better context
    4. AI-match only the remaining platforms
    """
    print(f"\n🎵 Resolving artist from MusicBrainz ID: {mbid}")
    mb = await mb_get_artist_details(mbid)
    artist_name = mb["name"]
    print(f"  Name: {artist_name}")
    print(f"  Disambiguation: {mb['disambiguation'] or '(none)'}")
    print(f"  Country: {mb['country'] or '?'}")
    print(f"  Genres: {', '.join(mb['genres']) or '(none)'}")

    # --- Seed platform IDs from MusicBrainz relations ---
    seeded = {}
    for mb_key, col in _MB_TO_COLUMN.items():
        if mb_key in mb["platform_ids"]:
            seeded[col] = mb["platform_ids"][mb_key]
            print(f"  🔗 {col}: {seeded[col]} (from MusicBrainz)")

    # --- Search remaining platforms ---
    # Always search ticketing platforms + any MB-seedable platforms that MB didn't provide
    unseeded_platforms = {
        platform for mb_key, platform in _MB_KEY_TO_PLATFORM.items()
        if mb_key not in mb["platform_ids"]
    }
    if unseeded_platforms:
        print(f"  🔍 MB missing: {', '.join(unseeded_platforms)} — will search")

    remaining_platforms = {
        p: cls for p, cls in PLATFORMS.items()
        if p in _SEARCH_ONLY_PLATFORMS or p in unseeded_platforms
    }

    search_results = {}

    async def _search(platform: str, source_cls):
        try:
            source = source_cls()
            found = await source.search_artist(artist_name)
            search_results[platform] = found
            if found:
                top = found[0]
                print(f"  ✅ {platform}: {len(found)} results — top: \"{top.name}\" (ID: {top.platform_id})")
            else:
                print(f"  ⚪ {platform}: 0 results")
        except Exception as e:
            logger.error("Platform %s search failed", platform, exc_info=True)
            err = str(e).split("\n")[0][:80]
            print(f"  ❌ {platform}: {err}")
            search_results[platform] = []

    print(f"\n🔍 Searching remaining platforms for \"{artist_name}\"...")
    await asyncio.gather(
        *[_search(p, cls) for p, cls in remaining_platforms.items()]
    )

    # --- AI matching for remaining platforms ---
    matches = {}

    if search_results:
        print(f"\n🤖 Asking AI to match remaining platforms...")
        context_parts = []
        if mb["disambiguation"]:
            context_parts.append(f"Disambiguation: {mb['disambiguation']}")
        if mb["country"]:
            context_parts.append(f"Country: {mb['country']}")
        if mb["genres"]:
            context_parts.append(f"Genres: {', '.join(mb['genres'])}")
        disamb_text = (
            "Additional context from MusicBrainz: " + "; ".join(context_parts)
            if context_parts else ""
        )

        prompt = RESOLUTION_PROMPT.format(
            artist_name=artist_name,
            disambiguator=disamb_text,
            platform_results=_format_results(search_results),
        )

        agent = AIAgent(provider="openai", agent_name="artist_resolver")
        response = agent.smart_completion(
            user_prompt=prompt,
            json_output=True,
        )
        metrics.record(
            "resolution", "smart",
            input_chars=len(prompt),
            output_chars=metrics.response_chars(response),
        )

        parsed = json.loads(response) if isinstance(response, str) else response
        ai_matches = parsed["matches"]
        print(f"  🧠 AI raw matches: {json.dumps(ai_matches, indent=2)[:600]}")

        for platform, match in ai_matches.items():
            pid = match.get("platform_id")
            confidence = match.get("confidence", "low")

            if pid is None:
                status = "not_found"
            elif confidence in ("high", "medium"):
                status = "resolved"
            else:
                status = "ambiguous"

            matches[platform] = {
                "platform_id": pid,
                "confidence": confidence,
                "reasoning": match.get("reasoning", ""),
                "status": status,
                "candidates": [
                    {
                        "platform_id": r.platform_id,
                        "name": r.name,
                        "url": r.url,
                        "followers": r.followers,
                        "bio": r.bio,
                    }
                    for r in search_results.get(platform, [])
                ],
            }

    # --- Build artist record ---
    artist_data = {
        "name": artist_name,
        "musicbrainz_id": mbid,
        "active": True,
    }

    # Apply seeded platform IDs
    for col, value in seeded.items():
        artist_data[col] = value

    # Apply AI-resolved platform IDs
    platform_to_column = {
        "spotify": "spotify_id",
        "ticketmaster": "ticketmaster_id",
        "bandsintown": "bandsintown_name",
        "instagram": "instagram_handle",
        "twitter": "twitter_handle",
        "skiddle": "skiddle_id",
        "concerts_tracker": "concerts_tracker_id",
        "ra": "ra_id",
        "dice": "dice_slug",
    }

    needs_review = []
    print(f"\n📋 Resolution results:")
    for platform, match in matches.items():
        col = platform_to_column.get(platform)
        if col and match["status"] == "resolved":
            value = match["platform_id"]
            # For social platforms, prefer username over numeric ID
            if platform in ("instagram", "twitter"):
                for r in search_results.get(platform, []):
                    if r.platform_id == value and r.extra and r.extra.get("username"):
                        value = r.extra["username"]
                        break
            artist_data[col] = value
            print(f"  ✅ {platform}: {match['platform_id']}")
        elif match["status"] == "ambiguous":
            needs_review.append(platform)
            print(f"  🟡 {platform}: ambiguous — {match.get('reasoning', '')[:60]}")
        else:
            print(f"  ⚫ {platform}: not found")

    # Enrich: genres from MusicBrainz, image + metadata from Spotify
    if mb["genres"]:
        artist_data["genres"] = mb["genres"]

    # Try Spotify for image, genres, and website
    spotify_id = seeded.get("spotify_id") or artist_data.get("spotify_id")
    if spotify_id:
        try:
            source = SpotifySource()
            details = await source.get_artist_details(spotify_id)
            artist_data["image_url"] = details.get("image_url")
            if details.get("website_url"):
                artist_data["website_url"] = details["website_url"]
            if details.get("genres"):
                existing = set(artist_data.get("genres", []))
                artist_data["genres"] = list(
                    existing | set(details["genres"])
                )
            print(f"\n🎵 Spotify enrichment: image={'yes' if details.get('image_url') else 'no'}")
        except Exception as e:
            print(f"\n⚠️  Spotify enrichment failed: {e}")

    # Fallback images: RA search result, then Instagram profile pic
    if not artist_data.get("image_url"):
        # Check RA search results for an image
        ra_results = search_results.get("ra", [])
        if ra_results and ra_results[0].image_url:
            artist_data["image_url"] = ra_results[0].image_url
            print(f"\n🖼️  Image from RA: {ra_results[0].image_url[:60]}...")

    if not artist_data.get("image_url"):
        # Check Instagram profile pic
        ig_handle = seeded.get("instagram_handle") or artist_data.get("instagram_handle")
        if ig_handle:
            try:
                ig_source = InstagramSource()
                ig_results = await ig_source.search_artist(ig_handle)
                for r in ig_results:
                    if r.image_url and ig_handle.lower() in (r.platform_id or "").lower():
                        artist_data["image_url"] = r.image_url
                        print(f"\n🖼️  Image from Instagram: {r.image_url[:60]}...")
                        break
            except Exception as e:
                print(f"\n⚠️  Instagram image fetch failed: {e}")

    # Insert artist
    result = supabase.table("artists").insert(artist_data).execute()
    artist_id = result.data[0]["id"]
    print(f"\n💾 Artist saved: {artist_id}")

    # Store resolution audit trail
    resolution_rows = []

    # Seeded platforms
    for mb_key, col in _MB_TO_COLUMN.items():
        if mb_key in mb["platform_ids"]:
            platform_name = col.replace("_id", "").replace("_handle", "").replace("_slug", "")
            resolution_rows.append({
                "artist_id": artist_id,
                "platform": platform_name,
                "status": "resolved",
                "confidence": "high",
                "candidates": [],
                "resolved_by": "musicbrainz",
                "resolved_at": "now()",
            })

    # AI-matched platforms
    for platform, match in matches.items():
        resolution_rows.append({
            "artist_id": artist_id,
            "platform": platform,
            "status": match["status"],
            "confidence": match["confidence"],
            "candidates": match.get("candidates", []),
            "resolved_by": "ai",
            "resolved_at": "now()",
        })

    if resolution_rows:
        supabase.table("artist_resolutions").insert(resolution_rows).execute()

    if needs_review:
        print(f"\n⚠️  Needs manual review: {', '.join(needs_review)}")
    else:
        print(f"\n🎉 All platforms resolved!")

    return {
        "artist_id": artist_id,
        "matches": {**{col: {"status": "resolved", "source": "musicbrainz"} for col in seeded}, **matches},
        "needs_review": needs_review,
    }


async def update_resolution(
    artist_id: str,
    platform: str,
    platform_id: str,
) -> None:
    """Manually resolve an ambiguous platform match."""
    platform_to_column = {
        "spotify": "spotify_id",
        "ticketmaster": "ticketmaster_id",
        "bandsintown": "bandsintown_name",
        "instagram": "instagram_handle",
        "twitter": "twitter_handle",
        "skiddle": "skiddle_id",
        "concerts_tracker": "concerts_tracker_id",
        "ra": "ra_id",
        "dice": "dice_slug",
    }

    col = platform_to_column.get(platform)
    if not col:
        raise ValueError(f"Unknown platform: {platform}")

    supabase.table("artists").update({col: platform_id}).eq("id", artist_id).execute()
    supabase.table("artist_resolutions").update(
        {
            "status": "resolved",
            "confidence": "high",
            "resolved_by": "user",
        }
    ).eq("artist_id", artist_id).eq("platform", platform).execute()
    print(f"  ✅ {platform} resolved to {platform_id}")
