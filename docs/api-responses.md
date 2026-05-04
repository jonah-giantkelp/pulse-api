# Pulse API — Response Reference

All endpoints require `Authorization: Bearer <supabase_jwt>` unless noted.
All responses are `application/json`.

---

## `GET /health`

No auth required.

```json
{ "status": "ok" }
```

---

## `GET /artists/search?q=<query>&limit=<n>`

Search MusicBrainz for artists. `limit` defaults to 8, max 20. `q` must be at least 2 characters.

**200** — Array of candidates (top 5 enriched with images):

```json
[
  {
    "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
    "name": "Radiohead",
    "disambiguation": "English rock band",
    "country": "GB",
    "tags": ["rock", "alternative rock", "electronic"],
    "image_url": "https://i.scdn.co/image/abc123...",
    "genres": ["alternative rock", "art rock"]
  }
]
```

Returns `[]` if `q` is too short or no results found.

| Field | Type | Notes |
|-------|------|-------|
| `musicbrainz_id` | `string` | Use this when calling `POST /artists` |
| `name` | `string` | |
| `disambiguation` | `string?` | Crucial for telling apart artists with the same name |
| `country` | `string?` | ISO 3166-1 alpha-2 |
| `tags` | `[string]?` | MusicBrainz genre tags |
| `image_url` | `string?` | Spotify image, only populated for top 5 results |
| `genres` | `[string]?` | Enriched genres (may differ from `tags`) |

---

## `POST /artists`

Add an artist and subscribe the current user.

**Request body:**

```json
{
  "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
  "name": "Radiohead",
  "city": "Berlin"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `musicbrainz_id` | Preferred | From search endpoint |
| `name` | Fallback | Used if no MBID provided |
| `city` | No | Optional — stored on the user's subscription for this artist. `null` if omitted. |

**200** — Artist already existed, user subscribed:

```json
{
  "artist_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "existing",
  "message": "Artist already exists, subscribed."
}
```

**201** — New artist created, platforms resolved via AI, user subscribed:

```json
{
  "artist_id": "550e8400-e29b-41d4-a716-446655440000",
  "matches": {
    "spotify_id": { "status": "resolved", "source": "musicbrainz" },
    "ticketmaster": { "status": "resolved", "confidence": "high", "candidates": [...] },
    "ra": { "status": "ambiguous", "confidence": "low", "candidates": [...] }
  },
  "needs_review": ["ra"]
}
```

| Field | Type | Notes |
|-------|------|-------|
| `artist_id` | `string` (uuid) | |
| `status` | `string` | `"existing"` or absent (new artists return `matches` instead) |
| `message` | `string?` | Human-readable, only on existing |
| `matches` | `object?` | Platform resolution results, only on new artists |
| `needs_review` | `[string]?` | Platforms that couldn't be confidently resolved |

**400:**

```json
{ "error": "musicbrainz_id or name is required" }
```

---

## `GET /artists/<artist_id>`

**200** — Full artist record:

```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Radiohead",
  "musicbrainz_id": "a74b1b7f-71a5-4011-9441-d0b5e4122711",
  "spotify_id": "4Z8W4fKeB5YxbusRsdQVPb",
  "ticketmaster_id": "K8vZ9171C-f",
  "bandsintown_name": "radiohead",
  "instagram_handle": "radiohead",
  "twitter_handle": "radiohead",
  "skiddle_id": "29384",
  "concerts_tracker_id": "123456",
  "songkick_id": null,
  "dice_slug": "radiohead",
  "ra_id": "12345",
  "website_url": "https://www.radiohead.com",
  "genres": ["alternative rock", "art rock", "electronic"],
  "image_url": "https://i.scdn.co/image/abc123...",
  "active": true,
  "created_at": "2026-03-15T10:30:00+00:00",
  "updated_at": "2026-04-10T08:15:00+00:00"
}
```

All platform ID fields are `string?` — `null` means not yet resolved or not found.

**404:**

```json
{ "error": "Artist not found" }
```

---

## `GET /artists/<artist_id>/resolutions`

**200** — Resolution audit trail (newest first):

```json
[
  {
    "id": "...",
    "artist_id": "550e8400-...",
    "platform": "ticketmaster",
    "status": "resolved",
    "confidence": "high",
    "candidates": [
      {
        "platform_id": "K8vZ9171C-f",
        "name": "Radiohead",
        "url": "https://..."
      }
    ],
    "resolved_at": "2026-03-15T10:30:05+00:00",
    "resolved_by": "ai",
    "created_at": "2026-03-15T10:30:05+00:00"
  }
]
```

| Field | Type | Values |
|-------|------|--------|
| `status` | `string` | `"resolved"`, `"ambiguous"`, `"not_found"` |
| `confidence` | `string?` | `"high"`, `"medium"`, `"low"` |
| `resolved_by` | `string?` | `"ai"`, `"user"`, `"musicbrainz"` |
| `candidates` | `array` | All search results the AI considered |

---

## `POST /artists/<artist_id>/resolve`

Manually resolve an ambiguous platform match.

**Request body:**

```json
{
  "platform": "ra",
  "platform_id": "12345"
}
```

**200:**

```json
{ "status": "resolved" }
```

**400:**

```json
{ "error": "platform and platform_id are required" }
```

---

## `GET /me/artists`

**200** — Array of tracked artists with subscription metadata:

```json
[
  {
    "artist_id": "550e8400-...",
    "city": null,
    "notify": true,
    "created_at": "2026-03-15T10:31:00+00:00",
    "artists": {
      "id": "550e8400-...",
      "name": "Radiohead",
      "musicbrainz_id": "a74b1b7f-...",
      "spotify_id": "4Z8W4fKeB5YxbusRsdQVPb",
      "ticketmaster_id": "K8vZ9171C-f",
      "bandsintown_name": "radiohead",
      "instagram_handle": "radiohead",
      "twitter_handle": "radiohead",
      "skiddle_id": "29384",
      "concerts_tracker_id": "123456",
      "songkick_id": null,
      "dice_slug": "radiohead",
      "ra_id": "12345",
      "website_url": "https://www.radiohead.com",
      "genres": ["alternative rock", "art rock"],
      "image_url": "https://i.scdn.co/image/abc123...",
      "active": true,
      "created_at": "2026-03-15T10:30:00+00:00",
      "updated_at": "2026-04-10T08:15:00+00:00"
    }
  }
]
```

The nested `artists` object is the full artist record (Supabase join on `user_artists → artists`). Returns `[]` if user tracks nothing.

---

## `DELETE /me/artists/<artist_id>`

**200:**

```json
{ "status": "untracked" }
```

---

## `GET /me/events`

Upcoming events across all tracked artists, deduplicated. Multi-artist events (festivals) appear once with all linked artists in the `artists` array.

**Query parameters (all optional):**

| Param | Type | Notes |
|-------|------|-------|
| `city` | `string` | Filter events by city (case-insensitive). e.g. `?city=Berlin` |
| `country` | `string` | Filter events by ISO 3166-1 alpha-2 country code. e.g. `?country=DE` |

Both can be combined: `?city=Berlin&country=DE`. If omitted, returns events in all locations.

**200:**

```json
[
  {
    "id": "event-uuid-...",
    "artist_id": "550e8400-...",
    "title": "Radiohead at The Roundhouse",
    "date": "2026-06-15T19:00:00+00:00",
    "venue": "The Roundhouse",
    "city": "London",
    "source": "ticketmaster",
    "source_id": "vvG1HZ9e-abc123",
    "ticket_url": "https://www.ticketmaster.co.uk/event/...",
    "source_post_ids": null,
    "date_precision": "exact",
    "time": "19:00",
    "artist_billing": "headline",
    "country": "GB",
    "confidence": null,
    "fingerprint": "a3b4c5d6e7f8g9h0",
    "created_at": "2026-04-01T12:00:00+00:00",
    "updated_at": "2026-04-01T12:00:00+00:00",
    "artists": [
      {
        "artist_id": "550e8400-...",
        "name": "Radiohead",
        "image_url": "https://i.scdn.co/image/abc123...",
        "billing": "headline"
      }
    ],
    "images": [
      {
        "image_url": "https://ra.co/images/events/flyer/2026/6/...",
        "image_type": "poster"
      }
    ],
    "ticket_links": [
      {
        "source": "dice",
        "url": "https://dice.fm/event/radiohead-roundhouse-...",
        "price_min": 45.0,
        "price_max": 45.0,
        "currency": "GBP"
      },
      {
        "source": "ticketmaster",
        "url": "https://www.ticketmaster.co.uk/event/..."
      },
      {
        "source": "ra",
        "url": "https://ra.co/events/2409922",
        "price_min": 40.0,
        "price_max": 40.0,
        "currency": "GBP"
      }
    ],
    "detail": {
      "description": "Hampton Court Palace and its beautiful grounds provide the perfect setting...",
      "doors_open": "17:30:00",
      "venue_address": "East Molesey, Surrey, KT8 9AU",
      "genre": "Rock",
      "status": "onsale"
    },
    "social_posts": []
  }
]
```

**Note:** `raw_data` is no longer included in the response — all useful fields are extracted into `detail` instead.

| Field | Type | Notes |
|-------|------|-------|
| `id` | `string` (uuid) | |
| `artist_id` | `string?` (uuid) | Legacy primary artist hint — use `artists` array instead |
| `title` | `string` | |
| `date` | `string` (ISO 8601) | |
| `venue` | `string?` | |
| `city` | `string` | City where the event takes place. Defaults to `"Unknown"` if not determined. |
| `source` | `string` | `"ticketmaster"`, `"bandsintown"`, `"skiddle"`, `"ra"`, `"dice"`, `"concerts_tracker"`, `"social_ai"`, `"website"` |
| `source_id` | `string?` | ID from the source platform |
| `ticket_url` | `string?` | Direct link to buy tickets (legacy — prefer `ticket_links`) |
| `source_post_ids` | `[string]?` | Social post IDs that surfaced this event |
| `date_precision` | `string` | `"exact"`, `"month"`, `"season"`, `"unknown"` |
| `time` | `string?` | `"HH:MM"` format |
| `artist_billing` | `string?` | `"headline"`, `"support"`, `"b2b"`, `"dj_set"`, `"live"`, `"festival_slot"` |
| `country` | `string?` | ISO 3166-1 alpha-2. `null` if unknown. |
| `confidence` | `string?` | AI confidence for `social_ai` events |
| `fingerprint` | `string?` | Dedup hash (venue + date) |
| `artists` | `[object]` | All tracked artists on this event |
| `artists[].artist_id` | `string` (uuid) | |
| `artists[].name` | `string` | |
| `artists[].image_url` | `string?` | Artist image |
| `artists[].billing` | `string?` | This artist's role |
| `images` | `[object]` | Event posters/flyers from ticketing sources + social |
| `images[].image_url` | `string` | |
| `images[].image_type` | `string` | `"poster"` |
| `ticket_links` | `[object]` | All ticket URLs across platforms, with per-source pricing |
| `ticket_links[].source` | `string` | `"dice"`, `"ra"`, `"skiddle"`, `"ticketmaster"`, etc. |
| `ticket_links[].url` | `string` | Direct ticket purchase link |
| `ticket_links[].price_min` | `number?` | Lowest price in whole currency units (e.g. 25.0 = £25). Omitted if unknown. |
| `ticket_links[].price_max` | `number?` | Highest price. Same as `price_min` for single-tier events. |
| `ticket_links[].currency` | `string?` | ISO 4217: `"GBP"`, `"EUR"`, `"USD"`. Omitted if unknown. |
| `detail` | `object?` | Enriched fields extracted from source data. Only present if any fields were found. |
| `detail.description` | `string?` | Event description / info text |
| `detail.lineup` | `[string]?` | Artist names on the bill (from RA/DICE). Distinct from `artists` which is only *tracked* artists. |
| `detail.doors_open` | `string?` | Door opening time — `"HH:MM"` or ISO datetime depending on source |
| `detail.last_entry` | `string?` | Last entry time (Skiddle only) |
| `detail.doors_close` | `string?` | Closing time (Skiddle only) |
| `detail.age_restriction` | `string?` | e.g. `"18+"`, `"This is a 21+ event"` |
| `detail.status` | `string?` | `"onsale"`, `"on-sale"`, `"sold-out"`, `"cancelled"`, `"free"`, `"streaming"` |
| `detail.venue_address` | `string?` | Full venue address |
| `detail.venue_type` | `string?` | e.g. `"Outdoors"`, `"Club"` (Skiddle only) |
| `detail.genre` | `string?` | Genre classification (Ticketmaster only) |
| `social_post` | `object?` | The most recent social post that surfaced this event. Only present if the event was discovered via social. |
| `social_post.post_id` | `string` | Platform post ID |
| `social_post.platform` | `string` | `"instagram"`, `"twitter"` |
| `social_post.caption` | `string?` | Full post text |is
| `social_post.media_url` | `string?` | Image/video URL |
| `social_post.posted_at` | `string` | ISO 8601 |

Returns `[]` if no tracked artists or no upcoming events.

**What each source contributes to `detail`:**

| Source | description | lineup | doors/times | age | status | venue_address | genre |
|--------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| DICE | about.description | summary_lineup | doors_open_date | highlights | on-sale/sold-out | venue address | |
| Skiddle | description | | doors/last_entry/close | minage | cancelled | address+postcode | |
| Ticketmaster | info/pleaseNote | | localTime | | onsale/offsale | embedded venue | genre |
| RA | | artists[] | startTime | | | | |
| Bandsintown | | | | | free/streaming | | |

**Frontend pricing notes:**
- `price_min == price_max` → show "£25"
- `price_min != price_max` → show "£25–55"
- No `price_min` → show "View tickets" (no price)
- `price_min == 0` → show "Free"
- Sources with pricing: DICE, Skiddle, RA. Ticketmaster and Bandsintown don't expose prices.

---

## `GET /artists/<artist_id>/events`

Upcoming events for a specific artist. Same event fields as `/me/events` but with `artist_billing` at the top level instead of the `artists` array.

**Query parameters (all optional):**

| Param | Type | Notes |
|-------|------|-------|
| `city` | `string` | Filter by city (case-insensitive) |
| `country` | `string` | Filter by ISO 3166-1 alpha-2 country code |

**200:**

```json
[
  {
    "id": "event-uuid-...",
    "artist_id": "550e8400-...",
    "title": "Radiohead at The Roundhouse",
    "date": "2026-06-15T19:00:00+00:00",
    "venue": "The Roundhouse",
    "city": "London",
    "source": "ticketmaster",
    "source_id": "vvG1HZ9e-abc123",
    "ticket_url": "https://www.ticketmaster.co.uk/event/...",
    "raw_data": { ... },
    "source_post_ids": null,
    "date_precision": "exact",
    "time": "19:00",
    "artist_billing": "headline",
    "country": "GB",
    "confidence": null,
    "fingerprint": "a3b4c5d6e7f8g9h0",
    "created_at": "2026-04-01T12:00:00+00:00",
    "updated_at": "2026-04-01T12:00:00+00:00",
    "images": [
      {
        "image_url": "https://media.ticketmaster.co.uk/...",
        "image_type": "poster"
      }
    ]
  }
]
```

Note: `artist_billing` here is the specific billing for the requested artist on each event. No `artists` array on this endpoint.

---

## `GET /artists/<artist_id>/social`

Most recent 50 social posts.

**200:**

```json
[
  {
    "id": "post-uuid-...",
    "artist_id": "550e8400-...",
    "platform": "instagram",
    "post_id": "CxYz123abc",
    "caption": "Playing at The Roundhouse June 15th! Tickets on sale now...",
    "media_url": "https://scontent-lhr8-1.cdninstagram.com/...",
    "posted_at": "2026-04-10T14:30:00+00:00",
    "raw_data": { ... },
    "created_at": "2026-04-10T15:00:00+00:00"
  }
]
```

| Field | Type | Notes |
|-------|------|-------|
| `platform` | `string` | `"instagram"` or `"twitter"` |
| `post_id` | `string` | Platform-specific post ID |
| `caption` | `string?` | Post text |
| `media_url` | `string?` | Image/video URL |
| `posted_at` | `string?` (ISO 8601) | When posted |

---

## `GET /artists/<artist_id>/social/summary`

Latest AI-generated summary of the artist's social activity.

**200:**

```json
{
  "id": "summary-uuid-...",
  "artist_id": "550e8400-...",
  "summary": "Radiohead have been promoting their upcoming London show at The Roundhouse on June 15th. Recent posts include venue rehearsal footage and ticket sale reminders.",
  "date": "2026-04-10",
  "model_used": "gpt-4o",
  "source_post_ids": ["post-uuid-1", "post-uuid-2"],
  "created_at": "2026-04-10T15:30:00+00:00"
}
```

**404:**

```json
{ "error": "No summaries yet" }
```

---

## `GET /me/email-preferences`

**200** — User has preferences set:

```json
{
  "user_id": "85f54f96-...",
  "email": "user@example.com",
  "digest_enabled": true,
  "default_cities": ["London", "Berlin"],
  "default_countries": ["GB", "DE"],
  "updated_at": "2026-04-10T08:15:00+00:00"
}
```

**200** — No preferences set yet:

```json
{
  "email": null,
  "digest_enabled": false,
  "default_cities": [],
  "default_countries": []
}
```

| Field | Type | Notes |
|-------|------|-------|
| `email` | `string?` | Digest delivery address |
| `digest_enabled` | `boolean` | Whether daily digest is on |
| `default_cities` | `[string]` | Cities to filter digest events by. Empty = all cities. |
| `default_countries` | `[string]` | ISO 3166-1 alpha-2 country codes. Empty = all countries. |

The digest filters events matching `city IN (default_cities) OR country IN (default_countries)`. If both arrays are empty, the digest includes events in all locations.

---

## `PUT /me/email-preferences`

Set or update email digest preferences. All fields are optional — only provided fields are updated.

**Request body:**

```json
{
  "email": "user@example.com",
  "digest_enabled": true,
  "default_cities": ["London", "Berlin"],
  "default_countries": ["DE"]
}
```

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `email` | No* | `string` | *Required on first setup if no record exists |
| `digest_enabled` | No | `boolean` | |
| `default_cities` | No | `[string]` | Replaces the full array. Pass `[]` to clear. |
| `default_countries` | No | `[string]` | ISO alpha-2 codes, auto-uppercased. Pass `[]` to clear. |

**200:**

```json
{
  "user_id": "85f54f96-...",
  "email": "user@example.com",
  "digest_enabled": true,
  "default_cities": ["London", "Berlin"],
  "default_countries": ["DE"],
  "updated_at": "2026-04-18T10:00:00+00:00"
}
```

**400:**

```json
{ "error": "at least one field is required" }
```

---

## `DELETE /me/email-preferences`

Remove all email preferences (opt out entirely).

**200:**

```json
{ "status": "removed" }
```

---

## `POST /sync`

Trigger full daily sync. No Bearer auth — uses API key header instead.

**Request headers:**

```
X-API-Key: <SYNC_API_KEY>
```

**200:**

```json
{ "status": "sync complete" }
```

**401:**

```json
{ "error": "Unauthorized" }
```

---

## Error responses

All errors follow the same shape:

```json
{
  "error": "Human-readable message",
  "detail": "Optional technical detail (e.g. exception text)"
}
```

| Status | Meaning |
|--------|---------|
| 400 | Bad request (missing/invalid fields) |
| 401 | Missing, invalid, or expired token |
| 404 | Resource not found |
| 500 | Server error |
