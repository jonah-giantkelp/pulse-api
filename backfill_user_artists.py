"""
Backfill script: subscribe a user to ALL artists in the database.

Usage:
    python backfill_user_artists.py <user-uuid>
"""

import sys

from pulse_api.db import supabase

DEFAULT_CITY = None


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python backfill_user_artists.py <user-uuid>")
    USER_ID = sys.argv[1]

    # 1. Fetch all artist IDs
    artists = (
        supabase.table("artists")
        .select("id")
        .execute()
    )
    all_artist_ids = [a["id"] for a in artists.data]
    print(f"Found {len(all_artist_ids)} artists in total.")

    # 2. Fetch artist IDs already tracked by this user
    existing = (
        supabase.table("user_artists")
        .select("artist_id")
        .eq("user_id", USER_ID)
        .execute()
    )
    existing_ids = {row["artist_id"] for row in existing.data}
    print(f"User already tracks {len(existing_ids)} artists.")

    # 3. Build rows for missing artists
    to_insert = [
        {
            "user_id": USER_ID,
            "artist_id": aid,
            "city": DEFAULT_CITY,
            "notify": True,
        }
        for aid in all_artist_ids
        if aid not in existing_ids
    ]

    if not to_insert:
        print("Nothing to backfill — user already tracks every artist.")
        return

    print(f"Inserting {len(to_insert)} new tracking rows …")

    # 4. Insert in batches of 500 (Supabase/PostgREST limit)
    BATCH = 500
    for i in range(0, len(to_insert), BATCH):
        batch = to_insert[i : i + BATCH]
        supabase.table("user_artists").insert(batch).execute()
        print(f"  … inserted batch {i // BATCH + 1} ({len(batch)} rows)")

    print("Done.")


if __name__ == "__main__":
    main()
