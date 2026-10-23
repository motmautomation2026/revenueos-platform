"""
brain_jobs/config.py  -  the settings, in one place.
"""

LEASE_SECONDS = 600              # a claimed job is ours for 10 minutes; after that another tick may take it (crash-safe)
CLAIM_LIMIT = 20                 # at most this many jobs per tick
TICK_BUDGET_SECONDS = 480        # stop starting new jobs after 8 minutes: a tick always ends inside its lease
MAX_ATTEMPTS = 5                 # then the job is dead (+ one answered job_dead decision)
BACKOFF_MINUTES = [1, 5, 30, 120]    # retry delays after attempt 1, 2, 3, 4+
BUSY_RETRY_MINUTES = 2           # run_step while the run lock is held: try again, NOT counted as an attempt
STALE_MINUTES = 15               # no tick for this long -> 'WORKER OFFLINE'

CAMPAIGN_EVERY_HOURS = 3         # campaign_check: at most this long between checks ...
CAMPAIGN_AFTER_CLOSE = {"calls": "19:30", "email": "18:00"}   # ... and once after each day's window closed (IST)

ALERT_KIND = "job_dead"
ACTIVE_RUN_STATUSES = {"planning", "running", "waiting_human"}
