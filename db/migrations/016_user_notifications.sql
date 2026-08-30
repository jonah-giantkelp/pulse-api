-- In-app notification feed: one row per (user, event) announcement,
-- written by the digest job alongside the push send.
create table if not exists user_notifications (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    event_id uuid not null references events(id) on delete cascade,
    created_at timestamptz not null default now(),
    read_at timestamptz,
    unique (user_id, event_id)
);

create index if not exists idx_user_notifications_user
    on user_notifications(user_id, created_at desc);

-- RLS: users can see their own notifications (writes go through the
-- service-role API, which bypasses RLS).
alter table user_notifications enable row level security;

create policy "Users can view their own notifications"
    on user_notifications for select
    using (auth.uid() = user_id);
