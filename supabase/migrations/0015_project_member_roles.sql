-- A member's role: a full-access salesperson, or a read-only client.
--
-- Until now every member row (0014) meant the same thing: full access to the platform project AND
-- that project's CRM workspace, exactly like the owner. A project now also needs *clients* — the end
-- customer who should only ever see the CRM, read-only. A client gets NO platform access and is made
-- read-only in the CRM. The two are told apart by this one column:
--   'salesperson' (default) — full access, as before.
--   'client'                — CRM only, read-only (enforced in the CRM; excluded from the platform here).
--
-- The platform exclusion is done in exactly one place: revenueos_can_access_project(), the function
-- every "member" policy from 0014 uses. Adding `and m.role <> 'client'` there drops clients from the
-- project and all its child rows (files, plans, checklist, agent runs, storage) at once. The CRM reads
-- the same column separately (crm_app has select on this table) to grant the client workspace access
-- and mark them read-only — so this change does not affect the CRM.
--
-- Safe to run more than once: the column is added only if missing, and the function is replaced.

alter table public.revenueos_project_members
  add column if not exists role text not null default 'salesperson'
  check (role in ('salesperson', 'client'));

-- Owner or FULL-ACCESS member: may this user use the platform project? Clients (role = 'client') are
-- deliberately excluded — they have no platform access. Otherwise identical to 0014 (SECURITY DEFINER
-- so its reads bypass RLS and don't recurse through the policy on revenueos_projects).
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
          where m.project_id = p.id
            and m.user_id = (select auth.uid())
            and m.role <> 'client'
        )
      )
  );
$$;
