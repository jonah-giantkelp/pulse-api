-- 003_event_overhaul.sql
-- Social post cursors, structured event metadata, event images,
-- cross-source dedup fingerprints, RLS policies, and iOS views.

-- ─────────────────────────────────────────────
-- 1. Social sync cursors
-- ─────────────────────────────────────────────

create table if not exists social_sync_cursors (
    id uuid primary key default gen_random_uuid(),
    artist_id uuid not null references artists(id) on delete cascade,
    platform text not null,
    last_post_id text,
    last_posted_at timestamptz,
    last_distilled_at timestamptz,  -- posts newer than this need AI analysis
    last_synced_at timestamptz not null default now(),
    unique (artist_id, platform)
);

create index if not exists idx_social_sync_cursors_artist
    on social_sync_cursors(artist_id, platform);

-- ─────────────────────────────────────────────
-- 2. New columns on events
-- ─────────────────────────────────────────────

alter table events
    add column if not exists date_precision text default 'exact',
    add column if not exists time text,
    add column if not exists artist_billing text,
    add column if not exists country text default 'GB',
    add column if not exists confidence text,
    add column if not exists fingerprint text;

create index if not exists idx_events_fingerprint
    on events(fingerprint);

-- ─────────────────────────────────────────────
-- 3. Event images (lineup posters, flyers, etc.)
-- ─────────────────────────────────────────────

create table if not exists event_images (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references events(id) on delete cascade,
    image_url text not null,
    source_post_id text,
    image_type text default 'poster',
    created_at timestamptz not null default now()
);

create index if not exists idx_event_images_event
    on event_images(event_id);

-- ─────────────────────────────────────────────
-- 4. RLS policies for iOS direct Supabase access
-- ─────────────────────────────────────────────

-- Enable RLS on tables that need it
alter table events enable row level security;
alter table social_posts enable row level security;
alter table artists enable row level security;
alter table social_summaries enable row level security;
alter table event_images enable row level security;

-- Events: users can read events for artists they subscribe to
create policy "Users can read events for their artists"
    on events for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- Social posts: same pattern
create policy "Users can read posts for their artists"
    on social_posts for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- Artists: users can read artists they subscribe to
create policy "Users can read their subscribed artists"
    on artists for select
    using (id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- Social summaries: same pattern
create policy "Users can read summaries for their artists"
    on social_summaries for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- Event images: users can read images for events they can see
create policy "Users can read event images"
    on event_images for select
    using (event_id in (
        select e.id from events e
        join user_artists ua on ua.artist_id = e.artist_id
        where ua.user_id = auth.uid()
    ));

-- Service role bypasses RLS, so the sync job (which uses service_role key)
-- can still write to all tables without needing insert/update policies.

-- ─────────────────────────────────────────────
-- 5. Views for common iOS queries
-- ─────────────────────────────────────────────

-- Upcoming events with artist info
create or replace view user_upcoming_events as
    select
        e.*,
        a.name as artist_name,
        a.image_url as artist_image
    from events e
    join artists a on a.id = e.artist_id
    where e.date >= now()
    order by e.date;

-- Event detail with artist platform links
create or replace view event_with_artist as
    select
        e.*,
        a.name as artist_name,
        a.image_url as artist_image,
        a.spotify_id,
        a.instagram_handle,
        a.twitter_handle,
        a.website_url
    from events e
    join artists a on a.id = e.artist_id;
