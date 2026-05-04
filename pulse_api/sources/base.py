from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    platform: str
    platform_id: str
    name: str
    url: str | None = None
    image_url: str | None = None
    followers: int | None = None
    genres: list[str] | None = None
    bio: str | None = None
    extra: dict | None = None


@dataclass
class EventResult:
    source: str
    source_id: str
    title: str
    date: str
    venue: str | None = None
    city: str | None = None
    ticket_url: str | None = None
    raw_data: dict | None = None
    image_url: str | None = None


@dataclass
class SocialPostResult:
    platform: str
    post_id: str
    caption: str | None = None
    media_url: str | None = None
    posted_at: str | None = None
    raw_data: dict | None = None


class EventSource(ABC):
    @abstractmethod
    async def search_artist(self, name: str) -> list[SearchResult]:
        ...

    @abstractmethod
    async def get_events(self, artist_id: str, city: str | None = None) -> list[EventResult]:
        ...


class SocialSource(ABC):
    @abstractmethod
    async def search_artist(self, name: str) -> list[SearchResult]:
        ...

    @abstractmethod
    async def get_posts(
        self,
        handle: str,
        limit: int = 20,
        since_post_id: str | None = None,
        since_posted_at: str | None = None,
    ) -> list[SocialPostResult]:
        ...


class MetadataSource(ABC):
    @abstractmethod
    async def search_artist(self, name: str) -> list[SearchResult]:
        ...
