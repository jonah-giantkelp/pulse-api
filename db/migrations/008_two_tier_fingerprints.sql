-- 008_two_tier_fingerprints.sql
-- Add a loose fingerprint column (city+date only) for same-artist dedup.
-- The strict fingerprint (venue+city+date) is used for cross-artist linking.
-- This prevents false multi-artist links when two events share a city+date
-- but are at different venues.

alter table events
    add column if not exists fingerprint_loose text;

create index if not exists idx_events_fingerprint_loose
    on events(fingerprint_loose);

-- Clear old fingerprints so the orchestrator recalculates them with the
-- new two-tier logic on next sync.
update events set fingerprint = null, fingerprint_loose = null;
