-- 015: account approval gate.
-- New accounts must be approved before the API serves them. A row is created
-- (approved=false) the first time an unknown user hits the API; existing
-- users are grandfathered in as approved.

create table if not exists user_approvals (
    user_id uuid primary key references auth.users(id) on delete cascade,
    approved boolean not null default false,
    requested_at timestamptz not null default now(),
    approved_at timestamptz
);

-- Grandfather every existing account so nobody currently using the app
-- (including the App Review demo account) gets locked out.
insert into user_approvals (user_id, approved, approved_at)
select id, true, now() from auth.users
on conflict (user_id) do nothing;
