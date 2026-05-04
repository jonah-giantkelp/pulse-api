-- User email preferences for daily digest
create table if not exists user_email_preferences (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade unique,
    email text not null,
    digest_enabled boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_user_email_prefs_user
    on user_email_preferences(user_id);

-- Log of sent digests (prevents double-sends and tracks what was included)
create table if not exists email_digest_log (
    id uuid primary key default gen_random_uuid(),
    user_id uuid not null references auth.users(id) on delete cascade,
    events_sent int not null default 0,
    sent_at timestamptz not null default now()
);

create index if not exists idx_email_digest_log_user
    on email_digest_log(user_id, sent_at);

-- RLS: users can manage their own email preferences
alter table user_email_preferences enable row level security;

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
