-- 017: Drop the 'GB' default on events.country.
--
-- The default silently stamped GB onto any event whose source didn't provide
-- a country, poisoning country-based filtering (Berlin gigs claiming GB).
-- The sync pipeline now derives country from raw_data / the canonical city
-- list (pulse_api/sync/geo.py); unknown stays NULL honestly.
-- Existing rows were corrected by scripts/geo_backfill_apply.py (2026-09-06).

ALTER TABLE events
    ALTER COLUMN country DROP DEFAULT;
