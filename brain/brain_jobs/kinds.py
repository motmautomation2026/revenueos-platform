"""
brain_jobs/kinds.py  -  what each job kind does. A kind returns an Outcome, or raises (= a failed attempt: retry
with backoff, dead after max_attempts).

    campaign_check  payload {reference, month}: campaign_monitor checks that ONE campaign. Finished -> done;
                    otherwise 'again' (the same row, attempts reset) at the next check time.
    run_step        payload {month}: python orchestrator.py run <month> --until-wait, a subprocess in this folder,
                    its output to logs/ unchanged. Run lock held -> 'busy' (again in 2 min, not an attempt);
                    the run is not active any more -> 'failed' (stopped for good, nothing to retry).
"""

import datetime
import os
import subprocess
import sys
from dataclasses import dataclass

from brain_jobs import config

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


@dataclass
class Outcome:
    status: str                   # done | again | busy | failed
    note: str = ""
    due_at: datetime.datetime | None = None


def next_campaign_check(now: datetime.datetime, kind: str) -> datetime.datetime:
    """The sooner of: CAMPAIGN_EVERY_HOURS from now, or the next 'after the window closed' time (IST)."""
    hours, minutes = (int(x) for x in config.CAMPAIGN_AFTER_CLOSE.get(kind, "19:30").split(":"))
    local = now.astimezone(IST)
    after_close = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    if after_close <= local:
        after_close += datetime.timedelta(days=1)
    return min(now + datetime.timedelta(hours=config.CAMPAIGN_EVERY_HOURS), after_close.astimezone(datetime.timezone.utc))


def campaign_check(job: dict, key: str, now: datetime.datetime, check_all=None) -> Outcome:
    if check_all is None:
        from campaign_monitor.check import check_all
    payload = job["payload"] or {}
    results = check_all(key, payload.get("month"), now=now, reference=payload["reference"])
    if not results:
        return Outcome("done", "campaign not tracked any more (finished, or not in the ledger)")
    result = results[0]
    if result.get("failed"):
        raise RuntimeError(f"campaign check failed: {result['failed']}")
    if result.get("finished"):
        return Outcome("done", result.get("line") or "finished")
    return Outcome("again", result.get("line") or result.get("skipped") or "checked",
                   next_campaign_check(now, result.get("kind") or payload.get("kind") or "calls"))


def run_step(job: dict, key: str, now: datetime.datetime, runs=None, start=None) -> Outcome:
    if runs is None:
        from state_store import get_runs as runs
    month = (job["payload"] or {})["month"]
    run = next((r for r in runs(key) if r["run_key"] == f"{key}-{month}"), None)
    if not run or run.get("status") not in config.ACTIVE_RUN_STATUSES:
        return Outcome("failed", f"run {key}-{month} is {run.get('status') if run else 'missing'}: nothing to run")
    code, text = (start or start_run)(month)
    if code != 0 and "STOPPED: run is busy" in text:
        return Outcome("busy", "run lock held by another run", now + datetime.timedelta(minutes=config.BUSY_RETRY_MINUTES))
    if code != 0:
        last = next((line for line in reversed(text.splitlines()) if line.strip()), f"exit {code}")
        raise RuntimeError(f"orchestrator run {month} --until-wait: exit {code}: {last[:300]}")
    return Outcome("done", f"orchestrator run {month} --until-wait: exit 0")


def start_run(month: str) -> tuple[int, str]:
    """The same command run_for starts, in this folder; its output goes to logs/ exactly as printed."""
    os.makedirs("logs", exist_ok=True)
    path = os.path.join("logs", datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-job-run.log")
    with open(path, "w", encoding="utf-8") as log:
        code = subprocess.run([sys.executable, "orchestrator.py", "run", month, "--until-wait"], stdout=log,
                              stderr=subprocess.STDOUT, timeout=config.LEASE_SECONDS - 60).returncode
    with open(path, encoding="utf-8", errors="replace") as log:
        return code, log.read()


KINDS = {"campaign_check": campaign_check, "run_step": run_step}
