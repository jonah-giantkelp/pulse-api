-- 005_event_external_ids.sql
-- Store all known external IDs for an event (RA, DICE, etc.)
-- so cross-source and cross-artist dedup can match on platform event IDs.

create table if not exists event_external_ids (
    id uuid primary key default gen_random_uuid(),
    event_id uuid not null references events(id) on delete cascade,
    source text not null,        -- 'ra', 'dice', 'ticketmaster', etc.
    external_id text not null,   -- the platform's event ID
    ticket_url text,             -- ticket link for this source
    created_at timestamptz not null default now(),
    unique (source, external_id)
);

create index if not exists idx_event_external_ids_event
    on event_external_ids(event_id);

create index if not exists idx_event_external_ids_lookup
    on event_external_ids(source, external_id);

-- Backfill from existing events
insert into event_external_ids (event_id, source, external_id, ticket_url)
    select id, source, source_id, ticket_url
    from events
    where source_id is not null
on conflict (source, external_id) do nothing;
