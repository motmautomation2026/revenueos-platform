"""
brain_jobs/tick.py  -  one tick for ONE customer (CUSTOMER_KEY of this folder):

    1. heartbeat (last_tick)
    2. discover: every tracked campaign in the ledger gets a campaign_check job (same key = no-op).
       run_step jobs are NEVER added here: only by hand (enqueue) or for a set time.
    3. claim the due jobs (lease LEASE_SECONDS): also those that came due while no worker ran (catch-up), oldest first
    4. run each: done | again (same row, new due_at, attempts reset) | busy (again soon, not an attempt) | failed;
       an error -> retry after BACKOFF_MINUTES; the MAX_ATTEMPTS-th error -> dead + ONE answered job_dead decision.
       After TICK_BUDGET_SECONDS no new job is started: the rest are given back (not counted as an attempt).
    5. heartbeat again with the summary; one log line per job.
"""

import datetime
import os
import secrets
import socket
import time

from brain_jobs import config
from brain_jobs.kinds import KINDS, Outcome
from brain_jobs.store import utc_now


def worker_name() -> str:
    return socket.gethostname()


def lease_owner() -> str:
    return f"{worker_name()}:{os.getpid()}:{secrets.token_hex(3)}"


def log_line(level: str, message: str, run: str | None = None, task: str | None = None) -> None:
    from orch.brain_log import write                     # file only; never prints, never raises
    write(level, message, run, task, "job")


def masked(text: str) -> str:
    from orch.brain_log import mask
    return mask(text)[:500]


def job_row(customer: str, run_key: str | None, kind: str, payload: dict, key: str,
            due_at: datetime.datetime | None = None) -> dict:
    return {"customer": customer, "run_key": run_key, "kind": kind, "payload": payload, "idempotency_key": key,
            "due_at": (due_at or utc_now()).isoformat(), "status": "queued", "attempts": 0,
            "max_attempts": config.MAX_ATTEMPTS}


def discover(key: str, store, campaigns=None, now: datetime.datetime | None = None) -> int:
    """campaign_check jobs for every tracked campaign (idempotent). Returns how many were new."""
    if campaigns is None:
        from campaign_monitor.find import campaigns
    added = 0
    for c in campaigns(key):
        if c["tracked"]:
            month = c["run"]["run_key"][len(key) + 1:]
            added += store.enqueue(job_row(key, c["run"]["run_key"], "campaign_check",
                                           {"reference": c["reference"], "month": month, "kind": c["kind"]},
                                           f"campaign_check:{c['reference']}", now))
    return added


def backoff(attempts: int) -> datetime.timedelta:
    return datetime.timedelta(minutes=config.BACKOFF_MINUTES[min(attempts, len(config.BACKOFF_MINUTES)) - 1])


def dead_alert(job: dict, error: str, record=None, runs=None) -> int | None:
    """ONE answered job_dead decision on the job's run (never open). None if the job has no run."""
    if record is None:
        from state_store import get_runs as runs, record_decision as record
    run = next((r for r in runs(job["customer"]) if r["run_key"] == job.get("run_key")), None)
    if not run:
        return None
    reason = f"job {job['kind']} {job['idempotency_key']} is dead after {job['attempts']} attempt(s): {error}"
    from quickicp import report_event                  # Sentry, only when SENTRY_DSN is set
    report_event(config.ALERT_KIND, reason[:1000], "error", run=job.get("run_key") or "-")
    return record(run["id"], config.ALERT_KIND, "raised", "brain", reason[:1000],
                  question={"job_id": job["id"], "kind": job["kind"], "key": job["idempotency_key"],
                            "attempts": job["attempts"], "text": error})["id"]


def settle(job: dict, owner: str, outcome: Outcome | None, error: str | None, store, now, alert) -> str:
    """Write the job's new state (only while we hold the lease). Returns what happened."""
    if error is not None:
        if job["attempts"] >= job["max_attempts"]:
            fields, what = {"status": "dead", "last_error": error, "finished_at": now.isoformat()}, "dead"
        else:
            fields = {"status": "queued", "last_error": error, "due_at": (now + backoff(job["attempts"])).isoformat()}
            what = "retry"
    elif outcome.status == "done":
        fields, what = {"status": "done", "finished_at": now.isoformat(), "last_error": None}, "done"
    elif outcome.status == "failed":
        fields, what = {"status": "failed", "finished_at": now.isoformat(), "last_error": outcome.note[:500]}, "failed"
    elif outcome.status == "again":
        fields, what = {"status": "queued", "due_at": outcome.due_at.isoformat(), "attempts": 0, "last_error": None}, "again"
    else:                                                                      # busy: not counted as an attempt
        fields, what = {"status": "queued", "due_at": outcome.due_at.isoformat(), "attempts": job["attempts"] - 1}, "busy"
    if not store.finish(job, owner, {**fields, "lease_owner": None, "lease_until": None}):
        return "lease lost"                                    # another tick took it over: it owns the job now
    if what == "dead":
        alert(job, error)
    return what


def tick(key: str, store, kinds=None, campaigns=None, alert=None, now_fn=utc_now, clock=time.monotonic,
         owner: str | None = None, worker: str | None = None) -> dict:
    kinds, owner, worker = kinds or KINDS, owner or lease_owner(), worker or worker_name()
    alert = alert or dead_alert
    started = clock()
    store.beat(worker, key, "JOBS: tick started")
    added = discover(key, store, campaigns, now_fn())
    claimed = store.claim(key, owner, config.LEASE_SECONDS, config.CLAIM_LIMIT)
    counts = {"done": 0, "again": 0, "busy": 0, "retry": 0, "dead": 0, "failed": 0, "lease lost": 0, "given back": 0}
    for job in claimed:
        if clock() - started > config.TICK_BUDGET_SECONDS:
            store.finish(job, owner, {"status": "queued", "attempts": job["attempts"] - 1, "lease_owner": None,
                                      "lease_until": None})
            counts["given back"] += 1
            continue
        outcome, error = None, None
        if job["attempts"] > job["max_attempts"]:                  # the worker died during its last attempt
            job = {**job, "attempts": job["max_attempts"]}
            error = "the worker stopped during the last attempt (lease expired)"
            counts[settle(job, owner, None, error, store, now_fn(), alert)] += 1
            log_line("WARNING", f"job #{job['id']} {job['kind']} {job['idempotency_key']}: dead ({error})", job.get("run_key"))
            continue
        try:
            run = kinds.get(job["kind"])
            if run is None:
                raise RuntimeError(f"unknown job kind '{job['kind']}'")
            outcome = run(job, key, now_fn())
        except Exception as problem:
            error = masked(f"{type(problem).__name__}: {problem}")
        what = settle(job, owner, outcome, error, store, now_fn(), alert)
        counts[what] += 1
        log_line("WARNING" if what in ("dead", "retry", "lease lost") else "INFO",
                 f"job #{job['id']} {job['kind']} {job['idempotency_key']}: {what}"
                 + (f" ({error})" if error else f" ({outcome.note})" if outcome and outcome.note else ""),
                 job.get("run_key"))
    summary = (f"JOBS: {len(claimed)} claimed, {added} new campaign job(s), "
               + ", ".join(f"{v} {k}" for k, v in counts.items() if v or k in ("done", "dead")))
    store.beat(worker, key, summary)
    return {"claimed": claimed, "counts": counts, "added": added, "summary": summary}
