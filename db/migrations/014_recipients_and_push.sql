-- Newsletter recipients (multiple emails) + push notification preference
alter table user_email_preferences
    add column if not exists recipients text[] not null default '{}';
alter table user_email_preferences
    add column if not exists push_enabled boolean not null default false;

-- Backfill: the existing single email becomes the first recipient
update user_email_preferences
    set recipients = array[email]
    where email is not null and recipients = '{}';

-- Device tokens for push notifications
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

create policy "Users can view their own push tokens"
    on push_tokens for select
    using (auth.uid() = user_id);

create policy "Users can insert their own push tokens"
    on push_tokens for insert
    with check (auth.uid() = user_id);

create policy "Users can delete their own push tokens"
    on push_tokens for delete
    using (auth.uid() = user_id);
