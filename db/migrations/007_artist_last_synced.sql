-- Track when an artist was last fully synced (events + social)
-- so dev runs can skip artists processed within the last N hours.
alter table artists add column if not exists last_synced_at timestamptz;
