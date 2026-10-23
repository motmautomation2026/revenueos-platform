"""
brain_jobs/  -  durable job queue (Supabase table brain_jobs, sql/004_brain_jobs.sql): brain work due later is a row,
so it is never lost when the machine is off; 'python -m brain_jobs tick' runs everything due (catch-up).
On its own: nothing imports it. Run: python -m brain_jobs tick | enqueue | status | selftest | realtest
"""
