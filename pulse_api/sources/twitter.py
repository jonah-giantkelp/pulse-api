import logging
import re

import httpx

from pulse_api.config import settings
from pulse_api.sources.base import SocialSource, SearchResult, SocialPostResult

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "twitter154.p.rapidapi.com"


class TwitterSource(SocialSource):
    def __init__(self):
        if not settings.rapidapi_twitter_key:
            logger.warning("Twitter: RapidAPI key missing")
        self.headers = {
            "X-RapidAPI-Key": settings.rapidapi_twitter_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        }

    async def search_artist(self, name: str) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://{RAPIDAPI_HOST}/search/search",
                    headers=self.headers,
                    params={"query": name, "section": "people", "limit": "10"},
                )
                resp.raise_for_status()
                data = resp.json()

            users = data.get("results", [])
            logger.info("Twitter: search %r → %d user(s)", name, len(users))
            if users:
                return [
                    SearchResult(
                        platform="twitter",
                        platform_id=u.get("user_id", ""),
                        name=u.get("name", ""),
                        url=f"https://x.com/{u.get('username', '')}",
                        image_url=u.get("profile_image"),
                        followers=u.get("follower_count"),
                        bio=u.get("description"),
                        extra={"username": u.get("username", "")},
                    )
                    for u in users
                ]
        except Exception as e:
            logger.warning("Twitter: API search failed: %s", e)

        # --- Fallback: web search for Twitter/X profile ---
        return await self._web_search_fallback(name)

    async def _web_search_fallback(self, name: str) -> list[SearchResult]:
        """Search the web for a Twitter/X profile URL when RapidAPI fails."""
        from pulse_api.sources.scraping import search_url

        logger.info("Twitter: falling back to web search for %r", name)
        url = await search_url(
            f"{name} musician DJ",
            "x.com",
            path_pattern="/",
            source_name="Twitter",
        )
        if not url:
            return []

        # Extract username from URL like https://x.com/hunee_s
        match = re.search(r"x\.com/([A-Za-z0-9_]+)", url)
        if not match:
            return []

        username = match.group(1)
        # Filter out non-profile pages
        if username.lower() in ("search", "explore", "home", "settings", "i"):
            return []

        logger.info("Twitter: web search found @%s", username)
        return [
            SearchResult(
                platform="twitter",
                platform_id=username,
                name=name,
                url=f"https://x.com/{username}",
                extra={"username": username, "source": "web_search"},
            ),
        ]

    async def get_posts(
        self,
        handle: str,
        limit: int = 20,
        since_post_id: str | None = None,
        since_posted_at: str | None = None,
    ) -> list[SocialPostResult]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://{RAPIDAPI_HOST}/user/tweets",
                headers=self.headers,
                params={"username": handle, "limit": str(min(limit, 100))},
            )
            resp.raise_for_status()
            data = resp.json()

        tweets = data.get("results", [])
        posts = []
        for t in tweets:
            post_id = t.get("tweet_id", "")
            posted_at = t.get("creation_date")

            # Stop if we've reached a post we've already seen
            if since_post_id and post_id == since_post_id:
                break
            if since_posted_at and posted_at and posted_at <= since_posted_at:
                break

            posts.append(
                SocialPostResult(
                    platform="twitter",
                    post_id=post_id,
                    caption=t.get("text"),
                    media_url=(
                        t.get("media_url", [None])[0]
                        if isinstance(t.get("media_url"), list)
                        else t.get("media_url")
                    ),
                    posted_at=posted_at,
                    raw_data=t,
                )
            )

        return posts
