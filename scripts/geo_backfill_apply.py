"""Apply the geo backfill: recompute (city, country) for every event from
raw_data via propose_geo and write the rows that change.

Run scripts/geo_backfill_dryrun.py first; this performs the same computation
and writes it. Re-running the dry run afterwards should report 0 changes.
"""

import logging
import time
from collections import Counter

from pulse_api.db import supabase
from pulse_api.sync.geo import propose_geo

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


def fetch_all_events() -> list[dict]:
    rows, page = [], 0
    while True:
        batch = (
            supabase.table("events")
            .select("id, source, title, city, country, raw_data")
            .order("id")
            .range(page * 1000, page * 1000 + 999)
            .execute()
            .data
        )
        rows.extend(batch)
        if len(batch) < 1000:
            return rows
        page += 1


def main():
    events = fetch_all_events()
    logger.info("Fetched %d events", len(events))

    london_before = sum(1 for e in events if e.get("city") == "London")

    updates = []
    for e in events:
        new_city, new_country, _ = propose_geo(
            e.get("source", ""), e.get("raw_data") or {}, e.get("city"), e.get("country")
        )
        if new_city != e.get("city") or new_country != e.get("country"):
            updates.append({"id": e["id"], "city": new_city, "country": new_country})

    logger.info("Applying %d updates...", len(updates))
    ok, failed = 0, 0
    for u in updates:
        for attempt in range(3):
            try:
                supabase.table("events").update(
                    {"city": u["city"], "country": u["country"]}
                ).eq("id", u["id"]).execute()
                ok += 1
                break
            except Exception as exc:
                if attempt == 2:
                    failed += 1
                    logger.warning("  ❌ %s: %s", u["id"], str(exc)[:80])
                else:
                    time.sleep(0.5 * (attempt + 1))
        if ok and ok % 100 == 0:
            logger.info("  ...%d/%d", ok, len(updates))

    logger.info("✅ Updated %d rows, %d failed", ok, failed)

    # Post-check: London count must be unchanged (alias merges never touch it)
    after = fetch_all_events()
    london_after = sum(1 for e in after if e.get("city") == "London")
    logger.info("London rows before: %d, after: %d", london_before, london_after)
    if london_after != london_before:
        logger.error("❌ LONDON COUNT CHANGED — investigate immediately")
    by_city = Counter(e.get("city") for e in after)
    logger.info("Top cities now: %s", by_city.most_common(12))


if __name__ == "__main__":
    main()
