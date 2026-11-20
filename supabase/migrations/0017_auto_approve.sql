-- 0017_auto_approve.sql
-- Per-project timed auto-approval of routine gate decisions.
--
-- When set to a positive number of seconds, the Brain's run loop (brain/orch/run.py, auto_resolve_overdue)
-- auto-answers routine gate decisions that have been open longer than this, so the run does not park in
-- the "Needs a person" column while no one objects:
--   * a human work task is auto-"skip" (non-blocking; never "done"), a content/readiness gate is
--     auto-"done", and email/call sends are auto-"yes";
--   * exception decisions (goal check, a paid call of unknown outcome, a failed task) are always left
--     for a person.
-- NULL = off: every gate waits for a person (the current, default behaviour). The Brain reads this column
-- directly (brain/supabase_store.py, auto_approve_seconds).

alter table public.revenueos_projects
  add column if not exists auto_approve_seconds integer;

comment on column public.revenueos_projects.auto_approve_seconds is
  'Opt-in: auto-answer routine gate decisions open longer than this many seconds (NULL = off, always ask a person).';
