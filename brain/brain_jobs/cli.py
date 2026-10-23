"""
brain_jobs/cli.py  -  the commands (CUSTOMER_KEY from .env; run in the customer's brain folder):

    python -m brain_jobs tick                                       run every due job of this customer (catch-up)
    python -m brain_jobs enqueue run_step --run <month> [--at <time>]       one orchestrator step, now or at a time
    python -m brain_jobs enqueue campaign_check --run <month>       a check job for every tracked campaign of a run
    python -m brain_jobs status                                     jobs by state, next due, dead ones, worker offline?
    python -m brain_jobs selftest                                   fakes only: no network, no Supabase
    python -m brain_jobs realtest                                   REAL: 2 worker processes claim 12 test jobs
                                                                    (needs sql/004; test rows deleted at the end)

Exit codes of 'tick': 0 = fine, 2 = a job died, 1 = stopped. The last line starts with 'JOBS:'.
<time> is ISO 8601 with a zone, e.g. 2026-11-03T09:05:00+05:30.
"""

import datetime
import json
import subprocess
import sys
import time

from brain_jobs import config

USAGE = __doc__.split("\n\n", 1)[1].split("\n\nExit", 1)[0].rstrip()
SELFTEST_PREFIX = "selftest-"


def customer() -> str:
    from state_store import current_customer
    return current_customer()


def parse_at(text: str) -> datetime.datetime:
    try:
        when = datetime.datetime.fromisoformat(text)
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        from quickicp import ToolError
        raise ToolError(f"STOPPED: --at must be an ISO time with a zone, e.g. 2026-11-03T09:05:00+05:30 (got '{text}').")
    return when.astimezone(datetime.timezone.utc)


def enqueue_command(key: str, kind: str, month: str, at: datetime.datetime | None, store, campaigns=None,
                    now: datetime.datetime | None = None) -> list[str]:
    """Adds the job(s); the same key again does nothing. Returns one line per job."""
    from brain_jobs.tick import job_row
    run_key = f"{key}-{month}"
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if kind == "run_step":
        due = at or now
        job_key = f"run_step:{run_key}:{due.strftime('%Y%m%dT%H%M%SZ')}"
        new = store.enqueue(job_row(key, run_key, "run_step", {"month": month}, job_key, due))
        return [f"{'added' if new else 'already there (nothing changed)'}: {job_key} due {due.isoformat()}"]
    if campaigns is None:
        from campaign_monitor.find import campaigns
    lines = []
    for c in campaigns(key, month):
        if c["tracked"]:
            job_key = f"campaign_check:{c['reference']}"
            new = store.enqueue(job_row(key, run_key, "campaign_check",
                                    {"reference": c["reference"], "month": month, "kind": c["kind"]}, job_key, at or now))
            lines.append(f"{'added' if new else 'already there (nothing changed)'}: {job_key}")
    return lines or [f"no tracked campaign in run {run_key}"]


def status_lines(key: str, store, now: datetime.datetime | None = None) -> list[str]:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    jobs = store.jobs(key)
    counts = {s: sum(1 for j in jobs if j["status"] == s) for s in ("queued", "leased", "done", "failed", "dead")}
    lines = [f"  {s}: {n}" for s, n in counts.items()]
    waiting = sorted((j for j in jobs if j["status"] == "queued"), key=lambda j: j["due_at"])
    if waiting:
        lines.append(f"  next due: {waiting[0]['due_at']} {waiting[0]['kind']} {waiting[0]['idempotency_key']}")
    for j in jobs:
        if j["status"] == "dead":
            lines.append(f"  DEAD #{j['id']} {j['kind']} {j['idempotency_key']}: {j.get('last_error')}")
    beats = store.heartbeats(key)
    last = max((datetime.datetime.fromisoformat(str(b["last_tick"]).replace("Z", "+00:00")) for b in beats), default=None)
    stale = last is None or now - last > datetime.timedelta(minutes=config.STALE_MINUTES)
    for b in beats:
        lines.append(f"  worker {b['worker']}: last tick {b['last_tick']} - {b.get('last_summary')}")
    lines.append(("JOBS: WORKER OFFLINE (no tick for " + (f"{int((now - last).total_seconds() // 60)} min" if last else "ever")
                  + f"; expected every 5 min, stale after {config.STALE_MINUTES})") if stale else
                 f"JOBS: worker online, {counts['queued']} queued, {counts['dead']} dead")
    return lines


def realtest(store=None) -> bool:
    """REAL Supabase: 12 jobs for a selftest-* customer, 2 worker processes claim at the same time; every job must
    be claimed exactly once. The test rows are deleted at the end, even on failure."""
    from brain_jobs.store import JOBS, SupabaseJobs
    store = store or SupabaseJobs()
    test = SELFTEST_PREFIX + "jobs-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    from brain_jobs.tick import job_row
    try:
        for n in range(12):
            store.enqueue(job_row(test, None, "noop", {}, f"{test}:noop:{n}"))
        twice = store.enqueue(job_row(test, None, "noop", {}, f"{test}:noop:0"))
        workers = [subprocess.Popen([sys.executable, "-m", "brain_jobs", "_claim", test, f"realtest-worker-{w}"],
                                    stdout=subprocess.PIPE, text=True) for w in (1, 2)]
        got = [json.loads(w.communicate(timeout=120)[0].strip().splitlines()[-1]) for w in workers]
        ids = got[0] + got[1]
        rows = store.jobs(test)
        ok = (len(rows) == 12 and not twice and len(ids) == 12 and len(set(ids)) == 12
              and all(r["status"] == "leased" and r["attempts"] == 1 for r in rows))
        print(f"  worker 1 claimed {len(got[0])}, worker 2 claimed {len(got[1])}; overlap: {len(ids) - len(set(ids))}; "
              f"duplicate enqueue added: {twice}; rows leased once: {sum(r['attempts'] == 1 for r in rows)} of {len(rows)}")
        print(f"BRAIN JOBS REAL TEST {'OK' if ok else 'FAILED'}: two workers never took the same job")
        return ok
    finally:
        if test.startswith(SELFTEST_PREFIX):
            store.db.table(JOBS).delete().eq("customer", test).execute()
            print(f"  test rows of {test} deleted: {len(store.jobs(test)) == 0}")


def claim_for_realtest(test: str, owner: str) -> None:
    """One worker of the real test: claim 3 at a time until nothing is left; print the ids as JSON."""
    from brain_jobs.store import SupabaseJobs
    if not test.startswith(SELFTEST_PREFIX):
        raise SystemExit("only selftest-* customers")
    store, ids = SupabaseJobs(), []
    while True:
        got = store.claim(test, owner, 600, 3)
        if not got:
            break
        ids += [j["id"] for j in got]
        time.sleep(0.05)
    print(json.dumps(ids))


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""
    try:
        if command == "selftest" and len(args) == 1:
            from brain_jobs.selftest import selftest
            sys.exit(0 if selftest() else 1)
        elif command == "realtest" and len(args) == 1:
            sys.exit(0 if realtest() else 1)
        elif command == "_claim" and len(args) == 3:
            claim_for_realtest(args[1], args[2])
        elif command == "tick" and len(args) == 1:
            from brain_jobs.store import SupabaseJobs
            from brain_jobs.tick import tick
            result = tick(customer(), SupabaseJobs())
            print(result["summary"])
            sys.exit(2 if result["counts"]["dead"] else 0)
        elif (command == "enqueue" and len(args) in (4, 6) and args[1] in ("run_step", "campaign_check")
              and args[2] == "--run" and (len(args) == 4 or args[4] == "--at")):
            from brain_jobs.store import SupabaseJobs
            from orch.common import check_month
            at = parse_at(args[5]) if len(args) == 6 else None
            for line in enqueue_command(customer(), args[1], check_month(args[3]), at, SupabaseJobs()):
                print(f"  {line}")
            print("JOBS: enqueue done")
        elif command == "status" and len(args) == 1:
            from brain_jobs.store import SupabaseJobs
            print("\n".join(status_lines(customer(), SupabaseJobs())))
        else:
            print("Usage:\n" + USAGE)
            sys.exit(1)
    except Exception as error:
        from quickicp import ToolError
        if not isinstance(error, ToolError):
            raise
        print(error)
        print("JOBS: stopped")
        sys.exit(1)
