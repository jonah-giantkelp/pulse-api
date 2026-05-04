-- Add a first-class lineup column to events.
--
-- Until now the lineup lived inside raw_data._canonical_lineup (a nested
-- jsonb field set during sync's merge step). That worked but it's:
--   - opaque to SQL — can't filter/aggregate easily
--   - dependent on the read path stripping raw_data correctly
--   - fragile (any code that overwrites raw_data wipes it)
--
-- Lift it to a top-level column. The orchestrator computes the canonical
-- lineup the same way (longest list across all sources for the merged
-- event); only the storage location moves.
--
-- Note: existing views (`event_with_artist`, `user_upcoming_events`) use
-- `select e.*` so their column list is frozen at view-creation time and
-- won't pick up `lineup` automatically. The API attaches the lineup with
-- a separate query (see app.py::_attach_lineup), so the views don't need
-- to be touched.

alter table events
    add column if not exists lineup text[];
