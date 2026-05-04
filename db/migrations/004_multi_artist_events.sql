-- 004_multi_artist_events.sql
-- Introduce an event_artists junction table so a single event can be
-- linked to multiple tracked artists (e.g. two tracked DJs on the
-- same festival lineup).  The fingerprint is changed to be
-- artist-agnostic so the same real-world event gets ONE row.

-- ─────────────────────────────────────────────
-- 1. Junction table
-- ─────────────────────────────────────────────

create table if not exists event_artists (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references events(id) on delete cascade,
    artist_id uuid not null references artists(id) on delete cascade,
    billing text,  -- headline, support, b2b, dj_set, live, festival_slot
    source text,   -- which source linked this artist to this event
    created_at timestamptz not null default now(),

    unique (event_id, artist_id)
);

create index if not exists idx_event_artists_event
    on event_artists(event_id);
create index if not exists idx_event_artists_artist
    on event_artists(artist_id);

-- ─────────────────────────────────────────────
-- 2. Migrate existing events.artist_id → event_artists
-- ─────────────────────────────────────────────

insert into event_artists (event_id, artist_id, billing, source)
select
    e.id,
    e.artist_id,
    e.artist_billing,
    e.source
from events e
on conflict (event_id, artist_id) do nothing;

-- ─────────────────────────────────────────────
-- 3. Make artist_id nullable on events
--    (kept for now as a "primary artist" hint;
--     all queries should use event_artists instead)
-- ─────────────────────────────────────────────

alter table events
    alter column artist_id drop not null;

-- ─────────────────────────────────────────────
-- 4. Recalculate fingerprints (artist-agnostic)
--    New format: sha256(normalised_venue + date_bucket)[:16]
-- ─────────────────────────────────────────────

-- Clear old artist-specific fingerprints so the orchestrator
-- recalculates them on next sync.  We can't recompute in pure SQL
-- because the normalisation logic lives in Python.
update events set fingerprint = null;

-- ─────────────────────────────────────────────
-- 5. RLS policies for event_artists
-- ─────────────────────────────────────────────

alter table event_artists enable row level security;

create policy "Users can read event_artists for their artists"
    on event_artists for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- ─────────────────────────────────────────────
-- 6. Update views to use junction table
-- ─────────────────────────────────────────────

-- Drop old views that reference events.artist_id directly
drop view if exists user_upcoming_events;
drop view if exists event_with_artist;

-- Upcoming events with all linked artists (aggregated)
create or replace view user_upcoming_events as
    select
        e.*,
        json_agg(json_build_object(
            'artist_id', a.id,
            'name', a.name,
            'image_url', a.image_url,
            'billing', ea.billing
        )) as artists
    from events e
    join event_artists ea on ea.event_id = e.id
    join artists a on a.id = ea.artist_id
    where e.date >= now()
    group by e.id
    order by e.date;

-- Event detail with all linked artists
create or replace view event_with_artist as
    select
        e.*,
        json_agg(json_build_object(
            'artist_id', a.id,
            'name', a.name,
            'image_url', a.image_url,
            'billing', ea.billing,
            'spotify_id', a.spotify_id,
            'instagram_handle', a.instagram_handle,
            'twitter_handle', a.twitter_handle,
            'website_url', a.website_url
        )) as artists
    from events e
    join event_artists ea on ea.event_id = e.id
    join artists a on a.id = ea.artist_id
    group by e.id;

-- ─────────────────────────────────────────────
-- 7. Update event_images RLS to use junction table
-- ─────────────────────────────────────────────

drop policy if exists "Users can read event images" on event_images;

create policy "Users can read event images"
    on event_images for select
    using (event_id in (
        select ea.event_id from event_artists ea
        join user_artists ua on ua.artist_id = ea.artist_id
        where ua.user_id = auth.uid()
    ));
