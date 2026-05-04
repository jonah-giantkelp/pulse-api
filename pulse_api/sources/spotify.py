import logging

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials

from pulse_api.config import settings
from pulse_api.sources.base import MetadataSource, SearchResult

logger = logging.getLogger(__name__)


class SpotifySource(MetadataSource):
    def __init__(self):
        if not settings.spotify_client_id or not settings.spotify_client_secret:
            logger.warning("Spotify: credentials missing")
        auth = SpotifyClientCredentials(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
        )
        self.client = spotipy.Spotify(auth_manager=auth)

    async def search_artist(self, name: str) -> list[SearchResult]:
        results = self.client.search(q=name, type="artist", limit=10)
        artists = results.get("artists", {}).get("items", [])
        logger.info("Spotify: search %r → %d artist(s)", name, len(artists))

        return [
            SearchResult(
                platform="spotify",
                platform_id=a["id"],
                name=a["name"],
                url=a["external_urls"].get("spotify"),
                image_url=a["images"][0]["url"] if a.get("images") else None,
                genres=a.get("genres", []),
                extra={"type": a.get("type")},
            )
            for a in artists
        ]

    async def get_artist_details(self, spotify_id: str) -> dict:
        artist = self.client.artist(spotify_id)
        # Try to get the artist's website from their external URLs
        website = artist.get("external_urls", {}).get("website")
        return {
            "name": artist["name"],
            "genres": artist.get("genres", []),
            "image_url": artist["images"][0]["url"] if artist.get("images") else None,
            "url": artist["external_urls"].get("spotify"),
            "website_url": website,
        }
