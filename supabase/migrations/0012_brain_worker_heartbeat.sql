-- The Brain worker's heartbeat, so the app can say whether the worker is running.
--
-- The worker (brain/run_for.py work) writes its row every time it looks for work (every 30 s).
-- The Projects overview shows "Brain worker online" while the row is fresh, and a warning when it
-- is not: a worker that stopped (window closed, server restarted) is then seen at once instead of
-- a run silently standing still.
--
-- Written by the worker, read by the app; both use the server-side key. There are no policies,
-- so the browser can never read or change it.

create table if not exists public.revenueos_brain_workers (
  worker text primary key,          -- the worker's name (its hostname, e.g. revenueos-worker)
  started_at timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  -- what it was doing at its last look, e.g. "2 steps running"
  note text
);

alter table public.revenueos_brain_workers enable row level security;
revoke all on public.revenueos_brain_workers from anon, authenticated;
