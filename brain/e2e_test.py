"""
e2e_test.py
===========

One command for a REAL end-to-end run in TEST MODE: platform plan -> month plan -> new run -> every task
-> brain review -> a short summary. It only calls the existing steps (intake_adapter, master_agent,
orchestrator); it adds no tool logic of its own.

WHY
---
Testing the whole chain by hand means ~20 commands and many answers. This runs them in order, answers
only what is safe to answer automatically, and asks a person for everything else.

FLOW
----
    guards      TEST_MODE must be on, REAL_CALLS_ENABLED off; type the CUSTOMER_KEY to start
    1. intake   intake_adapter: the platform's latest READY plan -> data/intake_document.txt (Supabase reads)
    2. plan     master_agent --intake: the LLM plans the month, YOU approve (yes / override / changes),
                proof saved. No valid fresh proof -> stop (nothing closed)
    3. close    this customer's latest RUNNING run is closed ("replaced by E2E test plan"); you type its key
    4. load     the next free revision of the month of this customer's LATEST run, whatever its status
                (e.g. 2026-10-r5 after a stopped r4), or of --month. Worked out BEFORE step 1, so a missing
                month stops before any LLM call. No run at all and no --month -> STOPPED.
                The loaded run must say test_mode = true.
    5. loop     run --until-wait, then act on why it stopped:
                  human task / gate decision      -> answered 'done', note 'E2E TEST', decided_by 'e2e-test'
                  next task would buy QuickICP    -> the DRY run is shown, then YOU type yes/no; yes = one
                                                     plain run step; no = stop (the run stays as it is)
                  email_approve / call_start      -> the full question is shown, YOU answer yes/no + note
                  goal_check, goal_check_final,   -> YOU type one of the allowed answers + a note
                  paid_call_unknown, task_failed,    ('quit' stops the test; the decision stays open)
                  review actions
                  a tool still running (calls)    -> run again (each run polls up to 10 minutes)
                  run done / stopped / nothing left / MAX_ROUNDS / MAX_HOURS -> loop ends
    6. review   ALWAYS runs at the end (LLM calls), then a short summary: tasks by status, accounts and
                contacts reused / bought, email recipients + status, call outcomes, stopped tasks

    --resume skips steps 1-4 and continues the loop on this customer's latest RUNNING run
    (e.g. after a crash, or after you said 'no' to a purchase): planning is never paid twice.

WHO DECIDES WHAT
----------------
    Automatic (decided_by 'e2e-test'): ONLY human tasks and gates (CAM, training, tele-calling, ...).
    You: plan approval, closing the old run, every QuickICP purchase, email_approve, call_start, and
    every other tool decision. Never automatic.

READS / WRITES
--------------
    Through the existing steps only: the platform tables (read), data/ plan files (master_agent),
    brain_* rows (orchestrator), the tools (QuickICP / email tool / Quickhowl, in TEST MODE).

RUN
---
    python e2e_test.py selftest                      fake tools, fake LLM, selftest-* rows: no network
    python e2e_test.py [--month 2026-10]             REAL test-mode run: LLM calls; QuickICP only after your yes
    python e2e_test.py --resume                      continue the latest running run

DEBUGGING
---------
    "STOPPED: TEST_MODE is off"            -> this command only runs in test mode (quickicp.TEST_MODE)
    "STOPPED: real calls are on"           -> REAL_CALLS_ENABLED must stay False for an E2E test
    "STOPPED: the plan was not approved"   -> master_agent ended without a valid fresh proof; nothing closed
    "STOPPED: not closed"                  -> the typed run key did not match; nothing loaded
    "STOPPED: you said no to buying T3"    -> run again with --resume when you want to buy
    Where it stands: python orchestrator.py status
"""

import sys
import time
from datetime import datetime, timezone

from quickicp import ToolError
from state_store import (ALLOWED_ANSWERS, answer_decision, current_customer, get_decisions, get_run, get_runs,
                         get_tasks, list_open_decisions)

INTAKE_DOC = "data/intake_document.txt"
CLOSE_REASON = "replaced by E2E test plan"
AUTO_NOTE = "E2E TEST"
AUTO_BY = "e2e-test"
MAX_ROUNDS = 60
MAX_HOURS = 3
ASK_HUMAN_KINDS = {"email_approve", "call_start"}   # shown in full, always answered by YOU


# ================= THE REAL STEPS (the selftest swaps them for fakes) =================

class Steps:
    """Every outside step e2e_test calls. The selftest replaces them; the real ones are below."""

    def test_mode(self) -> bool:
        import quickicp
        return bool(quickicp.TEST_MODE)

    def real_calls(self) -> bool:
        import quickhowl
        return bool(quickhowl.REAL_CALLS_ENABLED)

    def ask(self, prompt: str) -> str:
        return input(prompt)

    def intake(self) -> None:
        import intake_adapter
        intake_adapter.main()

    def plan(self) -> dict:
        """master_agent --intake (YOU approve), then the fresh proof. No valid proof -> ToolError."""
        import master_agent
        from plan_approval import read_proof
        master_agent.main(INTAKE_DOC)
        return read_proof()

    def load(self, customer: str, month: str) -> dict:
        from orchestrator import cmd_load, run_key_for
        cmd_load(month)
        return get_run(run_key_for(customer, month))

    def dry(self, customer: str, month: str, task_id: str) -> None:
        from orchestrator import cmd_dry
        cmd_dry(customer, month, task_id)

    def review_ask(self):
        """The LLM for the review: None = the real one."""
        return None


# ================= HELPERS =================

def say(text: str) -> None:
    print(f"\n[E2E] {text}")


def latest_running(customer: str) -> dict | None:
    """This customer's newest run with status 'running', or None."""
    running = [r for r in get_runs(customer) if r["status"] == "running"]
    return running[-1] if running else None


def latest_run(customer: str) -> dict | None:
    """This customer's newest run of ANY status (running, stopped or done), or None."""
    runs = get_runs(customer)
    return runs[-1] if runs else None


def target_month(customer: str, latest: dict | None, month: str | None) -> str:
    """
    The month to load: --month if given, else the month of the customer's LATEST run (any status);
    the next free revision when that month is taken (2026-10 -> 2026-10-r5).
    """
    from orchestrator import check_month, month_of, next_revision, run_key_for
    base = month or (month_of(latest) if latest else None)
    if not base:
        raise ToolError("STOPPED: no earlier run for this customer: give the month, e.g. --month 2026-10")
    base = check_month(base).split("-r")[0]
    return base if not get_run(run_key_for(customer, base)) else next_revision(customer, base)


def ask_allowed(steps: Steps, decision: dict, task_id: str) -> tuple[str, str] | None:
    """Ask YOU for an allowed answer + a note. None = you typed 'quit'."""
    from orchestrator import print_question
    # The question's own answers when it narrows them (e.g. no 'feedback' when no rounds are left).
    allowed = sorted((decision["question"] or {}).get("allowed_answers") or ALLOWED_ANSWERS[decision["kind"]])
    print(f"\n#{decision['id']} {task_id} {decision['kind']}")
    print_question(decision)
    while True:
        answer = steps.ask(f"{decision['kind']} #{decision['id']} ({task_id}): answer {' / '.join(allowed)}, "
                           "or 'quit': ").strip().lower()
        if answer == "quit":
            return None
        if answer in allowed:
            break
        print(f"  Not allowed. Type one of: {', '.join(allowed)}")
    note = ""
    while not note:
        note = steps.ask(f"Note for #{decision['id']} (required): ").strip()
    return answer, note


def handle_decisions(steps: Steps, customer: str, run: dict) -> tuple[int, bool]:
    """Answer the run's open decisions. Returns (answered, quit). Only human tasks/gates are automatic."""
    from orchestrator import HUMAN_KINDS, cmd_answer
    tasks = {t["id"]: t["task_id"] for t in get_tasks(run["id"])}
    answered = 0
    for decision in list_open_decisions(run["id"]):
        task_id = tasks.get(decision["task_row_id"], "run")
        if decision["kind"] in HUMAN_KINDS:
            answer_decision(decision["id"], "done", decided_by=AUTO_BY, reason=AUTO_NOTE)
            print(f"  {task_id} {decision['kind']} #{decision['id']} -> done ('{AUTO_NOTE}', by {AUTO_BY})")
            answered += 1
            continue
        reply = ask_allowed(steps, decision, task_id)
        if reply is None:
            return answered, True
        cmd_answer(customer, decision["id"], reply[0], reply[1])
        answered += 1
    return answered, False


def buy_step(steps: Steps, customer: str, month: str, task_id: str) -> bool:
    """A new QuickICP task: the dry run first, then YOU decide. True = bought (one plain run step)."""
    from orchestrator import cmd_run
    say(f"{task_id} would buy QuickICP data. Dry run first (nothing bought, nothing recorded):")
    steps.dry(customer, month, task_id)
    while True:
        answer = steps.ask(f"Buy data for {task_id} now? Type yes or no: ").strip().lower()
        if answer in ("yes", "no"):
            break
    if answer == "no":
        return False
    cmd_run(customer, month)
    return True


def summary_lines(run: dict) -> list[str]:
    """The short end summary, from the saved task results (code, no LLM)."""
    from brain_review import TOOL_TASKS, task_numbers
    tasks = get_tasks(run["id"])
    counts = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    lines = [f"Run {run['run_key']} | status {run['status']} | tasks: "
             + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))]
    for t in tasks:
        if (t["agent"], t["output_type"]) not in TOOL_TASKS or t["status"] != "done":
            continue
        n = task_numbers(t)
        if t["output_type"] == "accounts":
            lines.append(f"{t['task_id']} accounts: {n['achieved']} of {n['effective_target']} "
                         f"(reused {n.get('reused')}, bought {n.get('bought')})")
        elif t["output_type"] == "contacts":
            lines.append(f"{t['task_id']} contacts: {n['achieved']} of {n['effective_target']} (reused {n.get('reused')}, "
                         f"bought {n.get('bought')}, with email {n.get('with_email')}, with phone {n.get('with_phone')})")
        elif t["output_type"] == "emails":
            lines.append(f"{t['task_id']} email: {n.get('recipients')} recipient(s), campaign {n.get('campaign_status')}")
        elif t["output_type"] == "calls":
            lines.append(f"{t['task_id']} calls: {n.get('calls_completed')} completed, outcomes {n.get('outcome_counts')}")
    lines += [f"{t['task_id']} STOPPED: {t['last_error']}" for t in tasks if t["status"] == "stopped"]
    return lines


# ================= THE E2E RUN =================

def run_e2e(steps: Steps, customer: str, month: str | None = None, resume: bool = False) -> dict:
    """The whole test. Returns {"run_key", "ended_because", "summary", "review"} (for the selftest)."""
    from orchestrator import cmd_close, cmd_review, cmd_run_until_wait, month_of

    # Guards: before ANYTHING is read, planned or written.
    if not steps.test_mode():
        raise ToolError("STOPPED: TEST_MODE is off. e2e_test.py only runs in test mode.")
    if steps.real_calls():
        raise ToolError("STOPPED: real calls are on (REAL_CALLS_ENABLED). An E2E test never makes real calls.")
    say(f"E2E TEST for '{customer}' ({'resume' if resume else 'intake -> plan -> close -> load'} -> run -> review). "
        "Costs LLM calls; QuickICP data only after your yes.")
    if steps.ask(f"Type the customer key to start ({customer}): ").strip() != customer:
        raise ToolError("STOPPED: not started (the customer key did not match).")

    if resume:
        run = latest_running(customer)
        if not run:
            raise ToolError(f"STOPPED: no running run for '{customer}' to resume.")
    else:
        # Decided BEFORE any LLM call: a missing month stops here, not after paying for a plan.
        new_month = target_month(customer, latest_run(customer), month)
        say("1. intake: the platform plan (Supabase reads only)")
        steps.intake()
        say("2. plan: master_agent --intake (you approve)")
        try:
            steps.plan()
        except ToolError as error:
            raise ToolError(f"STOPPED: the plan was not approved ({error}). Nothing was closed or loaded.") from error
        old = latest_running(customer)         # only a RUNNING run is closed
        if old:
            say(f"3. close: {old['run_key']} (type its key)")
            if cmd_close(customer, month_of(old), CLOSE_REASON, confirm=steps.ask) is None:
                raise ToolError(f"STOPPED: not closed ({old['run_key']}). Nothing was loaded.")
        else:
            say("3. close: no running run to close")
        say(f"4. load: {new_month}")
        run = steps.load(customer, new_month)
    if not run["test_mode"]:
        raise ToolError(f"STOPPED: run {run['run_key']} is not a test-mode run.")
    month = month_of(run)

    say(f"5. run {run['run_key']} until done")
    deadline = time.monotonic() + MAX_HOURS * 3600
    ended = f"safety cap ({MAX_ROUNDS} rounds)"
    for _ in range(MAX_ROUNDS):
        if time.monotonic() > deadline:
            ended = f"safety cap ({MAX_HOURS} hours)"
            break
        result = cmd_run_until_wait(customer, month)
        run = get_run(run["run_key"])
        if run["status"] in ("done", "stopped"):
            ended = f"run {run['status']}"
            break
        answered, quit_ = handle_decisions(steps, customer, run)
        if quit_:
            ended = "you typed quit (the decision stays open; continue with --resume)"
            break
        if answered:
            continue
        blocked = (result.get("last") or {}).get("blocked_paid")
        if blocked:
            if not buy_step(steps, customer, month, blocked):
                ended = f"you said no to buying {blocked} (continue with --resume)"
                break
            continue
        if "still running" in (result.get("stopped_because") or ""):
            continue
        ended = f"nothing left to do now ({result.get('stopped_because')})"
        break

    say(f"loop ended: {ended}")
    review = None
    if not ended.startswith("you"):
        say("6. brain review (always)")
        review = cmd_review(customer, month, ask=steps.review_ask())
    run = get_run(run["run_key"])
    lines = summary_lines(run)
    say("SUMMARY")
    for line in lines:
        print(f"  {line}")
    from quickicp import usage_summary
    print(f"  {usage_summary()}   (every call: data/llm_usage.jsonl)")
    return {"run_key": run["run_key"], "ended_because": ended, "summary": lines, "review": review}


# ================= SELFTEST (fake tools, fake LLM, selftest-* rows) =================

def selftest() -> bool:
    """The whole E2E flow with fake steps and fake toolsets; scripted answers. No network, no LLM."""
    from brain_review import CallLabels, ReviewText
    from orchestrator import WIRED_RUNNERS, load_run
    from orchestrator_selftest import expect, network_guard, raises, sweep_selftest_runs
    from runner_calls import FakeCallTools
    from runner_email import FakeEmailTools
    from runner_quickicp_accounts import FakeAccountsTools
    from runner_quickicp_contacts import FakeContactsTools

    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"E2E selftest: customer '{customer}' (fake steps, fake tools, no network, no LLM)")
    real_entries = dict(WIRED_RUNNERS)
    proof = {"approved_at": "2026-10-12T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 7, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output, types, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": types, "week": 1, "depends_on": deps, "target": target, "instructions": ["test"],
                "done_when": f"{task_id} done"}
    orders = [wo("T1", "CAM", "coordination", "requirements", [], "kick-off"),
              wo("T2", "Content & Training", "data_approval", "data_approval", ["T1"], "data approved"),
              wo("T3", "QuickICP", "accounts", "data_build", ["T2"], "10 accounts"),
              wo("T4", "QuickICP", "contacts", "data_build", ["T3"], "6 contacts"),
              wo("T5", "Email Agent", "emails", "outreach", ["T4"], "emails to contacts"),
              wo("T6", "Quickhowl", "calls", "outreach", ["T4"], "calls to contacts"),
              wo("T7", "CAM", "review", "review", ["T5", "T6"], "review")]

    # Fake tools, chained by id like the real ones: accounts save 900000+, contacts save 800000+.
    companies = [{"id": 900000 + i, "customer": customer, "website": f"new{i + 1}.test", "linkedin": None,
                  "company_name": f"C{i}", "industry": "good"} for i in range(60)]
    people = [{"id": 800000 + i, "customer": customer, "full_name": f"Person {i}", "email": f"p{i}@prospect.test",
               "phone_number": f"+9198113005{i:02d}", "company": {"company_name": f"C{i}"}} for i in range(60)]
    fakes = {"accounts": FakeAccountsTools(fail_search=1),       # the 1st search fails -> task_failed (asked)
             "contacts": FakeContactsTools(companies, scores={c["id"]: 90 for c in companies}),
             "email": FakeEmailTools(people), "calls": FakeCallTools(people)}
    WIRED_RUNNERS["quickicp_accounts"] = (real_entries["quickicp_accounts"][0], lambda ctx: fakes["accounts"])
    WIRED_RUNNERS["quickicp_contacts"] = (real_entries["quickicp_contacts"][0], lambda ctx: fakes["contacts"])
    WIRED_RUNNERS["email"] = (real_entries["email"][0], lambda ctx: fakes["email"])
    WIRED_RUNNERS["calls"] = (real_entries["calls"][0], lambda ctx: fakes["calls"])

    class FakeSteps(Steps):
        """Scripted answers by prompt; every step logged in order."""

        def __init__(self, answers: dict, test_mode=True, real_calls=False, approve=True):
            self.answers, self._test, self._real, self.approve = answers, test_mode, real_calls, approve
            self.log, self.llm_calls = [], 0

        def test_mode(self):
            return self._test

        def real_calls(self):
            return self._real

        def ask(self, prompt):
            for key, value in self.answers.items():
                if key in prompt:
                    self.log.append(f"ask:{key}")
                    return value(prompt) if callable(value) else value
            raise AssertionError(f"unexpected question: {prompt}")

        def intake(self):
            self.log.append("intake")

        def plan(self):
            self.log.append("plan")
            if not self.approve:
                raise ToolError("STOPPED: no approval proof (test)")
            return proof

        def load(self, customer_, month_):
            self.log.append(f"load:{month_}")
            return load_run(customer_, month_, orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)[0]

        def dry(self, customer_, month_, task_id):
            self.log.append(f"dry:{task_id}")
            super().dry(customer_, month_, task_id)

        def review_ask(self):
            def ask(schema, prompt, name):
                self.llm_calls += 1
                if schema is CallLabels:
                    return CallLabels(labels=[])
                return ReviewText(what_worked=["Calls were completed"], whats_short=["Opens are not available yet"],
                                  next_actions=[])
            return ask

    def task(run, task_id):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == task_id][0]

    base = {"customer key": customer, "Note for": "selftest note", "email_approve": "yes", "call_start": "yes",
            "task_failed": "retry"}
    ok = False
    try:
        with network_guard() as blocked:
            # Guards: nothing read, planned or written
            off = FakeSteps(dict(base), test_mode=False)
            expect("TEST_MODE off -> STOPPED before anything (no question, no intake)",
                   raises(lambda: run_e2e(off, customer, "2026-10")) and off.log == [])
            real = FakeSteps(dict(base), real_calls=True)
            expect("real calls on -> STOPPED before anything", raises(lambda: run_e2e(real, customer, "2026-10"))
                   and real.log == [])
            wrong = FakeSteps({**base, "customer key": "someone-else"})
            expect("wrong customer key typed -> STOPPED, no intake", raises(lambda: run_e2e(wrong, customer, "2026-10"))
                   and wrong.log == ["ask:customer key"])

            none_yet = FakeSteps(dict(base))
            expect("no run at all and no --month -> STOPPED BEFORE intake and plan (no LLM spent)",
                   raises(lambda: run_e2e(none_yet, customer)) and none_yet.log == ["ask:customer key"])

            # The old run (running, with an open human task) that the new plan replaces
            old = load_run(customer, "2026-10", [wo("T1", "CAM", "coordination", "requirements", [], "x")],
                           {"month": "Month 1", "revenue_analysis": {}}, {}, True, {**proof, "work_orders_count": 1})[0]
            from orchestrator import cmd_run
            cmd_run(customer, "2026-10")
            not_ok = FakeSteps(dict(base), approve=False)
            expect("plan not approved -> STOPPED; the old run untouched (still running, its decision open)",
                   raises(lambda: run_e2e(not_ok, customer)) and not_ok.log == ["ask:customer key", "intake", "plan"]
                   and get_run(old["run_key"])["status"] == "running" and len(list_open_decisions(old["id"])) == 1)

            # Full run, but 'no' to buying T3
            no_buy = FakeSteps({**base, "run key": old["run_key"], "Buy data": "no"})
            out = run_e2e(no_buy, customer)
            new = get_run(f"{customer}-2026-10-r2")
            expect("order: intake -> plan -> close (run key typed) -> load 2026-10-r2",
                   no_buy.log[:5] == ["ask:customer key", "intake", "plan", "ask:run key", "load:2026-10-r2"])
            expect("the old run is CLOSED (stopped, 'CLOSED by user: replaced by E2E test plan')",
                   get_run(old["run_key"])["status"] == "stopped"
                   and get_run(old["run_key"])["stop_reason"] == f"CLOSED by user: {CLOSE_REASON}")
            auto = [d for d in get_decisions(new["id"]) if d["kind"] in ("human_task", "human_gate_task")]
            expect("T1 human task + T2 gate answered automatically: done, 'E2E TEST', decided_by 'e2e-test'",
                   {d["task_row_id"] for d in auto} == {task(new, "T1")["id"], task(new, "T2")["id"]}
                   and all(d["answer"] == "done" and d["reason"] == AUTO_NOTE and d["decided_by"] == AUTO_BY for d in auto))
            expect("T3: the DRY run is shown BEFORE the buy question; 'no' -> stops, nothing bought, T3 pending",
                   no_buy.log.index("dry:T3") < no_buy.log.index("ask:Buy data")
                   and fakes["accounts"].calls["search"] == 0 and task(new, "T3")["status"] == "pending"
                   and out["ended_because"].startswith("you said no to buying T3") and out["review"] is None)

            # --resume: no intake / plan / close; buys after 'yes'; asks every tool decision
            yes = FakeSteps({**base, "Buy data": "yes"})
            out = run_e2e(yes, customer, resume=True)
            run = get_run(new["run_key"])
            expect("--resume: no intake, plan, close or load", not {"intake", "plan", "ask:run key"} & set(yes.log)
                   and not any(e.startswith("load:") for e in yes.log))
            buys = [i for i, entry in enumerate(yes.log) if entry == "ask:Buy data"]
            expect("T3 and T4: each buy question comes right after its own dry run (dry T3, buy, dry T4, buy)",
                   len(buys) == 2 and yes.log.index("dry:T3") < buys[0] < yes.log.index("dry:T4") < buys[1])
            expect("task_failed (1st search failed) was ASKED, never automatic: answered 'retry' by user",
                   "ask:task_failed" in yes.log
                   and [d["decided_by"] for d in get_decisions(run["id"]) if d["kind"] == "task_failed"] == ["user"])
            outreach = {d["kind"]: d for d in get_decisions(run["id"]) if d["kind"] in ASK_HUMAN_KINDS}
            expect("email_approve and call_start were ASKED and answered 'yes' by user (never e2e-test)",
                   "ask:email_approve" in yes.log and "ask:call_start" in yes.log
                   and all(d["answer"] == "yes" and d["decided_by"] == "user" for d in outreach.values())
                   and set(outreach) == ASK_HUMAN_KINDS)
            expect("no automatic answer on anything but human tasks / gates",
                   {d["kind"] for d in get_decisions(run["id"]) if d["decided_by"] == AUTO_BY}
                   <= {"human_task", "human_gate_task"})
            expect("run DONE: every task done, the review ran (fake LLM), lock free",
                   run["status"] == "done" and out["ended_because"] == "run done" and out["review"] is not None
                   and yes.llm_calls >= 1 and run["locked_by"] is None)
            text = "\n".join(out["summary"])
            expect("summary: tasks, accounts + contacts reused/bought, email status, call outcomes",
                   "tasks: done 7" in text and "T3 accounts:" in text and "bought" in text
                   and "T4 contacts:" in text and "with email" in text and "T5 email:" in text
                   and "T6 calls:" in text and "outcomes" in text)

            # The r5 bug: the latest run is DONE (r2) and the old one stopped; nothing is running
            again = FakeSteps({**base, "Buy data": "no"})
            run_e2e(again, customer)
            expect("latest run done / stopped, nothing running -> loads 2026-10-r3 from the latest run's month; "
                   "closes nothing (no run key asked)",
                   "load:2026-10-r3" in again.log and "ask:run key" not in again.log
                   and get_run(new["run_key"])["status"] == "done")
        print(f"\n  Network guard: {len(blocked)} call(s) outside Supabase attempted (blocked): {blocked}")
        ok = not blocked
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS.update(real_entries)
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("E2E SELFTEST OK" if ok else "E2E SELFTEST FAILED")
    return ok


if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        args = sys.argv[1:]
        try:
            if args == ["selftest"]:
                sys.exit(0 if selftest() else 1)
            month_arg = args[args.index("--month") + 1] if "--month" in args else None
            if set(args) - {"--month", "--resume", month_arg}:
                print("Usage: python e2e_test.py selftest | python e2e_test.py [--month 2026-10] [--resume]")
                sys.exit(1)
            run_e2e(Steps(), current_customer(), month_arg, resume="--resume" in args)
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli, rerun="rerun the same command (once step 4 'load' is done: python e2e_test.py --resume)")
