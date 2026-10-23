-- 004_brain_jobs.sql
-- Durable job queue for the MOTM Revenue OS brain (brain_jobs/): brain work that is due later (hours, days or
-- months) is a row here, so it is never lost when the machine is off; 'python -m brain_jobs tick' runs
-- everything that is due, also jobs that came due while no worker was running (catch-up).
--
-- How the code uses it (brain_jobs/store.py):
--   enqueue: insert ... on conflict (idempotency_key) do nothing          -> the same key twice = no-op
--   claim:   select * from brain_claim_jobs(<customer>, <worker>, 600, 20)  -> atomic: two workers never get
--            the same job (FOR UPDATE SKIP LOCKED); an expired lease (a crashed worker) is claimed again
--   finish:  update ... where id = <job> and status = 'leased' and lease_owner = <worker>   (same worker only)
--   beat:    upsert brain_worker_heartbeats (worker, customer) -> last_tick = now()
--
-- Only CREATES 2 new brain_* tables + 1 function. It does not touch any existing table.
-- Row level security is ON with NO policies: only the service key (used by the brain) can read or write;
-- the anon / authenticated keys see nothing. If the platform should show "worker offline", read
-- brain_worker_heartbeats with the service key on the server (or add a select policy then).
-- Runs in one transaction: if any line fails, nothing is created.

begin;

-- =====================================================================
-- 1. brain_jobs: one row per job (a recurring job reuses its row)
-- =====================================================================
create table brain_jobs (
  id               bigint generated always as identity,
  customer         text not null,
  run_key          text,
  kind             text not null,
  payload          jsonb not null default '{}'::jsonb,
  due_at           timestamptz not null default now(),
  status           text not null default 'queued',
  lease_owner      text,
  lease_until      timestamptz,
  attempts         integer not null default 0,
  max_attempts     integer not null default 5,
  last_error       text,
  idempotency_key  text not null,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  finished_at      timestamptz,
  constraint brain_jobs_pkey                  primary key (id),
  constraint brain_jobs_idempotency_key_unique unique (idempotency_key),
  constraint brain_jobs_status_check
    check (status in ('queued','leased','done','failed','dead')),
  constraint brain_jobs_attempts_check        check (attempts >= 0 and max_attempts >= 1)
);

create index brain_jobs_due_idx on brain_jobs (customer, status, due_at);

create trigger brain_jobs_set_updated_at before update on brain_jobs
  for each row execute function brain_set_updated_at();

alter table brain_jobs enable row level security;   -- no policies: service key only

comment on table brain_jobs is
  'Durable job queue of the brain: work due later (campaign checks, a run step at a set time). '
  'Written only by the MOTM Revenue OS code (brain_jobs/). Claimed atomically with brain_claim_jobs().';
comment on column brain_jobs.id is
  'Row id, set by the database. Example: 1';
comment on column brain_jobs.customer is
  'Customer key, same value as brain_customer_runs.customer. A tick only claims its own customer''s jobs. Example: anvixa-ai-product';
comment on column brain_jobs.run_key is
  'The run the job belongs to (brain_customer_runs.run_key), if any. Example: anvixa-ai-product-2026-10-r2';
comment on column brain_jobs.kind is
  'What to do: campaign_check (campaign_monitor for one campaign; reschedules itself until the campaign is finished) '
  'or run_step (python orchestrator.py run <month> --until-wait, added by hand or for a set time). Example: campaign_check';
comment on column brain_jobs.payload is
  'The job''s inputs as JSON. Example: {"reference": "motm-anvixa-call-20261016-113010", "month": "2026-10-r2"}';
comment on column brain_jobs.due_at is
  'When the job may run (UTC). A job due while no worker ran is claimed on the next tick (catch-up). Example: 2026-10-24 14:30:00+00';
comment on column brain_jobs.status is
  'queued (waiting for due_at) | leased (a worker is running it until lease_until) | done | failed (stopped for good '
  'by the job itself) | dead (max_attempts reached; an answered job_dead decision was recorded). Example: queued';
comment on column brain_jobs.lease_owner is
  'The worker holding the job: <computer>:<process id>:<random>; null when not leased. Example: DESKTOP-1:4120:9f2c1a';
comment on column brain_jobs.lease_until is
  'When the lease expires (UTC); after it, another tick may claim the job again (crash-safe). Example: 2026-10-24 14:40:00+00';
comment on column brain_jobs.attempts is
  'How many times the job was claimed (a crash counts). Example: 1';
comment on column brain_jobs.max_attempts is
  'After this many failed attempts the job is dead. Example: 5';
comment on column brain_jobs.last_error is
  'The last failure (masked, max 500 characters); null if none. Example: ConnectionError: Quickhowl did not answer';
comment on column brain_jobs.idempotency_key is
  'Unique key: adding a job with a key that exists does nothing. Example: campaign_check:motm-anvixa-call-20261016-113010';
comment on column brain_jobs.created_at is
  'When the job was added (UTC). Example: 2026-10-23 10:00:00+00';
comment on column brain_jobs.updated_at is
  'Last change (UTC), set by the trigger. Example: 2026-10-23 10:05:00+00';
comment on column brain_jobs.finished_at is
  'When the job ended as done, failed or dead (UTC); null while queued or leased. Example: 2026-10-30 14:00:00+00';


-- =====================================================================
-- 2. brain_worker_heartbeats: one row per worker per customer
-- =====================================================================
create table brain_worker_heartbeats (
  worker        text not null,
  customer      text not null,
  last_tick     timestamptz not null default now(),
  last_summary  text,
  constraint brain_worker_heartbeats_pkey primary key (worker, customer)
);

alter table brain_worker_heartbeats enable row level security;   -- no policies: service key only

comment on table brain_worker_heartbeats is
  'Every brain_jobs tick writes its time here. No tick for 15 minutes = the worker is offline '
  '(python -m brain_jobs status says so). Written only by the MOTM Revenue OS code (brain_jobs/).';
comment on column brain_worker_heartbeats.worker is
  'The machine that ticks (computer name). Example: DESKTOP-1';
comment on column brain_worker_heartbeats.customer is
  'Customer key of the folder the tick ran in. Example: anvixa-ai-product';
comment on column brain_worker_heartbeats.last_tick is
  'When that worker last ticked for that customer (UTC). Example: 2026-10-23 10:05:00+00';
comment on column brain_worker_heartbeats.last_summary is
  'The tick''s last line. Example: JOBS: 2 claimed, 2 done, 0 retried, 0 dead';


-- =====================================================================
-- 3. brain_claim_jobs(): the atomic claim
-- =====================================================================
create or replace function brain_claim_jobs(p_customer text, p_owner text, p_lease_seconds integer, p_limit integer)
returns setof brain_jobs
language sql as $$
  update brain_jobs j
     set status = 'leased', lease_owner = p_owner,
         lease_until = now() + make_interval(secs => p_lease_seconds),
         attempts = j.attempts + 1
   where j.id in (select id from brain_jobs
                   where customer = p_customer
                     and ((status = 'queued' and due_at <= now())
                          or (status = 'leased' and lease_until < now()))
                   order by due_at, id
                   limit p_limit
                   for update skip locked)
  returning j.*;
$$;

comment on function brain_claim_jobs(text, text, integer, integer) is
  'Claims up to p_limit due jobs of one customer for p_owner for p_lease_seconds (status leased, attempts + 1): '
  'queued jobs whose due_at has passed and leased jobs whose lease expired (a crashed worker). FOR UPDATE SKIP '
  'LOCKED: two workers calling at the same time never get the same job. Oldest due first.';

revoke all on function brain_claim_jobs(text, text, integer, integer) from public, anon, authenticated;
grant execute on function brain_claim_jobs(text, text, integer, integer) to service_role;

commit;
