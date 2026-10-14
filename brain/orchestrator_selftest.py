"""
orchestrator_selftest.py
========================

Every selftest of orchestrator.py (moved here unchanged on 2026-10-13, so orchestrator.py holds only
the run controller). Same command as before:

    python orchestrator.py selftest      (or: python orchestrator_selftest.py)

Throwaway selftest-* runs only, fake toolsets, a network guard (only Supabase allowed), no LLM and no
paid call; every test row is deleted at the end. On STATE_BACKEND=sqlite it uses a throwaway temp file.
The parts: load, run, tools, contacts, email, calls, 5a, 5b, close, run data, manual tools, imports.
e2e_test.py reuses expect / raises / network_guard / sweep_selftest_runs from here.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from orchestrator import *          # the run controller under test (same objects, e.g. WIRED_RUNNERS)  # noqa: F401,F403
from state_store import delete_test_run

# ================= SELFTEST (throwaway selftest-* run) =================

def expect(label: str, condition: bool) -> None:
    """Print PASS, or stop the test with the label."""
    if not condition:
        raise AssertionError(label)
    print(f"  PASS  {label}")


def raises(function) -> bool:
    """True if calling function() raises ToolError."""
    try:
        function()
    except ToolError:
        return True
    return False


def fake_work_orders() -> list[dict]:
    """4 small work orders: T1 and T2 (no deps, weeks 2 and 1), T3 after T1+T2, T4 after T3."""
    def wo(task_id, week, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": "QuickICP", "output_type": "accounts",
                "week": week, "depends_on": deps, "target": target, "instructions": ["test"]}
    return [wo("T1", 2, [], "40 accounts"), wo("T2", 1, [], "no numeric target"),
            wo("T3", 3, ["T1", "T2"], "10 contacts"), wo("T4", 4, ["T3"], "5 calls")]


def no_real_toolset(ctx):
    """Stands in for every REAL toolset during the selftest: using one is a test failure."""
    raise AssertionError(f"a real toolset was requested in the selftest ({ctx.task['task_id']})")


def selftest() -> bool:
    """All parts: load/status/next/answer, run (human tasks), tools (fake QuickICP). Each deletes its own rows."""
    import tempfile
    import state_store
    real_entries = dict(WIRED_RUNNERS)
    for name, (runner, _) in real_entries.items():           # real toolsets are never used in a selftest
        WIRED_RUNNERS[name] = (runner, no_real_toolset)
    temp_dir = None
    if state_store.STATE_BACKEND == "sqlite":                # never the real data/brain_state.db
        temp_dir = tempfile.mkdtemp(prefix="brain_selftest_")
        state_store._backend = state_store.SqliteBackend(os.path.join(temp_dir, "selftest.db"))
        print(f"Selftest on a throwaway SQLite file in {temp_dir}")
    try:
        with network_guard() as blocked:
            results = [selftest_load(), selftest_run(), selftest_tools(), selftest_contacts(),
                       selftest_email(), selftest_calls(), selftest_5a(), selftest_5b(), selftest_close(),
                       selftest_run_data(), selftest_percent(), selftest_network(), selftest_manual_tools(), selftest_imports()]
        print(f"\nNetwork guard: {len(blocked)} call(s) outside Supabase attempted (blocked): {blocked}")
        return all(results) and not blocked
    finally:
        WIRED_RUNNERS.update(real_entries)
        if temp_dir:
            state_store._backend.close()
            state_store._backend = None
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


def sweep_selftest_runs(customer: str) -> tuple[int, bool]:
    """
    Delete EVERY run of a selftest-* customer, also one a failed load left half-done (not in the test's
    own list). delete_test_run() refuses anything not named selftest-*. Returns (rows deleted, any left).
    """
    deleted = sum(delete_test_run(run["id"]) for run in get_runs(customer))
    return deleted, bool(get_runs(customer))


def selftest_load() -> bool:
    """Exercise load / status / next / answer on a selftest-* customer; delete every row at the end."""
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    month = "2026-10"
    print(f"Orchestrator selftest: customer '{customer}'")
    orders = fake_work_orders()
    run, ok = None, False
    try:
        # Pure code checks (nothing written)
        expect("month format checked", raises(lambda: check_month("2026-13")) and raises(lambda: check_month("Oct")))
        expect("good plan has no errors", plan_errors(orders) == [])
        bad_dep = [dict(o) for o in orders]
        bad_dep[3] = {**bad_dep[3], "depends_on": ["T9"]}
        expect("unknown dependency refused", any("T9" in e for e in plan_errors(bad_dep)))
        loop = [dict(o) for o in orders]
        loop[0] = {**loop[0], "depends_on": ["T4"]}
        expect("dependency loop refused", any("loop" in e for e in plan_errors(loop)))
        dup = orders + [dict(orders[0])]
        expect("duplicate task id refused", any("more than once" in e for e in plan_errors(dup)))
        proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "override",
                 "overridden": True, "override_reason": "selftest", "work_orders_count": len(orders),
                 "validation_errors_at_approval": ["T4 test rule"], "work_orders_sha256": "x" * 64,
                 "month_plan_sha256": "y" * 64}
        expect("load with a broken plan writes nothing",
               raises(lambda: load_run(customer, month, loop, {}, {}, True, proof))
               and get_run(run_key_for(customer, month)) is None)
        expect("load WITHOUT approval proof refused, writes nothing",
               raises(lambda: load_run(customer, month, orders, {}, {}, True, None, "STOPPED: no approval proof (test)"))
               and get_run(run_key_for(customer, month)) is None)
        expect("proof for a different number of work orders refused, writes nothing",
               raises(lambda: load_run(customer, month, orders, {}, {}, True, {**proof, "work_orders_count": 9}))
               and get_run(run_key_for(customer, month)) is None)

        # Plan + revenue analysis
        revenue = {"revenue_goal": "not stated", "expected_orders_low": 4.0}
        full_plan = plan_for_run({"month": "Month 1"}, revenue)
        expect("plan_for_run adds 'revenue_analysis'", full_plan == {"month": "Month 1", "revenue_analysis": revenue})
        expect("plan_for_run refuses to overwrite an existing key",
               raises(lambda: plan_for_run({"month": "Month 1", "revenue_analysis": {}}, revenue)))
        expect("new run without plan (revenue analysis missing) refused, writes nothing",
               raises(lambda: load_run(customer, month, orders, None, {}, True, proof, None,
                                       "STOPPED: data/revenue_analysis.json is missing (test)"))
               and get_run(run_key_for(customer, month)) is None)

        # Load
        run, created = load_run(customer, month, orders, full_plan, {"customer_name": "Selftest"}, True, proof)
        tasks = get_tasks(run["id"])
        approvals = [d for d in get_decisions(run["id"]) if d["kind"] == "plan_approval"]
        expect("load creates run, 4 pending tasks, 1 approval, status running",
               created and len(tasks) == 4 and all(t["status"] == "pending" for t in tasks)
               and len(approvals) == 1 and run["status"] == "running")
        expect("run plan holds the month plan AND the revenue analysis",
               run["plan"]["month"] == "Month 1" and run["plan"]["revenue_analysis"] == revenue)
        expect("approval decision stores the full proof", approvals[0]["question"]["proof"] == proof)
        expect("status shows the override honestly",
               approval_line(approval_of(run["id"])).startswith("Plan approval: yes with OVERRIDE by Test Person")
               and "reason: selftest" in approval_line(approval_of(run["id"])))
        expect("a decision without proof shows 'no proof'",
               approval_line({"question": {"text": "Approve the month plan?"}})
               == "Plan approval: yes, approved BEFORE approval proof existed (no proof)")
        again, created = load_run(customer, month, orders, None, {}, True, None, "STOPPED: no approval proof (test)",
                                  "STOPPED: data/revenue_analysis.json is missing (test)")
        expect("load again needs no proof or revenue file: same run, plan kept, nothing added",
               not created and again["id"] == run["id"] and len(get_tasks(run["id"])) == 4
               and again["plan"]["revenue_analysis"] == revenue
               and len([d for d in get_decisions(run["id"]) if d["kind"] == "plan_approval"]) == 1)
        changed = [dict(o) for o in orders]
        changed[0] = {**changed[0], "target": "80 accounts"}
        expect("changed plan refused, tasks untouched",
               raises(lambda: load_run(customer, month, changed, {}, {}, True, proof))
               and get_tasks(run["id"])[0]["work_order"]["target"] == "40 accounts")

        # Ready / next
        expect("ready = T2 (week 1) then T1 (week 2); T3 waits",
               [t["task_id"] for t in ready_tasks(run["id"])] == ["T2", "T1"])
        for t in get_tasks(run["id"]):
            if t["task_id"] in ("T1", "T2"):
                update_task(t["id"], status="done")
        expect("after T1+T2 done, next is T3", [t["task_id"] for t in ready_tasks(run["id"])] == ["T3"])

        # Answer
        t3 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T3"][0]
        update_task(t3["id"], status="waiting_human", waiting_for="goal_check")
        question = open_decision(run["id"], "goal_check", {"text": "8 of 10 passed. Continue?"}, t3["id"])
        expect("wrong answer refused", raises(lambda: cmd_answer(customer, question["id"], "yes", None)))
        expect("another customer's decision refused",
               raises(lambda: cmd_answer("anvixa-ai-product", question["id"], "continue", None)))
        cmd_answer(customer, question["id"], "continue", "selftest")
        expect("second answer refused", raises(lambda: cmd_answer(customer, question["id"], "stop", None)))

        # Views do not crash
        show_status(find_run(customer, month))
        show_next(find_run(customer, None))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (load) OK" if ok else "ORCHESTRATOR SELFTEST (load) FAILED")
    return ok


def run_work_orders() -> list[dict]:
    """
    6 tasks for the run test:
        T1 CAM requirements (human)            T4 Email Agent after T1 (tool, NOT wired yet, week 1)
        T2 Content & Training after T1 (GATE)  T5 Human Tele Calling after T2
        T3 CAM review after T1 (human)         T6 CAM handover after T5
    """
    def wo(task_id, agent, output, types, week, deps):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": types, "week": week, "depends_on": deps, "target": "test",
                "instructions": ["test"], "done_when": f"{task_id} done", "escalate_if": "never"}
    return [wo("T1", "CAM", "coordination", "requirements, coordination", 1, []),
            wo("T2", "Content & Training", "client_training", "client_training, content", 2, ["T1"]),
            wo("T3", "CAM", "review", "review", 2, ["T1"]),
            wo("T4", "Email Agent", "emails", "outreach", 1, ["T1"]),
            wo("T5", "Human Tele Calling", "qualified_leads", "qualification", 3, ["T2"]),
            wo("T6", "CAM", "handover", "handover", 4, ["T5"])]


def selftest_run() -> bool:
    """'run' with human tasks, gate rule, stop rule, crash recovery; the email route is unwired here."""
    email_entry = WIRED_RUNNERS.pop("email")
    try:
        return selftest_run_body()
    finally:
        WIRED_RUNNERS["email"] = email_entry


def selftest_run_body() -> bool:
    """Exercise 'run' with human tasks, gate rule, stop rule and crash recovery. No tool, no LLM."""
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator RUN selftest: customer '{customer}'")
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 6, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    plan = {"month": "Month 1", "revenue_analysis": {}}
    runs, ok = [], False

    def task(run, task_id):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == task_id][0]

    def decision_for(run, task_id):
        row = task(run, task_id)
        return [d for d in get_decisions(run["id"]) if d["task_row_id"] == row["id"] and d["kind"] in HUMAN_KINDS]

    try:
        run, _ = load_run(customer, "2026-11", run_work_orders(), plan, {}, True, proof)
        runs.append(run)

        # 1. first run: only T1 is ready -> human decision, waiting
        result = cmd_run(customer, "2026-11")
        t1 = task(run, "T1")
        expect("run 1: T1 started as a human task, waiting, 1 decision",
               result["started"] == "T1" and t1["status"] == "waiting_human" and t1["waiting_for"] == "human_task"
               and len(decision_for(run, "T1")) == 1)
        d1 = decision_for(run, "T1")[0]
        expect("normal human task offers done / skip / stop", d1["question"]["allowed_answers"] == ["done", "skip", "stop"])
        result = cmd_run(customer, "2026-11")
        expect("run 2: nothing new while T1 waits; still 1 decision, run running",
               result["started"] is None and len(decision_for(run, "T1")) == 1 and result["run_status"] == "running")

        # 2. answer needs a note; done -> applied on the next run
        expect("answer without a note refused", raises(lambda: cmd_answer(customer, d1["id"], "done", None)))
        cmd_answer(customer, d1["id"], "done", "requirements meeting held")
        result = cmd_run(customer, "2026-11")
        expect("run 3: T1 done (note kept); tool task T4 reported 'not wired', stays pending; T2 started",
               task(run, "T1")["status"] == "done" and task(run, "T1")["result"]["note"] == "requirements meeting held"
               and any("T4" in w for w in result["not_wired"]) and task(run, "T4")["status"] == "pending"
               and result["started"] == "T2")

        # 3. GATE rule: T2 (client_training, content) offers only done / stop; skip refused
        d2 = decision_for(run, "T2")[0]
        expect("gate task T2: decision kind human_gate_task, answers done / stop only",
               d2["kind"] == "human_gate_task" and d2["question"]["allowed_answers"] == ["done", "stop"])
        expect("gate task: 'skip' refused", raises(lambda: cmd_answer(customer, d2["id"], "skip", "no time")))

        # 4. crash recovery on T3: 'running' without a decision, then 'running' WITH an open decision
        t3 = task(run, "T3")
        update_task(t3["id"], status="running", started_at=now_iso())          # crash after 'running'
        result = cmd_run(customer, "2026-11")
        expect("crash after 'running' (no decision): resumed, 1 decision, waiting_human",
               "T3" in result["resumed"] and len(decision_for(run, "T3")) == 1
               and task(run, "T3")["status"] == "waiting_human")
        update_task(t3["id"], status="running", waiting_for=None)              # crash after the decision opened
        cmd_run(customer, "2026-11")
        expect("crash after decision opened: SAME decision reused (still 1), waiting_human",
               len(decision_for(run, "T3")) == 1 and task(run, "T3")["status"] == "waiting_human")

        # 5. skip on a normal task -> done with skipped = true. Crash case: answered while the task
        #    was still 'running' (crash before waiting_human): the answer must be used, not asked again.
        d3 = decision_for(run, "T3")[0]
        update_task(t3["id"], status="running", waiting_for=None)
        cmd_answer(customer, d3["id"], "skip", "review merged into T1 meeting")
        # 6. STOP rule: stop gate T2 -> only T2 stopped; T5, T6 depend on it and never start
        cmd_answer(customer, d2["id"], "stop", "client cancelled training")
        result = cmd_run(customer, "2026-11")
        expect("skip -> T3 done with result.skipped = true",
               task(run, "T3")["status"] == "done" and task(run, "T3")["result"]["skipped"] is True)
        expect("answered while 'running' after a crash: answer used, no second question",
               len(decision_for(run, "T3")) == 1 and "T3" in result["resumed"])
        expect("stop -> ONLY T2 stopped, note in last_error",
               task(run, "T2")["status"] == "stopped" and "client cancelled training" in task(run, "T2")["last_error"])
        expect("tasks after the stopped one never start; run still 'running' (T4 can still move)",
               task(run, "T5")["status"] == "pending" and task(run, "T6")["status"] == "pending"
               and result["started"] is None and result["run_status"] == "running")
        expect("blocked tasks found: T5 and T6 blocked by T2",
               blocked_tasks(get_tasks(run["id"])) == {"T5": ["T2"], "T6": ["T2"]})

        # 7. when nothing can move anymore -> run 'stopped' with a stop_reason listing the stopped task
        update_task(task(run, "T4")["id"], status="done")                      # stands in for a future tool runner
        result = cmd_run(customer, "2026-11")
        stopped_run = get_run(run["run_key"])
        expect("nothing ready / waiting / running + T2 stopped -> run 'stopped', reason lists T2, T5, T6",
               result["run_status"] == "stopped" and "T2 (" in stopped_run["stop_reason"]
               and "T5, T6" in stopped_run["stop_reason"])
        expect("a stopped run does nothing", cmd_run(customer, "2026-11")["started"] is None)

        # 8. all done -> run 'done' (second run with only human tasks)
        done_run, _ = load_run(customer, "2026-12", run_work_orders()[:1], plan, {}, True, {**proof, "work_orders_count": 1})
        runs.append(done_run)
        cmd_run(customer, "2026-12")
        cmd_answer(customer, decision_for(done_run, "T1")[0]["id"], "done", "ok")
        expect("every task done -> run 'done'", cmd_run(customer, "2026-12")["run_status"] == "done")

        # 9. no tool module was imported anywhere in this test
        expect("no runner was called for the unwired tool task", task(run, "T4")["started_at"] is None)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (run) OK" if ok else "ORCHESTRATOR SELFTEST (run) FAILED")
    return ok


@contextmanager
def network_guard():
    """Selftest safety net: any HTTP call to a host other than Supabase is blocked and listed."""
    import httpx
    from urllib.parse import urlparse
    allowed = urlparse(os.getenv("SUPABASE_URL") or "").hostname
    blocked: list[str] = []
    original = httpx.Client.send

    def guarded(self, request, *args, **kwargs):
        if request.url.host != allowed:
            blocked.append(str(request.url.host))
            raise RuntimeError(f"selftest network guard: blocked a call to {request.url.host}")
        return original(self, request, *args, **kwargs)

    import requests
    original_requests = requests.Session.send

    def guarded_requests(self, request, *args, **kwargs):      # quickhowl.py uses 'requests'
        host = urlparse(request.url).hostname
        if host != allowed:
            blocked.append(str(host))
            raise RuntimeError(f"selftest network guard: blocked a call to {host}")
        return original_requests(self, request, *args, **kwargs)

    httpx.Client.send = guarded
    requests.Session.send = guarded_requests
    try:
        yield blocked
    finally:
        httpx.Client.send = original
        requests.Session.send = original_requests


def selftest_tools() -> bool:
    """quickicp_accounts runner + run lock + --dry, with FakeAccountsTools only (no network, no LLM)."""
    from runner_quickicp_accounts import FakeAccountsTools, SimulatedCrash
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator TOOL selftest (fake QuickICP): customer '{customer}'")
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 1, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    runs, ok = [], False
    saved_override = os.environ.pop("TEST_TARGET_OVERRIDE", None)
    real_entry = WIRED_RUNNERS["quickicp_accounts"]

    def new_run(month: str, target: str, fake: FakeAccountsTools) -> dict:
        wo = {"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "accounts",
              "task_type": "data_build", "week": 1, "depends_on": [], "target": target, "instructions": ["test"]}
        run, _ = load_run(customer, month, [wo], {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        runs.append(run)
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: fake)
        return run

    def t1(run: dict) -> dict:
        return get_tasks(run["id"])[0]

    def rows(run: dict, step: str) -> list[dict]:
        from state_store import get_steps
        return [r for r in get_steps(t1(run)["id"]) if r["step"] == step]

    def kinds(run: dict, kind: str) -> list[dict]:
        return [d for d in get_decisions(run["id"]) if d["kind"] == kind]

    def existing(n_good: int, n_bad: int = 0) -> list[dict]:
        return ([{**FakeAccountsTools.company(f"ex{i}.test", True), "_id": 500 + i} for i in range(1, n_good + 1)]
                + [{**FakeAccountsTools.company(f"bad{i}.test", False), "_id": 700 + i} for i in range(1, n_bad + 1)])

    def crash(month: str) -> bool:
        try:
            cmd_run(customer, month)
        except SimulatedCrash:
            return True
        return False

    try:
        # S0. industry keywords: no Prospeo names -> synonyms ONCE; only names Prospeo returned (fake lookup, no network)
        from quickicp import industry_options
        known = {"e-commerce": [], "retail": ["Retail"], "online retail": ["Retail", "Online Retail"],
                 "internet marketplace": [], "manufacturing": ["Manufacturing"], "logistics": []}
        looked_up = []

        def fake_lookup(field, keyword):
            looked_up.append((field, keyword))
            return list(known.get(keyword.lower(), []))
        found = industry_options(["E-commerce", "Manufacturing", "Logistics"], lookup=fake_lookup)
        expect("S0 'E-commerce' finds nothing -> synonyms retail / online retail / internet marketplace, once each",
               [k for _, k in looked_up] == ["E-commerce", "retail", "online retail", "internet marketplace",
                                             "Manufacturing", "Logistics"]
               and all(field == "industry_search" for field, _ in looked_up))
        expect("S0 synonym names listed under 'E-commerce', each once; a keyword with names gets no synonyms",
               found == {"E-commerce": ["Retail", "Online Retail"], "Manufacturing": ["Manufacturing"], "Logistics": []})

        # S1. reuse covers the target -> NO paid search
        fake = FakeAccountsTools(existing=existing(12))
        run = new_run("2027-01", "10 accounts", fake)
        cmd_run(customer, "2027-01")
        task = t1(run)
        expect("S1 reuse: enough companies already in Supabase -> 0 paid searches, task done, achieved 12",
               fake.calls["search"] == 0 and task["status"] == "done" and task["achieved_number"] == 12)
        expect("S1 company ids of this run recorded with customer + run_key; brain review 'done' recorded",
               task["result"]["company_ids"] == list(range(501, 513)) and task["result"]["customer"] == customer
               and task["result"]["run_key"] == run["run_key"] and kinds(run, "brain_review")[-1]["answer"] == "done")
        expect("S1 lock released after the run", get_run(run["run_key"])["locked_by"] is None)

        # S2. shortfall only; an existing domain in the reply is never counted again
        ledger_seen = []
        fake = FakeAccountsTools(existing=existing(6, 4))
        run = new_run("2027-02", "10 accounts", fake)
        fake.on_search = lambda: ledger_seen.append([r["status"] for r in rows(run, "search")][-1])
        original_search = fake.search

        def search_with_known_domain(payload):
            found = original_search(payload)
            return ([FakeAccountsTools.company("ex1.test", True)] + found[1:]) if fake.calls["search"] == 1 else found
        fake.search = search_with_known_domain
        cmd_run(customer, "2027-02")
        task = t1(run)
        first_request = rows(run, "search")[0]["request"]["payload"]["company_count"]
        expect("S2 buys only the shortfall: first search asks for 4 (= 10 target - 6 reused)", first_request == 4)
        expect("S2 the paid row was already 'running' INSIDE the call (saved before acting)",
               ledger_seen and all(status == "running" for status in ledger_seen))
        expect("S2 existing domain in the reply not counted -> 1 extra round -> done with 10 (6 reused + 4 new)",
               fake.calls["search"] == 2 and task["status"] == "done" and task["achieved_number"] == 10
               and task["result"]["bought"] == 4 and all(r["paid"] for r in rows(run, "search")))

        # S3. crash DURING the paid search -> unknown + human; never repeated automatically; rerun
        fake = FakeAccountsTools(crash_search=1)
        run = new_run("2027-03", "5 accounts", fake)
        expect("S3 crash during the paid search", crash("2027-03"))
        expect("S3 row left 'running', lock still released (finally)",
               rows(run, "search")[0]["status"] == "running" and get_run(run["run_key"])["locked_by"] is None)
        cmd_run(customer, "2027-03")
        expect("S3 next run: row 'unknown', 1 paid_call_unknown decision, task waiting, NOT searched again",
               rows(run, "search")[0]["status"] == "unknown" and len(kinds(run, "paid_call_unknown")) == 1
               and t1(run)["status"] == "waiting_human" and fake.calls["search"] == 1)
        cmd_run(customer, "2027-03")
        expect("S3 run again without an answer: still 1 decision, still no new search",
               len(kinds(run, "paid_call_unknown")) == 1 and fake.calls["search"] == 1)
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "rerun", "checked n8n: it did not run")
        cmd_run(customer, "2027-03")
        expect("S3 rerun: old row closed as 'failed' (human decided), a NEW search key, task done with 5",
               [r["status"] for r in rows(run, "search")] == ["failed", "done"] and fake.calls["search"] == 2
               and t1(run)["status"] == "done" and t1(run)["achieved_number"] == 5)
        expect("S3 payload built only once across the crash (reused from the ledger)", fake.calls["build_payload"] == 1)

        # S4. crash AFTER the search, before the judge -> saved reply judged, no new search
        fake = FakeAccountsTools(crash_judge=1)
        run = new_run("2027-04", "5 accounts", fake)
        expect("S4 crash before the judge", crash("2027-04"))
        cmd_run(customer, "2027-04")
        expect("S4 next run judges the SAVED reply: still 1 search, task done with 5",
               fake.calls["search"] == 1 and t1(run)["status"] == "done" and t1(run)["achieved_number"] == 5)

        # S5. goal check: more, more, then only continue / stop; continue -> done with shortfall
        fake = FakeAccountsTools(good_rate=0)
        run = new_run("2027-05", "5 accounts", fake)
        cmd_run(customer, "2027-05")
        g1 = kinds(run, "goal_check")
        expect("S5 short after round 0 + 2 extra rounds -> goal_check (continue / more / stop)",
               fake.calls["search"] == 3 and len(g1) == 1 and g1[0]["question"]["allowed_answers"] == ["continue", "more", "stop"]
               and t1(run)["waiting_for"] == "goal_check")
        cmd_answer(customer, g1[0]["id"], "more", "try other industries")
        cmd_run(customer, "2027-05")
        expect("S5 'more' -> exactly 2 more rounds, then a new goal_check", fake.calls["search"] == 5
               and len(kinds(run, "goal_check")) == 2)
        cmd_answer(customer, kinds(run, "goal_check")[-1]["id"], "more", "one last try")
        cmd_run(customer, "2027-05")
        final = kinds(run, "goal_check_final")
        expect("S5 after 2 'more': goal_check_final offers only continue / stop", fake.calls["search"] == 7
               and len(final) == 1 and final[0]["question"]["allowed_answers"] == ["continue", "stop"])
        expect("S5 'more' refused at the final check", raises(lambda: cmd_answer(customer, final[0]["id"], "more", "x")))
        cmd_answer(customer, final[0]["id"], "continue", "go on with what we have")
        cmd_run(customer, "2027-05")
        expect("S5 continue -> done with the shortfall recorded, no new search",
               t1(run)["status"] == "done" and t1(run)["result"]["shortfall"] == 5 and fake.calls["search"] == 7)

        # S6. stop at the goal check -> task stopped -> run stopped (nothing else can move)
        fake = FakeAccountsTools(good_rate=0)
        run = new_run("2027-06", "3 accounts", fake)
        cmd_run(customer, "2027-06")
        cmd_answer(customer, kinds(run, "goal_check")[0]["id"], "stop", "wrong segment")
        result = cmd_run(customer, "2027-06")
        expect("S6 stop -> task stopped, run stopped", t1(run)["status"] == "stopped" and result["run_status"] == "stopped")

        # S7. skip an unknown paid search -> that round counts as empty, the loop goes on
        fake = FakeAccountsTools(crash_search=1)
        run = new_run("2027-07", "4 accounts", fake)
        crash("2027-07")
        cmd_run(customer, "2027-07")
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "skip", "do not risk paying twice")
        cmd_run(customer, "2027-07")
        expect("S7 skip -> round treated as empty -> new filters (adjust) -> done with 4",
               fake.calls["adjust"] == 1 and fake.calls["search"] == 2 and t1(run)["status"] == "done"
               and t1(run)["achieved_number"] == 4)

        # S8. known failure (ToolError) in the search -> task_failed decision -> retry
        fake = FakeAccountsTools(fail_search=1)
        run = new_run("2027-08", "4 accounts", fake)
        cmd_run(customer, "2027-08")
        failed_q = kinds(run, "task_failed")
        expect("S8 known failure -> row 'failed', task_failed decision (retry / skip / stop), task waiting",
               rows(run, "search")[0]["status"] == "failed" and len(failed_q) == 1
               and t1(run)["waiting_for"] == "task_failed" and "502" in t1(run)["last_error"])
        cmd_answer(customer, failed_q[0]["id"], "retry", "n8n is back")
        cmd_run(customer, "2027-08")
        expect("S8 retry -> new search, done with 4", fake.calls["search"] == 2 and t1(run)["status"] == "done")

        # S9. run lock
        fake = FakeAccountsTools(existing=existing(5))
        run = new_run("2027-09", "5 accounts", fake)
        take_run_lock(run["id"], "other-process")
        expect("S9 a second 'run' while locked is refused", raises(lambda: cmd_run(customer, "2027-09"))
               and t1(run)["status"] == "pending")
        force_unlock(run["id"])
        expect("S9 unlock frees it", get_run(run["run_key"])["locked_by"] is None)
        take_run_lock(run["id"], "crashed-process", minutes=-1)            # an expired lease
        cmd_run(customer, "2027-09")
        expect("S9 an EXPIRED lock is taken over; released afterwards",
               t1(run)["status"] == "done" and get_run(run["run_key"])["locked_by"] is None)

        # S10. --dry: shows the shortfall, records nothing, buys nothing
        fake = FakeAccountsTools(existing=existing(3))
        run = new_run("2027-10", "8 accounts", fake)
        result = cmd_dry(customer, "2027-10")
        expect("S10 --dry: target 8, 3 reused, would buy 5; nothing recorded, task still pending",
               result.output["shortfall"] == 5 and result.output["would_request"] == 5 and fake.calls["search"] == 0
               and rows(run, "payload") == [] and t1(run)["status"] == "pending" and not get_decisions(run["id"])[1:])

        # TEST_TARGET_OVERRIDE (test mode only)
        from runner_quickicp_accounts import runner_target
        os.environ["TEST_TARGET_OVERRIDE"] = "7"
        expect("TEST_TARGET_OVERRIDE=7 used in test mode", runner_target(t1(run)) == 7)
        os.environ["TEST_TARGET_OVERRIDE"] = "seven"
        expect("TEST_TARGET_OVERRIDE must be a whole number", raises(lambda: runner_target(t1(run))))
        os.environ.pop("TEST_TARGET_OVERRIDE")
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        if saved_override is not None:
            os.environ["TEST_TARGET_OVERRIDE"] = saved_override
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {len(runs)} test runs, {total} rows deleted, "
              f"{'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (tools) OK" if ok else "ORCHESTRATOR SELFTEST (tools) FAILED")
    return ok


def selftest_contacts() -> bool:
    """quickicp_contacts runner (T4) with FakeContactsTools only, + save_people fill rule + phone types."""
    from runner_quickicp_accounts import SimulatedCrash
    from runner_quickicp_contacts import FakeContactsTools
    from supabase_store import (COMPANY_TABLE, PEOPLE_TABLE, db, delete_selftest_rows, phone_type,
                                save_companies, save_people, saved_people)
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    other = customer + "-other"
    print(f"\nOrchestrator CONTACTS selftest (fake QuickICP): customer '{customer}'")
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 2, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    runs, ok = [], False
    saved_override = os.environ.pop("TEST_TARGET_OVERRIDE", None)
    real_entry = WIRED_RUNNERS["quickicp_contacts"]
    person = FakeContactsTools.person

    def companies(n: int, first_id: int = 11) -> list[dict]:
        return [{"id": i, "customer": customer, "website": f"c{i}.test", "linkedin": None,
                 "company_name": f"C{i}", "industry": "x"} for i in range(first_id, first_id + n)]

    def new_run(month: str, target: str, fake: FakeContactsTools, company_ids: list[int]) -> dict:
        def wo(task_id, output, deps, tgt):
            return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": "QuickICP", "output_type": output,
                    "task_type": "data_build", "week": 1, "depends_on": deps, "target": tgt, "instructions": ["test"]}
        run, _ = load_run(customer, month, [wo("T1", "accounts", [], "40 accounts"), wo("T2", "contacts", ["T1"], target)],
                          {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        runs.append(run)
        t1 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T1"][0]
        update_task(t1["id"], status="done", result={"company_ids": company_ids, "customer": customer,
                                                     "run_key": run["run_key"]})
        WIRED_RUNNERS["quickicp_contacts"] = (real_entry[0], lambda ctx: fake)
        return run

    def t2(run: dict) -> dict:
        return [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]

    def rows(run: dict, step: str) -> list[dict]:
        from state_store import get_steps
        return [r for r in get_steps(t2(run)["id"]) if r["step"] == step]

    def kinds(run: dict, kind: str) -> list[dict]:
        return [d for d in get_decisions(run["id"]) if d["kind"] == kind]

    def crash(month: str) -> bool:
        try:
            cmd_run(customer, month)
        except SimulatedCrash:
            return True
        return False

    try:
        # phone types (code rule)
        expect("phone_type: +911126142451 landline, +91 98113 00501 mobile, 01126142451 landline, junk unknown",
               phone_type("+911126142451") == "landline" and phone_type("+91 98113 00501") == "mobile"
               and phone_type("9811300501") == "mobile" and phone_type("01126142451") == "landline"
               and phone_type("12345") == "unknown" and phone_type(None) == "unknown")

        # K1. reuse covers the target; enrich ONLY passed people missing email / phone
        cs = companies(3)
        existing = [person(f"e{i}", f"c{11 + i % 3}.test", True, email=None if i in (1, 2) else f"e{i}@x.test",
                           phone=None if i in (2, 3, 4) else "+919811300501", _id=600 + i, company_id=11 + i % 3)
                    for i in range(1, 13)]
        existing.append(person("lowlevel", "c11.test", False, _id=700, company_id=11))     # fails the judge
        existing.append(person("elsewhere", "c99.test", True, _id=701, company_id=99))    # not a T3 company
        fake = FakeContactsTools(cs, existing=existing)
        run = new_run("2028-01", "10 contacts", fake, [11, 12, 13])
        cmd_run(customer, "2028-01")
        task = t2(run)
        expect("K1 reuse covers target 10 (12 pass): 0 people searches, task done, achieved 12",
               fake.calls["search"] == 0 and task["status"] == "done" and task["achieved_number"] == 12)
        expect("K1 email finder got EXACTLY the 2 passed people without email",
               sorted(fake.sent["emails"]) == sorted(["https://linkedin.com/in/e1", "https://linkedin.com/in/e2"]))
        expect("K1 phone finder got EXACTLY the 3 passed people without phone",
               sorted(fake.sent["phones"]) == sorted([f"https://linkedin.com/in/e{i}" for i in (2, 3, 4)]))
        out = task["result"]
        expect("K1 output: 12 people ids, all with email + phone, mobile/landline counted, customer + run_key",
               len(out["people_ids"]) == 12 and out["with_email"] == 12 and out["with_phone"] == 12
               and out["mobile"] + out["landline"] == 12 and out["landline"] >= 1
               and out["customer"] == customer and out["run_key"] == run["run_key"])
        expect("K1 brain review 'done' recorded", kinds(run, "brain_review")[-1]["answer"] == "done")

        # K2. shortfall only, best companies first, another customer's company ignored, known person not recounted
        cs = companies(8) + [{"id": 99, "customer": other, "website": "c99.test", "linkedin": None,
                              "company_name": "C99", "industry": "x"}]
        scores = {11: 10, 12: 90, 13: 80, 14: 70, 15: 60, 16: 50, 17: 40, 18: 30, 99: 100}
        existing = [person(f"k{i}", "c11.test", True, email=f"k{i}@x.test", phone="+919811300501",
                           _id=610 + i, company_id=11) for i in range(1, 5)]
        ledger_seen = []
        fake = FakeContactsTools(cs, scores=scores, existing=existing, repeat_social="https://linkedin.com/in/k1")
        run = new_run("2028-02", "10 contacts", fake, [11, 12, 13, 14, 15, 16, 17, 18, 99])
        fake.on_search = lambda: ledger_seen.append(rows(run, "people")[-1]["status"])
        cmd_run(customer, "2028-02")
        task = t2(run)
        expect("K2 company of ANOTHER customer ignored (1 id dropped)",
               rows(run, "companies")[0]["output"]["dropped_other_customer"] == 1)
        expect("K2 first batch: best 5 companies by score (c12, c13, c14, c15, c16), 2 people each, for a shortfall of 6",
               fake.payloads[0]["domains"] == ["c12.test", "c13.test", "c14.test", "c15.test", "c16.test"]
               and fake.payloads[0]["count_per_company"] == 2)
        expect("K2 paid batch row already 'running' INSIDE the call", ledger_seen == ["running"])
        expect("K2 a person we already had is not counted again: 4 reused + 9 new = 13, 1 search",
               task["status"] == "done" and task["achieved_number"] == 13 and task["result"]["bought"] == 9
               and fake.calls["search"] == 1)

        # K3. crash in BATCH 2 of round 0 -> unknown -> rerun: batch 1 replayed, never bought again
        os.environ["TEST_TARGET_OVERRIDE"] = "40"
        fake = FakeContactsTools(companies(30), crash_search=2)
        run = new_run("2028-03", "350 contacts", fake, list(range(11, 41)))
        expect("K3 crash during batch 2 of round 0", crash("2028-03"))
        cmd_run(customer, "2028-03")
        expect("K3 next run: batch 2 'unknown', 1 paid_call_unknown decision, no new search",
               [r["status"] for r in rows(run, "people")] == ["done", "unknown"]
               and len(kinds(run, "paid_call_unknown")) == 1 and fake.calls["search"] == 2)
        cmd_run(customer, "2028-03")
        expect("K3 run again without an answer: still 1 decision, still no new search",
               len(kinds(run, "paid_call_unknown")) == 1 and fake.calls["search"] == 2)
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "rerun", "n8n log shows it did not run")
        cmd_run(customer, "2028-03")
        keys = {r["idempotency_key"].split(":T2:")[1]: r["status"] for r in rows(run, "people")}
        expect("K3 rerun: batch 1 REPLAYED (not bought again), batch 2 attempt a2 bought, task done",
               fake.calls["search"] == 3 and keys == {"people:r0:b1:a1": "done", "people:r0:b2:a1": "failed",
                                                      "people:r0:b2:a2": "done"} and t2(run)["status"] == "done")
        expect("K3 test cap: email and phone finders got at most 6 people each",
               len(fake.sent["emails"]) == 6 and len(fake.sent["phones"]) == 6)
        os.environ.pop("TEST_TARGET_OVERRIDE")

        # K4. crash in the email finder -> skip -> those people stay without email; phones still run
        existing = [person(f"m{i}", "c11.test", True, _id=620 + i, company_id=11) for i in range(1, 11)]
        fake = FakeContactsTools(companies(1), existing=existing, crash_enrich=("emails", 1))
        run = new_run("2028-04", "10 contacts", fake, [11])
        expect("K4 crash during the email finder", crash("2028-04"))
        cmd_run(customer, "2028-04")
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "skip", "do not pay twice")
        cmd_run(customer, "2028-04")
        task = t2(run)
        expect("K4 skip -> no emails, email finder not called again, phone finder ran, task done",
               fake.calls["emails"] == 1 and task["result"]["with_email"] == 0 and fake.calls["phones"] == 1
               and task["status"] == "done")

        # K5. goal check: more, then 'nothing left to try' -> final (continue / stop) -> continue
        fake = FakeContactsTools(companies(3), good_rate=0)
        run = new_run("2028-05", "10 contacts", fake, [11, 12, 13])
        cmd_run(customer, "2028-05")
        g = kinds(run, "goal_check")
        expect("K5 short after round 0 + 2 extra rounds (more companies -> more per company) -> goal_check",
               fake.calls["search"] == 3 and len(g) == 1 and g[0]["question"]["allowed_answers"] == ["continue", "more", "stop"])
        cmd_answer(customer, g[0]["id"], "more", "try more per company")
        cmd_run(customer, "2028-05")
        final = kinds(run, "goal_check_final")
        expect("K5 'more' -> 1 round until nothing is left to try -> goal_check_final (continue / stop)",
               fake.calls["search"] == 4 and len(final) == 1 and final[0]["question"]["allowed_answers"] == ["continue", "stop"])
        cmd_answer(customer, final[0]["id"], "continue", "go on")
        cmd_run(customer, "2028-05")
        expect("K5 continue -> done with the shortfall recorded", t2(run)["status"] == "done"
               and t2(run)["result"]["shortfall"] == 10)

        # K6. --dry for contacts
        existing = [person(f"d{i}", "c11.test", True, email=None if i == 1 else f"d{i}@x.test",
                           phone="+919811300501", _id=640 + i, company_id=11) for i in range(1, 4)]
        fake = FakeContactsTools(companies(5), existing=existing)
        run = new_run("2028-06", "10 contacts", fake, [11, 12, 13, 14, 15])
        result = cmd_dry(customer, "2028-06")
        expect("K6 --dry: shortfall 7 -> ~10 people (5 companies x 2), 6 emails, 6 phones (test cap); nothing recorded",
               result.output["shortfall"] == 7 and result.output["would_people"] == 10
               and result.output["would_emails"] == 6 and result.output["would_phones"] == 6
               and fake.calls["search"] == 0 and rows(run, "titles") == [] and t2(run)["status"] == "pending")

        # K7. save_people fills an EMPTY email / phone, never overwrites (real QuickICP tables, selftest customer)
        save_companies([{"COMPANY": "Fill Test", "WEBSITE": "filltest.test"}], customer)
        lookups = __import__("supabase_store").company_lookups(customer)
        base = {"FULL NAME": "Fill Test", "USER SOCIAL": "https://linkedin.com/in/fill-test",
                "COMPANY URL": "filltest.test"}
        save_people([{**base, "email": "first@x.test", "phone": None}], lookups, customer)
        save_people([{**base, "email": "second@x.test", "phone": "+919811300501"}], lookups, customer)
        row = saved_people(customer)["https://linkedin.com/in/fill-test"]
        expect("K7 save_people: empty phone filled, existing email NOT overwritten",
               row["email"] == "first@x.test" and row["phone_number"] == "+919811300501")
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_contacts"] = real_entry
        os.environ.pop("TEST_TARGET_OVERRIDE", None)
        if saved_override is not None:
            os.environ["TEST_TARGET_OVERRIDE"] = saved_override
        quickicp_deleted = delete_selftest_rows(customer)
        left_rows = (len(db.table(COMPANY_TABLE).select("id").eq("customer", customer).execute().data)
                     + len(db.table(PEOPLE_TABLE).select("id").eq("customer", customer).execute().data))
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left or left_rows else 'PASS'}  cleanup: {len(runs)} test runs, {total} state rows and "
              f"{quickicp_deleted} QuickICP rows deleted, {'SOME' if left or left_rows else 'none'} left")
        ok = ok and not left and not left_rows
    print("ORCHESTRATOR SELFTEST (contacts) OK" if ok else "ORCHESTRATOR SELFTEST (contacts) FAILED")
    return ok


def outreach_fixture(customer: str, month: str, agent: str, output: str, person_ids: list[int]) -> dict:
    """A selftest run: T1 = done QuickICP contacts task with person ids; T2 = the outreach task after it."""
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 2, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent_, output_, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent_, "output_type": output_,
                "task_type": "outreach", "week": 1, "depends_on": deps, "target": target, "instructions": ["test"]}
    run, _ = load_run(customer, month, [wo("T1", "QuickICP", "contacts", [], "10 contacts"),
                                        wo("T2", agent, output, ["T1"], "contacts reached")],
                      {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
    t1 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T1"][0]
    update_task(t1["id"], status="done", result={"people_ids": person_ids, "customer": customer, "run_key": run["run_key"]})
    return run


def selftest_email() -> bool:
    """Email runner (T11) with FakeEmailTools, + known issue 4 and send_launch 'pending' (no network, no LLM)."""
    import email_tool
    from runner_email import FakeEmailTools
    from runner_quickicp_accounts import SimulatedCrash
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator EMAIL selftest (fake email tool): customer '{customer}'")
    real_entry = WIRED_RUNNERS["email"]
    ok = False
    rows = [{"id": 901 + i, "customer": customer, "full_name": f"Person {i}", "email": f"p{i}@prospect.test",
             "company": {"company_name": f"C{i}"}} for i in range(5)]

    def setup(month, fake):
        WIRED_RUNNERS["email"] = (real_entry[0], lambda ctx: fake)
        return outreach_fixture(customer, month, "Email Agent", "emails", [r["id"] for r in fake.rows])

    def t2(run):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]

    def rows_of(run, step):
        from state_store import get_steps
        return [r for r in get_steps(t2(run)["id"]) if r["step"] == step]

    def decision(run):
        found = [d for d in get_decisions(run["id"]) if d["kind"] == "email_approve"]
        return found[-1] if found else None

    def crash(month):
        try:
            cmd_run(customer, month)
        except SimulatedCrash:
            return True
        return False

    saved_env = {name: os.environ.get(name) for name in ("CUSTOMER_DISPLAY_NAME", "CUSTOMER_LONG_NAMES")}
    real_call_tool = email_tool.call_tool
    real_load_json = getattr(email_tool, "load_json", None)    # none any more: email_tool reads no files
    try:
        # E1. request built ONCE; launch keyed by OUR reference; samples shown in the decision; yes -> RUNNING
        fake = FakeEmailTools(rows)
        run = setup("2029-01", fake)
        cmd_run(customer, "2029-01")
        launch = rows_of(run, "launch")
        reference = rows_of(run, "request")[0]["output"]["body"]["reference"]
        d = decision(run)
        expect("E1 request built once; launch row keyed by our reference, paid, done; task waits for email_approve",
               fake.calls["build"] == 1 and len(launch) == 1 and launch[0]["idempotency_key"] == reference
               and launch[0]["paid"] and launch[0]["status"] == "done" and t2(run)["waiting_for"] == "email_approve")
        expect("E1 the decision shows the samples and the recipient count (TEST MODE: 3 team addresses)",
               d["question"]["samples"][0]["subject"] == "Faster SAP work at C1" and "3 recipient(s)" in d["question"]["text"])
        cmd_run(customer, "2029-01")
        expect("E1 run again without an answer: no new launch, still 1 decision",
               fake.calls["launch"] == 1 and len([x for x in get_decisions(run["id"]) if x["kind"] == "email_approve"]) == 1)
        from state_store import start_step
        t1_row = [t for t in get_tasks(run["id"]) if t["task_id"] == "T1"][0]
        expect("E1 a key owned by ANOTHER task is refused (never reuse another task's reply)",
               raises(lambda: start_step(t1_row["id"], "launch", reference, "email_tool", True, {})))
        cmd_answer(customer, d["id"], "yes", "samples read, good")
        cmd_run(customer, "2029-01")
        task = t2(run)
        expect("E1 yes -> approve -> done when RUNNING; achieved 3; the 3 person ids recorded",
               fake.calls["approve"] == 1 and task["status"] == "done" and task["achieved_number"] == 3
               and task["result"]["people_ids"] == [901, 902, 903] and task["result"]["campaign_status"] == "RUNNING")

        # E2. crash DURING the launch -> the SAVED request is resent (same reference), never rebuilt
        fake = FakeEmailTools(rows, crash_launch=1)
        run = setup("2029-02", fake)
        expect("E2 crash during the launch", crash("2029-02"))
        cmd_run(customer, "2029-02")
        expect("E2 next run resends the SAVED request: same reference, content not rebuilt, no human asked",
               fake.calls["launch"] == 2 and len(set(fake.launch_bodies)) == 1 and fake.calls["build"] == 1
               and t2(run)["waiting_for"] == "email_approve"
               and not [x for x in get_decisions(run["id"]) if x["kind"] == "paid_call_unknown"])

        # E3. crash DURING approve -> approve sent again automatically ('already_started') -> done
        fake = FakeEmailTools(rows, crash_approve=1)
        run = setup("2029-03", fake)
        cmd_run(customer, "2029-03")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        expect("E3 crash during approve", crash("2029-03"))
        cmd_run(customer, "2029-03")
        expect("E3 next run approves again automatically -> done", fake.calls["approve"] == 2
               and t2(run)["status"] == "done")

        # E4. no -> task stopped, nothing approved (campaign stays a draft)
        fake = FakeEmailTools(rows)
        run = setup("2029-04", fake)
        cmd_run(customer, "2029-04")
        cmd_answer(customer, decision(run)["id"], "no", "subject lines too salesy")
        cmd_run(customer, "2029-04")
        expect("E4 no -> task stopped, approve never called", t2(run)["status"] == "stopped"
               and fake.calls["approve"] == 0 and "DRAFT" in t2(run)["last_error"])

        # E5. samples not ready -> 'running'; the next run reads them again
        fake = FakeEmailTools(rows, samples_after=2)
        run = setup("2029-05", fake)
        result = cmd_run(customer, "2029-05")
        expect("E5 samples still generating -> task stays 'running', no decision yet",
               t2(run)["status"] == "running" and decision(run) is None and "still running" in result["tool"])
        cmd_run(customer, "2029-05")
        expect("E5 next run: samples ready -> email_approve decision", decision(run) is not None
               and fake.calls["samples"] == 2 and fake.calls["launch"] == 1)

        # E6. --dry: nothing recorded, nothing launched
        fake = FakeEmailTools(rows)
        run = setup("2029-06", fake)
        result = cmd_dry(customer, "2029-06")
        expect("E6 --dry: 3 recipients, nothing recorded, no launch", result.output["recipients"] == 3
               and fake.calls["launch"] == 0 and rows_of(run, "request") == [] and t2(run)["status"] == "pending")

        # E7. send_launch: 'pending' -> the SAME request again (call_tool stubbed, no network)
        sent = []

        def stub(method, url, body=None):
            sent.append(body)
            return 200, ({"start_status": "pending"} if len(sent) < 3 else
                         {"start_status": "awaiting_approval", "campaign_id": "c", "workspace_id": "w"})
        email_tool.call_tool = stub
        os.environ.setdefault("EMAIL_TOOL_URL", "https://example.invalid/api/v1/x")
        body = {"reference": "R-1"}
        email_tool.send_launch(body, wait_seconds=0)
        expect("E7 send_launch: pending twice -> the same request sent 3 times", len(sent) == 3
               and all(b is body for b in sent))
        email_tool.call_tool = real_call_tool

        # E8. known issue 4: never_say in OUR reference emails -> one rewrite; signature from the display name.
        # Its own understanding + work order: reading ANY file through email_tool here is a test failure.
        from email_tool import CampaignContent, Objective, ReferenceEmail
        os.environ["CUSTOMER_DISPLAY_NAME"] = "Anvixa"
        os.environ["CUSTOMER_LONG_NAMES"] = "Anvixa AI, Anvixa AI Product"   # shorter first on purpose
        understanding = {"customer_name": "Anvixa AI", "business_summary": "selftest"}
        work_order = {"task_id": "T11", "agent": "Email Agent", "output_type": "emails", "target": "3 emails"}
        files_read = []

        def no_files(path, *args, **kwargs):
            files_read.append(path)
            raise AssertionError(f"E8 read a file: {path}")
        email_tool.load_json = no_files

        def content(subject, body_text, never_say=("guaranteed",)):
            return CampaignContent(
                campaign_name="x", campaign_description="x",
                objective=Objective(objective="Book a demo", offer="AI automation", cta="Would a short demo help?",
                                    target="Ops leaders", problem_solved="manual work", tone="direct",
                                    must_mention=[], never_say=list(never_say)),
                emails=[ReferenceEmail(subject=subject, preheader="Why ANVIXA AI teams save hours",
                                       body_html=f"<p>Hi {{{{first_name}}}},</p><p>{body_text}</p>"
                                                 "<p>Would a short demo help?</p>", wait_days_before=0)])

        def write(answers, asked):
            return email_tool.write_content(ask=lambda s, p, n: (asked.append(p), answers[len(asked) - 1])[1],
                                            understanding=understanding, work_order=work_order)

        expect("E8 long names: .env list + understanding, duplicates once, LONGEST first, display name left out",
               email_tool.long_names(understanding, "Anvixa") == ["Anvixa AI Product", "Anvixa AI"]
               and email_tool.long_names({"customer_name": "anvixa"}, "Anvixa") == ["Anvixa AI Product", "Anvixa AI"])
        asked = []
        fixed = write([content("Anvixa AI Product for {{company}}", "This is your last chance to try Anvixa AI Product."),
                       content("Anvixa AI Product for {{company}}",
                               "Anvixa AI Product saves hours. Anvixa AI works in your browser.")], asked)
        email = fixed.emails[0]
        expect("E8 'last chance' found -> exactly one rewrite, asked with the forbidden phrase named",
               len(asked) == 2 and "last chance" in asked[1].lower())
        expect("E8 the prompt holds the GIVEN understanding and work order",
               '"business_summary": "selftest"' in asked[0] and '"target": "3 emails"' in asked[0])
        expect("E8 signature '<p>Regards,<br>Team Anvixa</p>' added",
               email.body_html.endswith("<p>Regards,<br>Team Anvixa</p>"))
        expect("E8 'Anvixa AI Product' and 'Anvixa AI' (any case) -> 'Anvixa' in subject, preheader and body",
               email.subject == "Anvixa for {{company}}" and email.preheader == "Why Anvixa teams save hours"
               and "<p>Anvixa saves hours. Anvixa works in your browser.</p>" in email.body_html)
        everything = f"{email.subject} {email.preheader} {email.body_html}".lower()
        expect("E8 never 'Anvixa Product' or 'Anvixa AI' left anywhere",
               "anvixa product" not in everything and "anvixa ai" not in everything)
        os.environ.pop("CUSTOMER_LONG_NAMES")
        only = write([content("Anvixa AI for {{company}}", "Anvixa AI saves hours.")], [])
        expect("E8 without CUSTOMER_LONG_NAMES: the understanding's name is still replaced",
               only.emails[0].subject == "Anvixa for {{company}}" and "<p>Anvixa saves hours.</p>" in only.emails[0].body_html)
        expect("E8 still a forbidden phrase after the rewrite -> STOPPED",
               raises(lambda: write([content("s", "last chance"), content("s", "guaranteed results")], [])))

        # E8b. never_say: fixed list + self-ban rule in the FIRST prompt, whole phrases (strict), where it was found
        expect("E8b the first prompt names all 4 fixed phrases and the self-ban rule",
               all(f'"{phrase}"' in asked[0] for phrase in email_tool.NEVER_SAY_DEFAULTS)
               and "must not appear in YOUR OWN emails" in asked[0])

        def found(text, never_say=("free",)):
            return email_tool.hit_phrases(email_tool.never_say_hits(content("s", text, never_say)))
        expect("E8b whole phrases: 'freedom' and 'carefree' are NOT hits for 'free'",
               found("Enjoy the freedom of a carefree team.") == [])
        expect("E8b strict: 'free demo', 'Free trial', 'hassle-free', 'feel free' ARE hits for 'free'",
               all(found(text) == ["free"] for text in
                   ("Book a free demo.", "Start a Free trial.", "A hassle-free setup.", "Feel free to reply.")))
        expect("E8b fixed phrases any case: 'LAST CHANCE', '100% sure' (but not '1100%')",
               found("LAST CHANCE to join.", ()) == ["last chance"] and found("We are 100% sure.", ()) == ["100%"]
               and found("Growth of 1100% is rare.", ()) == [])
        hits = email_tool.never_say_hits(content("Free for {{company}}", "Hi there. Feel free to reply anytime.", ["free"]))
        expect("E8b each hit says where: email number, field and the text around it",
               {(h["email"], h["field"]) for h in hits} == {(1, "subject"), (1, "body")}
               and any("Feel free to reply" in h["snippet"] for h in hits if h["field"] == "body")
               and all("\n" not in h["snippet"] for h in hits))
        asked, message = [], ""
        try:
            write([content("s", "Try it risk-free today.", ["risk-free"]),
                   content("s", "A risk-free start for your team.", ["risk-free"])], asked)
        except ToolError as error:
            message = str(error)
        expect("E8b an LLM phrase ('risk-free') used twice -> STOPPED naming the email, field and sentence",
               message.startswith("STOPPED: the reference emails still contain never_say phrases ['risk-free']")
               and "'risk-free' in email 1 body:" in message and "A risk-free start for your team" in message)
        expect("E8b the rewrite request quotes the exact sentence and the whole-word rule",
               len(asked) == 2 and "Try it risk-free today" in asked[1] and "whole words" in asked[1])

        # E8d. the LLM bans an ordinary word ('free') for itself -> dropped by code; its longer phrases stay
        asked = []
        kept_free = write([content("s", "Feel free to reply.", ["free", "risk-free"])], asked)
        expect("E8d the LLM's own 'free' is DROPPED ('risk-free' and our fixed phrases stay): 'Feel free' passes, "
               "no rewrite (1 LLM call)",
               len(asked) == 1 and "free" not in [p.lower() for p in kept_free.objective.never_say]
               and "risk-free" in kept_free.objective.never_say
               and kept_free.objective.never_say[:4] == email_tool.NEVER_SAY_DEFAULTS)
        expect("E8d 'risk-free trial' (the LLM's longer phrase) is still caught",
               raises(lambda: write([content("s", "Start a risk-free trial.", ["free", "risk-free"]),
                                     content("s", "Start a risk-free trial today.", ["free", "risk-free"])], [])))
        expect("E8d our fixed 'last chance' stays strict, even with an empty LLM list",
               raises(lambda: write([content("s", "Your last chance.", []), content("s", "Last chance again.", [])], [])))
        expect("E8d the prompt: the LLM's list is for hype only, never ordinary words like 'free' or 'help'; "
               "the cold-email rule; email 3 follows up 'our earlier emails'",
               "Never add ordinary words you may need for polite writing" in asked[0] and "'free'" in asked[0]
               and email_tool.COLD_EMAIL_RULE in asked[0] and "never spoken with us" in asked[0]
               and "Never imply an earlier conversation, meeting, call or reply" in asked[0]
               and "polite final follow-up to our earlier emails" in asked[0])
        many = content("s", "ok", ["Cheapest", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8"])
        kept = email_tool.enforce_rules(many).objective.never_say
        expect("E8b our 4 fixed phrases come FIRST and are never cut (10 max, 'Cheapest' once)",
               kept[:4] == email_tool.NEVER_SAY_DEFAULTS and len(kept) == email_tool.MAX_PHRASES
               and [p.lower() for p in kept].count("cheapest") == 1)

        # E8c. email 3 is a calm follow-up; the rewrite names every hit field and says ALL fields count
        def three(subject3="A last idea for {{company}}", preheader3="One more thought", body3="Happy to help later."):
            def mail(subject, preheader, body, wait):
                return ReferenceEmail(subject=subject, preheader=preheader, wait_days_before=wait,
                                      body_html=f"<p>Hi {{{{first_name}}}},</p><p>{body}</p><p>Would a short demo help?</p>")
            return CampaignContent(
                campaign_name="x", campaign_description="x",
                objective=Objective(objective="Book a demo", offer="AI automation", cta="Would a short demo help?",
                                    target="Ops leaders", problem_solved="manual work", tone="direct",
                                    must_mention=[], never_say=["guaranteed"]),
                emails=[mail("Hello {{company}}", "Quick intro", "Intro.", 0),
                        mail("An idea for {{company}}", "How teams save time", "Value.", 3),
                        mail(subject3, preheader3, body3, 4)])
        import json
        schema_text = json.dumps(CampaignContent.model_json_schema())
        asked = []
        write([three(), three()], asked)
        expect("E8c the first prompt has the email 3 rule (calm, never 'last chance' in subject, preheader or body); "
               "the emails format says 'polite final follow-up', no longer 'final note'",
               email_tool.EMAIL3_RULE in asked[0] and "polite final follow-up (calm, no urgency)" in schema_text
               and "short final note" not in schema_text and len(asked) == 1)
        asked, message = [], ""
        try:
            write([three(subject3="Last chance to automate at {{company}}"),
                   three(preheader3="Your last chance to save hours")], asked)
        except ToolError as error:
            message = str(error)
        expect("E8c the r5 case: 'last chance' in the email 3 SUBJECT -> the rewrite names 'email 3 subject', says ALL "
               "fields (subject, preheader AND body) must be free of EVERY phrase, lists them, repeats the email 3 rule",
               len(asked) == 2 and "- email 3 subject: 'last chance'" in asked[1]
               and "ALL fields of ALL emails (subject, preheader AND body)" in asked[1]
               and all(f'"{p}"' in asked[1] for p in email_tool.NEVER_SAY_DEFAULTS)
               and "Moving a phrase to another field still fails" in asked[1]
               and asked[1].rstrip().endswith(email_tool.EMAIL3_RULE))
        expect("E8c ... moved to the email 3 PREHEADER after the rewrite -> the SAME STOPPED as before, naming the field",
               message.startswith("STOPPED: the reference emails still contain never_say phrases ['last chance'] after one rewrite")
               and "'last chance' in email 3 preheader:" in message)
        asked = []
        fixed = write([three(subject3="Last chance to automate at {{company}}"), three()], asked)
        expect("E8c a clean rewrite -> the content is used (one rewrite, signature added, no 'last chance' left)",
               len(asked) == 2 and fixed.emails[2].subject == "A last idea for {{company}}"
               and fixed.emails[2].body_html.endswith("<p>Regards,<br>Team Anvixa</p>")
               and not email_tool.never_say_hits(fixed))
        asked = []
        write([three(subject3="Last chance for {{company}}", body3="Guaranteed results, act today."), three()], asked)
        expect("E8c hits in two fields (subject + body) -> both listed in the rewrite request",
               "- email 3 body: 'guaranteed'" in asked[1] and "- email 3 subject: 'last chance'" in asked[1])
        expect("E8 no file in data/ was read", files_read == [])
        os.environ.pop("CUSTOMER_DISPLAY_NAME")
        expect("E8 CUSTOMER_DISPLAY_NAME missing -> STOPPED", raises(email_tool.display_name))

        # ---- F. sample review with feedback rounds (new samples shape; old shape + STANDARD unchanged) ----
        from state_store import DECISIONS, backend, now_iso

        def platform_answer(d, answer, note):
            """What the platform does: it writes the answer itself (no cmd_answer, no answer_decision checks)."""
            backend().update(DECISIONS, {"status": "answered", "answer": answer, "decided_by": "platform",
                                         "reason": note, "answered_at": now_iso()}, {"id": d["id"], "status": "open"})

        def approve_count(run):
            return len([d for d in get_decisions(run["id"]) if d["kind"] == "email_approve"])

        # F0. pick_samples: different companies first, then titles, then cities; thin people last; deterministic
        people = [{"email": "a1", "first_name": "A", "company": "Acme", "title": "CTO", "city": "Pune", "company_industry": "x"},
                  {"email": "a2", "first_name": "B", "company": "Acme", "title": "VP", "city": "Pune", "company_industry": "x"},
                  {"email": "t1", "first_name": "T", "company": "Zed"},                         # thin: no title / industry / site
                  {"email": "b1", "first_name": "C", "company": "Bolt", "title": "CTO", "city": "Pune", "company_industry": "x"},
                  {"email": "c1", "first_name": "D", "company": "Core", "title": "CFO", "city": "Delhi", "company_industry": "x"},
                  {"email": "d1", "first_name": "E", "company": "Dune", "title": "CTO", "city": "Pune", "company_industry": "x"}]
        picked = email_tool.pick_samples(people)
        expect("F0 pick_samples: 4 people from 4 different companies (new title + city preferred: c1 2nd), ties in "
               "input order, the thin one last, the same answer twice, fewer than 3 people -> all of them",
               picked == ["a1", "c1", "b1", "d1"] and email_tool.pick_samples(people) == picked
               and email_tool.pick_samples(people, n=6)[-1] == "t1"
               and email_tool.pick_samples(people[:2]) == ["a1", "a2"])

        # F0b. build_launch_request: sample_recipients for HYPER only (real function, content given, no LLM)
        saved = {name: os.environ.get(name) for name in ("EMAIL_TOOL_USER_EMAIL", "CLIENT_REFERENCE", "EMAIL_CAMPAIGN_TYPE")}
        saved_test_mode = email_tool.TEST_MODE
        try:
            os.environ.update({"EMAIL_TOOL_USER_EMAIL": "selftest@test.example", "CLIENT_REFERENCE": "selftest-client"})
            os.environ.pop("EMAIL_CAMPAIGN_TYPE", None)
            email_tool.TEST_MODE = False                 # real recipients (only 3 '+' copies in test mode)
            people_rows = [{"id": 970 + i, "full_name": f"Name{i} X", "email": f"n{i}@prospect.test", "job_position": title,
                            "location": "Pune, Maharashtra, India",
                            "company": {"company_name": company, "website": f"{company.lower()}.test", "industry": "x"}}
                           for i, (company, title) in enumerate([("Acme", "CTO"), ("Acme", "VP"), ("Bolt", "CFO"),
                                                                 ("Core", "COO"), ("Dune", "CTO")])]
            hyper, _ = email_tool.build_launch_request(people_rows, understanding={"customer_name": "Selftest"},
                                                       work_order={}, write=lambda: three())
            os.environ["EMAIL_CAMPAIGN_TYPE"] = "STANDARD"
            plain, _ = email_tool.build_launch_request(people_rows, understanding={"customer_name": "Selftest"},
                                                       work_order={}, write=lambda: three())
            expect("F0b build_launch_request: HYPER names 4 sample people from 4 companies; STANDARD names none",
                   len(hyper["sample_recipients"]) == 4
                   and len({r["company"] for r in hyper["recipients"] if r["email"] in hyper["sample_recipients"]}) == 4
                   and "sample_recipients" not in plain)
        finally:
            email_tool.TEST_MODE = saved_test_mode
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        rows_f = [{"id": 951 + i, "customer": customer, "full_name": f"Name{i} X", "email": f"f{i}@prospect.test",
                   "job_position": title, "company": {"company_name": company}}
                  for i, (company, title) in enumerate([("Acme", "CTO"), ("Acme", "VP"), ("Bolt", "CFO"),
                                                        ("Core", "COO"), ("Dune", "CTO"), ("Echo", "CEO")])]

        # F1. the request names 4 sample people from different companies, saved once
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-01", fake)
        cmd_run(customer, "2031-01")
        body = rows_of(run, "request")[0]["output"]["body"]
        picks = body["sample_recipients"]
        cmd_run(customer, "2031-01")
        d = decision(run)
        q = d["question"]
        expect("F1 launch body: 4 sample_recipients, all different companies; the same on a re-run (built once)",
               len(picks) == 4 and len({r["company"] for r in body["recipients"] if r["email"] in picks}) == 4
               and fake.calls["build"] == 1 and rows_of(run, "request")[0]["output"]["body"]["sample_recipients"] == picks)
        expect("F1 version 1 shown grouped per person ('Rahul, CTO at Acme', 4 x 3 emails), batch b1/d1, no failed "
               "checks, 3 rounds left, answers yes / no / feedback",
               q["round"] == 1 and q["batch_id"] == "b1" and q["config_digest"] == "d1" and q["checks"] == []
               and q["feedback_rounds_left"] == 3 and len(q["samples"]) == 12 and q["samples"][0]["for"] == "Rahul, CTO at Acme"
               and q["samples"][3]["for"].startswith("Priya") and q["allowed_answers"] == ["feedback", "no", "yes"]
               and "feedback + a note" in q["text"])

        # F2. feedback -> keyed <ref>:feedback:1, saved with batch b1 + the note; 'running' while rewriting; version 2
        reference = body["reference"]
        cmd_answer(customer, d["id"], "feedback", "Shorter, and mention the Pune plant")
        cmd_run(customer, "2031-01")
        fb = rows_of(run, "feedback")
        expect("F2 feedback: ONE call keyed '<ref>:feedback:1:d<decision id>', paid, request = batch b1 + the note; "
               "while the new batch is generating the task is 'running' (no decision yet)",
               len(fb) == 1 and fb[0]["idempotency_key"] == f"{reference}:feedback:1:d{d['id']}" and fb[0]["paid"]
               and fb[0]["request"] == {"batch_id": "b1", "feedback": "Shorter, and mention the Pune plant"}
               and fake.calls["feedback"] == 1 and t2(run)["status"] == "running" and approve_count(run) == 1)
        cmd_run(customer, "2031-01")
        q = decision(run)["question"]
        expect("F2 next run: batch b2 ready -> a new email_approve: version 2, batch b2, 2 rounds left, the earlier feedback listed",
               approve_count(run) == 2 and q["round"] == 2 and q["batch_id"] == "b2" and q["feedback_rounds_left"] == 2
               and q["previous_feedback"] == ["Shorter, and mention the Pune plant"] and "(v2)" in q["samples"][0]["subject"]
               and fake.calls["feedback"] == 1)

        # F3. a crash inside the feedback call -> the SAME saved request is resent; the tool rewrites once
        fake = FakeEmailTools(rows_f, new_shape=True, crash_feedback=1)
        run = setup("2031-03", fake)
        cmd_run(customer, "2031-03")
        cmd_answer(customer, decision(run)["id"], "feedback", "Less formal")
        expect("F3 crash during feedback", crash("2031-03"))
        fb = rows_of(run, "feedback")
        saved_first = len(fb) == 1 and fb[0]["status"] == "running" and fb[0]["request"]["batch_id"] == "b1"
        cmd_run(customer, "2031-03")
        cmd_run(customer, "2031-03")
        fb = rows_of(run, "feedback")
        expect("F3 the row was saved BEFORE the call (left 'running' by the crash); the next run resends the SAME "
               "request (batch b1, same note); the tool rewrote ONCE; then version 2 is asked",
               saved_first and fake.calls["feedback"] == 2 and fake.generations == 1 and len(fb) == 1
               and fb[0]["status"] == "done" and fake.feedback_bodies == [{"batch_id": "b1", "feedback": "Less formal"}]
               and decision(run)["question"]["batch_id"] == "b2")
        expect("F3 the resend got the tool's duplicate reply: duplicate true, the SAME new batch b2",
               fb[0]["response"] == {"campaign_id": "c-1", "batch_id": "b2", "samples_status": "generating", "duplicate": True})

        # F4. 3 rounds -> a 4th 'feedback' is not offered, refused when answered, and makes NO call
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-04", fake)
        cmd_run(customer, "2031-04")
        for number in (1, 2, 3):
            cmd_answer(customer, decision(run)["id"], "feedback", f"change {number}")
            cmd_run(customer, "2031-04")
            cmd_run(customer, "2031-04")
        q = decision(run)["question"]
        offered_ok = q["round"] == 4 and q["feedback_rounds_left"] == 0 and q["allowed_answers"] == ["no", "yes"]
        refused = raises(lambda: cmd_answer(customer, decision(run)["id"], "feedback", "change 4"))
        platform_answer(decision(run), "feedback", "change 4")                 # the platform writes it anyway
        cmd_run(customer, "2031-04")
        q = decision(run)["question"]
        expect("F4 after 3 rounds: version 4 offers only yes / no; 'feedback' is refused by the answer rules; written "
               "anyway (platform) -> NO call, the same version again with 'No feedback rounds left'",
               offered_ok and refused and fake.calls["feedback"] == 3 and q["batch_id"] == "b4"
               and q["text"].startswith("No feedback rounds left") and q["allowed_answers"] == ["no", "yes"])

        # F5. yes -> approve EXACTLY the version shown (b3/d3), even when the tool already has a newer batch
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-05", fake)
        cmd_run(customer, "2031-05")
        for number in (1, 2):
            cmd_answer(customer, decision(run)["id"], "feedback", f"change {number}")
            cmd_run(customer, "2031-05")
            cmd_run(customer, "2031-05")
        reference = rows_of(run, "request")[0]["output"]["body"]["reference"]
        fake.batch = 4                                                        # a newer batch exists in the tool
        cmd_answer(customer, decision(run)["id"], "yes", "version 3 is good")
        cmd_run(customer, "2031-05")
        approve = rows_of(run, "approve")
        expect("F5 yes -> approve body is exactly {batch b3, digest d3} of the version shown (not the newer b4); "
               "key '<ref>:approve:b3:d3'; done",
               fake.approve_bodies == [{"batch_id": "b3", "config_digest": "d3"}] and len(approve) == 1
               and approve[0]["idempotency_key"] == f"{reference}:approve:b3:d3" and t2(run)["status"] == "done")

        # F6. approve 409 batch_not_latest -> never retried or auto-approved; fresh samples asked again; yes approves those
        fake = FakeEmailTools(rows_f, new_shape=True, approve_errors=[(409, "batch_not_latest")])
        run = setup("2031-06", fake)
        cmd_run(customer, "2031-06")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2031-06")
        q = decision(run)["question"]
        failed = rows_of(run, "approve")
        first_ok = (fake.calls["approve"] == 1 and q["batch_id"] == "b2" and q["text"].startswith("The samples changed")
                    and len(failed) == 1 and failed[0]["status"] == "failed"
                    and failed[0]["error"].startswith(email_tool.CHANGED) and t2(run)["status"] == "waiting_human")
        cmd_answer(customer, decision(run)["id"], "yes", "the new ones are fine")
        cmd_run(customer, "2031-06")
        expect("F6 409 -> not retried, not approved; a new email_approve with the fresh batch b2; a later yes approves "
               "b2/d2 -> done",
               first_ok and fake.calls["approve"] == 2 and fake.approve_bodies[-1] == {"batch_id": "b2", "config_digest": "d2"}
               and t2(run)["status"] == "done")

        # F7. a sample with 'guaranteed' -> listed in checks; 'yes' not offered, refused, and never approved
        fake = FakeEmailTools(rows_f, new_shape=True, bad_sample=True)
        run = setup("2031-07", fake)
        cmd_run(customer, "2031-07")
        q = decision(run)["question"]
        checks_ok = (q["checks"] == ["Rahul (email 2): never_say 'guaranteed' in body"]
                     and q["allowed_answers"] == ["feedback", "no"] and "failed our checks" in q["text"])
        refused = raises(lambda: cmd_answer(customer, decision(run)["id"], "yes", "looks fine"))
        platform_answer(decision(run), "yes", "looks fine")
        cmd_run(customer, "2031-07")
        q = decision(run)["question"]
        expect("F7 'guaranteed' -> checks list it; yes not offered and refused; written anyway -> NOT approved, asked "
               "again ('failed checks')",
               checks_ok and refused and fake.calls["approve"] == 0 and q["text"].startswith("These samples failed checks")
               and q["batch_id"] == "b1" and t2(run)["status"] == "waiting_human")

        # F8. old samples shape -> feedback not offered; written anyway -> no call, yes / no again; approve without a body
        fake = FakeEmailTools(rows)
        run = setup("2031-08", fake)
        cmd_run(customer, "2031-08")
        q = decision(run)["question"]
        old_ok = "batch_id" not in q and q["allowed_answers"] == ["no", "yes"] and "feedback" not in q["text"]
        platform_answer(decision(run), "feedback", "shorter")
        cmd_run(customer, "2031-08")
        q2 = decision(run)["question"]
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2031-08")
        expect("F8 old shape: yes / no only; feedback written anyway -> no call, the same samples with 'Feedback is not "
               "available'; yes -> approve with NO body, as before",
               old_ok and fake.calls["feedback"] == 0 and q2["text"].startswith("Feedback is not available")
               and q2["samples"] == q["samples"] and fake.approve_bodies == [None] and t2(run)["status"] == "done")

        # F9. STANDARD -> feedback behaves like the old shape (no call), start is not sent
        fake = FakeEmailTools(rows, standard=True)
        run = setup("2031-09", fake)
        cmd_run(customer, "2031-09")
        offered = decision(run)["question"]["allowed_answers"]
        platform_answer(decision(run), "feedback", "shorter")
        cmd_run(customer, "2031-09")
        expect("F9 STANDARD: yes / no only; feedback -> no call, asked again; nothing started",
               offered == ["no", "yes"] and fake.calls["feedback"] == 0 and fake.calls["start"] == 0
               and decision(run)["question"]["text"].startswith("Feedback is not available"))

        # F10. 'feedback' needs a note (max 1000 characters); yes / no still need none
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-10", fake)
        cmd_run(customer, "2031-10")
        d = decision(run)
        expect("F10 feedback without a note, or with more than 1000 characters -> refused by cmd_answer; 'no' with "
               "no note -> accepted",
               raises(lambda: cmd_answer(customer, d["id"], "feedback", "  "))
               and raises(lambda: cmd_answer(customer, d["id"], "feedback", "x" * 1001))
               and cmd_answer(customer, d["id"], "no", None)["answer"] == "no")

        # ---- G. the email tool's real contract (error codes, stale / failed / none, preheader, PENDING / FAILED) ----
        def opened(run):
            return decision(run)["question"]

        def feedback_round(run, month, note):
            """Answer feedback, then run until the next version is asked (2 runs: rewriting, then ready)."""
            cmd_answer(customer, decision(run)["id"], "feedback", note)
            cmd_run(customer, month)
            if decision(run)["status"] == "answered":
                cmd_run(customer, month)

        # G1. approve 409 approval_stale -> the batch is stale (never remade by the tool): no yes; feedback rewrites it
        fake = FakeEmailTools(rows_f, new_shape=True, approve_errors=[(409, "approval_stale")])
        run = setup("2032-01", fake)
        cmd_run(customer, "2032-01")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2032-01")
        q = opened(run)
        stale_ok = (q["samples_status"] == "stale" and q["allowed_answers"] == ["feedback", "no"] and q["batch_id"] == "b1"
                    and q["text"].startswith("The samples changed after you saw them; please review again. The samples "
                                             "are out of date (edited in the app)"))
        platform_answer(decision(run), "yes", "approve anyway")
        cmd_run(customer, "2032-01")
        refused_yes = fake.calls["approve"] == 1 and opened(run)["text"].startswith("These samples failed checks or are out of date")
        feedback_round(run, "2032-01", "Rewrite them, please")
        cmd_answer(customer, decision(run)["id"], "yes", "the rewrite is good")
        cmd_run(customer, "2032-01")
        expect("G1 approve 409 approval_stale -> not retried; the samples are 'stale' -> asked with feedback / no only; "
               "a yes written anyway is refused; feedback on the stale batch b1 rewrites it (b2); yes approves b2/d2",
               stale_ok and refused_yes and fake.feedback_bodies == [{"batch_id": "b1", "feedback": "Rewrite them, please"}]
               and fake.approve_bodies[-1] == {"batch_id": "b2", "config_digest": "d2"} and t2(run)["status"] == "done")

        # G2. approve 409 previews_incomplete -> as before: the task fails (retry / skip / stop)
        fake = FakeEmailTools(rows_f, new_shape=True, approve_errors=[(409, "previews_incomplete")])
        run = setup("2032-02", fake)
        cmd_run(customer, "2032-02")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2032-02")
        task = t2(run)
        expect("G2 approve 409 previews_incomplete -> task_failed with the known message; no new email_approve",
               task["waiting_for"] == "task_failed" and "Samples are still being written" in (task["last_error"] or "")
               and approve_count(run) == 1)

        # G3. feedback 409 state_conflict -> no retry, a clear STOPPED, task_failed; not a round
        fake = FakeEmailTools(rows_f, new_shape=True, feedback_errors=[(409, "state_conflict")])
        run = setup("2032-03", fake)
        cmd_run(customer, "2032-03")
        cmd_answer(customer, decision(run)["id"], "feedback", "shorter")
        cmd_run(customer, "2032-03")
        task = t2(run)
        expect("G3 feedback 409 state_conflict -> task_failed 'STOPPED: the campaign is no longer a DRAFT'; sent once, "
               "no new email_approve, no round used",
               task["waiting_for"] == "task_failed" and "no longer a DRAFT" in (task["last_error"] or "")
               and fake.calls["feedback"] == 1 and approve_count(run) == 1
               and not [r for r in rows_of(run, "feedback") if r["status"] == "done"])

        # G4. feedback 409 conflict / batch_not_latest -> fresh samples, asked again; not retried, not a round
        fake = FakeEmailTools(rows_f, new_shape=True, feedback_errors=[(409, "conflict"), (409, "batch_not_latest")])
        run = setup("2032-04", fake)
        cmd_run(customer, "2032-04")
        texts = []
        for note in ("shorter", "shorter again"):
            cmd_answer(customer, decision(run)["id"], "feedback", note)
            cmd_run(customer, "2032-04")
            texts.append(opened(run)["text"])
        q = opened(run)
        expect("G4 feedback 409 conflict, then batch_not_latest -> each: one call, samples read again, asked again "
               "('samples changed'); still version 1 with 3 rounds left; nothing rewritten",
               fake.calls["feedback"] == 2 and fake.generations == 0 and approve_count(run) == 3
               and all(t.startswith("The samples changed after you saw them") for t in texts)
               and q["round"] == 1 and q["feedback_rounds_left"] == 3 and q["allowed_answers"] == ["feedback", "no", "yes"])

        # G5. refused notes (422 too long, 422 invalid, 429 daily limit) -> asked again with the reason; never a round
        fake = FakeEmailTools(rows_f, new_shape=True, feedback_errors=[(422, "feedback_too_long"), (422, "validation_error"),
                                                                       (429, "preview_budget_exhausted")])
        run = setup("2032-05", fake)
        cmd_run(customer, "2032-05")
        texts = []
        for note in ("long note", "odd note", "third note"):
            cmd_answer(customer, decision(run)["id"], "feedback", note)
            cmd_run(customer, "2032-05")
            texts.append(opened(run)["text"])
        left_after_refusals = opened(run)["feedback_rounds_left"]
        for number in (1, 2, 3):
            feedback_round(run, "2032-05", f"accepted {number}")
        q = opened(run)
        refused = raises(lambda: cmd_answer(customer, decision(run)["id"], "feedback", "one more"))
        expect("G5 422 feedback_too_long / validation_error / 429 preview_budget_exhausted -> asked again with the "
               "reason, 3 rounds still left; then 3 ACCEPTED notes -> version 4, no rounds left, feedback refused",
               texts[0].startswith("The feedback is too long") and texts[1].startswith("The email tool did not accept that note")
               and texts[2].startswith("The daily sample limit") and left_after_refusals == 3
               and q["round"] == 4 and q["feedback_rounds_left"] == 0 and q["allowed_answers"] == ["no", "yes"]
               and q["previous_feedback"] == ["accepted 1", "accepted 2", "accepted 3"] and refused
               and fake.calls["feedback"] == 6 and fake.generations == 3)

        # G6. stale / failed on the first read -> no waiting: feedback or no; stale with no rounds left -> no only
        fake = FakeEmailTools(rows_f, new_shape=True, statuses=["stale"])
        run = setup("2032-06", fake)
        cmd_run(customer, "2032-06")
        stale_q = opened(run)
        fake = FakeEmailTools(rows_f, new_shape=True, statuses=["failed"])
        run = setup("2032-07", fake)
        cmd_run(customer, "2032-07")
        failed_q = opened(run)
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2032-08", fake)
        cmd_run(customer, "2032-08")
        for number in (1, 2):
            feedback_round(run, "2032-08", f"change {number}")
        cmd_answer(customer, decision(run)["id"], "feedback", "change 3")
        cmd_run(customer, "2032-08")                     # rewriting
        fake.statuses = ["stale"]                       # edited in the app before the rewrite was read
        cmd_run(customer, "2032-08")
        last_q = opened(run)
        expect("G6 'stale' -> asked at once (no waiting): 'out of date (edited in the app)', feedback / no; 'failed' -> "
               "'could not write the samples', feedback / no; stale with no rounds left -> no only",
               stale_q["samples_status"] == "stale" and stale_q["allowed_answers"] == ["feedback", "no"]
               and stale_q["text"].startswith("The samples are out of date (edited in the app)")
               and failed_q["allowed_answers"] == ["feedback", "no"]
               and failed_q["text"].startswith("The tool could not write the samples")
               and last_q["samples_status"] == "stale" and last_q["allowed_answers"] == ["no"])

        # G7. 'none' (batch_id null) -> keep waiting with a clear message; never mistaken for the old shape
        fake = FakeEmailTools(rows_f, new_shape=True, statuses=["none"])
        run = setup("2032-09", fake)
        result = cmd_run(customer, "2032-09")
        waiting_ok = t2(run)["status"] == "running" and decision(run) is None and "('none')" in result["tool"]
        cmd_run(customer, "2032-09")
        expect("G7 'none' (batch_id null) -> 'running' with 'the tool has not finished the samples (none)', no decision; "
               "the next read (ready) asks version 1",
               waiting_ok and email_tool.samples_shape({"samples_status": "none", "batch_id": None}) == "new"
               and opened(run)["batch_id"] == "b1")

        # G8. state PENDING / FAILED -> listed; only OK passes
        problems = {state: email_tool.sample_problems(FakeEmailTools(rows_f, new_shape=True, email_state=state).samples({}),
                                                      {"objective": {"cta": "Would a short call help?"}})
                    for state in ("OK", "PENDING", "FAILED")}
        expect("G8 sample state: OK passes; PENDING and FAILED are each listed as a problem",
               problems["OK"] == [] and problems["PENDING"] == ["Rahul (email 1): the tool reports state 'PENDING', issues []"]
               and problems["FAILED"] == ["Rahul (email 1): the tool reports state 'FAILED', issues []"])

        # G9. the preheader: split from body_html (the tool's real markup); never_say + cold on subject, preheader AND
        # body; greeting, CTA and must_mention on the BODY only
        real_body = ('<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;'
                     'line-height:1px;color:transparent">A quick idea for Acme' + "&zwnj;&nbsp;" * 46 + '</div>'
                     '<p>Hi Sarah,</p><p>I noticed this about your work: Company: Acme.</p><p>We help SaaS companies '
                     'improve their outbound process by automating manual prospecting work.</p><p>Would you be open to a '
                     'quick conversation?</p><p>Best,<br>John</p>')
        preheader, rest = email_tool.split_preheader(real_body)
        plain = "<p>Hi Sarah,</p><p>Would you be open to a quick conversation?</p>"

        def sarah(body_html, **extra):
            return {"batch_id": "b1", "recipients": [{"first_name": "Sarah", "company": "Acme", "title": "CEO", "emails": [
                {"email_number": 1, "state": "OK", "subject": "A quick idea for Acme", "body_html": body_html,
                 "fallback_used": False, "failure_codes": [], **extra}]}]}
        request = {"objective": {"cta": "Would you be open to a quick conversation?", "must_mention": ["outbound"]}}
        bad_pre = real_body.replace("A quick idea for Acme", "As discussed, guaranteed idea for Acme")
        shown = email_tool.flat_samples(sarah(real_body))[0]
        expect("G9 split_preheader: the tool's real sample -> preheader 'A quick idea for Acme' (padding removed), body "
               "starts '<p>Hi Sarah,'; no <div> -> no preheader, body unchanged; the person sees the body + the preheader",
               preheader == "A quick idea for Acme" and rest.startswith("<p>Hi Sarah,</p>")
               and email_tool.split_preheader(plain) == ("", plain)
               and shown["preheader"] == "A quick idea for Acme" and shown["text"].startswith("Hi Sarah,"))
        expect("G9 checks: the real sample passes (greeting / CTA / must_mention on the body); never_say + cold phrase in "
               "the PREHEADER are caught; a preheader FIELD (if one ever appears) is checked too; without a <div> the "
               "greeting still passes",
               email_tool.sample_problems(sarah(real_body), request) == []
               and email_tool.sample_problems(sarah(bad_pre), request)
               == ["Sarah (email 1): never_say 'guaranteed' in preheader",
                   "Sarah (email 1): implies an earlier conversation ('as discussed') in preheader"]
               and email_tool.sample_problems(sarah(rest, preheader="Last chance for Acme"), request)
               == ["Sarah (email 1): never_say 'last chance' in preheader"]
               and email_tool.sample_problems(sarah(plain), {"objective": {"cta": request["objective"]["cta"]}}) == [])

        # G10. the REAL tool calls (call_tool stubbed, no network): each error code maps to the right outcome
        from runner_email import RealEmailTools
        sent, answers = [], []

        def stub(method, url, body=None):
            sent.append((url.rsplit("/", 1)[-1], body))
            return answers.pop(0)
        email_tool.call_tool = stub
        saved_key = os.environ.get("EMAIL_TOOL_KEY")
        os.environ.setdefault("EMAIL_TOOL_URL", "https://example.invalid/api/v1/x")
        os.environ.setdefault("EMAIL_TOOL_KEY", "selftest")
        try:
            real = RealEmailTools({}, {})
            where = {"workspace_id": "w", "campaign_id": "c"}

            def outcome(call, status, code):
                answers.append((status, {"error": {"code": code, "message": "m", "request_id": "r", "details": None}}))
                try:
                    real.feedback(where, "b1", "note") if call == "feedback" else real.approve(where, "b1", "d1")
                except email_tool.SamplesChanged:
                    return "changed"
                except email_tool.FeedbackRefused:
                    return "refused"
                except ToolError as error:
                    return "failed: " + str(error)
                return "ok"
            results = {(call, code): outcome(call, status, code) for call, status, code in [
                ("feedback", 409, "batch_not_latest"), ("feedback", 409, "conflict"), ("feedback", 409, "state_conflict"),
                ("feedback", 422, "feedback_too_long"), ("feedback", 422, "validation_error"),
                ("feedback", 429, "preview_budget_exhausted"), ("approve", 409, "batch_not_latest"),
                ("approve", 409, "approval_stale"), ("approve", 409, "previews_failed"), ("approve", 409, "samples_not_ready")]}
            answers.append((200, {"campaign_id": "c", "batch_id": "b2", "samples_status": "generating", "duplicate": False}))
            reply = real.feedback(where, "b1", "Shorter, please.")
            expect("G10 the real tools read error.code: feedback batch_not_latest / conflict -> samples changed; "
                   "state_conflict -> STOPPED (task fails); 422 / 429 -> refused (asked again); approve batch_not_latest / "
                   "approval_stale -> samples changed; previews_failed / samples_not_ready -> fails as before; the bodies "
                   "sent are exactly {batch_id, feedback} and {batch_id, config_digest}",
               [results[k] for k in [("feedback", "batch_not_latest"), ("feedback", "conflict")]] == ["changed", "changed"]
               and results[("feedback", "state_conflict")].startswith("failed: STOPPED: the campaign is no longer a DRAFT")
               and [results[("feedback", c)] for c in ("feedback_too_long", "validation_error", "preview_budget_exhausted")]
               == ["refused"] * 3
               and [results[("approve", c)] for c in ("batch_not_latest", "approval_stale")] == ["changed", "changed"]
               and "The sample failed and cannot be approved" in results[("approve", "previews_failed")]
               and "Samples are not ready yet" in results[("approve", "samples_not_ready")]
               and reply["batch_id"] == "b2" and sent[0] == ("feedback", {"batch_id": "b1", "feedback": "note"})
               and sent[6] == ("approve", {"batch_id": "b1", "config_digest": "d1"}))
        finally:
            email_tool.call_tool = real_call_tool
            if saved_key is None:
                os.environ.pop("EMAIL_TOOL_KEY", None)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["email"] = real_entry
        email_tool.call_tool = real_call_tool
        if real_load_json is None:
            email_tool.__dict__.pop("load_json", None)
        else:
            email_tool.load_json = real_load_json
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (email) OK" if ok else "ORCHESTRATOR SELFTEST (email) FAILED")
    return ok


def selftest_calls() -> bool:
    """Calls runner (T7) with FakeCallTools, + Quickhowl safety checks (no network, no LLM, no calls)."""
    import contextlib
    import io
    import quickhowl
    from runner_calls import POLL_LIMIT_SECONDS, POLL_EVERY_SECONDS, FakeCallTools
    from runner_quickicp_accounts import SimulatedCrash
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator CALLS selftest (fake Quickhowl): customer '{customer}'")
    real_entry = WIRED_RUNNERS["calls"]
    ok = False
    phones = ["+919811300501", "+911126142451", "+918126409111", "+919694757222"]   # 2nd is a landline
    rows = [{"id": 911 + i, "customer": customer, "full_name": f"Caller {i}", "phone_number": phone,
             "company": {"company_name": f"C{i}"}} for i, phone in enumerate(phones)]

    def setup(month, fake):
        WIRED_RUNNERS["calls"] = (real_entry[0], lambda ctx: fake)
        return outreach_fixture(customer, month, "Quickhowl", "calls", [r["id"] for r in rows])

    def t2(run):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]

    def decision(run, kind="call_start"):
        found = [d for d in get_decisions(run["id"]) if d["kind"] == kind]
        return found[-1] if found else None

    def started(month, fake):
        run = setup(month, fake)
        cmd_run(customer, month)
        cmd_answer(customer, decision(run)["id"], "yes", "call our test phones")
        return run

    saved_flags = (quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED)
    try:
        # Q1-Q3. mobile only; test numbers only; stored -> call_start -> start -> poll -> done with outcomes
        fake = FakeCallTools(rows)
        run = setup("2029-07", fake)
        cmd_run(customer, "2029-07")
        q = decision(run)["question"]
        expect("Q1 landline skipped; TEST MODE: 2 contacts (2 test numbers), our numbers only; stored, nobody called",
               [c["phone"] for c in q["contacts"]] == ["+919000000001", "+919000000002"]
               and fake.calls["store"] == 1 and fake.calls["start"] == 0 and t2(run)["waiting_for"] == "call_start")
        cmd_answer(customer, decision(run)["id"], "yes", "call our test phones")
        cmd_run(customer, "2029-07")
        task = t2(run)
        expect("Q3 yes -> start -> polled until finished -> done: 2 completed, outcomes per person, landline recorded",
               fake.calls["start"] == 1 and task["status"] == "done" and task["achieved_number"] == 2
               and [o["person_id"] for o in task["result"]["outcomes"]] == [911, 913]
               and task["result"]["skipped_landlines"][0]["phone_type"] == "landline"
               and task["result"]["outcome_counts"] == {"interested": 1, "no_answer": 1})

        # Q2. Quickhowl's own safety checks (module flags switched for this check only)
        quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = False, False
        expect("Q2 real mode refused by quickhowl.assert_safe (REAL_CALLS_ENABLED = False)",
               raises(lambda: quickhowl.assert_safe({"contacts": [{"phone": "+919811300501"}]})))
        quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = saved_flags
        if quickhowl.TEST_MODE and os.getenv("TEST_CALL_NUMBERS"):
            expect("Q2 test mode refuses a phone that is not a TEST_CALL_NUMBER",
                   raises(lambda: quickhowl.assert_safe({"contacts": [{"phone": "+910000000000"}]})))

        # Q4. no -> stopped, nobody called
        fake = FakeCallTools(rows)
        run = setup("2029-08", fake)
        cmd_run(customer, "2029-08")
        cmd_answer(customer, decision(run)["id"], "no", "not today")
        cmd_run(customer, "2029-08")
        expect("Q4 no -> task stopped, start never sent", t2(run)["status"] == "stopped" and fake.calls["start"] == 0)

        # Q5. crash DURING start -> sent again automatically ('already_started') -> done
        fake = FakeCallTools(rows, crash_start=1)
        run = setup("2029-09", fake)
        cmd_run(customer, "2029-09")
        cmd_answer(customer, decision(run)["id"], "yes", "go")
        try:
            cmd_run(customer, "2029-09")
            crashed = False
        except SimulatedCrash:
            crashed = True
        cmd_run(customer, "2029-09")
        expect("Q5 crash during start -> next run resends the SAME request (no human) -> done",
               crashed and fake.calls["start"] == 2 and fake.calls["build"] == 1 and t2(run)["status"] == "done")

        # Q6. still a DRAFT after start -> polling stops at once (known issue 3) -> task_failed
        fake = FakeCallTools(rows, statuses=("draft",))
        run = started("2029-10", fake)
        cmd_run(customer, "2029-10")
        expect("Q6 draft after start -> stops after ONE poll, task_failed decision (retry / skip / stop)",
               fake.calls["status"] == 1 and t2(run)["waiting_for"] == "task_failed" and "DRAFT" in t2(run)["last_error"])

        # Q7. calls still going after 10 minutes -> 'running'; the next run continues polling
        fake = FakeCallTools(rows, statuses=("running",))
        run = started("2029-11", fake)
        cmd_run(customer, "2029-11")
        first = fake.calls["status"]
        expect(f"Q7 still running after {POLL_LIMIT_SECONDS // 60} min -> task stays 'running' "
               f"({first} polls, {POLL_EVERY_SECONDS}s apart)",
               t2(run)["status"] == "running" and first == POLL_LIMIT_SECONDS // POLL_EVERY_SECONDS + 1)
        cmd_run(customer, "2029-11")
        expect("Q7 the next run continues polling (not started again)", fake.calls["status"] > first
               and fake.calls["start"] == 1)

        # Q8. outside the calling window -> 'running' at once
        fake = FakeCallTools(rows, statuses=("running",), in_window=False)
        run = started("2029-12", fake)
        cmd_run(customer, "2029-12")
        expect("Q8 outside the calling window -> 'running' after one poll", fake.calls["status"] == 1
               and t2(run)["status"] == "running")

        # Q9. --dry
        fake = FakeCallTools(rows)
        run = setup("2030-01", fake)
        result = cmd_dry(customer, "2030-01")
        expect("Q9 --dry: 2 contacts, 1 skipped landline; nothing stored, nothing recorded",
               result.output["contacts"] == 2 and result.output["skipped"] == 1 and fake.calls["store"] == 0
               and t2(run)["status"] == "pending")
        # Q10. the real-call gate: REAL_CALLS_ENABLED (go-live) AND QUICKHOWL_DND_HANDLED=true (flags in-process only)
        saved_dnd = os.environ.get("QUICKHOWL_DND_HANDLED")
        try:
            real_contact = {"contacts": [{"phone": "+919811300501"}]}
            quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = False, False
            os.environ["QUICKHOWL_DND_HANDLED"] = "true"
            expect("Q10 real mode + REAL_CALLS_ENABLED False -> blocked ('off ... go-live'), even with DND handled",
                   raises(lambda: quickhowl.assert_safe(real_contact))
                   and "go-live" in quickhowl.real_calls_block_reason())
            quickhowl.REAL_CALLS_ENABLED = True
            os.environ.pop("QUICKHOWL_DND_HANDLED")
            expect("Q10 real mode + REAL on + QUICKHOWL_DND_HANDLED missing -> blocked (names the setting)",
                   raises(lambda: quickhowl.assert_safe(real_contact))
                   and "QUICKHOWL_DND_HANDLED" in quickhowl.real_calls_block_reason())
            os.environ["QUICKHOWL_DND_HANDLED"] = "yes"
            expect("Q10 QUICKHOWL_DND_HANDLED=yes is not 'true' -> still blocked", raises(lambda: quickhowl.assert_safe(real_contact)))
            os.environ["QUICKHOWL_DND_HANDLED"] = "TRUE"
            quickhowl.assert_safe(real_contact)
            expect("Q10 real mode + REAL on + QUICKHOWL_DND_HANDLED=TRUE -> allowed (no call is made here)",
                   quickhowl.real_calls_block_reason() is None)
            quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = True, False
            os.environ.pop("QUICKHOWL_DND_HANDLED")
            expect("Q10 test mode unchanged: never blocked by the DND setting; the note says 'own numbers only'",
                   quickhowl.real_calls_block_reason() is None and "own numbers only" in quickhowl.dnd_note())
        finally:
            quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = saved_flags
            if saved_dnd is None:
                os.environ.pop("QUICKHOWL_DND_HANDLED", None)
            else:
                os.environ["QUICKHOWL_DND_HANDLED"] = saved_dnd

        # Q11 / Q12. DND note on every call start; contacts Quickhowl rejected -> T7 output + review counts
        rejected = [{"phone": "+919811300501", "reason": "Number is on the DND registry"},
                    {"phone": "+918126409111", "reason": "duplicate"}]
        fake = FakeCallTools(rows, rejected=rejected)
        run = setup("2030-02", fake)
        cmd_run(customer, "2030-02")
        q = decision(run)["question"]
        cmd_answer(customer, decision(run)["id"], "yes", "go")
        cmd_run(customer, "2030-02")
        from state_store import get_steps
        start_row = [r for r in get_steps(t2(run)["id"]) if r["step"] == "start"][0]
        result = t2(run)["result"]
        expect("Q11 call_start question shows the DND confirmation; the ledger start row saves it; T7 output keeps it",
               "DND: handled by the Quickhowl platform" in q["text"] and start_row["request"]["dnd_handled"] is True
               and "Quickhowl platform" in start_row["request"]["dnd_note"]
               and "Quickhowl platform" in result["dnd_confirmation"])
        expect("Q12 T7 output: 2 contacts rejected by Quickhowl (store + start replies, no duplicates), 1 do-not-call",
               result["rejected_by_quickhowl"] == 2 and result["rejected_do_not_call"] == 1
               and len(result["contacts_rejected"]) == 2)
        from brain_review import task_numbers
        numbers = task_numbers(t2(run))
        expect("Q12 the review counts them: rejected_by_quickhowl 2, rejected_do_not_call 1",
               numbers["rejected_by_quickhowl"] == 2 and numbers["rejected_do_not_call"] == 1)

        # Q13. check_system: Quickhowl DND PASS when true, WARN when missing; real calls reported as set.
        # The flag is set here (in-process, restored with saved_flags), never read from the shared quickhowl.py value.
        import check_system
        saved_dnd = os.environ.get("QUICKHOWL_DND_HANDLED")
        try:
            lines = {}
            for value, real in (("true", False), (None, True)):
                if value is None:
                    os.environ.pop("QUICKHOWL_DND_HANDLED", None)
                else:
                    os.environ["QUICKHOWL_DND_HANDLED"] = value
                quickhowl.REAL_CALLS_ENABLED = real
                check_system.results.clear()
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    check_system.check_quickhowl()
                lines[value] = captured.getvalue()
            expect("Q13 check_system: 'Quickhowl DND' PASS with true, WARN when missing; 'Real calls' PASS "
                   "'off until go-live' when off, WARN 'real prospects can be called' when on",
                   "PASS  Quickhowl DND" in lines["true"] and "WARN  Quickhowl DND" in lines[None]
                   and "PASS  Real calls" in lines["true"] and "off until go-live" in lines["true"]
                   and "WARN  Real calls" in lines[None] and "real prospects can be called" in lines[None])
        finally:
            check_system.results.clear()
            if saved_dnd is None:
                os.environ.pop("QUICKHOWL_DND_HANDLED", None)
            else:
                os.environ["QUICKHOWL_DND_HANDLED"] = saved_dnd
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["calls"] = real_entry
        quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = saved_flags
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (calls) OK" if ok else "ORCHESTRATOR SELFTEST (calls) FAILED")
    return ok


def selftest_5a() -> bool:
    """--until-wait, 'answer T<n>', full 'decisions' questions. Fake QuickICP only; no network, no LLM."""
    import contextlib
    import io
    from runner_quickicp_accounts import FakeAccountsTools
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator 5a selftest (--until-wait, answer T<n>, decisions): customer '{customer}'")
    real_entry = WIRED_RUNNERS["quickicp_accounts"]
    ok = False
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 4, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output, deps, target="test"):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "data_build" if agent == "QuickICP" else "coordination", "week": 1,
                "depends_on": deps, "target": target, "instructions": ["test"], "done_when": f"{task_id} done"}

    def task(run, task_id):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == task_id][0]

    try:
        fake = FakeAccountsTools(existing=[{**FakeAccountsTools.company(f"ex{i}.test", True), "_id": 500 + i}
                                           for i in range(1, 7)])
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: fake)
        run, _ = load_run(customer, "2030-02", [wo("T1", "CAM", "coordination", []),
                                                wo("T2", "CAM", "coordination", ["T1"]),
                                                wo("T3", "QuickICP", "accounts", ["T2"], "5 accounts"),
                                                wo("T4", "CAM", "handover", ["T3"])],
                          {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)

        # U1. a fresh run: step 1 starts T1 (a person is needed) -> stop
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U1 --until-wait stops after 1 step: T1 needs a person",
               out["steps"] == 1 and "T1 needs a person" in out["stopped_because"]
               and task(run, "T1")["status"] == "waiting_human")

        # U2. answer by TASK ID, then --until-wait applies it and stops at the next person
        cmd_answer_task(customer, "T1", "done", "kick-off held")
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U2 'answer T1 done' works; --until-wait applies it and stops at T2 (person)",
               task(run, "T1")["status"] == "done" and "T2 needs a person" in out["stopped_because"])

        # U3. next ready task is QuickICP (buys data) -> --until-wait stops BEFORE starting it
        cmd_answer_task(customer, "T2", "done", "ok")
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U3 --until-wait stops before a NEW QuickICP task: 'next would buy data', T3 not started, 0 calls",
               "would buy data (T3)" in out["stopped_because"] and task(run, "T3")["status"] == "pending"
               and fake.calls["build_payload"] == 0 and fake.calls["search"] == 0)

        # U4. a plain 'run' starts T3 (reuse covers it here); then --until-wait goes on to T4 (person)
        cmd_run(customer, "2030-02")
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U4 plain run did T3; --until-wait then stops at T4 (person)",
               task(run, "T3")["status"] == "done" and "T4 needs a person" in out["stopped_because"])

        # U5. nothing to do -> stops at once; the lock is free afterwards; a held lock is refused
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U5 nothing to do -> stops after 1 step ('nothing left to do now')",
               out["steps"] == 1 and "nothing left" in out["stopped_because"])
        expect("U5 lock released after --until-wait", get_run(run["run_key"])["locked_by"] is None)
        take_run_lock(run["id"], "other-process")
        expect("U5 --until-wait refused while another run holds the lock",
               raises(lambda: cmd_run_until_wait(customer, "2030-02")))
        force_unlock(run["id"])

        # U6. answer by task id: refused with 0 or 2 open decisions
        expect("U6 'answer T3' refused: T3 has no open decision", raises(lambda: cmd_answer_task(customer, "T3", "done", "x")))
        t4 = task(run, "T4")
        email_q = open_decision(run["id"], "email_approve", {
            "text": "Approve these sample emails?",
            "samples": [{"email": 1, "for": "Asha at C1", "subject": "Hello Anvixa", "text": "Hi Asha,\nShort demo?"}]},
            t4["id"])
        expect("U6 'answer T4' refused: T4 has 2 open decisions (lists them)",
               raises(lambda: cmd_answer_task(customer, "T4", "done", "x")))

        # U7. 'decisions' prints the full email samples and the call numbers
        call_q = open_decision(run["id"], "call_start", {
            "text": "Start calling?", "contacts": [{"name": "Ravi", "company": "C2", "phone": "+919000000001"}]},
            t4["id"])
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            cmd_decisions(customer)
        text = printed.getvalue()
        expect("U7 decisions shows the email sample (subject + text) and who will be called on which number",
               "Subject: Hello Anvixa" in text and "Short demo?" in text and "Ravi | C2 | number +919000000001" in text)

        # U8. the stop rules themselves (pure code)
        base = {"run_status": "running", "blocked_paid": None, "human_started": False, "tool_kind": None,
                "applied": [], "resumed": [], "started": None, "tool": None}
        expect("U8 stop rules: done/stopped, paid next, person, tool waiting/failed, tool running, no progress",
               until_wait_stop({**base, "run_status": "done"}) == "run is done"
               and "buy data" in until_wait_stop({**base, "blocked_paid": "T9"})
               and "a tool task needs a human" in until_wait_stop({**base, "tool_kind": "waiting", "tool": "x"})
               and "still running" in until_wait_stop({**base, "tool_kind": "running", "tool": "x"})
               and "nothing left" in until_wait_stop(base))
        expect("U8 keep going after progress with no human needed (answer applied / tool done)",
               until_wait_stop({**base, "applied": ["T1 -> done"]}) is None
               and until_wait_stop({**base, "tool_kind": "done", "tool": "T3 -> done", "started": "T3"}) is None)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (5a) OK" if ok else "ORCHESTRATOR SELFTEST (5a) FAILED")
    return ok


def selftest_5b() -> bool:
    """review: code numbers, data quality, objections, fake LLM text, checks, action decisions. No network, no real LLM."""
    import contextlib
    import io
    from brain_review import (NOT_AVAILABLE, TEST_CAP_LINE, TRANSCRIPT_CHARS, CallLabel, CallLabels, NextAction,
                              ReviewText, call_text)
    from state_store import finish_step, start_step
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator 5b selftest (review, fake LLM): customer '{customer}'")
    ok = False
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 5, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    rule = "If lead response rate is below 10% then refine outreach messaging"
    plan = {"month": "Month 1", "objectives": ["Build the account list"], "revenue_analysis": {},
            "kpis_to_track": ["Number of accounts built", "Number of AI calls made and outcomes"],
            "optimization_rules": [rule]}

    def wo(task_id, agent, output, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "x", "week": 1, "depends_on": [], "target": target, "instructions": ["test"]}

    def fake_ask(texts, labels=None):
        """A fake LLM: CallLabels -> the given labels; ReviewText -> the given texts in order. Records prompts."""
        review_prompts, label_prompts = [], []

        def ask(schema, prompt, name):
            if schema is CallLabels:
                label_prompts.append(prompt)
                return CallLabels(labels=labels or [])
            review_prompts.append(prompt)
            return texts[len(review_prompts) - 1]
        ask.review_prompts, ask.label_prompts = review_prompts, label_prompts
        return ask

    def action(text_, kind="quality", follows="new", task_id="T7"):
        return NextAction(action=text_, why="from the numbers", task_id=task_id, kind=kind, follows_rule=follows)

    def text(worked, actions):
        return ReviewText(what_worked=[worked], whats_short=["Email opens are not available yet"], next_actions=actions)

    def answer_open_actions(run):
        for d in get_decisions(run["id"]):
            if d["kind"].startswith("review_action_") and d["status"] == "open":
                cmd_answer(customer, d["id"], "reject", "test")

    labels3 = [CallLabel(index=0, label="building_own_solution", quote="we are building our own AI"),
               CallLabel(index=1, label="call_later", quote="call me next month"),
               CallLabel(index=2, label="made_up_label", quote="hmm")]

    try:
        run, _ = load_run(customer, "2030-03", [wo("T3", "QuickICP", "accounts", "150-200 accounts"),
                                                wo("T4", "QuickICP", "contacts", "250-350 contacts"),
                                                wo("T11", "Email Agent", "emails", "Emails sent to 250-350 contacts"),
                                                wo("T7", "Quickhowl", "calls", "Calls to 250-350 contacts"),
                                                wo("T8", "Human Tele Calling", "qualified_leads", "10-12 enquiries")],
                          plan, {}, True, proof)
        tasks = {t["task_id"]: t for t in get_tasks(run["id"])}
        results = {
            "T3": (181, {"target": 40, "reused": 181, "bought": 0, "company_ids": [1]}),
            "T4": (15, {"target": 10, "reused": 15, "bought": 0, "with_email": 15, "with_phone": 15, "mobile": 12,
                        "landline": 3, "unknown_phone": 0, "people_ids": [5]}),
            "T11": (3, {"target": 3, "recipients": 3, "rejected": 0, "campaign_status": "RUNNING"}),
            "T7": (3, {"target": 3, "outcome_counts": {"not_interested": 2, "callback": 1},
                       "skipped_landlines": [{"person_id": 9}],
                       "outcomes": [{"name": "Ravi", "company": "C2", "outcome": "not_interested",
                                     "duration_seconds": 61, "summary": "Not interested right now."}]}),
        }
        for task_id, (achieved, result) in results.items():
            update_task(tasks[task_id]["id"], status="done", achieved_number=achieved, result=result)
        # Ledger rows the review reads: judge counts (reuse) and call transcripts (poll)
        for task_id, output in (("T3", {"existing": 192, "passed": 181}),
                                ("T4", {"existing": 22, "passed": [{"x": i} for i in range(15)]})):
            row, _ = start_step(tasks[task_id]["id"], "reuse", f"{run['run_key']}:{task_id}:reuse", "supabase", False, {})
            finish_step(row["id"], "done", output=output)
        long_line = "word " * 600
        calls = {f"call-{i}": {"call_id": f"call-{i}", "contact": {"name": f"P{i}"}, "outcome": "not_interested",
                               "summary": f"summary {i}", "transcript": [{"speaker": "agent", "text": "Hello"},
                                                                         {"speaker": "user", "text": long_line}]}
                 for i in range(3)}
        row, _ = start_step(tasks["T7"]["id"], "poll", f"{run['run_key']}:T7:poll:1", "quickhowl", False, {})
        finish_step(row["id"], "done", output={"since": None, "calls": calls, "status": "completed"})

        # R1. numbers (code)
        ask = fake_ask([text("181 accounts were reused, 0 bought",
                             [action("Ask about in-house AI plans early in the script", "script", rule),
                              action("Scale calls towards the plan target", "volume"),
                              action("Remove landlines before calling", "data_quality"),
                              action("Re-check job titles that failed", "targeting"),
                              action("A fifth action", "process")])], labels3)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            out = cmd_review(customer, "2030-03", ask=ask)
        n = {t["task_id"]: t for t in out["numbers"]["tool_tasks"]}
        expect("R1 4 done tool tasks reviewed (T8 is a human task, not reviewed)", sorted(n) == ["T11", "T3", "T4", "T7"])
        expect("R1 T4: 15 of effective 10 = 150%, of plan 350 = 4%",
               n["T4"]["pct_of_effective_target"] == 150 and n["T4"]["pct_of_plan_target"] == 4)
        expect("R1 T11: opens and replies 'not available yet'",
               n["T11"]["email_opens"] == NOT_AVAILABLE and n["T11"]["email_replies"] == NOT_AVAILABLE)

        # R6. data quality (code): % and flags at the thresholds (landline >= 20, people judge >= 30)
        dq = out["numbers"]["data_quality"]
        flagged = sorted(f["signal"] for f in dq["flagged"])
        expect("R6 data quality: landline 20%, without email 0%, companies failed 6%, people failed 32%",
               dq["landline_pct"] == 20 and dq["without_email_pct"] == 0 and dq["companies_failed_judge_pct"] == 6
               and dq["people_failed_judge_pct"] == 32)
        expect("R6 flagged exactly: landline (20 >= 20) and people judge (32 >= 30); not email, not companies",
               flagged == ["landline_pct", "people_failed_judge_pct"])

        # R7. objections: the LLM labels, code counts; unknown label -> other; quotes kept; text cut
        obj = out["numbers"]["objections"]
        expect("R7 3 calls labelled in 1 LLM call; counts by code; 'made_up_label' -> other; quotes kept",
               len(ask.label_prompts) == 1 and obj["calls_labelled"] == 3
               and obj["counts"] == {"building_own_solution": 1, "call_later": 1, "other": 1}
               and obj["quotes"][0]["quote"] == "we are building our own AI")
        short = call_text({"summary": "s", "outcome": "x", "transcript": [{"speaker": "agent", "text": "Hello sir"},
                                                                           {"speaker": "user", "text": "No thanks"}]})
        expect(f"R7 each call's text cut to {TRANSCRIPT_CHARS} characters; prospect lines come before the caller's",
               len(call_text(calls["call-0"])) == TRANSCRIPT_CHARS
               and short.index("Prospect: No thanks") < short.index("Caller: Hello sir"))
        expect("R7 the review prompt gets the objection counts, the quotes and the flags",
               "building_own_solution" in ask.review_prompts[0] and "we are building our own AI" in ask.review_prompts[0]
               and "people_failed_judge_pct" in ask.review_prompts[0] and "TEST MODE IS ON" in ask.review_prompts[0])

        # R8. TEST_MODE: volume action dropped; 'test caps' said once; max 3 actions; rules shown
        decisions = get_decisions(run["id"])
        actions = sorted([d for d in decisions if d["kind"].startswith("review_action_") and d["status"] == "open"],
                         key=lambda d: d["kind"])
        expect("R8 TEST_MODE: the 'volume' action dropped, 3 of the remaining 4 kept (2 LLM calls in all)",
               out["llm_calls"] == 2 and len(actions) == 3
               and [d["question"]["kind"] for d in actions] == ["script", "data_quality", "targeting"])
        expect("R8 'volume shortfalls are test caps' said exactly once",
               out["text"].whats_short.count(TEST_CAP_LINE) == 1)
        expect("R8 each action names its plan rule (exact) or 'new'",
               actions[0]["question"]["follows_rule"] == rule and actions[1]["question"]["follows_rule"] == "new"
               and "follows plan rule:" in actions[0]["question"]["text"])

        # R2. saved: run_review + task results tagged, runner fields untouched
        review = [d for d in decisions if d["kind"] == "run_review"]
        t4 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T4"][0]
        expect("R2 run_review recorded (brain) with numbers, text and the 3 action ids; T4 result tagged, fields kept",
               len(review) == 1 and review[0]["decided_by"] == "brain"
               and sorted(review[0]["question"]["action_decision_ids"]) == sorted(d["id"] for d in actions)
               and t4["result"]["review"]["review_decision_id"] == review[0]["id"] and t4["result"]["mobile"] == 12)

        # R3. open actions block a new review; answers need a note; answering only records
        expect("R3 a new review is refused while review actions are open",
               raises(lambda: cmd_review(customer, "2030-03", ask=fake_ask([text("x", [])], labels3))))
        expect("R3 accept without a note refused", raises(lambda: cmd_answer(customer, actions[0]["id"], "accept", None)))
        before = {t["task_id"]: t["status"] for t in get_tasks(run["id"])}
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            cmd_answer(customer, actions[0]["id"], "accept", "yes, change the opener")
        expect("R9 answering a review action prints 'Recorded. Nothing runs automatically.'",
               "Recorded. Nothing runs automatically." in printed.getvalue() and "applied on the next" not in printed.getvalue())
        answer_open_actions(run)
        expect("R3 accepting / rejecting only records the choice: no task changed",
               {t["task_id"]: t["status"] for t in get_tasks(run["id"])} == before)

        # R10. follows_rule not a plan rule -> one retry naming it -> fixed
        ask = fake_ask([text("ok", [action("Do X", "script", "If calls fail then call more")]),
                        text("ok", [action("Do X", "script", rule)])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R10 invented rule -> one retry naming it -> the exact plan rule is saved",
               len(ask.review_prompts) == 2 and "If calls fail then call more" in ask.review_prompts[1]
               and out["text"].next_actions[0].follows_rule == rule and out["note"] == "")
        answer_open_actions(run)
        ask = fake_ask([text("ok", [action("Do X", "script", "made-up rule")]),
                        text("ok", [action("Do X", "script", "still made up")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R10 still not a plan rule after the retry -> kept, marked 'new (rule not matched)', with a note",
               out["text"].next_actions[0].follows_rule == "new (rule not matched)" and "not matched" in out["note"])
        answer_open_actions(run)

        # R4 / R5. invented numbers -> one retry -> fixed, or numbers-only
        ask = fake_ask([text("We reached 999 decision makers", [action("Do X")]),
                        text("Accounts were fully reused", [action("Do X")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R4 invented '999' -> one retry naming it -> the second text is saved (1 label + 2 review calls)",
               out["llm_calls"] == 3 and "999" in ask.review_prompts[1]
               and out["text"].what_worked == ["Accounts were fully reused"])
        answer_open_actions(run)
        ask = fake_ask([text("777 calls", [action("Do Y")]), text("still 777", [action("Do Y")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R5 invented twice -> numbers-only review: no text, no actions, the note says why",
               out["text"] is None and out["action_ids"] == [] and "invented numbers ['777']" in out["note"])
        expect("R5 the lock is free after a review", get_run(run["run_key"])["locked_by"] is None)

        # R11 / R12. claims RESULTS do not show (outcomes here: no booked demo) -> one retry -> fixed, or numbers-only
        from brain_review import unsupported_claims
        def calls_data(outcomes, completed=None, summary=""):
            """RESULTS with one calls task whose calls have these outcomes (the outcome data the guard reads)."""
            return {"tool_tasks": [{"task_id": "T7", "outcome_counts": {o: 1 for o in outcomes},
                                    "calls_completed": len(outcomes) if completed is None else completed,
                                    "calls": [{"outcome": o, "summary": summary} for o in outcomes]}],
                    "objections": {"counts": {}, "quotes": []}}
        expect("R11 code: negated claims and suggested actions are fine; evidence in RESULTS allows the claim",
               unsupported_claims(text("No demo was booked yet", [action("Get a demo booked with the prospect")]), {}) == []
               and unsupported_claims(text("A demo was scheduled", []), calls_data(["demo_scheduled"])) == []
               and len(unsupported_claims(text("A demo was scheduled", []), calls_data(["interested"]))) == 1)
        ask = fake_ask([text("A demo was scheduled with the interested prospect", [action("Do Z")]),
                        text("Calls reached decision makers", [action("Do Z")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R11 'demo was scheduled' (outcome only 'interested') -> one retry naming it -> the second text is saved",
               out["llm_calls"] == 3 and "A demo was scheduled" in ask.review_prompts[1]
               and out["text"].what_worked == ["Calls reached decision makers"])
        answer_open_actions(run)
        ask = fake_ask([text("Meeting booked with the prospect", [action("Do Z")]),
                        text("We signed a deal this week", [action("Do Z")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R12 claims twice -> numbers-only review: no text, no actions, the note says 'claims not in the data'",
               out["text"] is None and out["action_ids"] == [] and "claims not in the data" in out["note"]
               and "We signed a deal this week" in out["note"])

        # R13. the wider guard: every outcome claim needs outcome data (never the plan); negations stay fine
        cases = [("prospect replied", "Two prospects replied to the emails", calls_data(["call_later"]),
                  calls_data(["call_later"], summary="the prospect replied by email asking for pricing")),
                 ("meeting or demo held", "A demo was held with the CIO", calls_data(["interested"]),
                  calls_data(["demo_held"])),
                 ("call completed", "Three calls were completed", calls_data([], completed=0),
                  calls_data(["interested", "call_later", "not_interested"])),
                 ("lead qualified", "We got 2 qualified leads", calls_data(["interested"]), calls_data(["qualified"])),
                 ("prospect interested", "One prospect is interested", calls_data(["not_interested"]),
                  calls_data(["interested"])),
                 ("proposal or RFQ", "A proposal was sent to Tata Steel", calls_data(["interested"]),
                  calls_data(["interested"], summary="asked us to share a proposal")),
                 ("POC or pilot", "The pilot started at C1", calls_data(["interested"]),
                  calls_data(["interested"], summary="agreed to a pilot")),
                 ("deal or order won", "A purchase order was received", calls_data(["interested"]),
                  calls_data(["order_received"]))]
        caught = {name: [c.split(":")[0] for c in unsupported_claims(text(sentence, []), without)]
                  for name, sentence, without, _ in cases}
        allowed = {name: unsupported_claims(text(sentence, []), with_) for name, sentence, _, with_ in cases}
        expect("R13 caught WITHOUT outcome data: replied, demo held, calls completed, lead qualified, interested, "
               "proposal sent, pilot started, purchase order received",
               all(name in caught[name] for name in caught))
        expect("R13 allowed WITH matching outcome data (call outcomes, summaries, calls completed)",
               all(found == [] for found in allowed.values()))
        expect("R13 negations stay fine: 'nobody replied yet', 'no meeting was held', 'not qualified'",
               unsupported_claims(text("Nobody replied yet", []), calls_data([])) == []
               and unsupported_claims(text("No meeting was held", []), calls_data([])) == []
               and unsupported_claims(text("The leads were not qualified", []), calls_data([])) == [])
        plan_only = {**calls_data(["call_later"]), "tool_tasks": [{**calls_data(["call_later"])["tool_tasks"][0],
                                                                   "plan_target": "10-12 qualified enquiries"}]}
        expect("R13 the plan's own text is never evidence ('qualified' in the plan target); a target sentence "
               "is not a claim; 'not interested' is not evidence of interest",
               caught["lead qualified"] and len(unsupported_claims(text("We got 2 qualified leads", []), plan_only)) == 1
               and unsupported_claims(text("We are short of the 10-12 qualified enquiries target", []), plan_only) == []
               and len(unsupported_claims(text("One prospect is interested", []), calls_data(["not_interested"]))) == 1)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (5b) OK" if ok else "ORCHESTRATOR SELFTEST (5b) FAILED")
    return ok


def selftest_close() -> bool:
    """
    close + revision months: the old run is closed honestly (ledger, done tasks, answers kept), the new plan
    loads as <month>-r2 next to it, and nothing runs or is answered on the old run afterwards. No tool, no LLM.
    """
    from state_store import finish_step, get_steps, start_step
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator CLOSE selftest: customer '{customer}'")
    proof = {"approved_at": "2026-10-12T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 6, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    plan = {"month": "Month 1", "revenue_analysis": {}}
    reason = "replaced by the intake plan"
    ok = False

    def task(run, task_id):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == task_id][0]

    def snapshot(run) -> tuple:
        """Everything close may change or must keep: run, tasks, decisions, ledger rows."""
        row = get_run(run["run_key"])
        tasks = get_tasks(run["id"])
        return ((row["status"], row["stop_reason"], row["locked_by"]),
                [(t["task_id"], t["status"], t["last_error"], t["finished_at"], t["waiting_for"]) for t in tasks],
                [(d["id"], d["status"], d["answer"], d["decided_by"], d["reason"]) for d in get_decisions(run["id"])],
                [(s["idempotency_key"], s["status"], s["response"]) for t in tasks for s in get_steps(t["id"])])

    try:
        # Month pattern (pure code)
        expect("month 2026-10 and revisions 2026-10-r2 / -r12 accepted",
               all(check_month(m) == m for m in ("2026-10", "2026-10-r2", "2026-10-r12")))
        expect("bad revisions refused: -r0, -r1, -r02, -x2, 2026-13-r2, 2026-10-r",
               all(raises(lambda m=m: check_month(m))
                   for m in ("2026-10-r0", "2026-10-r1", "2026-10-r02", "2026-10-x2", "2026-13-r2", "2026-10-r")))
        expect("month_of reads the month back from the run key",
               month_of({"run_key": "anvixa-ai-product-2026-10-r2", "customer": "anvixa-ai-product"}) == "2026-10-r2"
               and month_of({"run_key": "anvixa-ai-product-2026-10", "customer": "anvixa-ai-product"}) == "2026-10")
        expect("closing answers: gate/human/goal -> stop, email/call -> no, review action -> reject",
               close_answer("human_gate_task") == "stop" and close_answer("goal_check") == "stop"
               and close_answer("email_approve") == "no" and close_answer("call_start") == "no"
               and close_answer("review_action_1") == "reject")

        # The old run: T1 done, T2 gate + T3 human waiting, T4 email waiting for approval (+ a ledger row),
        # T5 stopped earlier for its own reason, T6 pending, and an open review action on the run.
        old, _ = load_run(customer, "2026-10", run_work_orders(), plan, {}, True, proof)
        update_task(task(old, "T1")["id"], status="done", finished_at=now_iso())
        for task_id, kind in (("T2", "human_gate_task"), ("T3", "human_task"), ("T4", "email_approve")):
            row = task(old, task_id)
            update_task(row["id"], status="waiting_human", waiting_for=kind)
            open_decision(old["id"], kind, {"text": f"selftest {task_id}"}, row["id"])
        update_task(task(old, "T5")["id"], status="stopped", last_error="stopped by user: client cancelled",
                    finished_at=now_iso())
        open_decision(old["id"], "review_action_1", {"text": "selftest action"})
        step, _ = start_step(task(old, "T4")["id"], "launch", f"{old['run_key']}:T4:launch", "email_tool", False,
                             {"campaign": "selftest"})
        finish_step(step["id"], "done", response={"campaign_id": "selftest-campaign"}, external_ref="selftest-campaign")
        before = snapshot(old)

        # Refusals: nothing changes
        expect("close without a reason refused, nothing changed",
               raises(lambda: close_run(customer, "2026-10", "  ", "selftest-holder")) and snapshot(old) == before)
        take_run_lock(old["id"], "someone-else")
        locked = snapshot(old)
        expect("close while another 'run' holds the lock refused, nothing changed, the lock stays theirs",
               raises(lambda: close_run(customer, "2026-10", reason, "selftest-holder"))
               and snapshot(old) == locked and locked[0][2] == "someone-else")
        force_unlock(old["id"])
        update_task(task(old, "T4")["id"], status="running", waiting_for=None)
        held = snapshot(old)
        expect("close while a TOOL task is running refused, nothing changed, lock released",
               raises(lambda: close_run(customer, "2026-10", reason, "selftest-holder"))
               and snapshot(old) == held and get_run(old["run_key"])["locked_by"] is None)
        update_task(task(old, "T4")["id"], status="waiting_human", waiting_for="email_approve")
        expect("the test run is back as it was", snapshot(old) == before)
        expect("typing the wrong run key does not close",
               cmd_close(customer, "2026-10", reason, confirm=lambda _: "anvixa-ai-product-2026-10") is None
               and snapshot(old) == before)

        # Close
        result = cmd_close(customer, "2026-10", reason, confirm=lambda _: old["run_key"])
        after = get_run(old["run_key"])
        answers = {(task(old, t)["id"] if t else None): a for t, a in
                   (("T2", "stop"), ("T3", "stop"), ("T4", "no"), (None, "reject"))}
        decided = {d["task_row_id"]: d for d in get_decisions(old["id"]) if d["kind"] != "plan_approval"}
        expect("close answers the 4 open decisions: T2 gate stop, T3 stop, T4 email 'no', review action reject",
               result["closed_now"] and len(result["decisions"]) == 4
               and all(decided[k]["status"] == "answered" and decided[k]["answer"] == a for k, a in answers.items()))
        expect("each answer: decided by 'user', note 'run closed: <reason>'",
               all(decided[k]["decided_by"] == "user" and decided[k]["reason"] == f"run closed: {reason}"
                   for k in answers))
        expect("unfinished tasks T2, T3, T4, T6 -> stopped with 'closed: <reason>'",
               sorted(result["tasks"]) == ["T2", "T3", "T4", "T6"]
               and all(task(old, t)["status"] == "stopped" and task(old, t)["last_error"] == f"closed: {reason}"
                       and task(old, t)["waiting_for"] is None for t in ("T2", "T3", "T4", "T6")))
        expect("done T1 untouched; T5 (stopped earlier) keeps its own reason",
               before[1][0] == snapshot(old)[1][0] and task(old, "T5")["last_error"] == "stopped by user: client cancelled")
        expect("ledger row of T4 unchanged; plan approval unchanged",
               snapshot(old)[3] == before[3]
               and [d for d in get_decisions(old["id"]) if d["kind"] == "plan_approval"][0]["answer"] == "yes")
        expect("run -> stopped, 'CLOSED by user: <reason>', lock free",
               after["status"] == "stopped" and after["stop_reason"] == f"CLOSED by user: {reason}"
               and after["locked_by"] is None and is_closed(after))
        closed = snapshot(old)
        expect("closing twice changes nothing",
               not close_run(customer, "2026-10", reason, "selftest-holder")["closed_now"]
               and cmd_close(customer, "2026-10", reason, confirm=lambda _: old["run_key"]) is None
               and snapshot(old) == closed)
        expect("a closed run never runs: run + --until-wait do nothing, no open decisions",
               cmd_run(customer, "2026-10")["started"] is None
               and cmd_run_until_wait(customer, "2026-10")["steps"] == 0
               and list_open_decisions(old["id"]) == [] and snapshot(old) == closed)
        show_status(get_run(old["run_key"]))

        # The new plan: same task ids T1..T3, loaded as a revision next to the old run
        new_orders = [{"work_order_id": f"WO-N{i}", "task_id": f"T{i}", "agent": "CAM", "output_type": "coordination",
                       "task_type": "coordination", "week": 1, "depends_on": [], "target": "test",
                       "instructions": ["new plan"], "done_when": f"T{i} done"} for i in (1, 2, 3)]
        new_proof = {**proof, "work_orders_count": 3}
        expect("next free revision is 2026-10-r2", next_revision(customer, "2026-10") == "2026-10-r2")
        expect("loading the new plan as 2026-10 is still refused (the closed run is never replaced)",
               raises(lambda: load_run(customer, "2026-10", new_orders, plan, {}, True, new_proof))
               and snapshot(old) == closed)
        expect("2026-10-r2 without proof refused, writes nothing",
               raises(lambda: load_run(customer, "2026-10-r2", new_orders, plan, {}, True, None, "STOPPED: no proof (test)"))
               and get_run(run_key_for(customer, "2026-10-r2")) is None)
        new, created = load_run(customer, "2026-10-r2", new_orders, plan, {}, True, new_proof)
        expect("2026-10-r2 is a separate run: own key, 3 pending tasks, own approval, running",
               created and new["id"] != old["id"] and new["run_key"] == f"{customer}-2026-10-r2"
               and [t["status"] for t in get_tasks(new["id"])] == ["pending"] * 3
               and approval_of(new["id"])["question"]["proof"] == new_proof and new["status"] == "running")
        expect("commands without a month use the new run; the old one is unchanged",
               find_run(customer, None)["id"] == new["id"] and snapshot(old) == closed)
        cmd_run(customer, "2026-10-r2")
        cmd_answer_task(customer, "T1", "done", "selftest: new plan")
        new_t1 = task(new, "T1")
        expect("'answer T1' without a month answers the NEW run's T1, never the old one",
               [d["answer"] for d in get_decisions(new["id"]) if d["task_row_id"] == new_t1["id"]] == ["done"]
               and snapshot(old) == closed)
        expect("the next free revision is now 2026-10-r3", next_revision(customer, "2026-10-r2") == "2026-10-r3")
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (close) OK" if ok else "ORCHESTRATOR SELFTEST (close) FAILED")
    return ok


def selftest_run_data() -> bool:
    """
    The REAL toolsets (LLM and paid parts faked) use the run's understanding + the task's own work order,
    never data/understanding.json / data/work_orders.json; the company judge uses the task's LEDGER payload,
    never data/quickicp_payload.json, also after a dry run and after a crash. No LLM, no paid call, no file written.
    """
    import email_tool
    import quickhowl
    import quickicp
    import runner_quickicp_accounts as accounts_module
    import validator
    from email_tool import CampaignContent, Objective, ReferenceEmail
    from quickicp import CompanyPayload
    from runner_calls import RealCallTools
    from runner_email import RealEmailTools
    from runner_quickicp_accounts import RealAccountsTools, SimulatedCrash
    from runner_quickicp_contacts import RealContactsTools
    from state_store import get_steps

    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator RUN DATA selftest (real toolsets, LLM + paid parts faked): customer '{customer}'")
    plan_files = {"data/understanding.json", "data/work_orders.json", "data/quickicp_payload.json"}
    changed = {"data/understanding.json": {"customer_name": "CHANGED Corp", "business_summary": "CHANGED"},
               "data/work_orders.json": [{"agent": "QuickICP", "output_type": "accounts", "tier": "CHANGED"},
                                         {"agent": "QuickICP", "output_type": "contacts", "instructions": ["CHANGED"]},
                                         {"agent": "Email Agent", "output_type": "emails", "target": "CHANGED"},
                                         {"agent": "Quickhowl", "output_type": "calls", "target": "CHANGED"}],
               "data/quickicp_payload.json": {"locations": ["Delhi, India"], "company_size": ["5001-10000"]}}
    run_understanding_ = {"customer_name": "Run Customer", "business_summary": "browser automation"}
    accounts_wo = {"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "accounts",
                   "task_type": "data_build", "week": 1, "depends_on": [], "target": "2 accounts", "tier": "P1-RUN",
                   "instructions": ["run"]}
    contacts_wo = {"task_id": "T2", "agent": "QuickICP", "output_type": "contacts", "instructions": ["RUN roles"],
                   "target": "6 contacts"}
    email_wo = {"task_id": "T3", "agent": "Email Agent", "output_type": "emails", "target": "RUN 3 emails"}
    calls_wo = {"task_id": "T4", "agent": "Quickhowl", "output_type": "calls", "target": "RUN 2 calls"}
    proof = {"approved_at": "2026-10-12T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 1, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    # ---- swaps (all restored in finally) ----
    reads, writes, contexts = [], [], []
    real = {"quickicp.load_json": quickicp.load_json, "validator.load_json": getattr(validator, "load_json", None),
            "email_tool.load_json": getattr(email_tool, "load_json", None),
            "quickhowl.load_json": getattr(quickhowl, "load_json", None),
            "quickicp.save_json": quickicp.save_json,
            "validator.load_map": validator.load_map, "validator.fill_map": validator.fill_map}
    patched_methods = {name: getattr(RealAccountsTools, name)
                       for name in ("build_payload", "existing_companies", "search", "save_companies", "save_log")}
    other = {"email_tool.write_content": email_tool.write_content, "email_tool.check_settings": email_tool.check_settings,
             "email_tool.test_inboxes": email_tool.test_inboxes, "quickhowl.check_settings": quickhowl.check_settings,
             "quickhowl.make_profile": quickhowl.make_profile, "quickhowl.assert_safe": quickhowl.assert_safe}
    saved_ref = os.environ.get("CLIENT_REFERENCE")

    def reader(module_load):
        def load(path, *args, **kwargs):
            if path in plan_files or module_load is None:      # a module without a reader must read nothing
                reads.append(path)
                return changed.get(path)
            return module_load(path, *args, **kwargs)
        return load

    def recorder(path, *args, **kwargs):
        if path in plan_files:
            writes.append(path)
        return real["quickicp.save_json"](path, *args, **kwargs)

    maps = {"industry": {"items": {"good": {"score": 90, "source": "llm", "reason": "fits"}}, "company_overrides": {}},
            "title": {"items": {"head procurement": {"function": "procurement", "function_relevant": True,
                                                       "source": "llm"}}, "company_overrides": {}}}
    payload_file = "data/quickicp_payload.json"
    file_before = open(payload_file, "rb").read() if os.path.exists(payload_file) else None

    def company(domain):
        return {"COMPANY": domain, "WEBSITE": domain, "LOCATION": "Surat, Gujarat, India", "INDUSTRY": "good",
                "HEADCOUNT": 500}

    built, searched, judge_crash = [], [], {"armed": False}
    payload_locations = ["Pune, India", "Surat Metropolitan Area, India"]    # 1st build: the dry run; 2nd: the real run

    def build_payload(self, work_order):
        built.append({"understanding": self.understanding, "work_order": work_order})
        return CompanyPayload(industries=["good"], locations=[payload_locations[min(len(built), 2) - 1]],
                              company_size=["201-500"], company_count=0, coveredpages=0)

    def search(self, payload):
        searched.append(payload.model_dump())
        return [company("s1.test"), company("s2.test")]

    real_judge = RealAccountsTools.judge

    def judge(self, companies):
        if judge_crash["armed"] and companies:
            judge_crash["armed"] = False
            raise SimulatedCrash("crash after the paid search, before the judge")
        return real_judge(self, companies)

    def toolset(ctx):
        return RealAccountsTools(ctx.run["run_key"], ctx.task["task_id"], run_understanding(ctx.run), ctx.task["work_order"])

    real_entry = WIRED_RUNNERS["quickicp_accounts"]
    ok = False
    try:
        for module, name in ((quickicp, "quickicp"), (validator, "validator"), (email_tool, "email_tool"),
                             (quickhowl, "quickhowl")):
            module.load_json = reader(real[f"{name}.load_json"])
        quickicp.save_json = recorder
        accounts_module.save_json = recorder
        validator.load_map = lambda kind: {**maps[kind], "items": dict(maps[kind]["items"])}
        validator.fill_map = lambda kind, data, labels, context: contexts.append((kind, context))
        RealAccountsTools.build_payload = build_payload
        RealAccountsTools.existing_companies = lambda self, c: []
        RealAccountsTools.search = search
        RealAccountsTools.save_companies = lambda self, cs, c: {d["WEBSITE"]: 700000 + i for i, d in enumerate(cs)}
        RealAccountsTools.save_log = lambda self, log: None
        RealAccountsTools.judge = judge
        WIRED_RUNNERS["quickicp_accounts"] = (run_quickicp_accounts, toolset)

        run, _ = load_run(customer, "2026-10", [accounts_wo], {"month": "Month 1", "revenue_analysis": {}},
                          run_understanding_, True, proof)
        t1 = get_tasks(run["id"])[0]

        # 1. A dry run first (payload Pune): records nothing, writes no file
        dry = invoke_runner(run, t1, None, dry=True)
        expect("D1 dry run: own payload (Pune), nothing in the ledger, no file written",
               dry.kind == "dry" and get_steps(t1["id"]) == [] and writes == [])

        # 2. The real run: payload Surat (ledger); crash after the paid search, before the judge
        judge_crash["armed"] = True
        crashed = False
        try:
            invoke_runner(run, t1, None)
        except SimulatedCrash:
            crashed = True
        steps = {s["step"]: s for s in get_steps(t1["id"])}
        expect("D2 real run: payload Surat saved in the ledger, the paid search done, then a crash before the judge",
               crashed and steps["payload"]["output"]["locations"] == ["Surat Metropolitan Area, India"]
               and steps["search"]["status"] == "done" and len(searched) == 1)

        # 3. Restart (a new toolset = a new process; the 'changed' file still says Delhi)
        result = invoke_runner(run, t1, None)
        judged = (([s for s in get_steps(t1["id"]) if s["step"] == "state"][-1]["output"] or {}).get("loop") or {}).get("all_judged", [])
        expect("D3 restart: the payload comes back from the LEDGER (no new build, no new search); "
               "the same Surat companies still pass",
               result.kind == "done" and result.achieved == 2 and len(built) == 2 and len(searched) == 1
               and [c["validation"]["status"] for c in judged] == ["pass", "pass"])
        expect("D4 the dry run (Pune) and the changed file (Delhi) never reached the judge",
               all("Outside target geography" not in c["validation"]["reason"] for c in judged))
        expect("D5 build_payload got the RUN's understanding and THIS task's work order",
               built[-1] == {"understanding": run_understanding_, "work_order": t1["work_order"]})
        industry_ctx = [c for kind, c in contexts if kind == "industry"]
        expect("D6 the industry judge's context: the run's customer + this task's tier (not 'CHANGED')",
               industry_ctx and all(c["customer"] == "Run Customer" and c["target_tier"] == "P1-RUN" for c in industry_ctx))
        tools = RealAccountsTools(run["run_key"], "T1", run_understanding_, accounts_wo)
        expect("D7 the judge without a ledger payload -> STOPPED (never the file)",
               raises(lambda: real_judge(tools, [company("x.test")])))

        # 4. Contacts: the title judge's context
        people = RealContactsTools(run["run_key"], "T2", run_understanding_, contacts_wo)
        people.company_keys = {"s1.test"}
        verdicts = people.judge([{"FULL NAME": "A", "JOB POSITION": "Head Procurement", "COMPANY URL": "s1.test"}])
        title_ctx = [c for kind, c in contexts if kind == "title"]
        expect("D8 contacts judge: the run's customer + THIS task's roles; the person passes",
               verdicts[0]["validation"]["status"] == "pass" and title_ctx[-1]["customer"] == "Run Customer"
               and title_ctx[-1]["target_roles_work_order"]["instructions"] == ["RUN roles"])

        # 5. Email: content from the run's understanding + this task's work order
        given = {}

        def write_content(ask=None, understanding=None, work_order=None):
            given.update(understanding=understanding, work_order=work_order)
            return CampaignContent(campaign_name="x", campaign_description="x",
                                   objective=Objective(objective="o", offer="o", cta="Demo?", target="t",
                                                       problem_solved="p", tone="t", must_mention=[], never_say=[]),
                                   emails=[ReferenceEmail(subject="s", preheader="p", body_html="<p>b</p>",
                                                          wait_days_before=0)])
        email_tool.write_content = write_content
        email_tool.check_settings = lambda *names: None
        email_tool.test_inboxes = lambda: ["team@test.example"]
        os.environ["CLIENT_REFERENCE"] = saved_ref or "selftest-reference"
        request = RealEmailTools(run_understanding_, email_wo).build_request(
            [{"id": 1, "customer": customer, "full_name": "Asha Rao", "email": "a@prospect.test", "company": {}}])
        expect("D9 email: write_content got the run's understanding + THIS task's work order; reference 'RUN-CUSTOMER'",
               given == {"understanding": run_understanding_, "work_order": email_wo}
               and "MOTM-RUN-CUSTOMER-HYPER-" in request["body"]["reference"])

        # 6. Calls: the profile from the run's understanding + this task's work order
        profiled = {}

        def make_profile(understanding, work_order):
            profiled.update(understanding=understanding, work_order=work_order)
            return {"profile": {"company_name": "Run Customer"}}
        quickhowl.make_profile, quickhowl.check_settings = make_profile, lambda *names: None
        quickhowl.assert_safe = lambda request_: None
        RealCallTools(run["run_key"], "T4", run_understanding_, calls_wo).build_request(
            [{"name": "Asha", "phone": "+919000000001", "company": "C"}])
        expect("D10 calls: make_profile got the run's understanding + THIS task's work order",
               profiled == {"understanding": run_understanding_, "work_order": calls_wo})

        expect("D11 not one read of data/understanding.json, data/work_orders.json or data/quickicp_payload.json "
               "on the run path; nothing written to them", reads == [] and writes == [])
        expect("D12 data/quickicp_payload.json is byte-for-byte unchanged",
               (open(payload_file, "rb").read() if os.path.exists(payload_file) else None) == file_before)
        expect("D13 a run without a saved understanding -> STOPPED (no fallback to the file)",
               raises(lambda: run_understanding({"run_key": "x", "understanding": None})))
        no_fallback = []
        for call in (lambda: validator.company_context(), lambda: validator.people_context(),
                     lambda: validator.judge_companies([]), lambda: other["email_tool.write_content"](),
                     lambda: quickhowl.build_request([])):
            try:
                call()
            except TypeError:
                no_fallback.append(True)
        expect("D14 no file fallback any more: the judges, email content and call request REQUIRE the run's inputs",
               len(no_fallback) == 5 and reads == [])
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        for module, name in ((quickicp, "quickicp"), (validator, "validator"), (email_tool, "email_tool"),
                             (quickhowl, "quickhowl")):
            if real[f"{name}.load_json"] is None:
                module.__dict__.pop("load_json", None)
            else:
                module.load_json = real[f"{name}.load_json"]
        quickicp.save_json = real["quickicp.save_json"]
        accounts_module.save_json = real["quickicp.save_json"]
        validator.load_map, validator.fill_map = real["validator.load_map"], real["validator.fill_map"]
        for name, method in patched_methods.items():
            setattr(RealAccountsTools, name, method)
        RealAccountsTools.judge = real_judge
        for key, value in other.items():
            module_name, attr = key.split(".")
            setattr({"email_tool": email_tool, "quickhowl": quickhowl}[module_name], attr, value)
        if saved_ref is None:
            os.environ.pop("CLIENT_REFERENCE", None)
        else:
            os.environ["CLIENT_REFERENCE"] = saved_ref
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (run data) OK" if ok else "ORCHESTRATOR SELFTEST (run data) FAILED")
    return ok


# ================= PERCENT TARGETS: a share of the base task's achieved number =================

def selftest_percent() -> bool:
    """'100% of T1 contacts' -> T1's achieved number (runner_kit.target_for + the review); no tool, no LLM."""
    from brain_review import task_numbers
    from runner_kit import percent_target, target_for
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator PERCENT selftest: customer '{customer}'")
    proof = {"approved_at": "2026-10-13T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 4, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "x", "week": 1, "depends_on": deps, "target": target, "instructions": ["test"]}
    orders = [wo("T1", "QuickICP", "contacts", [], "6 contacts"),
              wo("T2", "QuickICP", "contacts", ["T1"], "50% of T1 contacts"),
              wo("T3", "Email Agent", "emails", ["T1"], "Emails to 100% of validated contacts"),
              wo("T4", "Quickhowl", "calls", ["T1", "T2"], "Calls to 100% of contacts")]
    saved_override = os.environ.pop("TEST_TARGET_OVERRIDE", None)
    ok = False
    try:
        import quickicp
        run, _ = load_run(customer, "2026-10", orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        task = {t["task_id"]: t for t in get_tasks(run["id"])}
        expect("P1 a % is never stored as a count: '50% of T1 contacts' / '100% of validated contacts' -> no "
               "target_number; '6 contacts' -> 6",
               task["T1"]["target_number"] == 6 and task["T2"]["target_number"] is None
               and task["T3"]["target_number"] is None)
        expect("P2 the base task not done yet -> STOPPED (never 100)", raises(lambda: target_for(task["T2"], 40)))
        update_task(task["T1"]["id"], status="done", achieved_number=7)
        expect("P3 '50% of T1 contacts' with T1 done at 7 -> ceil(3.5) = 4 (named task); '100% of validated "
               "contacts' -> 7 (the only counted dependency)",
               percent_target(task["T2"]) == (4, "T1") and percent_target(task["T3"]) == (7, "T1")
               and target_for(task["T2"], 40) == 4)
        expect("P4 TEST_MODE still caps it: cap 3 -> 3", quickicp.TEST_MODE and target_for(task["T3"], 3) == 3)
        expect("P5 two counted dependencies and no task named -> STOPPED (not clear which)",
               raises(lambda: target_for(task["T4"], 40)))
        update_task(task["T3"]["id"], status="done", achieved_number=3, result={"target": 3})
        numbers = task_numbers(get_tasks(run["id"])[2])
        expect("P6 the review shows the resolved plan target: 7, '100% of T1' (not 100)",
               numbers["plan_target_number"] == 7 and numbers["plan_target_basis"] == "100% of T1"
               and numbers["pct_of_plan_target"] == 43)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        if saved_override is not None:
            os.environ["TEST_TARGET_OVERRIDE"] = saved_override
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (percent) OK" if ok else "ORCHESTRATOR SELFTEST (percent) FAILED")
    return ok


# ================= NETWORK DROP: one STOPPED line at every entry point =================

def selftest_network() -> bool:
    """
    run_cli with fake network errors; a network error inside a paid search still -> 'unknown' + paid_call_unknown;
    every entry point in a child process whose every service URL is https://selftest.invalid behind a closed local
    proxy port (nothing real can be reached). No LLM, no paid call.
    """
    import contextlib
    import io
    import subprocess
    import httpx
    import requests
    import quickicp
    import runner_kit
    import state_store
    from runner_quickicp_accounts import FakeAccountsTools
    from state_store import get_steps
    print("\nOrchestrator NETWORK selftest (fake drops; child processes with no reachable service)")
    root = os.path.dirname(os.path.abspath(__file__))
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    saved = (dict(state_store.STATE_WRITES), set(state_store.HELD_LOCKS), list(runner_kit.PAID_IN_FLIGHT), list(sys.argv))
    real_entry = WIRED_RUNNERS["quickicp_accounts"]

    def cli(raise_error, argv=("x",), writes=None, locks=(), paid=()):
        """Run run_cli(fn) like an entry point: (exit code or the exception it raised, printed text)."""
        state_store.STATE_WRITES.update(writes or {"done": 0, "last": None, "pending": None})
        state_store.HELD_LOCKS.clear()
        state_store.HELD_LOCKS.update(locks)
        runner_kit.PAID_IN_FLIGHT[:] = list(paid)
        sys.argv[:] = list(argv)
        out = io.StringIO()

        def fn():
            raise raise_error
        try:
            with contextlib.redirect_stdout(out):
                quickicp.run_cli(fn)
            return None, out.getvalue()
        except SystemExit as stop:
            return stop.code, out.getvalue()
        except BaseException as error:
            return error, out.getvalue()

    supabase_url = (os.getenv("SUPABASE_URL") or "https://x.supabase.co").rstrip("/")
    dns = httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=httpx.Request("GET", f"{supabase_url}/rest/v1/runs"))
    ok = False
    try:
        code, text = cli(dns)
        expect("N1 DNS failure (getaddrinfo) -> ONE line 'STOPPED: no network (Supabase, ...)', exit code 3, "
               "'Nothing was saved by this command.', no traceback",
               code == 3 and text.startswith("STOPPED: no network (Supabase, [Errno 11001] getaddrinfo failed). "
                                             "Nothing lost: check the internet, then rerun the same command.")
               and "Nothing was saved by this command." in text and "Traceback" not in text)
        refused = requests.exceptions.ConnectionError(
            "Connection refused", request=requests.Request("GET", f"{os.getenv('QUICKHOWL_URL') or 'https://q.example'}/x").prepare())
        timeout = httpx.ReadTimeout("timed out", request=httpx.Request("GET", f"{os.getenv('EMAIL_TOOL_URL') or 'https://e.example'}/x"))
        try:
            raise RuntimeError("postgrest wrapper") from ConnectionResetError(10054, "forcibly closed by the remote host")
        except RuntimeError as wrapped:
            reset = wrapped
        results = [cli(refused), cli(timeout), cli(reset)]
        expect("N2 refused (requests, Quickhowl), read timeout (httpx, email tool), a reset wrapped in another error "
               "-> each one STOPPED line naming the service, exit code 3",
               [r[0] for r in results] == [3, 3, 3]
               and "(Quickhowl, " in results[0][1] and "(email tool, " in results[1][1]
               and results[2][1].startswith("STOPPED: no network (a service, ") and "forcibly closed" in results[2][1])
        tool, value = cli(ToolError("STOPPED: a known problem")), cli(ValueError("a bug"))
        expect("N3 a ToolError and a non-network error are NOT swallowed (the entry point handles them as before)",
               isinstance(tool[0], ToolError) and isinstance(value[0], ValueError) and tool[1] == value[1] == "")
        debug = cli(dns, argv=("x", "status", "--debug"))
        expect("N4 --debug: the network error is raised (full traceback) and '--debug' is removed from the arguments",
               isinstance(debug[0], httpx.ConnectError) and sys.argv == ["x", "status"])
        sent = cli(dns, writes={"done": 2, "last": "brain_decisions update", "pending": "brain_tool_calls insert"},
                   locks={7}, paid=["anvixa-ai-product-2026-10-r6:T3:search:0"])[1]
        expect("N5 a write sent but unanswered -> 'may or may not have been saved' (never 'Nothing was saved'); "
               "what was saved; the lock hint; a paid call in progress -> 'unknown' + paid_call_unknown",
               "The last change (brain_tool_calls insert) may or may not have been saved" in sent
               and "Nothing was saved" not in sent
               and "Saved before the drop: 2 state change(s) (last: brain_decisions update)" in sent
               and "python orchestrator.py unlock <YYYY-MM>" in sent
               and "A paid call was in progress (anvixa-ai-product-2026-10-r6:T3:search:0)" in sent
               and "paid_call_unknown" in sent)

        # A network error INSIDE a paid search: the row stays 'running' -> next run 'unknown' + paid_call_unknown
        def drop():
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        proof = {"approved_at": "2026-10-13T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
                 "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
                 "work_orders_count": 1, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
                 "revenue_analysis_sha256": "z" * 64}
        order = {"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "accounts",
                 "task_type": "data_build", "week": 1, "depends_on": [], "target": "10 accounts", "instructions": ["t"]}
        run, _ = load_run(customer, "2026-10", [order], {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        runner_kit.PAID_IN_FLIGHT.clear()
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: FakeAccountsTools(on_search=drop))
        dropped = None
        try:
            cmd_run(customer, "2026-10")
        except httpx.ConnectError as error:
            dropped = error
        task = get_tasks(run["id"])[0]
        searches = [s for s in get_steps(task["id"]) if s["step"] == "search"]
        expect("N6 a network drop inside the PAID search: not caught there; its ledger row stays 'running'; noted "
               "as in flight; the run lock is released",
               dropped is not None and [s["status"] for s in searches] == ["running"]
               and runner_kit.PAID_IN_FLIGHT == [searches[0]["idempotency_key"]]
               and get_run(run["run_key"])["locked_by"] is None)
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: FakeAccountsTools())
        cmd_run(customer, "2026-10")
        searches = [s for s in get_steps(task["id"]) if s["step"] == "search"]
        unknown = [d for d in get_decisions(run["id"]) if d["kind"] == "paid_call_unknown"]
        expect("N7 the next run: that search -> 'unknown' + an open paid_call_unknown decision (exactly as after a crash); "
               "never repeated automatically",
               [s["status"] for s in searches] == ["unknown"] and len(unknown) == 1 and unknown[0]["status"] == "open")

        # Every entry point in a child process: nothing real can be reached
        child = {**os.environ, "PYTHONIOENCODING": "utf-8", "HTTPS_PROXY": "http://127.0.0.1:9",
                 "HTTP_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
                 # one .invalid host per service (never resolvable), so each failure names its own service
                 "SUPABASE_URL": "https://supabase.selftest.invalid", "QUICKHOWL_URL": "https://quickhowl.selftest.invalid",
                 "EMAIL_TOOL_URL": "https://email.selftest.invalid", "QUICKICP_WEBHOOK_URL": "https://n8n.selftest.invalid/a",
                 "QUICKICP_PEOPLE_WEBHOOK_URL": "https://n8n.selftest.invalid/b",
                 "QUICKICP_EMAIL_WEBHOOK_URL": "https://n8n.selftest.invalid/c",
                 "QUICKICP_PHONE_WEBHOOK_URL": "https://n8n.selftest.invalid/d",
                 "OPENAI_BASE_URL": "https://openai.selftest.invalid/v1", "OPENAI_API_BASE": "https://openai.selftest.invalid/v1",
                 "PROSPEO_URL": "https://prospeo.selftest.invalid/x"}

        def run_child(args, stdin=""):
            done = subprocess.run([sys.executable, *args], cwd=root, env=child, input=stdin, capture_output=True,
                                  text=True, timeout=240, encoding="utf-8")
            return done.returncode, done.stdout + done.stderr
        commands = [["orchestrator.py", "status"], ["orchestrator.py", "answer", "1", "done", "selftest"],
                    ["intake_adapter.py"], ["quickhowl.py", "status", "--run", "2026-10", "T1"],
                    ["email_tool.py", "samples", "--run", "2026-10", "T1"]]
        outcomes = {" ".join(c): run_child(c) for c in commands}

        def e2e_child(real_calls: bool):
            """e2e_test.py --resume, unchanged, with REAL_CALLS_ENABLED set for this child only (test-only override)."""
            wrapper = (f"import runpy, sys, quickhowl; quickhowl.REAL_CALLS_ENABLED = {real_calls}; "
                       "sys.argv = ['e2e_test.py', '--resume']; runpy.run_path('e2e_test.py', run_name='__main__')")
            return run_child(["-c", wrapper], stdin=f"{current_customer()}\n")

        outcomes["e2e_test.py --resume"] = e2e_child(False)
        bad = {cmd: out[-400:] for cmd, (code, out) in outcomes.items()
               if code != 3 or "STOPPED: no network (Supabase, " not in out or "Traceback" in out}
        if bad:
            print(f"  details: {bad}")
        expect(f"N8 every entry point, unreachable network: ONE 'STOPPED: no network (Supabase, ...)' line, exit 3, "
               f"no traceback ({', '.join(outcomes)}; e2e with REAL_CALLS_ENABLED off for that child)", bad == {})
        code, out = e2e_child(True)
        expect("N8b e2e_test.py with REAL_CALLS_ENABLED on -> its guard stops it first ('real calls are on', exit 1), "
               "before anything is read: no network line, no traceback",
               code == 1 and "STOPPED: real calls are on" in out and "no network" not in out and "Traceback" not in out)
        code, out = run_child(["orchestrator.py", "status", "--debug"])
        expect("N9 the same with --debug -> the full traceback (exit code not 3)", code not in (0, 3) and "Traceback" in out)
        code, out = run_child(["check_system.py"])
        expect("N10 check_system: each live check -> FAIL 'no network (<service>)' and the OTHER checks still run "
               "(the result line is printed); no whole-run STOPPED, no traceback",
               code == 1 and "no network (Supabase)" in out and "no network (OpenAI)" in out
               and "no network (Prospeo)" in out and "Result:" in out and "STOPPED: no network" not in out
               and "Traceback" not in out)
        source = open(os.path.join(root, "master_agent.py"), encoding="utf-8").read()
        expect("N11 master_agent.py (not run: it plans with the LLM) uses run_cli too, like the 8 others",
               all("    run_cli(cli" in open(os.path.join(root, f), encoding="utf-8").read()
                   for f in ("orchestrator.py", "e2e_test.py", "master_agent.py", "intake_adapter.py", "check_system.py",
                             "quickhowl.py", "email_tool.py", "validator.py", "supabase_store.py"))
               and source.count("run_cli(cli)") == 1)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        state_store.STATE_WRITES.update(saved[0])
        state_store.HELD_LOCKS.clear()
        state_store.HELD_LOCKS.update(saved[1])
        runner_kit.PAID_IN_FLIGHT[:] = saved[2]
        sys.argv[:] = saved[3]
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (network) OK" if ok else "ORCHESTRATOR SELFTEST (network) FAILED")
    return ok


# ================= MANUAL TOOLS: campaign from the ledger; pause never hits the wrong campaign =================

def selftest_manual_tools() -> bool:
    """quickhowl status/results/watch/pause and email_tool samples with --run: ledger only, fake API, no network."""
    import email_tool
    import quickhowl
    from state_store import finish_step, get_steps, start_step
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    tag = customer.split("-")[-1]                   # ledger keys are unique across ALL runs: one set per test run
    ref_a, ref_b, email_ref = f"motm-st{tag}-call-a", f"motm-st{tag}-call-b", f"MOTM-ST{tag}-HYPER-1"
    print(f"\nOrchestrator MANUAL TOOLS selftest (ledger lookups, fake Quickhowl / email tool): customer '{customer}'")
    proof = {"approved_at": "2026-10-13T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 3, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "outreach", "week": 1, "depends_on": [], "target": "3", "instructions": ["test"]}
    orders = [wo("T1", "Quickhowl", "calls"), wo("T2", "Email Agent", "emails"), wo("T3", "CAM", "coordination")]
    saved_key = os.environ.get("CUSTOMER_KEY")
    real_call_api, real_call_tool = quickhowl.call_api, email_tool.call_tool
    api, fake = [], {"status": "running", "reply_reference": None}

    def call_api(method, path, params=None, body=None, timeout=120):
        api.append((method, path, dict(params or {})))
        ref = path.split("/")[2]
        if method == "POST":
            return {"paused": True, "status": "paused", "reason": "selftest"}
        if path.endswith("/calls"):
            return {"items": [{"call_id": "c1", "contact": {"name": "Asha"}, "outcome": "interested", "finished": True}],
                    "has_more": False, "campaign_status": fake["status"]}
        return {"reference": fake["reply_reference"] or ref, "status": fake["status"],
                "contacts_still_to_call": 0 if fake["status"] == "completed" else 2,
                "contacts_total": 3, "calls_total": 1}

    tool_urls = []

    def call_tool(method, url, body=None):
        tool_urls.append((method, url))
        return 200, {"samples_status": "ready", "approval_status": "NONE",
                     "samples": [{"email_number": 1, "subject": "Hello", "body_html": "<p>Hi</p>"}]}

    def stored(task_row, ref, step="store", response=None):
        row, _ = start_step(task_row["id"], step, f"{ref}:{step}" if step == "store" else ref,
                            "quickhowl" if step == "store" else "email_tool", True, {"reference": ref, "auto_start": False})
        finish_step(row["id"], "done", response=response or {"client_reference": "selftest-client"})

    ok = False
    try:
        os.environ["CUSTOMER_KEY"] = customer
        quickhowl.call_api, email_tool.call_tool = call_api, call_tool
        run, _ = load_run(customer, "2026-10", orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        tasks = {t["task_id"]: t for t in get_tasks(run["id"])}
        load_run(customer, "2026-11", orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        stored(tasks["T1"], ref_a)
        stored(tasks["T2"], email_ref, step="launch",
               response={"campaign_id": "c-9", "workspace_id": "w-9", "campaign_status": "DRAFT"})

        def steps_now():
            return sum(len(get_steps(t["id"])) for t in tasks.values())
        before_steps = steps_now()

        expect("M1 --run is required; a missing task id or extra words -> usage, nothing done",
               raises(lambda: quickhowl.parse_manual_args(["pause"]))
               and raises(lambda: quickhowl.parse_manual_args(["pause", "--run", "2026-10"]))
               and raises(lambda: quickhowl.parse_manual_args(["pause", "--run", "2026-10", "T1", "now"]))
               and raises(lambda: quickhowl.parse_manual_args(["launch", "--run", "2026-10", "T1"]))
               and quickhowl.parse_manual_args(["status", "--run", "2026-10", "T1"]) == ("status", "2026-10", "T1", None)
               and api == [])
        campaign = quickhowl.ledger_campaign("2026-10", "T1")
        quickhowl.cmd_status(campaign)
        expect("M2 status: the reference comes from the task's ledger 'store' step (not data/call_campaign_state.json); "
               "one GET for it with its client_reference; nothing written to the ledger",
               campaign["reference"] == ref_a and campaign["client_reference"] == "selftest-client"
               and api == [("GET", f"/campaigns/{ref_a}", {"client_reference": "selftest-client"})]
               and steps_now() == before_steps)
        expect("M3 refused: an email task as a calls task, an unknown task, a calls task with no stored campaign",
               raises(lambda: quickhowl.ledger_campaign("2026-10", "T2"))
               and raises(lambda: quickhowl.ledger_campaign("2026-10", "T9"))
               and raises(lambda: quickhowl.ledger_campaign("2026-11", "T1")))
        stored(tasks["T1"], ref_b)
        message = ""
        try:
            quickhowl.ledger_campaign("2026-10", "T1")
        except ToolError as error:
            message = str(error)
        expect("M4 two campaigns in the task -> STOPPED listing both; --reference picks one exactly; an unknown one is refused",
               ref_a in message and ref_b in message
               and quickhowl.ledger_campaign("2026-10", "T1", ref_b)["reference"] == ref_b
               and raises(lambda: quickhowl.ledger_campaign("2026-10", "T1", "motm-other")))
        campaign = quickhowl.ledger_campaign("2026-10", "T1", ref_a)
        posts = lambda: [c for c in api if c[0] == "POST"]
        fake["reply_reference"] = "motm-somebody-else"
        expect("M5 pause: Quickhowl answers for ANOTHER campaign -> STOPPED, nothing POSTed",
               raises(lambda: quickhowl.cmd_pause(campaign, confirm=lambda _: ref_a)) and posts() == [])
        fake["reply_reference"] = None
        results = []
        for status in ("completed", "draft", "paused"):
            fake["status"] = status
            results.append(quickhowl.cmd_pause(campaign, confirm=lambda _: ref_a))
        expect("M6 pause: a finished, draft or already paused campaign -> nothing to pause, nothing POSTed",
               results == [None, None, None] and posts() == [])
        fake["status"] = "running"

        def eof(_):
            raise EOFError
        answers = [quickhowl.cmd_pause(campaign, confirm=lambda _: "yes"),
                   quickhowl.cmd_pause(campaign, confirm=lambda _: ref_b),
                   quickhowl.cmd_pause(campaign, confirm=lambda _: ""), quickhowl.cmd_pause(campaign, confirm=eof)]
        expect("M7 pause: 'yes', ANOTHER task reference, empty, end of input -> not paused, nothing POSTed",
               answers == [None, None, None, None] and posts() == [])
        reply = quickhowl.cmd_pause(campaign, confirm=lambda _: f" {ref_a} ")
        expect("M8 pause: the exact reference typed -> exactly ONE POST, for that campaign, with its client_reference",
               reply["paused"] and posts() == [("POST", f"/campaigns/{ref_a}/pause",
                                                 {"client_reference": "selftest-client"})])
        fake["status"] = "completed"
        outcome = quickhowl.cmd_results(campaign)
        slept = []
        quickhowl.cmd_watch(campaign, sleep=slept.append)
        expect("M9 results / watch: read-only (GETs only), finished campaign -> watch stops at once; ledger untouched",
               len(outcome["calls"]) == 1 and slept == [] and all(c[0] == "GET" for c in api if c not in posts())
               and steps_now() == before_steps + 1)       # +1 = the second store row added for M4
        email_campaign = email_tool.ledger_email_campaign("2026-10", "T2")
        email_tool.run_samples(email_campaign)
        expect("M10 email samples: campaign + workspace ids from the email task's 'launch' step; one GET; "
               "a calls task as an email task is refused",
               (email_campaign["campaign_id"], email_campaign["workspace_id"]) == ("c-9", "w-9")
               and len(tool_urls) == 1 and tool_urls[0][0] == "GET"
               and "/workspaces/w-9/" in tool_urls[0][1] and tool_urls[0][1].endswith("/campaigns/c-9/samples")
               and raises(lambda: email_tool.ledger_email_campaign("2026-10", "T1")))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        quickhowl.call_api, email_tool.call_tool = real_call_api, real_call_tool
        if saved_key is None:
            os.environ.pop("CUSTOMER_KEY", None)
        else:
            os.environ["CUSTOMER_KEY"] = saved_key
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (manual tools) OK" if ok else "ORCHESTRATOR SELFTEST (manual tools) FAILED")
    return ok


# ================= IMPORTS: the old flow is gone from the run path =================

MOVED_MODULES = ("pipeline", "goal_check", "email_campaign_payload", "company_loop_cli", "people_loop_cli")
MOVED_FUNCTIONS = ("run_loop", "run_people_loop",                                   # old loop CLIs
                   "cmd_preview", "cmd_launch", "cmd_start", "build_contacts", "calls_work_order",  # quickhowl
                   "run_launch", "run_approve", "email_work_order", "validated_people_links",      # email_tool
                   "run_companies", "run_people", "run_emails", "run_phones", "run_all",            # quickicp /
                   "run_enrichment", "load_domains", "build_people_payloads", "load_quickicp_work_order",  # validator
                   "passed_company_keys")
RUN_PATH_MODULES = ("orchestrator", "orchestrator_selftest", "e2e_test", "intake_adapter", "master_agent",
                    "brain_review", "runner_kit", "runner_quickicp_accounts", "runner_quickicp_contacts",
                    "runner_email", "runner_calls", "company_loop", "people_loop", "validator", "quickicp",
                    "email_tool", "quickhowl", "state_store", "supabase_store", "plan_approval")


def moved_references(path: str) -> list[str]:
    """Every import of a moved module, and every use of a moved function, in one file (AST: also inside functions)."""
    import ast
    found = []
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in MOVED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in MOVED_MODULES:
                found.append(f"from {node.module} import ...")
            found += [f"from {node.module} import {a.name}" for a in node.names if a.name in MOVED_FUNCTIONS]
        elif isinstance(node, ast.Name) and node.id in MOVED_FUNCTIONS:
            found.append(f"uses {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in MOVED_FUNCTIONS:
            found.append(f"uses .{node.attr}")
    return [f"{os.path.basename(path)}:{ref}" for ref in found]


def selftest_imports() -> bool:
    """archive/legacy/ is never imported by the run path (static scan + a fresh Python process); the kept tools exist."""
    import glob
    import subprocess
    print("\nOrchestrator IMPORTS selftest (archive/legacy is off the run path)")
    ok = False
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        files, skipped = [], []
        for path in sorted(glob.glob(os.path.join(root, "*.py"))):
            try:
                compile(open(path, encoding="utf-8").read(), path, "exec")
                files.append(path)
            except SyntaxError:
                skipped.append(os.path.basename(path))     # not valid Python: nothing can import it either
        if skipped:
            print(f"  note: skipped (not valid Python, cannot be imported): {', '.join(skipped)}")
        refs = [ref for path in files for ref in moved_references(path)]
        expect(f"static scan of {len(files)} .py files (also imports inside functions): nothing imports "
               f"{', '.join(MOVED_MODULES)} or uses run_loop / run_people_loop", refs == [])
        expect("the moved files are in archive/legacy/ and no longer in the root folder",
               all(os.path.exists(os.path.join(root, "archive", "legacy", f"{m}.py")) for m in MOVED_MODULES)
               and not any(os.path.exists(os.path.join(root, f"{m}.py")) for m in MOVED_MODULES))
        code = ("import sys\n" + "".join(f"import {m}\n" for m in RUN_PATH_MODULES)
                + f"print('LOADED', sorted(m for m in {MOVED_MODULES!r} if m in sys.modules))\n"
                + "import company_loop, people_loop\n"
                + "print('FUNCTIONS', [f for f in ('run_loop', 'save_results') if hasattr(company_loop, f)]"
                  " + [f for f in ('run_people_loop', 'company_queue', 'save_results') if hasattr(people_loop, f)])\n")
        done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=180,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        lines = done.stdout.splitlines()
        expect(f"a fresh Python process imports all {len(RUN_PATH_MODULES)} run-path modules; none of the moved "
               "modules gets loaded; the loop modules no longer have the CLI functions",
               done.returncode == 0 and "LOADED []" in lines and "FUNCTIONS []" in lines)
        if done.returncode != 0:
            print(done.stderr[-1500:])
        import email_tool
        import quickhowl
        import validator
        import quickicp
        import supabase_store
        source = {name: open(os.path.join(root, f"{name}.py"), encoding="utf-8").read()
                  for name in ("quickicp", "supabase_store")}
        expect("the paid / send CLI modes are gone: quickhowl = status/results/watch/pause, email_tool = samples, "
               "validator = maps/reset-maps/selftest, quickicp has no CLI, supabase_store has no 'save'",
               set(quickhowl.COMMANDS) == {"status", "results", "watch", "pause"} and set(email_tool.MODES) == {"samples"}
               and set(validator.MODES) == {"maps", "reset-maps", "selftest"} and not hasattr(quickicp, "MODES")
               and 'if __name__ == "__main__"' not in source["quickicp"]
               and 'command == "save"' not in source["supabase_store"]
               and not any(hasattr(module, name) for module in (quickhowl, email_tool, quickicp, validator, supabase_store)
                           for name in MOVED_FUNCTIONS + ("customer_name", "STATE_FILE", "COMPANIES_FILE")))
        expect("their code is in archive/legacy/ (quickhowl_cli, email_tool_cli, quickicp_cli, validator_cli, "
               "supabase_store_cli)",
               all(os.path.exists(os.path.join(root, "archive", "legacy", f"{m}_cli.py"))
                   for m in ("quickhowl", "email_tool", "quickicp", "validator", "supabase_store")))
        expect("kept manual tools still there: quickhowl pause/status/results/watch, email_tool samples, "
               "validator maps/reset-maps, extract_html.py",
               {"pause", "status", "results", "watch"} <= set(quickhowl.COMMANDS) and "samples" in email_tool.MODES
               and {"maps", "reset-maps"} <= set(validator.MODES) and os.path.exists(os.path.join(root, "extract_html.py")))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    print("ORCHESTRATOR SELFTEST (imports) OK" if ok else "ORCHESTRATOR SELFTEST (imports) FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
