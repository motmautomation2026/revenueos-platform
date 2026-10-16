-- Archiving a project: it leaves the main Projects list and can be restored at any time.
-- Nothing is deleted: documents, checklist, plan and the Brain's results all stay.

alter table public.revenueos_projects add column if not exists archived_at timestamptz;

comment on column public.revenueos_projects.archived_at is
  'When the project was archived; null while it is active. Archiving hides it from the main list and blocks starting execution; nothing is deleted.';

create index if not exists revenueos_projects_owner_active_idx
  on public.revenueos_projects (owner_id, updated_at desc)
  where archived_at is null;
