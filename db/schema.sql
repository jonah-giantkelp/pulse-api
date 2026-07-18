-- Pulse API schema
-- Consolidated from migrations 001–010. This file represents the target state
-- of the database. It does NOT include the data backfill / fingerprint-reset
-- statements that appear inside individual migrations — those are one-off ops.
--
-- To bootstrap a fresh database: run this file, then any future migrations
-- (011+). To migrate an existing database from an older schema: run the
-- numbered migrations in db/migrations/ in order.

-- ─────────────────────────────────────────────
-- Artists: global source of truth (shared across users)
-- ─────────────────────────────────────────────
create table if not exists artists (
    id uuid primary key default gen_random_uuid(),
    name text not null,
    musicbrainz_id text unique,

    -- Platform identifiers (null = not yet linked)
    spotify_id text,
    ticketmaster_id text,
    bandsintown_name text,
    instagram_handle text,
    twitter_handle text,
    skiddle_id text,
    concerts_tracker_id text,
    songkick_id text,
    dice_slug text,
    ra_id text,

    -- Website
    website_url text,

    -- Metadata (enriched from Spotify)
    genres text[],
    image_url text,

    -- Tracking
    active boolean not null default true,
    last_synced_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- User-artist subscriptions (which users track which artists)
-- ─────────────────────────────────────────────
create table if not exists user_artists (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    artist_id uuid not null references artists(id) on delete cascade,
    city text default null,            -- NULL = all cities
    notify boolean not null default true,
    created_at timestamptz not null default now(),

    unique (user_id, artist_id)
);

-- ─────────────────────────────────────────────
-- Artist resolution audit trail
-- ─────────────────────────────────────────────
create table if not exists artist_resolutions (
    id uuid primary key default gen_random_uuid(),
    artist_id uuid not null references artists(id) on delete cascade,
    platform text not null,
    status text not null check (status in ('resolved', 'ambiguous', 'not_found')),
    confidence text check (confidence in ('high', 'medium', 'low')),
    candidates jsonb,
    resolved_at timestamptz,
    resolved_by text check (resolved_by in ('ai', 'user', 'musicbrainz')),
    created_at timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- Events from all sources, deduplicated
-- ─────────────────────────────────────────────
create table if not exists events (
    id uuid primary key default gen_random_uuid(),
    -- Primary-artist hint (kept for back-compat). All multi-artist queries
    -- should go through event_artists instead.
    artist_id uuid references artists(id) on delete cascade,
    title text not null,
    date timestamptz not null,
    date_precision text default 'exact',
    time text,
    venue text,
    city text not null default 'Unknown',
    country text default 'GB',
    source text not null,
    source_id text,
    ticket_url text,
    raw_data jsonb,
    source_post_ids text[],            -- platform post IDs that produced this event
    artist_billing text,
    confidence text,
    fingerprint text,                  -- strict: venue+city+date (cross-artist linking)
    fingerprint_loose text,            -- loose: city+date only (same-artist dedup)
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),

    unique (source, source_id)
);

-- ─────────────────────────────────────────────
-- Multi-artist event junction
-- ─────────────────────────────────────────────
create table if not exists event_artists (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references events(id) on delete cascade,
    artist_id uuid not null references artists(id) on delete cascade,
    billing text,                      -- headline, support, b2b, dj_set, live, festival_slot
    source text,                       -- which source linked this artist to this event
    created_at timestamptz not null default now(),

    unique (event_id, artist_id)
);

-- ─────────────────────────────────────────────
-- External IDs per event (for cross-source dedup)
-- ─────────────────────────────────────────────
create table if not exists event_external_ids (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references events(id) on delete cascade,
    source text not null,              -- 'ra', 'dice', 'ticketmaster', etc.
    external_id text not null,         -- the platform's event ID
    ticket_url text,
    price_min numeric,
    price_max numeric,
    currency text,
    created_at timestamptz not null default now(),

    unique (source, external_id)
);

-- ─────────────────────────────────────────────
-- Event images (lineup posters, flyers, etc.)
-- ─────────────────────────────────────────────
create table if not exists event_images (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references events(id) on delete cascade,
    image_url text not null,
    source_post_id text,
    image_type text default 'poster',
    created_at timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- Social media posts (Instagram + Twitter)
-- ─────────────────────────────────────────────
create table if not exists social_posts (
    id uuid primary key default gen_random_uuid(),
    artist_id uuid not null references artists(id) on delete cascade,
    platform text not null,
    post_id text not null,
    caption text,
    media_url text,
    posted_at timestamptz,
    raw_data jsonb,
    created_at timestamptz not null default now(),

    unique (platform, post_id)
);

-- ─────────────────────────────────────────────
-- Social sync cursors (per artist / per platform)
-- ─────────────────────────────────────────────
create table if not exists social_sync_cursors (
    id uuid primary key default gen_random_uuid(),
    artist_id uuid not null references artists(id) on delete cascade,
    platform text not null,
    last_post_id text,
    last_posted_at timestamptz,
    last_distilled_at timestamptz,     -- posts newer than this need AI analysis
    last_synced_at timestamptz not null default now(),

    unique (artist_id, platform)
);

-- ─────────────────────────────────────────────
-- AI-distilled summaries of social activity
-- ─────────────────────────────────────────────
create table if not exists social_summaries (
    id uuid primary key default gen_random_uuid(),
    artist_id uuid not null references artists(id) on delete cascade,
    summary text not null,
    date date not null,
    model_used text,
    source_post_ids uuid[],
    created_at timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- Sync log for daily runs
-- ─────────────────────────────────────────────
create table if not exists sync_log (
    id uuid primary key default gen_random_uuid(),
    artist_id uuid not null references artists(id) on delete cascade,
    source text not null,
    status text not null check (status in ('success', 'error', 'skipped')),
    error_message text,
    events_found int default 0,
    posts_found int default 0,
    synced_at timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- Email digest preferences + send log
-- ─────────────────────────────────────────────
create table if not exists user_email_preferences (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade unique,
    email text not null,
    digest_enabled boolean not null default true,
    default_cities text[] not null default '{}',
    default_countries text[] not null default '{}',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists email_digest_log (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    events_sent int not null default 0,
    sent_at timestamptz not null default now()
);

-- ─────────────────────────────────────────────
-- Indexes
-- ─────────────────────────────────────────────
create index if not exists idx_user_artists_user
    on user_artists(user_id);
create index if not exists idx_user_artists_artist
    on user_artists(artist_id);
create index if not exists idx_events_artist_date
    on events(artist_id, date);
create index if not exists idx_events_source
    on events(source, source_id);
create index if not exists idx_events_fingerprint
    on events(fingerprint);
create index if not exists idx_events_fingerprint_loose
    on events(fingerprint_loose);
create index if not exists idx_event_artists_event
    on event_artists(event_id);
create index if not exists idx_event_artists_artist
    on event_artists(artist_id);
create index if not exists idx_event_external_ids_event
    on event_external_ids(event_id);
create index if not exists idx_event_external_ids_lookup
    on event_external_ids(source, external_id);
create index if not exists idx_event_images_event
    on event_images(event_id);
create index if not exists idx_social_posts_artist
    on social_posts(artist_id, posted_at);
create index if not exists idx_social_sync_cursors_artist
    on social_sync_cursors(artist_id, platform);
create index if not exists idx_sync_log_artist
    on sync_log(artist_id, synced_at);
create index if not exists idx_artist_resolutions_artist
    on artist_resolutions(artist_id);
create index if not exists idx_user_email_prefs_user
    on user_email_preferences(user_id);
create index if not exists idx_email_digest_log_user
    on email_digest_log(user_id, sent_at);

-- ─────────────────────────────────────────────
-- Row-Level Security (Supabase Auth)
--
-- The sync job uses the service_role key, which bypasses RLS, so we only
-- need read policies on the data tables and full CRUD on user-owned tables.
-- ─────────────────────────────────────────────
alter table artists                enable row level security;
alter table user_artists           enable row level security;
alter table events                 enable row level security;
alter table event_artists          enable row level security;
alter table event_images           enable row level security;
alter table social_posts           enable row level security;
alter table social_summaries       enable row level security;
alter table user_email_preferences enable row level security;

-- user_artists: full CRUD for the owning user
create policy "Users can view their own subscriptions"
    on user_artists for select
    using (auth.uid() = user_id);

create policy "Users can insert their own subscriptions"
    on user_artists for insert
    with check (auth.uid() = user_id);

create policy "Users can delete their own subscriptions"
    on user_artists for delete
    using (auth.uid() = user_id);

-- artists: read-only, scoped to subscribed artists
create policy "Users can read their subscribed artists"
    on artists for select
    using (id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- events: read-only, scoped via the event_artists junction OR primary artist
create policy "Users can read events for their artists"
    on events for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- event_artists: read-only, scoped to subscribed artists
create policy "Users can read event_artists for their artists"
    on event_artists for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- event_images: read-only, scoped via event_artists junction
create policy "Users can read event images"
    on event_images for select
    using (event_id in (
        select ea.event_id from event_artists ea
        join user_artists ua on ua.artist_id = ea.artist_id
        where ua.user_id = auth.uid()
    ));

-- social_posts: read-only, scoped to subscribed artists
create policy "Users can read posts for their artists"
    on social_posts for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- social_summaries: read-only, scoped to subscribed artists
create policy "Users can read summaries for their artists"
    on social_summaries for select
    using (artist_id in (
        select artist_id from user_artists where user_id = auth.uid()
    ));

-- user_email_preferences: full CRUD for the owning user
create policy "Users can view their own email preferences"
    on user_email_preferences for select
    using (auth.uid() = user_id);

create policy "Users can insert their own email preferences"
    on user_email_preferences for insert
    with check (auth.uid() = user_id);

create policy "Users can update their own email preferences"
    on user_email_preferences for update
    using (auth.uid() = user_id);

create policy "Users can delete their own email preferences"
    on user_email_preferences for delete
    using (auth.uid() = user_id);

-- ─────────────────────────────────────────────
-- Auto-create email preferences on signup
-- ─────────────────────────────────────────────
-- Fires on new auth.users rows; defaults default_cities to {'London'}.
create or replace function public.handle_new_user_email_prefs()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.email is not null and new.email <> '' then
        insert into public.user_email_preferences
            (user_id, email, default_cities)
        values
            (new.id, new.email, ARRAY['London'])
        on conflict (user_id) do nothing;
    end if;
    return new;
end;
$$;

drop trigger if exists on_auth_user_created_email_prefs on auth.users;
create trigger on_auth_user_created_email_prefs
    after insert on auth.users
    for each row
    execute function public.handle_new_user_email_prefs();

-- ─────────────────────────────────────────────
-- Views (iOS / client convenience)
-- ─────────────────────────────────────────────

-- Upcoming events with all linked artists (aggregated)
create or replace view user_upcoming_events as
    select
        e.*,
        json_agg(json_build_object(
            'artist_id', a.id,
            'name',      a.name,
            'image_url', a.image_url,
            'billing',   ea.billing
        )) as artists
    from events e
    join event_artists ea on ea.event_id = e.id
    join artists a        on a.id        = ea.artist_id
    where e.date >= now()
    group by e.id
    order by e.date;

-- Event detail with all linked artists (incl. platform handles)
create or replace view event_with_artist as
    select
        e.*,
        json_agg(json_build_object(
            'artist_id',        a.id,
            'name',             a.name,
            'image_url',        a.image_url,
            'billing',          ea.billing,
            'spotify_id',       a.spotify_id,
            'instagram_handle', a.instagram_handle,
            'twitter_handle',   a.twitter_handle,
            'website_url',      a.website_url
        )) as artists
    from events e
    join event_artists ea on ea.event_id = e.id
    join artists a        on a.id        = ea.artist_id
    group by e.id;

-- User favourite events
create table if not exists user_event_favourites (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    event_id uuid not null references events(id) on delete cascade,
    created_at timestamptz not null default now(),
    unique (user_id, event_id)
);

create index if not exists idx_user_event_favourites_user
    on user_event_favourites(user_id);

alter table user_event_favourites enable row level security;

-- Newsletter recipients + push preference (migration 014)
-- recipients text[] and push_enabled boolean live on user_email_preferences

create table if not exists push_tokens (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    device_token text not null,
    platform text not null default 'ios',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, device_token)
);

create index if not exists idx_push_tokens_user on push_tokens(user_id);
alter table push_tokens enable row level security;
