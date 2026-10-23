# brain_jobs: a durable job queue

Brain work that is due later (hours, days or months) is a row in the Supabase table `brain_jobs`, so it is never
lost when the machine is off. `python -m brain_jobs tick` runs everything that is due, including jobs that came due
while no worker was running (catch-up).

Table, heartbeat and the claim function: `sql/004_brain_jobs.sql` (run it once in Supabase; RLS on, service key
only).

## Commands (CUSTOMER_KEY from .env; run in the customer's brain folder)

    python -m brain_jobs tick                                        run every due job of this customer
    python -m brain_jobs enqueue run_step --run 2026-10-r2 [--at 2026-11-03T09:05:00+05:30]
    python -m brain_jobs enqueue campaign_check --run 2026-10-r2     a check job for every tracked campaign of a run
    python -m brain_jobs status                                      jobs by state, next due, dead ones, worker offline?
    python -m brain_jobs selftest                                    fakes only: no network, no Supabase
    python -m brain_jobs realtest                                    REAL: 2 worker processes claim 12 test jobs

- `tick` exit codes: 0 = fine, 2 = a job died, 1 = stopped.
- The last line of `tick` and `status` starts with `JOBS:`.
- `realtest` needs sql/004. It uses a `selftest-jobs-<time>` customer and deletes its rows at the end.

## What a tick does

1. **Heartbeat** (`brain_worker_heartbeats.last_tick`). `status` says `WORKER OFFLINE` after 15 minutes without a
   tick.
2. **Discover:** every tracked campaign in the ledger (campaign_monitor) gets a `campaign_check` job. The same key
   means no-op. `run_step` jobs are never added here, only by hand (`enqueue`) or for a set time (`--at`).
3. **Claim:** `brain_claim_jobs()` is atomic (`FOR UPDATE SKIP LOCKED`), so two workers never take the same job.
   - It takes due jobs, oldest first, at most 20 per tick, each with a 10-minute lease.
   - A job whose lease expired is taken again; that is how a crashed worker's job gets done.
4. **Run each job:**
   - **done**;
   - **again:** a recurring job keeps the same row, gets a new `due_at`, and its attempts reset;
   - **busy:** the run lock is held. The job goes back in 2 minutes, and that does not count as an attempt;
   - **failed:** stopped for good;
   - **error:** a retry after 1, 5, 30, then 120 minutes. The 5th error (`max_attempts`) makes the job **dead**,
     with ONE answered `job_dead` decision on its run. It is never open, so it blocks nothing.
   - A worker that died during the last attempt: its job is marked dead without being run again.
5. **Time limit:** after 8 minutes no new job is started; the rest are given back, which does not count as an
   attempt. So a tick always ends inside its lease.
6. **Log:** one line per job in `logs/brain-YYYY-MM-DD.log`.

## Job kinds

- **`campaign_check`** (key `campaign_check:<reference>`):
  - campaign_monitor checks that one campaign (`check_all(..., reference=...)`).
  - Not finished: it runs again at the sooner of 3 hours from now, or once after the day's window has closed
    (calls 19:30, email 18:00, India time).
  - Finished: done.
- **`run_step`** (key `run_step:<run_key>:<due time>`):
  - It runs `python orchestrator.py run <month> --until-wait` in this folder, as a subprocess. Its output goes to
    `logs/<time>-job-run.log`, exactly as printed.
  - The run lock is held: busy.
  - The run is not active any more: failed.

## Next to run_for (their worker): no double work

- `campaign_check` is work run_for never does.
- `run_step` is the same command run_for starts, so run_for keeps its 30-second loop and brain_jobs never adds
  `run_step` by itself.
- When both do start at the same moment, the orchestrator's run lock stops the second one: the job is busy and
  goes back.
- A second `--until-wait` after the first is a no-op: the ledger keys are idempotent, and `--until-wait` never buys
  data.

## What must run on the server (their side)

`python -m brain_jobs tick` every 5 minutes, in each customer's brain folder, with that customer's `.env`. The
lease and the 8-minute limit make overlapping ticks safe.

- **Windows** (as `install_worker.ps1` does now): one Scheduled Task per customer, repeating every 5 minutes:
  `cmd /c cd /d <customer folder> && <python> -m brain_jobs tick`.
- **Linux:** `*/5 * * * * cd <customer folder> && .venv/bin/python -m brain_jobs tick >> logs/jobs.log 2>&1`, or a
  systemd timer with `OnUnitActiveSec=5min`.
- Later, run_for can call the same command from its loop, so there is one process per machine. That would be their
  change; no existing command changes.

## Isolation (enforced by `python tools/baseline.py check-imports`)

- brain_jobs may only use:
  - from `quickicp`: `run_cli`, `ToolError`;
  - from `state_store`: `get_runs`, `record_decision`, `current_customer`;
  - from `supabase_store`: `db`;
  - from campaign_monitor: `check_all`, `campaigns`;
  - from `orch`: `brain_log` (`write`, `mask`), `check_month`.
- Nothing imports brain_jobs.
