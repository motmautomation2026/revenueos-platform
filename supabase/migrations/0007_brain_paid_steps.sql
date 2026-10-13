-- Approving a step that buys data, from the app.
--
-- The Brain's background worker never STARTS a task that buys data (a QuickICP search) on its
-- own: it stops and says a person must start it. One row here is that stop, shown on the
-- project's Dashboard with a "Start" button. The person's click is recorded on the row; the
-- worker then runs that one step and carries on.
--
-- Written by the Brain's worker (the offer, the outcome) and by the app (the approval). Both use
-- the server-side key; there are no policies, so the browser can never read or change it.

create table if not exists public.revenueos_brain_paid_steps (
  id uuid primary key default gen_random_uuid(),
  project_id uuid not null references public.revenueos_projects (id) on delete cascade,
  customer_key text not null,
  month text not null,              -- the run's month, e.g. 2026-10
  task_id text not null,            -- the task that would buy data, e.g. T3
  -- waiting -> approved -> running -> done   (or failed; withdrawn when it is no longer the next step)
  status text not null default 'waiting'
    check (status in ('waiting', 'approved', 'running', 'done', 'failed', 'withdrawn')),
  approved_by text,
  approved_at timestamptz,
  error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One open offer per task of a run.
create unique index if not exists revenueos_brain_paid_steps_one_open_idx
  on public.revenueos_brain_paid_steps (customer_key, month, task_id)
  where status in ('waiting', 'approved', 'running');
create index if not exists revenueos_brain_paid_steps_project_idx
  on public.revenueos_brain_paid_steps (project_id, created_at desc);

drop trigger if exists set_updated_at on public.revenueos_brain_paid_steps;
create trigger set_updated_at before update on public.revenueos_brain_paid_steps
  for each row execute function public.revenueos_set_updated_at();

alter table public.revenueos_brain_paid_steps enable row level security;
revoke all on public.revenueos_brain_paid_steps from anon, authenticated;
