-- Every project gets its own Brain customer key, automatically, the moment it is created.
--
-- The Brain files a customer's runs, companies and contacts under a "customer key" (lowercase
-- letters, digits and single dashes). Until now that key and the project it belongs to were
-- typed into a settings file. Storing the key on the project lets the app find a project's
-- runs, and lets the Brain pick its project, straight from the database.
--
-- The key never changes once set: the Brain's data is filed under it.

alter table public.revenueos_projects add column if not exists brain_customer_key text;

create unique index if not exists revenueos_projects_brain_customer_key_idx
  on public.revenueos_projects (brain_customer_key)
  where brain_customer_key is not null;

comment on column public.revenueos_projects.brain_customer_key is
  'The Brain''s customer key for this project (lowercase, digits, single dashes). Set automatically on insert and never changed. Example: example-co-60d911';

create or replace function public.revenueos_brain_key_for(p_name text, p_id uuid)
returns text
language sql
immutable
set search_path = ''
as $$
  -- "Example Co. (India)" + id 60d91130-... -> "example-co-india-60d911"
  select coalesce(
           nullif(trim(both '-' from left(trim(both '-' from regexp_replace(lower(coalesce(p_name, '')), '[^a-z0-9]+', '-', 'g')), 40)), ''),
           'customer'
         )
         || '-' || left(replace(p_id::text, '-', ''), 6);
$$;

-- The project that was already running in the Brain keeps the key its data is filed under.
-- It is identified without guessing: the Brain has exactly one customer so far, and exactly one
-- project existed when that customer's first run was created. If either is not true (several
-- Brain customers, or several projects that old), nothing is linked here and the keys must be
-- set by hand before the trigger below locks them.
update public.revenueos_projects p
set brain_customer_key = (select min(r.customer) from public.brain_customer_runs r)
where p.brain_customer_key is null
  and (select count(distinct r.customer) from public.brain_customer_runs r) = 1
  and p.created_at < (select min(r.created_at) from public.brain_customer_runs r)
  and (
    select count(*) from public.revenueos_projects older
    where older.created_at < (select min(r.created_at) from public.brain_customer_runs r)
  ) = 1;

-- Any other existing project gets a fresh key.
update public.revenueos_projects p
set brain_customer_key = public.revenueos_brain_key_for(p.customer_name, p.id)
where p.brain_customer_key is null;

create or replace function public.revenueos_set_brain_customer_key()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if tg_op = 'INSERT' then
    -- Always generated here, never taken from the request, so nobody can claim another customer's key.
    new.brain_customer_key := public.revenueos_brain_key_for(new.customer_name, new.id);
  elsif old.brain_customer_key is not null and new.brain_customer_key is distinct from old.brain_customer_key then
    raise exception 'brain_customer_key cannot be changed once set: the Brain''s data is filed under it';
  end if;
  return new;
end;
$$;

drop trigger if exists revenueos_projects_brain_key on public.revenueos_projects;
create trigger revenueos_projects_brain_key
  before insert or update of brain_customer_key on public.revenueos_projects
  for each row execute function public.revenueos_set_brain_customer_key();

-- ---------------------------------------------------------------------------
-- Starting execution from the app
-- ---------------------------------------------------------------------------
--
-- One row per "Start execution" click. It is the mailbox between the app and the Brain:
--   the app      creates the row, and later records the person's answer to the month plan
--   the Brain    claims the row, publishes the month plan it wrote, and reports progress
--
-- This is the ONE revenueos_* table the Brain writes to. Both sides use the server-side key;
-- there are no policies, so the browser can never read or change it directly.

create table if not exists public.revenueos_brain_requests (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  customer_key text not null,
  -- requested -> planning -> awaiting_approval -> finishing -> loaded
  --                                  (failed or cancelled can end it at any point)
  status text not null default 'requested'
    check (status in ('requested', 'planning', 'awaiting_approval', 'finishing', 'loaded', 'failed', 'cancelled')),
  month text,                       -- the Brain's month for the run, e.g. 2026-10 or 2026-10-r2
  test_mode boolean,                -- as set in the Brain when it claimed the request
  requested_by uuid references auth.users (id) on delete set null,
  requested_by_name text,

  -- Published by the Brain when the month plan is ready to be approved.
  plan jsonb,
  revenue_analysis jsonb,
  plan_errors jsonb not null default '[]',
  brain_message text,               -- e.g. why the last answer was refused

  -- Recorded by the app; consumed (and cleared) by the Brain.
  answer text check (answer in ('yes', 'override', 'changes')),
  answer_text text,
  answered_by text,
  answered_at timestamptz,

  -- The answer that finally approved the plan, kept for the record.
  approved_by text,
  approved_at timestamptz,

  failed_step text,
  error text,
  worker text,
  heartbeat_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One request in progress per project.
create unique index if not exists revenueos_brain_requests_one_active_idx
  on public.revenueos_brain_requests (project_id)
  where status in ('requested', 'planning', 'awaiting_approval', 'finishing');
create index if not exists revenueos_brain_requests_project_idx
  on public.revenueos_brain_requests (project_id, created_at desc);

drop trigger if exists set_updated_at on public.revenueos_brain_requests;
create trigger set_updated_at before update on public.revenueos_brain_requests
  for each row execute function public.revenueos_set_updated_at();

alter table public.revenueos_brain_requests enable row level security;
revoke all on public.revenueos_brain_requests from anon, authenticated;
