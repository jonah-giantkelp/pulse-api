# pulse-api

Backend for a London-focused artist performance tracker. Follow a list of
musical artists, and every day pulse-api scrapes a handful of ticketing
platforms and social networks to surface upcoming gigs and recent posts,
de-duplicates across sources, stores them in Supabase, and emails a daily
digest.

## What it does

- **Event sync.** For each followed artist, pulls upcoming shows from
  Ticketmaster, DICE, Bandsintown, Skiddle, Resident Advisor, a RapidAPI
  concerts aggregator, and the artist's own website — then cross-source
  deduplicates by `(artist, venue, date, city)`.
- **Social sync.** Pulls recent Instagram and Twitter posts via RapidAPI,
  distils the interesting ones with an LLM, and keeps per-(artist, platform)
  cursors so we only fetch what's new.
- **Artist resolution.** Given a name or MusicBrainz ID, resolves to
  Spotify / Bandsintown / DICE / RA / Ticketmaster slugs using MusicBrainz
  + AI assistance.
- **Daily digest.** Sends each user a Postmark email of what's new for the
  artists they follow.
- **HTTP API.** Flask app exposing artist search, follow/unfollow, event
  and social feeds, email preferences, and a cron-triggered `/sync`
  endpoint.

## Stack

- Python 3.11+ / Poetry
- Flask + Flask-CORS (HTTP)
- Supabase (Postgres, auth)
- httpx + BeautifulSoup4 (scraping)
- pydantic-settings + python-dotenv (config)
- [giantkelp-ai](https://pypi.org/project/giantkelp-ai/) (LLM helpers)
- Postmark (transactional email)
- Jina Reader (`r.jina.ai`) for website scraper page→markdown conversion

## Layout

```
pulse_api/
  app.py                # Flask routes
  auth.py               # Supabase JWT middleware
  config.py             # pydantic-settings
  db.py                 # Supabase client
  orchestrator.py       # Daily sync pipeline (events + social)
  email_digest.py       # Postmark digest sender
  email_template.py
  ai/
    resolver.py         # Name → platform slugs
    distiller.py        # Social-post summarisation
  sources/
    base.py             # EventSource / SearchResult / EventResult
    scraping.py         # Shared Jina helpers, direct_fetch, search_url
    bandsintown.py      # window.__data → events
    dice.py             # __NEXT_DATA__ → events
    ticketmaster.py
    skiddle.py
    ra.py
    concerts.py         # RapidAPI concerts-artists-events-tracker
    website.py          # AI-driven scrape of an artist's own site
    spotify.py
    musicbrainz.py
    instagram.py
    twitter.py
db/
  schema.sql            # Current schema
  migrations/           # Applied-in-order SQL migrations
docs/                   # API response examples, iOS client plan
```

## Setup

1. **Clone + install.**
   ```bash
   poetry install
   ```

2. **Configure env.** Copy `.env.example` to `.env` and fill in the keys
   you have. At minimum you need `SUPABASE_URL`, `SUPABASE_KEY`, and
   `OPENAI_API_KEY`; the event-source API keys are all optional and each
   source silently skips if its key is missing.

3. **Apply migrations** against your Supabase database in numeric order:
   ```
   db/schema.sql
   db/migrations/001_*.sql
   db/migrations/002_*.sql
   ...
   ```

4. **Run locally.**
   ```bash
   poetry run pulse
   ```
   This starts the Flask app (see `pulse_api.app:run`).

## Daily sync

The cron-triggered endpoint:

```
POST /sync
x-api-key: $SYNC_API_KEY
```

Or invoke the pipeline directly in a script:

```python
import asyncio
from pulse_api.orchestrator import run_daily_sync

asyncio.run(run_daily_sync())
```

### Dev-mode throttle

Set `PULSE_DEV_SKIP_HOURS=12` to skip artists whose `last_synced_at` is
newer than the threshold — useful when iterating locally so you don't
hammer every source on every run.

## Scraping notes

- **DICE** and **Bandsintown** are scraped directly (no proxy) by parsing
  their Next.js state blobs (`<script id="__NEXT_DATA__">` and
  `window.__data` respectively).
- **Artist website scraper** uses Jina Reader (`r.jina.ai`) to convert
  arbitrary artist homepages to markdown for AI analysis. This is the only
  component that requires `JINA_API_KEY`.
- **Search.** Artist-slug discovery (DICE, Bandsintown, Instagram,
  Twitter) uses OpenAI `web_search` via giantkelp-ai.

## Error isolation

The sync pipeline is wrapped so a single artist's failure (bad data,
source outage, AI timeout) can't kill the whole run — errors are logged
to the `sync_log` table and the loop continues.

## Contributing

This is a personal project; no public contribution workflow. If you've
forked it to run your own tracker, the bits most likely to need tweaking
are `default_city` in `config.py` and the geo filters in `orchestrator.py`.
