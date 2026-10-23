"""
brain_jobs/selftest.py  -  python -m brain_jobs selftest. A fake job store with the SAME rules as
sql/004_brain_jobs.sql (claim: due queued + expired leases, oldest first, attempts + 1; finish only by the lease
owner), a fake clock, fake job kinds: no network, no Supabase, no orchestrator. The log goes to a temp folder.
The real FOR UPDATE SKIP LOCKED is tested by 'python -m brain_jobs realtest' (needs sql/004).
"""

import contextlib
import datetime
import io
import os
import tempfile
import threading

from brain_jobs import cli, config, kinds
from brain_jobs.kinds import Outcome
from brain_jobs.tick import discover, job_row, tick

KEY = "selftest-jobs"
UTC = datetime.timezone.utc


def parse(stamp) -> datetime.datetime:
    return stamp if isinstance(stamp, datetime.datetime) else datetime.datetime.fromisoformat(str(stamp))


class Clock:
    def __init__(self):
        self.now = datetime.datetime(2026, 10, 19, 4, 30, tzinfo=UTC)          # Mon 19 Oct, 10:00 IST

    def __call__(self) -> datetime.datetime:
        return self.now

    def add(self, **delta):
        self.now += datetime.timedelta(**delta)


class FakeJobs:
    """brain_jobs + brain_worker_heartbeats + brain_claim_jobs(), in memory, with the SQL's rules."""

    def __init__(self, clock):
        self.clock, self.rows, self.beats, self.lock = clock, [], {}, threading.Lock()

    def enqueue(self, row):
        with self.lock:
            if any(r["idempotency_key"] == row["idempotency_key"] for r in self.rows):
                return False
            self.rows.append({**row, "id": len(self.rows) + 1, "lease_owner": None, "lease_until": None,
                              "last_error": None, "finished_at": None})
            return True

    def claim(self, customer, owner, lease_seconds, limit):
        with self.lock:                                   # the database does this in one statement
            now = self.clock()
            due = [r for r in self.rows if r["customer"] == customer and (
                (r["status"] == "queued" and parse(r["due_at"]) <= now)
                or (r["status"] == "leased" and parse(r["lease_until"]) < now))]
            got = sorted(due, key=lambda r: (parse(r["due_at"]), r["id"]))[:limit]
            for r in got:
                r.update(status="leased", lease_owner=owner, attempts=r["attempts"] + 1,
                         lease_until=(now + datetime.timedelta(seconds=lease_seconds)).isoformat())
            return [dict(r) for r in got]

    def finish(self, job, owner, fields):
        with self.lock:
            row = next((r for r in self.rows if r["id"] == job["id"] and r["status"] == "leased"
                        and r["lease_owner"] == owner), None)
            if row:
                row.update(fields)
            return bool(row)

    def beat(self, worker, customer, summary):
        self.beats[(worker, customer)] = {"worker": worker, "customer": customer,
                                          "last_tick": self.clock().isoformat(), "last_summary": summary}

    def jobs(self, customer):
        return sorted((dict(r) for r in self.rows if r["customer"] == customer), key=lambda r: parse(r["due_at"]))

    def heartbeats(self, customer):
        return [b for b in self.beats.values() if b["customer"] == customer]

    def row(self, key):
        return next(r for r in self.rows if r["idempotency_key"] == key)


def selftest() -> bool:
    checks = []

    def expect(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    saved = {k: os.environ.get(k) for k in ("BRAIN_LOG_DIR", "BRAIN_LOG")}
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.environ["BRAIN_LOG_DIR"] = folder
            os.environ.pop("BRAIN_LOG", None)
            no_campaigns = lambda key, month=None: []
            alerts = []
            alert = lambda job, error: alerts.append((job["id"], error)) or 1

            def run_tick(store, clock, job_kinds, owner="w1", campaigns=no_campaigns, ticks=None):
                return tick(KEY, store, job_kinds, campaigns, alert, clock, ticks or (lambda: 0.0), owner, "PC-1")

            # ---- enqueue: idempotent ----
            clock = Clock()
            store = FakeJobs(clock)
            first = store.enqueue(job_row(KEY, None, "noop", {}, "k-1", clock()))
            second = store.enqueue(job_row(KEY, None, "noop", {"other": 1}, "k-1", clock()))
            expect("enqueue: the same idempotency key twice -> the second does nothing (one row, first payload kept)",
                   first and not second and len(store.rows) == 1 and store.rows[0]["payload"] == {})

            # ---- lease + done; a job not due yet is left alone ----
            ran = []
            done_kind = {"noop": lambda job, key, now: ran.append(job["id"]) or Outcome("done", "ok")}
            store.enqueue(job_row(KEY, None, "noop", {}, "later", clock.now + datetime.timedelta(hours=1)))
            result = run_tick(store, clock, done_kind)
            expect("tick: a due job is leased (attempts 1), run, marked done with its lease cleared; a job due in 1 h "
                   "is not touched; the heartbeat has the summary",
                   ran == [1] and store.row("k-1")["status"] == "done" and store.row("k-1")["attempts"] == 1
                   and store.row("k-1")["lease_owner"] is None and store.row("later")["status"] == "queued"
                   and store.beats[("PC-1", KEY)]["last_summary"] == result["summary"]
                   and result["summary"].startswith("JOBS: 1 claimed"))

            # ---- two workers ----
            store2 = FakeJobs(Clock())
            for n in range(12):
                store2.enqueue(job_row(KEY, None, "noop", {}, f"two-{n}", store2.clock()))
            taken = {"A": [], "B": []}

            def worker(name):
                while True:
                    got = store2.claim(KEY, name, 600, 3)
                    if not got:
                        return
                    taken[name] += [j["id"] for j in got]
            threads = [threading.Thread(target=worker, args=(w,)) for w in ("A", "B")]
            [t.start() for t in threads]
            [t.join() for t in threads]
            ids = taken["A"] + taken["B"]
            expect("two workers claiming at the same time: every job taken exactly once (12 jobs, no overlap)",
                   len(ids) == 12 and len(set(ids)) == 12 and all(r["attempts"] == 1 for r in store2.rows))

            # ---- crash mid-job ----
            clock = Clock()
            store = FakeJobs(clock)
            store.enqueue(job_row(KEY, None, "noop", {}, "crash", clock()))
            crashed = store.claim(KEY, "w-crashed", config.LEASE_SECONDS, 5)[0]     # ... and never finishes
            clock.add(minutes=5)
            early = store.claim(KEY, "w2", config.LEASE_SECONDS, 5)
            clock.add(minutes=6)
            ran.clear()
            run_tick(store, clock, done_kind, owner="w2")
            late = store.finish(crashed, "w-crashed", {"status": "done"})
            expect("crash in the middle of a job: nobody else takes it while the lease runs (5 min); after the lease "
                   "(11 min) the next tick takes it over (attempt 2) and finishes it; the crashed worker's late write "
                   "is refused (lease lost)",
                   early == [] and ran == [1] and store.row("crash")["status"] == "done"
                   and store.row("crash")["attempts"] == 2 and late is False)

            # ---- retry with backoff, then dead + ONE alert ----
            clock = Clock()
            store = FakeJobs(clock)
            store.enqueue({**job_row(KEY, None, "boom", {}, "boom", clock()), "max_attempts": 3})
            failing = {"boom": lambda job, key, now: (_ for _ in ()).throw(ConnectionError("Quickhowl did not answer"))}
            run_tick(store, clock, failing)
            after1 = dict(store.row("boom"))
            clock.add(seconds=30)
            not_yet = run_tick(store, clock, failing)["claimed"]
            clock.add(seconds=31)
            run_tick(store, clock, failing)
            after2 = dict(store.row("boom"))
            clock.add(minutes=5, seconds=1)
            last = run_tick(store, clock, failing)
            clock.add(hours=3)
            run_tick(store, clock, failing)
            expect("an error -> retry after 1 min (not before), then 5 min; the error is kept (last_error)",
                   after1["status"] == "queued" and parse(after1["due_at"]) == Clock().now + datetime.timedelta(minutes=1)
                   and "ConnectionError: Quickhowl did not answer" in after1["last_error"] and not_yet == []
                   and after2["attempts"] == 2 and parse(after2["due_at"]) == clock.now - datetime.timedelta(hours=3, minutes=5, seconds=1) + datetime.timedelta(minutes=5))
            expect("the 3rd failure (max_attempts 3) -> dead, finished_at set, exactly ONE alert; later ticks never "
                   "take it again",
                   store.row("boom")["status"] == "dead" and store.row("boom")["finished_at"]
                   and last["counts"]["dead"] == 1 and len(alerts) == 1 and alerts[0][0] == store.row("boom")["id"])

            store.enqueue({**job_row(KEY, None, "noop", {}, "died-last", clock()), "max_attempts": 2})
            store.claim(KEY, "w-a", config.LEASE_SECONDS, 5)
            clock.add(minutes=11)
            store.claim(KEY, "w-b", config.LEASE_SECONDS, 5)                   # attempt 2, that worker dies too
            clock.add(minutes=11)
            ran.clear()
            run_tick(store, clock, done_kind)
            expect("a worker that died during the LAST attempt: the next tick marks the job dead without running it again",
                   ran == [] and store.row("died-last")["status"] == "dead" and len(alerts) == 2
                   and "stopped during the last attempt" in store.row("died-last")["last_error"])

            # ---- catch-up after 2 days off ----
            clock = Clock()
            store = FakeJobs(clock)
            for n, hours in enumerate([30, 2, 47, 12, 20]):
                store.enqueue(job_row(KEY, None, "noop", {}, f"due-{n}", clock.now + datetime.timedelta(hours=hours)))
            store.enqueue(job_row(KEY, None, "noop", {}, "future", clock.now + datetime.timedelta(days=3)))
            order = []
            clock.add(days=2)
            run_tick(store, clock, {"noop": lambda job, key, now: order.append(job["idempotency_key"]) or Outcome("done")})
            expect("machine off for 2 days: the first tick runs ALL 5 jobs that came due, oldest first; a job due in "
                   "3 days waits",
                   order == ["due-1", "due-3", "due-4", "due-0", "due-2"] and store.row("future")["status"] == "queued")

            # ---- tick budget ----
            clock = Clock()
            store = FakeJobs(clock)
            for n in range(3):
                store.enqueue(job_row(KEY, None, "noop", {}, f"budget-{n}", clock()))
            seconds = iter([0, 0, 500, 500, 500, 500])
            budget = run_tick(store, clock, done_kind, ticks=lambda: next(seconds))
            expect("after 8 minutes no new job is started: the rest are given back (queued, attempt not counted)",
                   budget["counts"]["done"] == 1 and budget["counts"]["given back"] == 2
                   and all(store.row(f"budget-{n}")["status"] == "queued" and store.row(f"budget-{n}")["attempts"] == 0
                           for n in (1, 2)))

            # ---- campaign_check: discover, reschedule, finished ----
            clock = Clock()
            store = FakeJobs(clock)
            camp = [{"tracked": True, "reference": "REF-CALL", "kind": "calls", "run": {"run_key": f"{KEY}-2026-10-r2"}},
                    {"tracked": True, "reference": "REF-MAIL", "kind": "email", "run": {"run_key": f"{KEY}-2026-10-r2"}},
                    {"tracked": False, "reference": "REF-OLD", "kind": "calls", "run": {"run_key": f"{KEY}-2026-10"}}]
            campaigns = lambda key, month=None: camp
            added, again = discover(KEY, store, campaigns, clock()), discover(KEY, store, campaigns, clock())
            expect("discover: one campaign_check job per TRACKED campaign (finished ones skipped), with the run's month; "
                   "the second time adds nothing; never a run_step job",
                   added == 2 and again == 0 and [r["idempotency_key"] for r in store.rows]
                   == ["campaign_check:REF-CALL", "campaign_check:REF-MAIL"]
                   and store.rows[0]["payload"] == {"reference": "REF-CALL", "month": "2026-10-r2", "kind": "calls"}
                   and not any(r["kind"] == "run_step" for r in store.rows))
            states = {"REF-CALL": [{"kind": "calls", "finished": False, "line": "on track"},
                                   {"kind": "calls", "finished": True, "line": "FINISHED"}],
                      "REF-MAIL": [{"kind": "email", "finished": False, "line": "n/a"}] * 2}
            seen = []
            fake_check_all = lambda key, month, now, reference: seen.append((month, reference)) or [states[reference].pop(0)]
            check_kinds = {"campaign_check": lambda job, key, now: kinds.campaign_check(job, key, now, fake_check_all)}
            run_tick(store, clock, check_kinds, campaigns=campaigns)
            call_row = dict(store.row("campaign_check:REF-CALL"))
            clock.now = parse(call_row["due_at"])
            run_tick(store, clock, check_kinds, campaigns=campaigns)
            expect("campaign_check: not finished -> the SAME row is queued again (attempts 0) at the next check time; "
                   "finished -> done; each check is for ONE campaign (its reference + month)",
                   call_row["status"] == "queued" and call_row["attempts"] == 0
                   and parse(call_row["due_at"]) == Clock().now + datetime.timedelta(hours=3)
                   and store.row("campaign_check:REF-CALL")["status"] == "done"
                   and store.row("campaign_check:REF-MAIL")["status"] == "queued"
                   and seen[0] == ("2026-10-r2", "REF-CALL") and len(store.rows) == 2)
            ist = lambda h, m=0: datetime.datetime(2026, 10, 19, h, m, tzinfo=kinds.IST)
            expect("next check: every 3 h, but also once after the window closed (calls 19:30, email 18:00 IST)",
                   kinds.next_campaign_check(ist(10), "calls") == ist(13)
                   and kinds.next_campaign_check(ist(18), "calls") == ist(19, 30)
                   and kinds.next_campaign_check(ist(16), "email") == ist(18)
                   and kinds.next_campaign_check(ist(20), "calls") == ist(23))

            # ---- run_step ----
            clock = Clock()
            store = FakeJobs(clock)
            runs = lambda key: [{"run_key": f"{KEY}-2026-10-r2", "status": "running"}, {"run_key": f"{KEY}-2026-09", "status": "done"}]
            replies = iter([(1, "STOPPED: run is busy: locked by PC-2 until ..."), (0, "Run ... | status running")])
            started = []
            step_kinds = {"run_step": lambda job, key, now: kinds.run_step(
                job, key, now, runs, lambda month: started.append(month) or next(replies))}
            with contextlib.redirect_stdout(io.StringIO()):
                lines = cli.enqueue_command(KEY, "run_step", "2026-10-r2", None, store, now=clock())
                twice = cli.enqueue_command(KEY, "run_step", "2026-10-r2", None, store, now=clock())
                at = cli.parse_at("2026-11-03T09:05:00+05:30")
                cli.enqueue_command(KEY, "run_step", "2026-09", at, store)
            run_tick(store, clock, step_kinds)
            busy = dict(store.rows[0])
            clock.add(minutes=2)
            run_tick(store, clock, step_kinds)
            clock.now = at
            run_tick(store, clock, step_kinds)
            expect("run_step (added by hand, now or at a set time; the same key twice = no-op): run lock held -> again "
                   "in 2 min, NOT counted as an attempt; then exit 0 -> done; a run that is not active -> failed for good",
                   lines[0].startswith("added: run_step:selftest-jobs-2026-10-r2:") and twice[0].startswith("already there")
                   and busy["status"] == "queued" and busy["attempts"] == 0
                   and parse(busy["due_at"]) == Clock().now + datetime.timedelta(minutes=2)
                   and store.rows[0]["status"] == "done" and started == ["2026-10-r2", "2026-10-r2"]
                   and store.rows[1]["idempotency_key"] == "run_step:selftest-jobs-2026-09:20261103T033500Z"
                   and store.rows[1]["status"] == "failed" and "is done: nothing to run" in store.rows[1]["last_error"])
            try:
                cli.parse_at("2026-11-03 09:05")
                zone_needed = False
            except Exception as error:
                zone_needed = "with a zone" in str(error)
            expect("--at needs a time zone (a bare time is refused)", zone_needed)

            # ---- heartbeat / status ----
            clock = Clock()
            store = FakeJobs(clock)
            store.enqueue({**job_row(KEY, None, "boom", {}, "dead-one", clock()), "status": "dead", "last_error": "x"})
            fresh = cli.status_lines(KEY, store, clock())
            run_tick(store, clock, done_kind)
            online = cli.status_lines(KEY, store, clock.now + datetime.timedelta(minutes=14))
            offline = cli.status_lines(KEY, store, clock.now + datetime.timedelta(minutes=16))
            expect("status: no tick ever / none for 15 min -> 'WORKER OFFLINE'; a recent tick -> online; dead jobs listed",
                   fresh[-1].startswith("JOBS: WORKER OFFLINE (no tick for ever") and online[-1].startswith("JOBS: worker online")
                   and offline[-1].startswith("JOBS: WORKER OFFLINE (no tick for 16 min")
                   and any(line.startswith("  DEAD #1 boom dead-one") for line in online))

            log = open(os.path.join(folder, f"brain-{datetime.date.today().isoformat()}.log"), encoding="utf-8").read()
            expect("one log line per job (WARNING for retry / dead)",
                   "step=job job #1 boom boom: retry (ConnectionError: Quickhowl did not answer)" in log
                   and "job #1 boom boom: dead" in log)

            from state_store import ALLOWED_ANSWERS, open_decision
            from quickicp import ToolError
            try:
                open_decision(1, config.ALERT_KIND, {"text": "x"})
                refused = False
            except ToolError:
                refused = True
            expect("state_store: job_dead answers only 'raised' and can never be an OPEN decision",
                   ALLOWED_ANSWERS["job_dead"] == {"raised"} and refused)
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    ok = all(checks)
    print(f"BRAIN JOBS SELFTEST {'OK' if ok else 'FAILED'} ({sum(checks)} of {len(checks)})")
    return ok
