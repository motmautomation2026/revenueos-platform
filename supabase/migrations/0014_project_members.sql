-- Share a project with team members, with full access.
--
-- Until now a project had one owner (revenueos_projects.owner_id) and only the owner could see or
-- change it. A project can now also have members: a member has the SAME full access as the owner
-- (read and write everything — uploads, the checklist, running the plan, answering the Brain's
-- decisions, the tool accounts). The owner is simply the admin who created the project; the admin's
-- only extra power is creating projects and assigning members.
--
-- This mirrors the CRM, where one workspace = one project and a workspace has many members. When a
-- salesperson is added to a client's CRM workspace, the CRM also adds them here (same auth user id),
-- so they get that client's project on the platform too.
--
-- Safe to run more than once: the table, index and policies are created only if missing, and the
-- policies are dropped first (including the read-only names from an earlier draft) so a re-run
-- always leaves the full-access set.

create table if not exists public.revenueos_project_members (
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  user_id uuid not null references auth.users (id) on delete cascade,
  added_by uuid references auth.users (id) on delete set null,
  added_at timestamptz not null default now(),
  primary key (project_id, user_id)
);
create index if not exists revenueos_project_members_user_id_idx on public.revenueos_project_members (user_id);

alter table public.revenueos_project_members enable row level security;

-- Owner or member: may this user use the project? SECURITY DEFINER so its reads bypass RLS: it is
-- used in the policy ON revenueos_projects, and a plain function would re-trigger that policy when
-- it selects revenueos_projects, causing infinite recursion for a member (the owner matches the
-- owner-only policy first and never recurses). It only ever returns a boolean about the caller's
-- own access (auth.uid() still reads the caller's JWT inside a definer function), so it leaks nothing.
create or replace function public.revenueos_can_access_project(p_project_id uuid)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.revenueos_projects p
    where p.id = p_project_id
      and (
        p.owner_id = (select auth.uid())
        or exists (
          select 1 from public.revenueos_project_members m
          where m.project_id = p.id and m.user_id = (select auth.uid())
        )
      )
  );
$$;

-- Drop any earlier version of these policies (both the read-only draft names and the current ones)
-- so this file can be run again cleanly.
drop policy if exists "revenueos_project_members: own rows select" on public.revenueos_project_members;
drop policy if exists "revenueos_project_members: owner manage" on public.revenueos_project_members;
drop policy if exists "revenueos_projects: member select" on public.revenueos_projects;
drop policy if exists "revenueos_projects: member all" on public.revenueos_projects;
drop policy if exists "revenueos_project_files: member select" on public.revenueos_project_files;
drop policy if exists "revenueos_project_files: member all" on public.revenueos_project_files;
drop policy if exists "revenueos_plans: member select" on public.revenueos_plans;
drop policy if exists "revenueos_plans: member all" on public.revenueos_plans;
drop policy if exists "revenueos_agent_runs: member select" on public.revenueos_agent_runs;
drop policy if exists "revenueos_agent_runs: member all" on public.revenueos_agent_runs;
drop policy if exists "revenueos_checklists: member select" on public.revenueos_checklists;
drop policy if exists "revenueos_checklists: member all" on public.revenueos_checklists;
drop policy if exists "revenueos_checklist_items: member select" on public.revenueos_checklist_items;
drop policy if exists "revenueos_checklist_items: member all" on public.revenueos_checklist_items;
drop policy if exists "revenueos-project-files: member select" on storage.objects;
drop policy if exists "revenueos-project-files: member read" on storage.objects;
drop policy if exists "revenueos-project-files: member insert" on storage.objects;
drop policy if exists "revenueos-project-files: member update" on storage.objects;
drop policy if exists "revenueos-project-files: member delete" on storage.objects;

-- Membership rows: a user sees their own; the project's owner (and the CRM, as crm_app) manages
-- who the members are. Members do not add other members: assigning salespeople is the admin's job.
create policy "revenueos_project_members: own rows select" on public.revenueos_project_members
  for select to authenticated using (user_id = (select auth.uid()));
create policy "revenueos_project_members: owner manage" on public.revenueos_project_members
  for all to authenticated
  using (public.revenueos_owns_project(project_id)) with check (public.revenueos_owns_project(project_id));

-- Members get the SAME full access as the owner to the project and its child rows. These sit next
-- to the owner "all" policies (0001); a row passes if either policy allows it.
create policy "revenueos_projects: member all" on public.revenueos_projects
  for all to authenticated
  using (public.revenueos_can_access_project(id)) with check (public.revenueos_can_access_project(id));
create policy "revenueos_project_files: member all" on public.revenueos_project_files
  for all to authenticated
  using (public.revenueos_can_access_project(project_id)) with check (public.revenueos_can_access_project(project_id));
create policy "revenueos_plans: member all" on public.revenueos_plans
  for all to authenticated
  using (public.revenueos_can_access_project(project_id)) with check (public.revenueos_can_access_project(project_id));
create policy "revenueos_agent_runs: member all" on public.revenueos_agent_runs
  for all to authenticated
  using (public.revenueos_can_access_project(project_id)) with check (public.revenueos_can_access_project(project_id));
create policy "revenueos_checklists: member all" on public.revenueos_checklists
  for all to authenticated
  using (public.revenueos_can_access_project(project_id)) with check (public.revenueos_can_access_project(project_id));
create policy "revenueos_checklist_items: member all" on public.revenueos_checklist_items
  for all to authenticated
  using (exists (
    select 1 from public.revenueos_checklists c
    where c.id = checklist_id and public.revenueos_can_access_project(c.project_id)
  ))
  with check (exists (
    select 1 from public.revenueos_checklists c
    where c.id = checklist_id and public.revenueos_can_access_project(c.project_id)
  ));

-- Members get full access to the project's uploaded files too. Path is
-- {owner_id}/{project_id}/{uuid}-{name}, so the project id is the second folder segment.
create policy "revenueos-project-files: member read" on storage.objects
  for select to authenticated
  using (bucket_id = 'revenueos-project-files' and public.revenueos_can_access_project(((storage.foldername(name))[2])::uuid));
create policy "revenueos-project-files: member insert" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'revenueos-project-files' and public.revenueos_can_access_project(((storage.foldername(name))[2])::uuid));
create policy "revenueos-project-files: member update" on storage.objects
  for update to authenticated
  using (bucket_id = 'revenueos-project-files' and public.revenueos_can_access_project(((storage.foldername(name))[2])::uuid))
  with check (bucket_id = 'revenueos-project-files' and public.revenueos_can_access_project(((storage.foldername(name))[2])::uuid));
create policy "revenueos-project-files: member delete" on storage.objects
  for delete to authenticated
  using (bucket_id = 'revenueos-project-files' and public.revenueos_can_access_project(((storage.foldername(name))[2])::uuid));
