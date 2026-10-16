"""
orch_tests/part_load_run.py
===========================

Parts 'load' and 'run' (human tasks).
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from datetime import datetime, timezone
from orchestrator import *
from orch_tests.common import expect, fake_work_orders, raises, run_work_orders, sweep_selftest_runs


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
