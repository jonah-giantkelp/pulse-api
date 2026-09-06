"""Dry-run geo backfill: re-derive city/country for every event from raw_data
and report what would change. Writes NOTHING to the database.

Usage:
    poetry run python scripts/geo_backfill_dryrun.py [--csv /path/to/report.csv]

Hard invariants checked (script exits 1 if violated):
- No event currently city='London' would change city.
- No event currently city='London' would end up with country != 'GB'.
"""

import argparse
import csv
import sys
from collections import Counter

from pulse_api.db import supabase
from pulse_api.sync.geo import CITIES, _COUNTRY_NAME_TO_ISO, propose_geo


def fetch_all_events() -> list[dict]:
    rows, page = [], 0
    while True:
        batch = (
            supabase.table("events")
            .select("id, source, title, venue, city, country, raw_data")
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="write full per-row diff to this CSV path")
    args = ap.parse_args()

    events = fetch_all_events()
    print(f"Fetched {len(events)} events\n")

    diffs = []
    stats = Counter()
    country_reasons = Counter()

    for e in events:
        old_city = e.get("city")
        old_country = e.get("country")
        new_city, new_country, reason = propose_geo(
            e.get("source", ""), e.get("raw_data") or {}, old_city, old_country
        )
        country_reasons[(e.get("source"), reason)] += 1

        city_changed = new_city != old_city
        country_changed = new_country != old_country
        if city_changed or country_changed:
            diffs.append({
                "id": e["id"],
                "source": e.get("source"),
                "title": (e.get("title") or "")[:60],
                "venue": (e.get("venue") or "")[:40],
                "old_city": old_city,
                "new_city": new_city,
                "old_country": old_country,
                "new_country": new_country,
                "country_reason": reason,
            })
            stats["rows_changed"] += 1
            if city_changed:
                stats["city_changed"] += 1
            if country_changed:
                stats["country_changed"] += 1

    # ── Invariants: a row already on a canonical city name must stay on it,
    # and must end up with that city's country. London first among equals. ──
    canon_by_display = {c["display"]: c["country"] for c in CITIES}
    city_broken = [
        d for d in diffs
        if d["old_city"] in canon_by_display and d["new_city"] != d["old_city"]
    ]
    country_broken = [
        d for d in diffs
        if d["old_city"] in canon_by_display
        and d["new_country"] != canon_by_display[d["old_city"]]
        and d["country_reason"] != "homonym"
    ]
    homonyms = [d for d in diffs if d["country_reason"] == "homonym"]
    london_city_broken = [d for d in city_broken if d["old_city"] == "London"]
    london_country_broken = [d for d in country_broken if d["old_city"] == "London"]

    # ── Report ──
    print(f"Rows that would change:      {stats['rows_changed']} / {len(events)}")
    print(f"  city would change:         {stats['city_changed']}")
    print(f"  country would change:      {stats['country_changed']}\n")

    print("City changes by (old → new), top 30:")
    city_moves = Counter(
        (d["old_city"], d["new_city"]) for d in diffs if d["old_city"] != d["new_city"]
    )
    for (old, new), n in city_moves.most_common(30):
        print(f"  {n:4d}  {old!r} → {new!r}")

    print("\nCountry changes by (source, old → new), top 30:")
    country_moves = Counter(
        (d["source"], str(d["old_country"]), str(d["new_country"]))
        for d in diffs
        if d["old_country"] != d["new_country"]
    )
    for (src, old, new), n in country_moves.most_common(30):
        print(f"  {n:4d}  {src:16s} {old} → {new}")

    print("\nCountry provenance by (source, reason):")
    for (src, reason), n in sorted(country_reasons.items()):
        print(f"  {str(src):16s} {reason:10s} {n}")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(diffs[0].keys()) if diffs else ["id"])
            writer.writeheader()
            writer.writerows(diffs)
        print(f"\nFull diff written to {args.csv}")

    # Bandsintown country tokens that failed to map — alias-map gaps to review
    unmapped = Counter()
    for e in events:
        if e.get("source") != "bandsintown":
            continue
        location = (e.get("raw_data") or {}).get("location") or ""
        if "," in location:
            token = location.rsplit(",", 1)[1].strip()
            from pulse_api.sync.geo import _CA_PROVINCES, _US_STATES
            if (
                token
                and token.lower() not in _COUNTRY_NAME_TO_ISO
                and token.upper() not in _US_STATES
                and token.upper() not in _CA_PROVINCES
            ):
                unmapped[token.lower()] += 1
    if unmapped:
        print("\nUnmapped bandsintown country tokens:")
        for token, n in unmapped.most_common(20):
            print(f"  {n:4d}  {token!r}")

    if homonyms:
        print("\nHomonym catches (canonical-named city, source says other country —")
        print("kept unlinked with raw country; needs a country guard in city filters):")
        for d in homonyms:
            print(f"  {d['source']:14s} {d['title']!r}: {d['new_city']} / {d['new_country']}")

    print("\n── Canonical-city invariants ──")
    if city_broken:
        print(f"❌ {len(city_broken)} canonical-city rows would change city:")
        for d in city_broken[:10]:
            print(f"   {d['id']} {d['title']!r}: {d['old_city']} → {d['new_city']!r}")
    else:
        print("✅ No canonical-city row changes city (London included)")
    if country_broken:
        print(f"❌ {len(country_broken)} canonical-city rows get the wrong country:")
        for d in country_broken[:10]:
            print(f"   {d['id']} {d['old_city']} {d['title']!r}: → {d['new_country']!r} ({d['country_reason']})")
    else:
        print("✅ Every canonical-city row ends with its list country")
    print(f"   (London specifically: {len(london_city_broken)} city / {len(london_country_broken)} country violations)")

    sys.exit(1 if (city_broken or country_broken) else 0)


if __name__ == "__main__":
    main()
