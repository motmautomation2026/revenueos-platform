-- MOTM GTM Planner: initial schema, RLS and storage policies.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
create type public.revenueos_project_status as enum (
  'draft', 'uploading', 'checklist_generating', 'checklist_ready',
  'checklist_submitted', 'plan_running', 'plan_blocked', 'plan_ready', 'failed'
);
create type public.revenueos_doc_type as enum (
  'bd_handover', 'bd_proposal', 'signed_scope', 'cam_notes', 'filled_checklist', 'other'
);
create type public.revenueos_extraction_status as enum ('pending', 'done', 'failed');
create type public.revenueos_checklist_status as enum ('draft', 'submitted');
create type public.revenueos_plan_status as enum (
  'queued', 'diagnosis_running', 'blocked', 'strategy_running',
  'execution_running', 'ready', 'failed'
);
create type public.revenueos_agent_name as enum ('checklist', 'diagnosis', 'strategy', 'execution');
create type public.revenueos_agent_run_status as enum ('running', 'ok', 'repaired', 'failed');

-- ---------------------------------------------------------------------------
-- updated_at trigger
-- ---------------------------------------------------------------------------
create function public.revenueos_set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table public.revenueos_profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  full_name text,
  email text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table public.revenueos_projects (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  customer_name text not null,
  engagement_type text not null,
  description text,
  status public.revenueos_project_status not null default 'draft',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index revenueos_projects_owner_id_idx on public.revenueos_projects (owner_id, updated_at desc);

create table public.revenueos_project_files (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  owner_id uuid not null default auth.uid() references auth.users (id) on delete cascade,
  doc_type public.revenueos_doc_type not null default 'other',
  file_name text not null,
  storage_path text not null unique,
  mime_type text,
  size_bytes bigint,
  extracted_text text,
  char_count integer not null default 0,
  extraction_status public.revenueos_extraction_status not null default 'pending',
  extraction_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index revenueos_project_files_project_id_idx on public.revenueos_project_files (project_id);
create index revenueos_project_files_owner_id_idx on public.revenueos_project_files (owner_id);

create table public.revenueos_plans (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  version integer not null,
  status public.revenueos_plan_status not null default 'queued',
  diagnosis jsonb,
  strategy jsonb,
  execution jsonb,
  blockers jsonb,
  override_reason text,
  html text,
  error text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (project_id, version)
);
-- One running plan per project at a time.
create unique index revenueos_plans_one_running_per_project_idx on public.revenueos_plans (project_id)
  where status in ('queued', 'diagnosis_running', 'strategy_running', 'execution_running');

create table public.revenueos_agent_runs (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  plan_id uuid references public.revenueos_plans (id) on delete set null,
  agent public.revenueos_agent_name not null,
  model text,
  prompt_file text,
  prompt_hash text,
  input_chars integer,
  raw_output text,
  parsed_output jsonb,
  repairs jsonb,
  validation_errors jsonb,
  prompt_tokens integer,
  completion_tokens integer,
  duration_ms integer,
  status public.revenueos_agent_run_status not null default 'running',
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index revenueos_agent_runs_project_id_idx on public.revenueos_agent_runs (project_id, created_at desc);
create index revenueos_agent_runs_plan_id_idx on public.revenueos_agent_runs (plan_id);

create table public.revenueos_checklists (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  is_active boolean not null default true,
  known_facts jsonb,
  flags jsonb,
  cam_notes jsonb,
  status public.revenueos_checklist_status not null default 'draft',
  submitted_at timestamptz,
  agent_run_id uuid references public.revenueos_agent_runs (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
-- One active checklist per project; older ones are kept with is_active = false.
create unique index revenueos_checklists_one_active_per_project_idx on public.revenueos_checklists (project_id)
  where is_active;
create index revenueos_checklists_project_id_idx on public.revenueos_checklists (project_id);
create index revenueos_checklists_agent_run_id_idx on public.revenueos_checklists (agent_run_id);

create table public.revenueos_checklist_items (
  id uuid primary key default gen_random_uuid(),
  checklist_id uuid not null references public.revenueos_checklists (id) on delete cascade,
  position integer not null,
  code text not null,
  category text not null,
  question text not null,
  options text[] not null default '{}',
  prefilled_answer text,
  answer text,
  reason text,
  answered_by uuid references auth.users (id) on delete set null,
  answered_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index revenueos_checklist_items_checklist_id_idx on public.revenueos_checklist_items (checklist_id, position);
create index revenueos_checklist_items_answered_by_idx on public.revenueos_checklist_items (answered_by);

create trigger set_updated_at before update on public.revenueos_profiles
  for each row execute function public.revenueos_set_updated_at();
create trigger set_updated_at before update on public.revenueos_projects
  for each row execute function public.revenueos_set_updated_at();
create trigger set_updated_at before update on public.revenueos_project_files
  for each row execute function public.revenueos_set_updated_at();
create trigger set_updated_at before update on public.revenueos_plans
  for each row execute function public.revenueos_set_updated_at();
create trigger set_updated_at before update on public.revenueos_agent_runs
  for each row execute function public.revenueos_set_updated_at();
create trigger set_updated_at before update on public.revenueos_checklists
  for each row execute function public.revenueos_set_updated_at();
create trigger set_updated_at before update on public.revenueos_checklist_items
  for each row execute function public.revenueos_set_updated_at();

-- ---------------------------------------------------------------------------
-- Profile row on sign-up
-- ---------------------------------------------------------------------------
create function public.revenueos_handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.revenueos_profiles (id, full_name, email)
  values (new.id, new.raw_user_meta_data ->> 'full_name', new.email)
  on conflict (id) do nothing;
  return new;
end;
$$;
revoke execute on function public.revenueos_handle_new_user() from public, anon, authenticated;

create trigger revenueos_on_auth_user_created
  after insert on auth.users
  for each row execute function public.revenueos_handle_new_user();

-- Users who signed up before this migration (the database is shared with other apps).
insert into public.revenueos_profiles (id, full_name, email)
select u.id, u.raw_user_meta_data ->> 'full_name', u.email
from auth.users u
on conflict (id) do nothing;

-- ---------------------------------------------------------------------------
-- Row level security
-- ---------------------------------------------------------------------------
alter table public.revenueos_profiles enable row level security;
alter table public.revenueos_projects enable row level security;
alter table public.revenueos_project_files enable row level security;
alter table public.revenueos_plans enable row level security;
alter table public.revenueos_agent_runs enable row level security;
alter table public.revenueos_checklists enable row level security;
alter table public.revenueos_checklist_items enable row level security;

create function public.revenueos_owns_project(p_project_id uuid)
returns boolean
language sql
stable
set search_path = ''
as $$
  select exists (
    select 1 from public.revenueos_projects p
    where p.id = p_project_id and p.owner_id = (select auth.uid())
  );
$$;

create policy "revenueos_profiles: own row select" on public.revenueos_profiles
  for select to authenticated using (id = (select auth.uid()));
create policy "revenueos_profiles: own row update" on public.revenueos_profiles
  for update to authenticated
  using (id = (select auth.uid())) with check (id = (select auth.uid()));

create policy "revenueos_projects: owner all" on public.revenueos_projects
  for all to authenticated
  using (owner_id = (select auth.uid())) with check (owner_id = (select auth.uid()));

create policy "revenueos_project_files: owner all" on public.revenueos_project_files
  for all to authenticated
  using (owner_id = (select auth.uid()) and public.revenueos_owns_project(project_id))
  with check (owner_id = (select auth.uid()) and public.revenueos_owns_project(project_id));

create policy "revenueos_plans: project owner all" on public.revenueos_plans
  for all to authenticated
  using (public.revenueos_owns_project(project_id)) with check (public.revenueos_owns_project(project_id));

create policy "revenueos_agent_runs: project owner all" on public.revenueos_agent_runs
  for all to authenticated
  using (public.revenueos_owns_project(project_id)) with check (public.revenueos_owns_project(project_id));

create policy "revenueos_checklists: project owner all" on public.revenueos_checklists
  for all to authenticated
  using (public.revenueos_owns_project(project_id)) with check (public.revenueos_owns_project(project_id));

create policy "revenueos_checklist_items: project owner all" on public.revenueos_checklist_items
  for all to authenticated
  using (exists (
    select 1 from public.revenueos_checklists c
    where c.id = checklist_id and public.revenueos_owns_project(c.project_id)
  ))
  with check (exists (
    select 1 from public.revenueos_checklists c
    where c.id = checklist_id and public.revenueos_owns_project(c.project_id)
  ));

-- ---------------------------------------------------------------------------
-- Storage: private bucket, path {owner_id}/{project_id}/{uuid}-{file_name}
-- ---------------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit)
values ('revenueos-project-files', 'revenueos-project-files', false, 20971520)
on conflict (id) do nothing;

create policy "revenueos-project-files: owner select" on storage.objects
  for select to authenticated
  using (bucket_id = 'revenueos-project-files' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "revenueos-project-files: owner insert" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'revenueos-project-files' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "revenueos-project-files: owner update" on storage.objects
  for update to authenticated
  using (bucket_id = 'revenueos-project-files' and (storage.foldername(name))[1] = (select auth.uid())::text)
  with check (bucket_id = 'revenueos-project-files' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "revenueos-project-files: owner delete" on storage.objects
  for delete to authenticated
  using (bucket_id = 'revenueos-project-files' and (storage.foldername(name))[1] = (select auth.uid())::text);
