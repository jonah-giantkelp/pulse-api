import logging
import re
from datetime import datetime, timezone

import httpx

from pulse_api.config import settings
from pulse_api.sources.base import SocialSource, SearchResult, SocialPostResult

logger = logging.getLogger(__name__)

RAPIDAPI_HOST = "instagram-scraper-api2.p.rapidapi.com"


class InstagramSource(SocialSource):
    def __init__(self):
        if not settings.rapidapi_instagram_key:
            logger.warning("Instagram: RapidAPI key missing")
        self.headers = {
            "X-RapidAPI-Key": settings.rapidapi_instagram_key,
            "X-RapidAPI-Host": RAPIDAPI_HOST,
        }

    async def search_artist(self, name: str) -> list[SearchResult]:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"https://{RAPIDAPI_HOST}/v1/search_users",
                    headers=self.headers,
                    params={"search_query": name},
                )
                resp.raise_for_status()
                data = resp.json()

            users = data.get("data", {}).get("items", [])
            logger.info("Instagram: search %r → %d user(s)", name, len(users))
            if users:
                return [
                    SearchResult(
                        platform="instagram",
                        platform_id=str(u.get("id", u.get("user", {}).get("pk", ""))),
                        name=u.get("full_name", u.get("user", {}).get("full_name", "")),
                        url=f"https://instagram.com/{u.get('username', u.get('user', {}).get('username', ''))}",
                        image_url=u.get("profile_pic_url", u.get("user", {}).get("profile_pic_url")),
                        followers=u.get("follower_count", u.get("user", {}).get("follower_count")),
                        bio=u.get("biography", u.get("user", {}).get("biography")),
                        extra={
                            "username": u.get("username", u.get("user", {}).get("username", "")),
                            "is_verified": u.get("is_verified", u.get("user", {}).get("is_verified")),
                            "is_private": u.get("is_private", u.get("user", {}).get("is_private")),
                        },
                    )
                    for u in users
                ]
        except Exception as e:
            logger.warning("Instagram: API search failed: %s", e)

        # --- Fallback: web search for Instagram profile ---
        return await self._web_search_fallback(name)

    async def _web_search_fallback(self, name: str) -> list[SearchResult]:
        """Search the web for an Instagram profile URL when RapidAPI fails."""
        from pulse_api.sources.scraping import search_url

        logger.info("Instagram: falling back to web search for %r", name)
        url = await search_url(
            f"{name} musician DJ",
            "instagram.com",
            path_pattern="/",
            source_name="Instagram",
        )
        if not url:
            return []

        # Extract username from URL like https://instagram.com/hunee_s/
        match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", url)
        if not match:
            return []

        username = match.group(1)
        logger.info("Instagram: web search found @%s", username)
        return [
            SearchResult(
                platform="instagram",
                platform_id=username,
                name=name,
                url=f"https://instagram.com/{username}",
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
        posts = []
        pagination_token = None
        hit_cursor = False

        async with httpx.AsyncClient() as client:
            while len(posts) < limit and not hit_cursor:
                params = {"username_or_id_or_url": handle}
                if pagination_token:
                    params["pagination_token"] = pagination_token

                resp = await client.get(
                    f"https://{RAPIDAPI_HOST}/v1/posts",
                    headers=self.headers,
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()

                items = data.get("data", {}).get("items", [])
                if not items:
                    break

                for p in items:
                    if len(posts) >= limit:
                        break

                    post_id = p.get("id", p.get("code", ""))
                    posted_at_iso = None
                    if p.get("taken_at"):
                        posted_at_iso = datetime.fromtimestamp(
                            int(p["taken_at"]), tz=timezone.utc
                        ).isoformat()

                    # Stop if we've reached a post we've already seen
                    if since_post_id and post_id == since_post_id:
                        hit_cursor = True
                        break
                    if since_posted_at and posted_at_iso and posted_at_iso <= since_posted_at:
                        hit_cursor = True
                        break

                    posts.append(
                        SocialPostResult(
                            platform="instagram",
                            post_id=post_id,
                            caption=(p.get("caption") or {}).get("text"),
                            media_url=(
                                p.get("image_versions2", {})
                                .get("candidates", [{}])[0]
                                .get("url")
                                if p.get("image_versions2")
                                else None
                            ),
                            posted_at=posted_at_iso,
                            raw_data=p,
                        )
                    )

                pagination_token = data.get("data", {}).get("pagination_token")
                if not pagination_token:
                    break

        return posts
