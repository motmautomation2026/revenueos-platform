-- Staging or Production, per project.
--
-- Production runs the plan for real: the plan's own targets, real prospects.
-- Staging is for trying a customer out cheaply and safely:
--   * only as many companies and people are fetched as the person asks for
--   * calls go to the person's own test numbers, and emails to their own test inboxes,
--     never to the prospects that were found
--
-- The choice is made in the app next to "Start execution" and read by the Brain's worker
-- (brain/run_for.py). A run keeps the mode it was started in.

alter table public.revenueos_projects
  add column if not exists run_mode text not null default 'production'
    check (run_mode in ('staging', 'production')),
  add column if not exists staging_companies integer check (staging_companies between 1 and 500),
  add column if not exists staging_people integer check (staging_people between 1 and 200),
  add column if not exists staging_phones text,     -- the person's own numbers, comma separated
  add column if not exists staging_emails text;     -- the person's own inboxes, comma separated

comment on column public.revenueos_projects.run_mode is
  'staging = the Brain''s test mode for this customer only (small limits, own test numbers and inboxes); production = real.';
