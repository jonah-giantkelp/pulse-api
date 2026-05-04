"""Per-(artist, platform) sync cursors stored in `social_sync_cursors`.

Tracks the last post fetched and the last time AI distilled posts for an
artist+platform pair, so each sync only does incremental work.
"""

from datetime import datetime, timezone

from pulse_api.db import supabase


def get_cursor(artist_id: str, platform: str) -> dict | None:
    """Read the sync cursor for an (artist, platform) pair."""
    result = (
        supabase.table("social_sync_cursors")
        .select("*")
        .eq("artist_id", artist_id)
        .eq("platform", platform)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def update_cursor(
    artist_id: str,
    platform: str,
    posts: list[dict],
    distilled: bool = False,
):
    """Upsert the cursor after fetching/distilling posts."""
    if not posts:
        return

    # Find the newest post in the batch
    newest = max(
        posts,
        key=lambda p: p.get("posted_at") or "",
    )

    row = {
        "artist_id": artist_id,
        "platform": platform,
        "last_post_id": newest.get("post_id"),
        "last_posted_at": newest.get("posted_at"),
        "last_synced_at": datetime.now(timezone.utc).isoformat(),
    }
    if distilled:
        row["last_distilled_at"] = datetime.now(timezone.utc).isoformat()

    supabase.table("social_sync_cursors").upsert(
        row, on_conflict="artist_id,platform"
    ).execute()


def get_posts_since_distill(artist_id: str, platform: str) -> str | None:
    """Return the last_distilled_at timestamp for cursor-based AI filtering."""
    cursor = get_cursor(artist_id, platform)
    if cursor:
        return cursor.get("last_distilled_at")
    return None


def mark_distilled(artist_id: str, platforms: list[str]):
    """Update last_distilled_at for the given platforms after AI analysis."""
    now = datetime.now(timezone.utc).isoformat()
    for platform in platforms:
        try:
            supabase.table("social_sync_cursors").upsert(
                {
                    "artist_id": artist_id,
                    "platform": platform,
                    "last_distilled_at": now,
                    "last_synced_at": now,
                },
                on_conflict="artist_id,platform",
            ).execute()
        except Exception:
            pass
