"""Sign in via Supabase auth, then add artists by MusicBrainz ID."""
import httpx
import json
import sys
import time

from pulse_api.config import settings

# (name, musicbrainz_id) — MBIDs verified to avoid disambiguation errors
ARTISTS = [
    ("Hunee", "feb974b4-031d-4f20-b072-91f506d6c6dd"),
]
ARTISTS_OLD = [
    ("Antal", "8db522dd-6eab-4515-b67f-f9e42c5940da"),
    ("Blur", "ba853904-ae25-4ebb-89d6-c44cfbd71bd2"),
    ("Calibre", "afa502e0-fb84-4cf4-b3c0-21ed0c695939"),
    ("Caribou", "735e3514-a8ae-401f-af3b-6300df1b8d2c"),
    ("Daft Punk", "056e4f3e-d505-4dad-8ec1-d04f521cbb56"),
    ("Daphni", "859216c4-5d01-479b-b6c3-e20cc591a86a"),
    ("Darkside", "116b79ab-b049-4299-8fd8-17ac8a18b7f3"),
    ("Floating Points", "69d9c5ba-7bba-4cb7-ab32-8ccc48ad4f97"),
    ("Four Tet", "3bcff06f-675a-451f-9075-99e8657047e8"),
    ("Fred Again..", "bca46a0c-25c9-42ca-98c2-e64c8a5e337e"),
    ("Gorillaz", "e21857d5-3256-4547-afb3-4b6ded592596"),
    ("Hunee", "feb974b4-031d-4f20-b072-91f506d6c6dd"),
    ("Jungle", "6bbb3983-ce8a-4971-96e0-7cae73268fc4"),
    ("Justice", "860b2707-6153-4e3a-aa57-74d2b42c55b5"),
    ("LCD Soundsystem", "2aaf7396-6ab8-40f3-9776-a41c42c8e26b"),
    ("Little Simz", "3cdb40fe-a63e-4bb9-b40d-17cda5f50979"),
    ("Mafalda", "a180ebef-1ca6-4599-8e3d-6b317bf5942f"),
    ("Metronomy", "93eb7110-0bc9-4d3f-816b-4b52ef982ec8"),
    ("Moby", "8970d868-0723-483b-a75b-51088913d3d4"),
    ("Muse", "9c9f1380-2516-4fc9-a3e6-f9f61941d090"),
    ("Nicolas Jaar", "06e99a1b-4020-4380-ab27-1a3e0c5e557c"),
    ("O'Flynn", "24c24af0-ce68-4cb1-b76b-2f11664d1567"),
    ("Orbital", "f3e2a7d9-c6bb-4848-95e5-04c0a1e2f511"),
    ("Palms Trax", "bba6534d-f62c-4c7d-aa74-f9a7696113c1"),
    ("Radiohead", "a74b1b7f-71a5-4011-9441-d0b5e4122711"),
    ("SAULT", "23b19bc5-813e-4456-bbae-ac3ca118f535"),
    ("Scissor Sisters", "4236d929-9a81-4c8e-97c3-8d3306780f50"),
    ("The Chemical Brothers", "1946a82a-f927-40c2-8235-38d64f50d043"),
]

API_BASE = "http://localhost:3000"


def get_token() -> str:
    """Authenticate with Supabase and return an access token."""
    if not settings.test_email or not settings.test_password:
        print("ERROR: TEST_EMAIL and TEST_PASSWORD must be set in .env")
        sys.exit(1)

    print("Signing in...")
    resp = httpx.post(
        f"{settings.supabase_url}/auth/v1/token?grant_type=password",
        headers={
            "apikey": settings.supabase_key,
            "Content-Type": "application/json",
        },
        json={
            "email": settings.test_email,
            "password": settings.test_password,
        },
    )

    if resp.status_code != 200:
        print(f"Auth failed ({resp.status_code}): {resp.text}")
        sys.exit(1)

    token = resp.json()["access_token"]
    print(f"Got token: {token[:30]}...\n")
    return token


def add_artists(token: str):
    """Add each artist directly by MusicBrainz ID (skips search step)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    success = []
    failed = []

    for name, mbid in ARTISTS:
        print(f"➕ Adding: {name} (mbid: {mbid[:12]}...)")

        try:
            resp = httpx.post(
                f"{API_BASE}/artists",
                headers=headers,
                json={"musicbrainz_id": mbid},
                timeout=120.0,
            )
            resp.raise_for_status()
            result = resp.json()
            status = result.get("status", "added")
            print(f"   ✅ {status}: {result.get('artist_id', '?')}\n")
            success.append(name)
        except Exception as e:
            print(f"   ❌ Failed: {e}\n")
            failed.append(name)

        # Pause to respect MusicBrainz rate limits during resolution
        time.sleep(2)

    print("=" * 50)
    print(f"✅ Added: {len(success)}/{len(ARTISTS)}")
    if failed:
        print(f"❌ Failed: {', '.join(failed)}")


if __name__ == "__main__":
    token = get_token()
    add_artists(token)
