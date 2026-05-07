"""Top-level sync orchestration.

`run_daily_sync` walks all tracked artists and for each one:
  1. fetches events from every linked ticketing source (in parallel)
  2. fetches social posts (with cursors)
  3. runs the upsert pipeline (clean → dedup → write)
  4. distills events out of social posts via AI
  5. stamps `last_synced_at`

The actual primitives (fingerprinting, dedup, AI calls, DB writes) live
in sibling modules; this file is the choreography.
"""

import asyncio
import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone

from dateutil import parser as dateparser

from pulse_api.ai import metrics as ai_metrics
from pulse_api.ai.distiller import distill_posts, enrich_dateless_gigs, web_enrich_event
from pulse_api.db import supabase
from pulse_api.sources.bandsintown import BandsintownSource
from pulse_api.sources.concerts import ConcertsTrackerSource
from pulse_api.sources.dice import DiceSource
from pulse_api.sources.instagram import InstagramSource
from pulse_api.sources.ra import RASource
from pulse_api.sources.skiddle import SkiddleSource
from pulse_api.sources.ticketmaster import TicketmasterSource
from pulse_api.sources.twitter import TwitterSource
from pulse_api.sources.website import scrape_artist_website
from pulse_api.sync.adapters import event_to_row, post_to_row
from pulse_api.sync.cursors import (
    get_cursor,
    get_posts_since_distill,
    mark_distilled,
    update_cursor,
)
from pulse_api.sync.dedup import (
    batch_dedup,
    deduplicate_event,
    filter_known_external_ids,
)
from pulse_api.sync.enrichment import (
    ai_clean_titles_and_cities,
    ai_fill_geo,
    extract_lineup,
    parse_date_hint,
)
from pulse_api.sync.fingerprint import make_fingerprint, make_fingerprint_loose
from pulse_api.sync.persistence import (
    link_artist_to_event,
    store_event_images,
    store_external_id,
    upsert_posts,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Per-artist sync
# ─────────────────────────────────────────────


def _log_sync(
    artist_id: str,
    source: str,
    status: str,
    error: str | None = None,
    events_found: int = 0,
    posts_found: int = 0,
):
    try:
        supabase.table("sync_log").insert({
            "artist_id": artist_id,
            "source": source,
            "status": status,
            "error_message": error,
            "events_found": events_found,
            "posts_found": posts_found,
        }).execute()
    except Exception:
        pass  # Don't let logging failures break the sync


async def sync_events_for_artist(artist: dict) -> list[dict]:
    results = []
    city = artist.get("city")
    name = artist["name"]

    async def _sync(source_name, source_cls, id_field, is_name=False):
        id_val = artist.get(id_field)
        if not id_val:
            logger.info("    ⚪ %s: skipped (no ID)", source_name)
            return
        try:
            source = source_cls()
            events = await source.get_events(id_val, city)
            for e in events:
                row = event_to_row(artist["id"], e, city)
                if row is not None:
                    results.append(row)
            if events:
                logger.info("    🎫 %s: %d event(s)", source_name, len(events))
                for e in events[:3]:
                    logger.info("       → %s | %s | %s", e.title, e.date, e.venue or "?")
                if len(events) > 3:
                    logger.info("       ... and %d more", len(events) - 3)
            else:
                logger.info("    ⚪ %s: 0 events", source_name)
            _log_sync(artist["id"], source_name, "success", events_found=len(events))
        except Exception as e:
            err = str(e).split("\n")[0][:80]
            logger.error("    ❌ %s: %s", source_name, err)
            _log_sync(artist["id"], source_name, "error", error=str(e))

    async def _sync_website():
        if not artist.get("website_url"):
            logger.info("    ⚪ website: skipped (no URL)")
            return
        try:
            events = await scrape_artist_website(artist["website_url"], name, city)
            for e in events:
                row = event_to_row(artist["id"], e, city)
                if row is not None:
                    results.append(row)
            if events:
                logger.info("    🌐 website: %d event(s)", len(events))
                for e in events[:3]:
                    logger.info("       → %s | %s | %s", e.title, e.date, e.venue or "?")
            else:
                logger.info("    ⚪ website: 0 events")
            _log_sync(artist["id"], "website", "success", events_found=len(events))
        except Exception as e:
            err = str(e).split("\n")[0][:80]
            logger.error("    ❌ website: %s", err)
            _log_sync(artist["id"], "website", "error", error=str(e))

    await asyncio.gather(
        _sync("ticketmaster", TicketmasterSource, "ticketmaster_id"),
        _sync("bandsintown", BandsintownSource, "bandsintown_name"),
        _sync("skiddle", SkiddleSource, "skiddle_id"),
        _sync("concerts_tracker", ConcertsTrackerSource, "concerts_tracker_id"),
        _sync("ra", RASource, "ra_id"),
        _sync("dice", DiceSource, "dice_slug"),
        _sync_website(),
    )

    # Early exit: strip events whose (source, source_id) already exists in
    # event_external_ids — no point running AI geo/title/merge on known events.
    before = len(results)
    results = filter_known_external_ids(results)
    skipped = before - len(results)
    if skipped:
        logger.info("    ♻️  %d event(s) already known — skipped", skipped)
    if not results:
        logger.info("    📊 All %d event(s) already known — nothing new", before)
        return results

    # Fill in missing city/country via AI for events that lack them
    missing_geo = [e for e in results if not e.get("city")]
    if missing_geo:
        logger.info("    🌍 %d event(s) missing city — asking AI...", len(missing_geo))
        await ai_fill_geo(missing_geo, all_events=results)

    return results


async def sync_social_for_artist(artist: dict) -> list[dict]:
    posts = []
    name = artist["name"]

    async def _sync(platform_name, source_cls, handle_field):
        handle = artist.get(handle_field)
        if not handle:
            logger.info("    ⚪ %s: skipped (no handle)", platform_name)
            return
        try:
            # Read cursor for this artist+platform
            cursor = get_cursor(artist["id"], platform_name)
            since_post_id = cursor.get("last_post_id") if cursor else None
            since_posted_at = cursor.get("last_posted_at") if cursor else None

            source = source_cls()
            new_posts = await source.get_posts(
                handle,
                since_post_id=since_post_id,
                since_posted_at=since_posted_at,
            )
            post_dicts = [post_to_row(artist["id"], p) for p in new_posts]
            posts.extend(post_dicts)

            # Update cursor with newest post from this fetch
            update_cursor(artist["id"], platform_name, post_dicts)

            if new_posts:
                logger.info("    📱 %s: %d new post(s)", platform_name, len(new_posts))
                for p in new_posts[:2]:
                    caption_preview = (p.caption or "(no caption)")[:60]
                    logger.info("       → %s", caption_preview)
            else:
                logger.info("    ⚪ %s: 0 new posts", platform_name)
            _log_sync(artist["id"], platform_name, "success", posts_found=len(new_posts))
        except Exception as e:
            err = str(e).split("\n")[0][:80]
            logger.error("    ❌ %s: %s", platform_name, err)
            _log_sync(artist["id"], platform_name, "error", error=str(e))

    await asyncio.gather(
        _sync("instagram", InstagramSource, "instagram_handle"),
        _sync("twitter", TwitterSource, "twitter_handle"),
    )
    return posts


# ─────────────────────────────────────────────
# Event upsert pipeline
# ─────────────────────────────────────────────


def upsert_events(events: list[dict]):
    """Top-level event-write pipeline.

    Flow: AI clean → fingerprint → batch dedup → DB dedup → upsert →
    backfill event_artists, external IDs, images.
    """
    if not events:
        return

    # Clean up titles and cities before dedup
    ai_clean_titles_and_cities(events)

    # Add fingerprints (strict = venue+city+date, loose = city+date)
    for e in events:
        if not e.get("fingerprint"):
            e["fingerprint"] = make_fingerprint(
                e["artist_id"], e.get("venue"), e.get("date"), e.get("city"),
            )
        if not e.get("fingerprint_loose"):
            e["fingerprint_loose"] = make_fingerprint_loose(
                e.get("date"), e.get("city"),
            )

    # In-memory dedup within the batch (cross-source, same artist)
    events, batch_extra_ids = batch_dedup(events)

    # Events that didn't go through a merge group still need a lineup stamped
    # from their own raw_data so the read path has one place to look.
    for e in events:
        if e.get("lineup"):
            continue
        names = extract_lineup(e.get("source", ""), e.get("raw_data"))
        if names:
            e["lineup"] = names

    # DB-level dedup against existing events
    deduped = []
    for e in events:
        result = deduplicate_event(e)
        if result is not None:
            deduped.append(result)

    if not deduped:
        logger.info("    📊 All events were duplicates")
        return

    # Remove exact (source, source_id) duplicates within the batch
    seen_keys = set()
    unique_deduped = []
    for e in deduped:
        key = (e.get("source"), e.get("source_id"))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        unique_deduped.append(e)
    deduped = unique_deduped

    # Check which events already exist to log new vs updated
    source_ids = [e["source_id"] for e in deduped if e.get("source_id")]
    existing = set()
    if source_ids:
        try:
            result = (
                supabase.table("events")
                .select("source_id")
                .in_("source_id", source_ids)
                .execute()
            )
            existing = {row["source_id"] for row in result.data}
        except Exception:
            pass

    new_count = 0
    updated_count = 0
    for e in deduped:
        sid = e.get("source_id", "")
        if sid in existing:
            updated_count += 1
            logger.info("    ♻️  Updated: %s (%s)", e.get("title", "?")[:50], e.get("source"))
        else:
            new_count += 1
            logger.info("    ✅ New: %s (%s)", e.get("title", "?")[:50], e.get("source"))

    if new_count or updated_count:
        logger.info("    📊 %d new, %d updated", new_count, updated_count)

    # Separate image URLs before upserting (not a DB column)
    image_map = {}
    for e in deduped:
        img = e.pop("_image_url", None)
        if img:
            image_map[(e["source"], e["source_id"])] = img

    supabase.table("events").upsert(
        deduped, on_conflict="source,source_id"
    ).execute()

    # Populate event_artists, external IDs, and images for every upserted event
    for e in deduped:
        if not e.get("artist_id"):
            continue
        # Look up the event ID by source+source_id
        try:
            db_row = (
                supabase.table("events")
                .select("id")
                .eq("source", e["source"])
                .eq("source_id", e["source_id"])
                .limit(1)
                .execute()
            )
            if not db_row.data:
                continue
            event_id = db_row.data[0]["id"]

            link_artist_to_event(
                event_id,
                e["artist_id"],
                billing=e.get("artist_billing"),
                source=e.get("source"),
            )

            # Store the primary external ID
            store_external_id(
                event_id,
                e["source"],
                e["source_id"],
                e.get("ticket_url"),
                raw_data=e.get("raw_data"),
            )

            # Store external IDs from batch-deduped duplicates
            # Check all possible keys used by batch_dedup passes
            _fp_keys = [
                e.get("fingerprint_loose"),
                e.get("fingerprint"),
                e.get("source_id"),
            ]
            for fp in _fp_keys:
                if fp and fp in batch_extra_ids:
                    for ext in batch_extra_ids[fp]:
                        if ext.get("external_id"):
                            store_external_id(
                                event_id,
                                ext["source"],
                                ext["external_id"],
                                ext.get("ticket_url"),
                                raw_data=ext.get("raw_data"),
                            )
                    break  # only use the first matching key

            img_url = image_map.get((e["source"], e["source_id"]))
            if img_url:
                store_event_images(event_id, [{
                    "image_url": img_url,
                    "image_type": "poster",
                }])
        except Exception:
            pass


# ─────────────────────────────────────────────
# Daily sync entry point
# ─────────────────────────────────────────────


async def run_daily_sync(include_social: bool = True):
    logger.info("")
    logger.info("=" * 50)
    logger.info("🔄 DAILY SYNC%s", "" if include_social else " (events only)")
    logger.info("=" * 50)

    # Reset AI metrics + run-level counters
    ai_metrics.reset()
    run_stats = {
        "artists_synced": 0,
        "artists_skipped": 0,
        "events_found": 0,
        "posts_found": 0,
        "ai_gig_mentions": 0,
    }

    tracked = (
        supabase.table("user_artists")
        .select("artist_id")
        .execute()
    )
    artist_ids = list({row["artist_id"] for row in tracked.data})

    if not artist_ids:
        logger.info("😴 No tracked artists to sync.")
        return

    result = (
        supabase.table("artists")
        .select("*")
        .in_("id", artist_ids)
        .eq("active", True)
        .execute()
    )
    artists = result.data

    if not artists:
        logger.info("😴 No active artists to sync.")
        return

    logger.info("🎤 Syncing %d artist(s)...", len(artists))

    # Dev guard: skip artists synced within the last N hours (set via env).
    # Unset / 0 in prod means "always sync".
    skip_hours = int(os.getenv("PULSE_DEV_SKIP_HOURS", "0") or 0)
    skip_cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=skip_hours)
        if skip_hours > 0
        else None
    )

    for i, artist in enumerate(artists, 1):
        if skip_cutoff and artist.get("last_synced_at"):
            try:
                last = dateparser.isoparse(artist["last_synced_at"])
                if last >= skip_cutoff:
                    logger.info(
                        "⏭️  [%d/%d] %s — synced < %dh ago, skipping",
                        i, len(artists), artist["name"], skip_hours,
                    )
                    run_stats["artists_skipped"] += 1
                    continue
            except (ValueError, TypeError):
                pass
        linked = [
            k.replace("_id", "").replace("_name", "")
            .replace("_handle", "").replace("_slug", "")
            for k in [
                "spotify_id", "ticketmaster_id", "bandsintown_name",
                "instagram_handle", "twitter_handle", "skiddle_id",
                "concerts_tracker_id", "ra_id", "dice_slug",
            ]
            if artist.get(k)
        ]

        logger.info("─" * 50)
        logger.info("🎵 [%d/%d] %s", i, len(artists), artist["name"])
        logger.info("   Linked: %s", ", ".join(linked) if linked else "none")

        try:
            # Events
            logger.info("  📅 Events:")
            events = await sync_events_for_artist(artist)
        except Exception as e:
            err = str(e).split("\n")[0][:120]
            logger.error("  ❌ Event sync failed: %s", err)
            events = []

        if include_social:
            try:
                # Social (with cursor-based fetching)
                logger.info("  💬 Social:")
                posts = await sync_social_for_artist(artist)
            except Exception as e:
                err = str(e).split("\n")[0][:120]
                logger.error("  ❌ Social sync failed: %s", err)
                posts = []
        else:
            logger.info("  💬 Social: skipped (weekly cadence)")
            posts = []

        # Upsert — isolate failures so one artist can't nuke the pipeline
        try:
            upsert_events(events)
        except Exception as e:
            err = str(e).split("\n")[0][:120]
            logger.error("  ❌ Event upsert failed: %s", err)
        try:
            upsert_posts(posts)
        except Exception as e:
            err = str(e).split("\n")[0][:120]
            logger.error("  ❌ Post upsert failed: %s", err)
        logger.info("  💾 Saved: %d events, %d posts", len(events), len(posts))
        run_stats["artists_synced"] += 1
        run_stats["events_found"] += len(events)
        run_stats["posts_found"] += len(posts)

        # AI distill → extract events from social posts
        if posts:
            try:
                logger.info("  🤖 Extracting events from social posts...")

                # Use cursor-based filtering: only distill posts newer than
                # last_distilled_at, rather than scanning source_post_ids
                platforms_in_batch = list({p["platform"] for p in posts})
                cutoffs = {}
                for platform in platforms_in_batch:
                    cutoffs[platform] = get_posts_since_distill(
                        artist["id"], platform
                    )

                new_posts = [
                    p for p in posts
                    if p.get("post_id") and (
                        not cutoffs.get(p["platform"])
                        or not p.get("posted_at")
                        or p["posted_at"] > cutoffs[p["platform"]]
                    )
                ]

                skipped = len(posts) - len(new_posts)
                if skipped:
                    logger.info("    ♻️  %d post(s) already distilled, skipping", skipped)
                if not new_posts:
                    logger.info("  ⚪ No new posts to analyse")
                    # Still mark as distilled so we don't re-check
                    mark_distilled(artist["id"], platforms_in_batch)
                    continue

                captions_with_text = [p for p in new_posts if p.get("caption")]
                logger.info("    📝 %d/%d posts have captions", len(captions_with_text), len(new_posts))

                # Fetch lightweight list of known events so AI can skip duplicates
                known_events_result = (
                    supabase.table("events")
                    .select("title, date, venue")
                    .eq("artist_id", artist["id"])
                    .gte("date", "now()")
                    .order("date", desc=False)
                    .limit(50)
                    .execute()
                )
                known_events = known_events_result.data or []
                if known_events:
                    logger.info("    📋 %d known events passed to AI for dedup", len(known_events))

                summary = await distill_posts(
                    artist["id"],
                    artist["name"],
                    new_posts,
                    known_events=known_events,
                )

                gig_mentions = summary.get("gig_mentions", [])
                run_stats["ai_gig_mentions"] += len(gig_mentions)
                if gig_mentions:
                    # Enrich dateless/partial gigs via images, quoted tweets, and web
                    dateless = [g for g in gig_mentions if not g.get("date")]
                    if dateless:
                        logger.info("    🔍 Enriching %d dateless gig(s)...", len(dateless))
                        posts_by_id = {
                            p["post_id"]: p for p in new_posts if p.get("post_id")
                        }
                        await enrich_dateless_gigs(
                            artist["name"], gig_mentions, posts_by_id
                        )

                    # Web enrichment for partial events (missing date or venue)
                    partial = [
                        g for g in gig_mentions
                        if (g.get("date_precision") != "exact" or not g.get("venue_name"))
                        and g.get("confidence") in ("high", "medium")
                    ]
                    if partial:
                        logger.info("    🌐 Web-enriching %d partial event(s)...", len(partial))
                        for gig in partial:
                            try:
                                enriched = await web_enrich_event(
                                    artist["name"], gig
                                )
                                gig.update(enriched)
                            except Exception as e:
                                logger.warning("        ⚠️  Web enrichment failed: %s", str(e)[:60])

                    # Drop events too vague to be actionable:
                    # no exact date AND no venue (e.g. "all nighter in December")
                    before_vague = len(gig_mentions)
                    gig_mentions = [
                        g for g in gig_mentions
                        if g.get("venue_name") or g.get("date_precision") == "exact"
                    ]
                    vague_skipped = before_vague - len(gig_mentions)
                    if vague_skipped:
                        logger.info(
                            "    🚫 Filtered %d vague event(s) (no venue + imprecise date)",
                            vague_skipped,
                        )

                    ai_events = []
                    event_images_pending = []  # (event_index, images_list)

                    for gig in gig_mentions:
                        gig_title = gig.get("event_name") or gig.get("text", "")
                        logger.info("     🎫 %s", gig_title[:60])
                        if gig.get("date") or gig.get("venue_name"):
                            logger.info(
                                "        📍 %s | %s | %s",
                                gig.get("venue_name", "?"), gig.get("city", "?"), gig.get("date", "?"),
                            )

                        parsed_date = parse_date_hint(
                            gig.get("date") or gig.get("date_hint")
                        )
                        if not parsed_date:
                            logger.warning("        ⚠️  Skipped: unparseable date '%s'", gig.get("date"))
                            continue

                        title = gig_title or f"{artist['name']} live"

                        # Resolve source platform from the post that mentioned this event
                        source_post_id = gig.get("source_post_id", "")
                        source_post = next(
                            (p for p in new_posts if p.get("post_id") == source_post_id),
                            None,
                        )
                        source_platform = (
                            source_post["platform"] if source_post
                            else "social_ai"
                        )

                        # Deterministic source_id based on content
                        venue_for_hash = gig.get("venue_name") or gig.get("city") or "unknown"
                        hash_input = f"{artist['id']}|{venue_for_hash}|{parsed_date}"
                        source_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:12]

                        event_dict = {
                            "artist_id": artist["id"],
                            "title": title[:200],
                            "date": parsed_date,
                            "venue": gig.get("venue_name"),
                            "city": gig.get("city"),
                            "source": source_platform,
                            "source_id": f"{source_platform}_{source_hash}",
                            "ticket_url": gig.get("ticket_url"),
                            "raw_data": gig,
                            "source_post_ids": [source_post_id] if source_post_id else [],
                            "date_precision": gig.get("date_precision", "exact"),
                            "time": gig.get("time"),
                            "artist_billing": gig.get("artist_billing"),
                            "country": gig.get("country"),
                            "confidence": gig.get("confidence"),
                            "fingerprint": make_fingerprint(
                                artist["id"],
                                gig.get("venue_name"),
                                parsed_date,
                                gig.get("city") or artist.get("city"),
                            ),
                            "fingerprint_loose": make_fingerprint_loose(
                                parsed_date,
                                gig.get("city") or artist.get("city"),
                            ),
                        }

                        ai_events.append(event_dict)

                        # Collect images from source posts flagged as having event imagery
                        if gig.get("has_event_image"):
                            source_post = next(
                                (p for p in new_posts if p.get("post_id") == gig.get("source_post_id")),
                                None,
                            )
                            if source_post and source_post.get("media_url"):
                                event_images_pending.append((
                                    len(ai_events) - 1,
                                    [{
                                        "image_url": source_post["media_url"],
                                        "source_post_id": source_post["post_id"],
                                        "image_type": "poster",
                                    }],
                                ))

                    upsert_events(ai_events)

                    # Store event images (need event IDs from DB after upsert)
                    for idx, images in event_images_pending:
                        ev = ai_events[idx]
                        try:
                            db_event = (
                                supabase.table("events")
                                .select("id")
                                .eq("source", ev["source"])
                                .eq("source_id", ev["source_id"])
                                .limit(1)
                                .execute()
                            )
                            if db_event.data:
                                store_event_images(db_event.data[0]["id"], images)
                        except Exception:
                            pass

                    events.extend(ai_events)
                    logger.info("  💾 +%d event(s) extracted from social posts", len(ai_events))
                else:
                    logger.info("  ⚪ No events found in social posts")

                # Mark all platforms as distilled regardless of whether events were found
                mark_distilled(artist["id"], platforms_in_batch)

            except Exception as e:
                err = str(e).split("\n")[0][:80]
                logger.error("  ❌ AI extraction failed: %s", err)

        # Stamp last_synced_at so dev runs can skip recently-synced artists.
        try:
            supabase.table("artists").update(
                {"last_synced_at": datetime.now(timezone.utc).isoformat()}
            ).eq("id", artist["id"]).execute()
        except Exception as e:
            logger.warning("  ⚠️  Failed to stamp last_synced_at: %s", str(e)[:80])


    logger.info("=" * 50)
    logger.info("✅ Sync complete!")
    logger.info("=" * 50)
    logger.info(
        "🎤 Artists synced: %d   ⏭️  skipped: %d",
        run_stats["artists_synced"], run_stats["artists_skipped"],
    )
    logger.info(
        "📅 Events found: %d   📱 Posts found: %d   🧠 AI gig mentions: %d",
        run_stats["events_found"], run_stats["posts_found"],
        run_stats["ai_gig_mentions"],
    )
    for line in ai_metrics.summary_lines():
        logger.info(line)
    logger.info("=" * 50)

    return run_stats
