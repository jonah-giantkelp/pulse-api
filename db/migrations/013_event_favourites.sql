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

-- RLS: users can manage their own favourites
alter table user_event_favourites enable row level security;

create policy "Users can view their own favourites"
    on user_event_favourites for select
    using (auth.uid() = user_id);

create policy "Users can insert their own favourites"
    on user_event_favourites for insert
    with check (auth.uid() = user_id);

create policy "Users can delete their own favourites"
    on user_event_favourites for delete
    using (auth.uid() = user_id);
