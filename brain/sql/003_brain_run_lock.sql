-- 003_brain_run_lock.sql
-- Run lock (a lease) on brain_customer_runs, so two 'python orchestrator.py run' commands can never
-- work on the same run at the same time. Crash-safe: the lock expires by itself at locked_until.
--
-- How the code uses it (state_store.py):
--   take:    update ... set locked_by = me, locked_until = now + 30 min
--            where id = <run> and (locked_until is null or locked_until < now)   -> only one process wins
--   renew:   before every paid call (same holder only)
--   release: at the end of every run, also after an error (same holder only)
--   unlock:  python orchestrator.py unlock <YYYY-MM>   (asks you to type the run key)
--
-- Only ADDS 2 nullable columns to our own table brain_customer_runs. Nothing else is changed.

begin;

alter table brain_customer_runs
  add column locked_by    text,
  add column locked_until timestamptz;

comment on column brain_customer_runs.locked_by is
  'Who holds the run lock: <computer>:<process id>:<random>; null when free. Example: DESKTOP-1:4120:9f2c1a';
comment on column brain_customer_runs.locked_until is
  'When the run lock expires (UTC); after this time another run may take it over (crash-safe). '
  'Null when free. Example: 2026-10-10 11:30:00+00';

commit;
