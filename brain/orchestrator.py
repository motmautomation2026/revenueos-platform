"""
orchestrator.py
===============

The brain's run controller. It loads the approved plan into a run, shows where the run stands,
records human answers, and moves the run forward ONE task per 'run' call, under a run lock.
Human tasks wait for a person's answer. Tool tasks run through a runner (WIRED_RUNNERS):
wired: quickicp_accounts (runner_quickicp_accounts.py), quickicp_contacts (runner_quickicp_contacts.py),
email (runner_email.py), calls (runner_calls.py).

WHY
---
Supabase (via state_store.py) is the single source of truth. Every task of the approved plan
becomes a row with a status, so the system can stop at any point and resume exactly there.
Tool runners follow runner_kit.py: save before acting, reuse existing Supabase data before any
paid call, a crash during a paid call -> unknown + a human decides. The brain review is code:
achieved vs target, at most 2 extra rounds per try, then a goal_check decision.

FLOW
----
    master_agent.py  ->  data/work_orders.json (approved)  ->  python orchestrator.py load 2026-10
        1. check the plan with CODE: unique task ids, every depends_on exists, no loops
        2. create the run  <CUSTOMER_KEY>-<YYYY-MM>  (e.g. anvixa-ai-product-2026-10), status 'planning'
        3. add one task per work order (status 'pending')
        4. record the plan approval decision (answered 'yes')
        5. run status -> 'running'
    Every step is safe to repeat: after a crash, 'load' again finishes what is missing.
    If work_orders.json CHANGED after the run was loaded, 'load' stops (never merges silently).

    A NEW PLAN FOR THE SAME MONTH (never mixed with the old run)
        python orchestrator.py close 2026-10 "replaced by the intake plan"   (type the run key to confirm)
            under the run lock: open decisions answered stop / no / reject (by 'user', note 'run closed:
            <reason>'), unfinished tasks -> 'stopped', run -> 'stopped' with 'CLOSED by user: <reason>'.
            Kept as is: done tasks, answered decisions, the ledger. Closing twice changes nothing.
        python orchestrator.py load 2026-10-r2   -> a separate run anvixa-ai-product-2026-10-r2 (needs proof)
            commands without a month (status, next, answer T8) then use it: it is the latest run.
        NOTE: the new run does not know whom the old run already emailed or called. Before REAL
        prospects, an 'already contacted in an earlier run' filter is needed (separate step, not built).

    status / next / decisions   read-only
    answer <id> <answer> "note" records one human answer (human tasks need a note)

    run 2026-10  (one step per call; nothing loops automatically)
        1. apply answered decisions:  done -> task done | skip -> task done, result.skipped = true
                                      stop -> ONLY that task stopped (note in last_error)
        2. crash recovery: a human task left 'running' gets its decision (the same one if it exists)
        3. start at most ONE ready task (earliest week first):
              human task (CAM, Content & Training, Human Tele Calling, LinkedIn Agent, Sales Engineer)
                  -> running -> decision opened -> waiting_human; the person does it, then answers
                  gate tasks (data_approval, content, client_training, readiness): done / stop only
              tool task  -> its runner (if wired): done / waiting for a decision / stopped / failed;
                            not wired yet: stays pending, printed as waiting
           at most ONE tool runner per call (continuing one, or starting one)
        4. run status: 'done' when every task is done; 'stopped' only when no task can move
           (nothing ready, waiting or running) and some task is stopped; stop_reason lists them
        Tasks that depend on a stopped task never start. Unrelated tasks keep going.
        RUN LOCK (sql/003): one 'run' at a time per run; a crashed run's lock expires after 30 min,
        or: python orchestrator.py unlock <YYYY-MM>  (type the run key to confirm)
        Tool decisions: goal_check (continue/more/stop), goal_check_final (continue/stop),
        paid_call_unknown (rerun/skip/stop), task_failed (retry/skip/stop),
        email_approve (yes/no: samples shown), call_start (yes/no: contacts shown).
        A tool task may also stay 'running' (e.g. calls in progress): the next 'run' continues it.

    CRASH SAFETY (part 1)
        crash while applying an answer        -> task still waiting_human: applied again next run
        crash after 'running', no decision    -> next run opens the decision, sets waiting_human
        crash after the decision was opened   -> the SAME decision is reused (one per task+kind)
        ... and answered before the next run  -> the answer is used in that run, never asked again
        tool task (not wired)                 -> nothing was written; it is reported again
        tool task (wired)                     -> see the CRASH SAFETY table in runner_quickicp_accounts.py

PLAN APPROVAL PROOF
-------------------
    master_agent.py writes data/plan_approval.json after approval (who, when, rule errors at
    approval, override + reason, fingerprints of work_orders.json and month_plan.json).
    'load' needs that proof whenever it records a NEW approval, checks it still matches the files,
    and stores it in the plan_approval decision. Without proof it stops and writes nothing.
    A run approved before proof existed (anvixa-ai-product-2026-10) keeps loading, and 'status'
    shows it honestly: "yes, approved BEFORE approval proof existed (no proof)". Proof is never
    invented afterwards.

WHO DECIDES WHAT
----------------
    Code : plan checks, run key, task order, which task is ready, allowed answers
    Human: approves the plan (master_agent.py), answers decisions here

READS
-----
    .env                         CUSTOMER_KEY, STATE_BACKEND (+ SUPABASE_* for the supabase backend)
    data/work_orders.json        approved work orders (master_agent.py)
    data/month_plan.json         the approved plan (saved into the run's plan JSON)
    data/revenue_analysis.json   the revenue analysis (saved in the run's plan JSON under 'revenue_analysis';
                                 required for a new run)
    data/understanding.json      the brain's understanding (saved into the run)
    data/plan_approval.json      approval proof (master_agent.py), checked by plan_approval.read_proof()
    state store                  brain_customer_runs, brain_plan_tasks, brain_decisions

WRITES (state store only)
-------------------------
    load       1 run, 1 task per work order, 1 plan_approval decision
    answer     the answer on one open decision
    close      answers on the run's open decisions, unfinished tasks + run -> 'stopped'
    selftest   rows of one selftest-* run, deleted at the end

RUN
---
    python orchestrator.py selftest                        test everything on a throwaway selftest-* run
                                                           (the tests live in orchestrator_selftest.py)
    python orchestrator.py load 2026-10                    load the approved plan as the run for that month
    python orchestrator.py status [2026-10]                tasks, next ready task, open decisions (read-only)
    python orchestrator.py next [2026-10]                  the next ready task in full (read-only, not started)
    python orchestrator.py decisions                       open decisions of this customer (read-only)
    python orchestrator.py answer <id> <answer> ["note"]   answer one open decision (note required for human tasks)
    python orchestrator.py run 2026-10                     one step: apply answers, start one ready task
    python orchestrator.py run 2026-10 --until-wait        steps until a person is needed, a tool is still
                                                           running, nothing is left, or the NEXT task would
                                                           buy data (QuickICP): that one needs a plain 'run'
    python orchestrator.py answer T11 yes "note"           a task id works when it has exactly ONE open decision
    python orchestrator.py run 2026-10 --dry               what the next tool task WOULD buy (LLM + Prospeo,
                                                           no paid search, nothing recorded)
    python orchestrator.py run 2026-10 --dry T7            the same for one wired tool task (e.g. T11, T7)
    python orchestrator.py review 2026-10                  the brain reviews done tool tasks against the plan:
                                                           code numbers + 1 LLM call (2 if it invents a number);
                                                           max 3 suggested actions -> accept / reject decisions
    python orchestrator.py unlock 2026-10                  free the run lock after a crash
    python orchestrator.py close 2026-10 "reason"          close a run a new plan replaces (then: load 2026-10-r2)
    selftest: fake QuickICP toolset, network guard (only Supabase allowed); on STATE_BACKEND=sqlite
              it uses a throwaway temp file, never data/brain_state.db
    Without a month, status/next use this customer's latest run.

DEBUGGING
---------
    "STOPPED: CUSTOMER_KEY is missing"         -> add CUSTOMER_KEY=<key> to .env
    "STOPPED: month must look like 2026-10"    -> give the month as YYYY-MM, or a revision 2026-10-r2
    "STOPPED: a reason is required"            -> close <YYYY-MM> "why this run is replaced"
    "STOPPED: tool task(s) ... still running"  -> close refused: let the tool finish (run again) first
    "STOPPED: run is busy: locked by ..."      -> another 'run' is working; close waits for it
    "STOPPED: plan check failed"               -> the printed task ids / depends_on are wrong in
                                                  data/work_orders.json; re-run master_agent.py
    "STOPPED: the approved plan changed ..."   -> work_orders.json differs from the loaded run;
                                                  load it as another month, or ask to replace the run
    "STOPPED: no approval proof"               -> run master_agent.py and approve the plan
    "STOPPED: data/revenue_analysis.json is missing" -> a new run needs it; re-run master_agent.py
    "STOPPED: ... changed after approval"      -> work_orders.json / month_plan.json edited after
                                                  approval; re-run master_agent.py
    "STOPPED: no run ... load it first"        -> run: python orchestrator.py load <YYYY-MM>
    "STOPPED: decision N belongs to another customer" -> check CUSTOMER_KEY in .env
    Look at the rows: Supabase tables brain_customer_runs / brain_plan_tasks / brain_decisions.
"""

import os
import re
import sys

from plan_approval import describe, read_proof
from quickicp import TEST_MODE, ToolError, load_json
from state_store import (
    ALLOWED_ANSWERS, answer_decision, add_tasks, check_customer_key, create_run, current_customer,
    force_unlock, get_decisions, get_run, get_runs, get_tasks, list_open_decisions, lock_holder,
    now_iso, open_decision, ready_tasks, record_decision, release_run_lock, renew_run_lock, take_run_lock, update_run,
    update_task,
)
from runner_kit import RunnerContext, RunnerResult, run_understanding
from runner_quickicp_accounts import RealAccountsTools, run_quickicp_accounts
from runner_quickicp_contacts import RealContactsTools, run_quickicp_contacts
from runner_email import RealEmailTools, run_email
from runner_calls import RealCallTools, run_calls

WORK_ORDERS_FILE = "data/work_orders.json"
PLAN_FILE = "data/month_plan.json"
REVENUE_FILE = "data/revenue_analysis.json"
UNDERSTANDING_FILE = "data/understanding.json"

# 2026-10, or a revision of that month that replaces a closed run: 2026-10-r2, 2026-10-r3, ... (no -r0 / -r1).
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-r([2-9]|[1-9]\d+))?$")


# ================= PLAN CHECKS (code, no LLM) =================

def check_month(month: str) -> str:
    """Return month if it looks like 2026-10 or 2026-10-r2; stop otherwise."""
    if not MONTH_PATTERN.match(month or ""):
        raise ToolError(f"STOPPED: month must look like 2026-10 (YYYY-MM) or 2026-10-r2 (a revision), got '{month}'.")
    return month


def month_of(run: dict) -> str:
    """The month part of the run key: 'anvixa-ai-product-2026-10-r2' -> '2026-10-r2'."""
    return run["run_key"][len(run["customer"]) + 1:]


def run_key_for(customer: str, month: str) -> str:
    """'anvixa-ai-product' + '2026-10' -> 'anvixa-ai-product-2026-10'."""
    return f"{check_customer_key(customer)}-{check_month(month)}"


def plan_errors(work_orders: list[dict]) -> list[str]:
    """Every rule the work orders break: empty plan, missing fields, duplicate ids, unknown dependencies, loops."""
    if not work_orders:
        return ["no work orders"]
    errors = []
    ids = [wo.get("task_id") for wo in work_orders]
    for wo in work_orders:
        missing = [field for field in ("task_id", "agent", "output_type") if not wo.get(field)]
        if missing:
            errors.append(f"{wo.get('task_id') or '?'} is missing {', '.join(missing)}")
    errors += [f"task id {task_id} is used more than once" for task_id in sorted({i for i in ids if ids.count(i) > 1 and i})]
    known = set(ids)
    for wo in work_orders:
        for dep in wo.get("depends_on", []):
            if dep not in known:
                errors.append(f"{wo['task_id']} depends on {dep}, which does not exist")
            if dep == wo.get("task_id"):
                errors.append(f"{dep} depends on itself")

    # Loop check: repeatedly remove tasks whose dependencies are all removed; what is left is in a loop.
    remaining = {wo["task_id"]: set(wo.get("depends_on", [])) & known for wo in work_orders if wo.get("task_id")}
    while True:
        free = [task_id for task_id, deps in remaining.items() if not deps & remaining.keys()]
        if not free:
            break
        for task_id in free:
            del remaining[task_id]
    if remaining:
        errors.append(f"dependency loop between: {', '.join(sorted(remaining))}")
    return errors


def changed_tasks(saved_tasks: list[dict], work_orders: list[dict]) -> list[str]:
    """
    Saved tasks that no longer match work_orders.json (removed or different content).
    Work orders not saved yet are NOT a change: they are added (crash during an earlier load).
    """
    by_id = {wo["task_id"]: wo for wo in work_orders}
    changes = []
    for task in saved_tasks:
        if task["task_id"] not in by_id:
            changes.append(f"{task['task_id']} is in the run but not in {WORK_ORDERS_FILE}")
        elif task["work_order"] != by_id[task["task_id"]]:
            changes.append(f"{task['task_id']} has different content in {WORK_ORDERS_FILE}")
    return changes


# ================= LOAD =================

def plan_for_run(month_plan: dict, revenue_analysis: dict) -> dict:
    """The plan JSON saved in the run: the month plan + its revenue analysis under 'revenue_analysis'."""
    if "revenue_analysis" in month_plan:
        raise ToolError("STOPPED: month_plan.json already has a 'revenue_analysis' key; refusing to overwrite it.")
    return {**month_plan, "revenue_analysis": revenue_analysis}


def approval_of(run_id: int) -> dict | None:
    """The run's answered plan_approval 'yes' decision, or None."""
    approvals = [d for d in get_decisions(run_id)
                 if d["kind"] == "plan_approval" and d["status"] == "answered" and d["answer"] == "yes"]
    return approvals[-1] if approvals else None


def load_run(customer: str, month: str, work_orders: list[dict], plan: dict | None,
             understanding: dict | None, test_mode: bool, proof: dict | None,
             proof_error: str | None = None, plan_error: str | None = None) -> tuple[dict, bool]:
    """
    Load approved work orders as a run. Safe to repeat. Returns (run, created_now).
    Order (each step safe to repeat after a crash): checks -> run -> tasks -> approval -> running.
    proof: the approval proof from plan_approval.read_proof(). It is REQUIRED whenever a new
    approval must be recorded (new run, or a crash before the approval was saved). A run that
    already has its approval loads again without it (proof is never invented afterwards).
    plan: plan_for_run(month plan, revenue analysis). REQUIRED when the run has no plan yet (new run);
    an existing run keeps the plan it was loaded with. plan_error is the reason when it is missing.
    """
    errors = plan_errors(work_orders)
    if errors:
        raise ToolError("STOPPED: plan check failed:\n  - " + "\n  - ".join(errors))

    run_key = run_key_for(customer, month)
    existing = get_run(run_key)
    if existing:
        changes = changed_tasks(get_tasks(existing["id"]), work_orders)
        if changes:
            raise ToolError(f"STOPPED: the approved plan changed after run {run_key} was loaded:\n  - "
                            + "\n  - ".join(changes)
                            + "\nLoad it as another month, or ask the developer to replace this run.")

    # Checked BEFORE writing anything: no plan (with revenue analysis) for a new run, no load.
    if (not existing or existing["plan"] is None) and plan is None:
        raise ToolError(plan_error or "STOPPED: no plan to save in the run.")

    # Checked BEFORE writing anything: no proof, no new approval.
    needs_approval = not existing or approval_of(existing["id"]) is None
    if needs_approval:
        if not proof:
            raise ToolError(proof_error or "STOPPED: no approval proof. Run master_agent.py and approve the plan.")
        if proof.get("work_orders_count") != len(work_orders):
            raise ToolError(f"STOPPED: approval proof is for {proof.get('work_orders_count')} work orders, "
                            f"but {WORK_ORDERS_FILE} has {len(work_orders)}.")

    run = existing or create_run(run_key, customer, test_mode, understanding)
    if run["plan"] is None and plan is not None:
        run = update_run(run["id"], plan=plan)
    count = add_tasks(run["id"], work_orders)

    if needs_approval:
        record_decision(run["id"], "plan_approval", "yes", "user", describe(proof),
                        question={"text": "Approve the month plan?", "source": WORK_ORDERS_FILE,
                                  "work_orders": len(work_orders), "proof": proof})
    if run["status"] == "planning":
        run = update_run(run["id"], status="running")

    print(f"Run {run_key}: {'created' if not existing else 'already loaded, nothing changed'} | "
          f"{count} tasks | status {run['status']} | test_mode {run['test_mode']}")
    return run, existing is None


def cmd_load(month: str) -> None:
    """Load data/work_orders.json (+ plan, understanding, approval proof) as this customer's run for month."""
    try:
        proof, proof_error = read_proof(), None
    except ToolError as error:
        proof, proof_error = None, str(error)   # only fatal if load must record a new approval
    # The run stores the month plan WITH its revenue analysis, so both can always be restored.
    if os.path.exists(REVENUE_FILE):
        plan, plan_error = plan_for_run(load_json(PLAN_FILE), load_json(REVENUE_FILE)), None
    else:
        plan, plan_error = None, f"STOPPED: {REVENUE_FILE} is missing; re-run master_agent.py."
    load_run(current_customer(), month, load_json(WORK_ORDERS_FILE), plan,
             load_json(UNDERSTANDING_FILE), TEST_MODE, proof, proof_error, plan_error)


# ================= READ-ONLY VIEWS =================

def find_run(customer: str, month: str | None) -> dict:
    """The run for month, or this customer's latest run when month is None."""
    if month:
        run = get_run(run_key_for(customer, month))
        if not run:
            raise ToolError(f"STOPPED: no run {run_key_for(customer, month)}. "
                            f"Load it first: python orchestrator.py load {month}")
        return run
    runs = get_runs(customer)
    if not runs:
        raise ToolError(f"STOPPED: no run for customer '{customer}'. Load one: python orchestrator.py load <YYYY-MM>")
    return runs[-1]


def short(text, width: int) -> str:
    """Cut text to width characters for table columns."""
    text = str(text or "")
    return text if len(text) <= width else text[:width - 1] + "…"


def approval_line(approval: dict | None) -> str:
    """Honest one-line plan approval status. A decision without proof is shown as 'no proof'."""
    if approval is None:
        return "Plan approval: NONE recorded"
    return "Plan approval: " + describe((approval.get("question") or {}).get("proof"))


def show_status(run: dict) -> None:
    """Print the run, its tasks, the ready tasks and its open decisions."""
    tasks = get_tasks(run["id"])
    ready = ready_tasks(run["id"])
    ready_ids = {t["task_id"] for t in ready}
    print(f"Run {run['run_key']} | customer {run['customer']} | status {run['status']} | "
          f"test_mode {run['test_mode']} | updated {run['updated_at']}")
    if run["status"] == "stopped" and run.get("stop_reason"):
        print(f"  {run['stop_reason']}")
    print(f"\n  {'task':<5} {'wk':<3} {'agent':<20} {'output':<16} {'after':<10} {'status':<16} target")
    for t in tasks:
        status = t["status"] + (" (ready)" if t["task_id"] in ready_ids else "")
        print(f"  {t['task_id']:<5} {str(t['week'] or ''):<3} {short(t['agent'], 20):<20} "
              f"{short(t['output_type'], 16):<16} {short(','.join(t['depends_on']) or '-', 10):<10} "
              f"{status:<16} {short(t['work_order'].get('target'), 60)}")
    counts = {}
    for t in tasks:
        counts[t["status"]] = counts.get(t["status"], 0) + 1
    print("\n  Totals: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    print(f"  Ready now: {', '.join(t['task_id'] for t in ready) or 'none'}"
          + (f"  ->  next: {ready[0]['task_id']}" if ready else ""))

    by_task = {t["id"]: t["task_id"] for t in tasks}
    decisions = get_decisions(run["id"])
    opened = [d for d in decisions if d["status"] == "open"]
    print(f"\n  Open decisions: {len(opened)}")
    for d in opened:
        print(f"    #{d['id']} {d['kind']} ({by_task.get(d['task_row_id'], 'run')}): "
              f"{(d['question'] or {}).get('text', '')}  answers: {' / '.join(ALLOWED_ANSWERS[d['kind']])}")
    for t in tasks:
        if t["status"] == "waiting_human":
            answered = [d for d in decisions if d["task_row_id"] == t["id"] and d["kind"] == t["waiting_for"]
                        and d["status"] == "answered"]
            if answered:
                print(f"    {t['task_id']}: answered '{answered[-1]['answer']}', applied on the next run")
    print(f"\n  {approval_line(approval_of(run['id']))}")


def show_next(run: dict) -> None:
    """Print the next ready task in full. It is NOT started."""
    ready = ready_tasks(run["id"])
    if not ready:
        print(f"Run {run['run_key']}: no task is ready (all done, running, waiting for a human, or blocked).")
        return
    task = ready[0]
    wo = task["work_order"]
    print(f"NEXT TASK (not started): {task['task_id']}  {wo.get('agent')} / {wo.get('output_type')}  "
          f"week {wo.get('week')}  after: {', '.join(task['depends_on']) or 'none'}")
    print(f"  Target:        {wo.get('target')}   (target_number by code: {task['target_number']})")
    print(f"  Tier / where:  {wo.get('tier')} / {wo.get('geography')}")
    print(f"  Revenue link:  {wo.get('revenue_link')}")
    print("  Instructions:")
    for number, step in enumerate(wo.get("instructions", []), start=1):
        print(f"    {number}. {step}")
    print(f"  Needs:         {'; '.join(wo.get('inputs_needed', [])) or 'nothing'}")
    print(f"  Returns:       {'; '.join(wo.get('expected_output', []))}")
    print(f"  Done when:     {wo.get('done_when')}")
    print(f"  Escalate if:   {wo.get('escalate_if')}")
    if len(ready) > 1:
        print(f"  Also ready:    {', '.join(t['task_id'] for t in ready[1:])}")


def cmd_decisions(customer: str) -> None:
    """Print every open decision of this customer's runs."""
    count = 0
    for run in get_runs(customer):
        tasks = {t["id"]: t["task_id"] for t in get_tasks(run["id"])}
        for d in list_open_decisions(run["id"]):
            count += 1
            print(f"\n#{d['id']}  {run['run_key']}  {tasks.get(d['task_row_id'], 'run')}  {d['kind']}  "
                  f"answers: {' / '.join(sorted(ALLOWED_ANSWERS[d['kind']]))}")
            print_question(d)
    if not count:
        print(f"No open decisions for customer '{customer}'.")


def print_question(decision: dict) -> None:
    """The full question: email samples (subject + text), call contacts (name, company, number)."""
    q = decision["question"] or {}
    print(f"      {q.get('text', '')}")
    if decision["kind"] == "email_approve":
        if q.get("round"):
            print(f"      Version {q['round']} | feedback rounds left: {q.get('feedback_rounds_left')}")
        for number, note in enumerate(q.get("previous_feedback") or [], start=1):
            print(f"      your feedback {number}: {note}")
        for problem in q.get("checks") or []:
            print(f"      CHECK FAILED: {problem}")
        for s in q.get("samples", []):
            print(f"      --- sample email {s.get('email')} for {s.get('for')}")
            print(f"      Subject: {s.get('subject')}")
            for line in (s.get("text") or "").splitlines():
                print(f"        {line}")
    if decision["kind"] == "call_start":
        for c in q.get("contacts", []):
            print(f"      will call: {c.get('name')} | {c.get('company')} | number {c.get('phone')}")
        print("      calling window: 09:00-19:00 Mon-Sat (one call at a time)")
    if q.get("instructions"):
        for number, step in enumerate(q["instructions"], start=1):
            print(f"      {number}. {step}")


def cmd_answer_task(customer: str, task_id: str, answer: str, reason: str | None) -> dict:
    """'answer T11 yes "note"': the ONE open decision of that task in this customer's latest run."""
    run = find_run(customer, None)
    task = next((t for t in get_tasks(run["id"]) if t["task_id"] == task_id), None)
    if not task:
        raise ToolError(f"STOPPED: no task {task_id} in run {run['run_key']}.")
    open_ones = [d for d in list_open_decisions(run["id"]) if d["task_row_id"] == task["id"]]
    if len(open_ones) != 1:
        listed = ", ".join(f"#{d['id']} {d['kind']}" for d in open_ones) or "none"
        raise ToolError(f"STOPPED: {task_id} in run {run['run_key']} has {len(open_ones)} open decisions ({listed}); "
                        "answer by decision number instead.")
    print(f"Run {run['run_key']}: {task_id} -> decision #{open_ones[0]['id']} ({open_ones[0]['kind']})")
    return cmd_answer(customer, open_ones[0]["id"], answer, reason)


def cmd_answer(customer: str, decision_id: int, answer: str, reason: str | None) -> dict:
    """Answer one open decision of THIS customer (never another customer's). Human tasks need a note."""
    own_decisions = {d["id"]: d for run in get_runs(customer) for d in get_decisions(run["id"])}
    if decision_id not in own_decisions:
        raise ToolError(f"STOPPED: decision {decision_id} does not exist or belongs to another customer "
                        f"(CUSTOMER_KEY is '{customer}').")
    kind = own_decisions[decision_id]["kind"]
    if kind in NOTE_REQUIRED_KINDS and not (reason or "").strip():
        raise ToolError(f'STOPPED: a note is required, e.g. answer {decision_id} done "meeting held 10 Oct"')
    if (kind, answer) in NOTE_REQUIRED_ANSWERS and not (reason or "").strip():
        raise ToolError(f'STOPPED: "{answer}" needs a note saying what to change, e.g. answer {decision_id} '
                        f'{answer} "shorter, and mention the Pune plant"')
    if len(reason or "") > MAX_NOTE_LENGTH:
        raise ToolError(f"STOPPED: keep the note under {MAX_NOTE_LENGTH} characters (it has {len(reason)}).")
    answered = answer_decision(decision_id, answer, decided_by="user", reason=reason)
    if answered["kind"].startswith("review_action_"):
        print(f"Decision #{decision_id} ({answered['kind']}) answered '{answer}'. Recorded. Nothing runs automatically.")
    else:
        print(f"Decision #{decision_id} ({answered['kind']}) answered '{answer}'. "
              "It is applied on the next: python orchestrator.py run <YYYY-MM>")
    return answered


# ================= RUN (part 1: human tasks; tool runners plug in later) =================

# People do these tasks: the orchestrator opens a decision and the person answers it.
HUMAN_AGENTS = {"CAM", "Content & Training", "Human Tele Calling", "LinkedIn Agent", "Sales Engineer"}
# Gate tasks can never be skipped: only done or stop (code rule, enforced by the decision kind).
GATE_TYPES = {"data_approval", "content", "client_training", "readiness"}
HUMAN_KINDS = {"human_task", "human_gate_task"}
# A note is required when a person answers these.
NOTE_REQUIRED_KINDS = HUMAN_KINDS | {"review_action_1", "review_action_2", "review_action_3"}
# ...and for these answers of other kinds (the note is what gets sent on). Same rules: lib/brain/answers.ts.
NOTE_REQUIRED_ANSWERS = {("email_approve", "feedback")}
MAX_NOTE_LENGTH = 1000

# Tool tasks: (agent, output_type) -> runner name. A runner is wired when it is in WIRED_RUNNERS.
TOOL_ROUTES = {
    ("QuickICP", "accounts"): "quickicp_accounts",   # company_loop -> save_companies
    ("QuickICP", "contacts"): "quickicp_contacts",   # people_loop, emails, phones -> save_people
    ("Email Agent", "emails"): "email",              # email_tool launch -> samples -> approve
    ("Quickhowl", "calls"): "calls",                 # quickhowl launch -> start -> results
}
# Wired runners: name -> (runner function, toolset factory). The selftest swaps the factory for fakes.
WIRED_RUNNERS: dict = {
    "quickicp_accounts": (run_quickicp_accounts, lambda ctx: RealAccountsTools(
        ctx.run["run_key"], ctx.task["task_id"], run_understanding(ctx.run), ctx.task["work_order"])),
    "quickicp_contacts": (run_quickicp_contacts, lambda ctx: RealContactsTools(
        ctx.run["run_key"], ctx.task["task_id"], run_understanding(ctx.run), ctx.task["work_order"])),
    "email": (run_email, lambda ctx: RealEmailTools(run_understanding(ctx.run), ctx.task["work_order"])),
    "calls": (run_calls, lambda ctx: RealCallTools(
        ctx.run["run_key"], ctx.task["task_id"], run_understanding(ctx.run), ctx.task["work_order"])),
}
# Every real toolset gets the run's understanding (brain_customer_runs) and THIS task's work order:
# a run never reads data/understanding.json or data/work_orders.json (they belong to the latest plan).
# Decisions a tool task can wait for; the runner itself reads and acts on the answer.
TOOL_DECISION_KINDS = {"goal_check", "goal_check_final", "paid_call_unknown", "email_approve", "call_start"}


def task_types(task: dict) -> set[str]:
    """The task's types from its work order ('requirements, coordination' or a list)."""
    raw = task["work_order"].get("task_types") or task["work_order"].get("task_type") or ""
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return {item.strip() for item in items if item.strip()}


def route(task: dict) -> tuple[str, str | None]:
    """('human', None) or ('tool', runner_name). Code decides from agent and output type."""
    if task["agent"] in HUMAN_AGENTS:
        return "human", None
    runner = TOOL_ROUTES.get((task["agent"], task["output_type"]))
    if runner:
        return "tool", runner
    raise ToolError(f"STOPPED: no human or tool route for {task['task_id']} "
                    f"(agent '{task['agent']}', output '{task['output_type']}').")


def human_kind(task: dict) -> str:
    """'human_gate_task' (done / stop only) for gate tasks, otherwise 'human_task' (done / skip / stop)."""
    return "human_gate_task" if task_types(task) & GATE_TYPES else "human_task"


def human_question(task: dict) -> dict:
    """What the person is asked to do, from the work order."""
    wo = task["work_order"]
    return {"text": f"Do {task['task_id']} ({task['agent']} / {task['output_type']}). Done when: {wo.get('done_when')}",
            "target": wo.get("target"), "instructions": wo.get("instructions", []),
            "done_when": wo.get("done_when"), "escalate_if": wo.get("escalate_if")}


def start_human_task(run: dict, task: dict) -> dict:
    """
    pending/running -> decision open -> waiting_human. Each step is safe to repeat after a crash:
    open_decision() returns the SAME open decision if one already exists for this task and kind.
    """
    kind = human_kind(task)
    if task["status"] == "pending":
        task = update_task(task["id"], status="running", started_at=now_iso())
    # After a crash the decision may already exist, even already ANSWERED: reuse it, never ask twice.
    earlier = [d for d in get_decisions(run["id"]) if d["task_row_id"] == task["id"] and d["kind"] == kind]
    decision = earlier[-1] if earlier else open_decision(run["id"], kind, human_question(task), task["id"])
    update_task(task["id"], status="waiting_human", waiting_for=kind)
    return decision


def apply_answers(run: dict) -> list[str]:
    """Apply every answered decision a waiting task is waiting for. Returns what was applied."""
    applied = []
    decisions = get_decisions(run["id"])
    for task in get_tasks(run["id"]):
        if task["status"] != "waiting_human" or task["waiting_for"] not in HUMAN_KINDS:
            continue
        answered = [d for d in decisions if d["task_row_id"] == task["id"] and d["kind"] == task["waiting_for"]
                    and d["status"] == "answered"]
        if not answered:
            continue
        d = answered[-1]
        note = {"answer": d["answer"], "note": d["reason"], "decided_by": d["decided_by"], "decision_id": d["id"]}
        if d["answer"] == "stop":
            update_task(task["id"], status="stopped", waiting_for=None, last_error=f"stopped by {d['decided_by']}: "
                        f"{d['reason']}", result=note, finished_at=now_iso())
        else:   # done, or skip (never offered for gate tasks)
            update_task(task["id"], status="done", waiting_for=None,
                        result={**note, "skipped": d["answer"] == "skip"}, finished_at=now_iso())
        applied.append(f"{task['task_id']} -> {'stopped' if d['answer'] == 'stop' else 'done'} "
                       f"({d['answer']}: {d['reason']})")
    return applied


def blocked_tasks(tasks: list[dict]) -> dict[str, list[str]]:
    """Pending tasks that can never start, with the stopped task(s) that block them (directly or indirectly)."""
    by_id = {t["task_id"]: t for t in tasks}
    memo: dict[str, set[str]] = {}

    def stoppers(task_id: str) -> set[str]:
        if task_id not in memo:
            memo[task_id] = set()
            for dep in by_id[task_id]["depends_on"]:
                if by_id[dep]["status"] == "stopped":
                    memo[task_id].add(dep)
                memo[task_id] |= stoppers(dep)
        return memo[task_id]

    return {t["task_id"]: sorted(stoppers(t["task_id"])) for t in tasks
            if t["status"] == "pending" and stoppers(t["task_id"])}


def settle_run(run: dict) -> dict:
    """Run -> 'done' when every task is done; -> 'stopped' when nothing can move and some task is stopped."""
    tasks = get_tasks(run["id"])
    statuses = {t["status"] for t in tasks}
    if statuses == {"done"}:
        return update_run(run["id"], status="done")
    can_move = ready_tasks(run["id"]) or statuses & {"waiting_human", "running"}
    if not can_move and "stopped" in statuses:
        stopped = [f"{t['task_id']} ({t['last_error']})" for t in tasks if t["status"] == "stopped"]
        blocked = blocked_tasks(tasks)
        reason = ("STOPPED: no task can move. Stopped: " + "; ".join(stopped)
                  + (f". Blocked by them: {', '.join(sorted(blocked))}" if blocked else ""))
        return update_run(run["id"], status="stopped", stop_reason=reason)
    return run


def apply_task_failed(run: dict) -> list[str]:
    """A tool task waiting on task_failed: retry -> running (the runner runs again), skip -> done, stop -> stopped."""
    applied = []
    decisions = get_decisions(run["id"])
    for task in get_tasks(run["id"]):
        if task["status"] != "waiting_human" or task["waiting_for"] != "task_failed":
            continue
        answered = [d for d in decisions if d["task_row_id"] == task["id"] and d["kind"] == "task_failed"
                    and d["status"] == "answered"]
        if not answered:
            continue
        d = answered[-1]
        note = {"answer": d["answer"], "note": d["reason"], "decided_by": d["decided_by"], "decision_id": d["id"]}
        if d["answer"] == "retry":
            update_task(task["id"], status="running", waiting_for=None, attempts=task["attempts"] + 1)
        elif d["answer"] == "skip":
            update_task(task["id"], status="done", waiting_for=None, result={**note, "skipped": True},
                        finished_at=now_iso())
        else:
            update_task(task["id"], status="stopped", waiting_for=None, result=note, finished_at=now_iso(),
                        last_error=f"stopped by {d['decided_by']} after a failure: {d['reason']}")
        applied.append(f"{task['task_id']} -> {d['answer']} after a failure ({d['reason']})")
    return applied


def tool_task_to_resume(run: dict) -> dict | None:
    """A tool task the runner must continue: left 'running' (crash / retry), or its decision is answered."""
    decisions = get_decisions(run["id"])
    for task in get_tasks(run["id"]):
        if route(task)[0] != "tool":
            continue
        if task["status"] == "running":
            return task
        if task["status"] == "waiting_human" and task["waiting_for"] in TOOL_DECISION_KINDS:
            mine = [d for d in decisions if d["task_row_id"] == task["id"] and d["kind"] == task["waiting_for"]]
            if mine and mine[-1]["status"] == "answered":
                return task
    return None


def invoke_runner(run: dict, task: dict, holder: str | None, dry: bool = False) -> RunnerResult:
    """Call the task's runner with its toolset. A known failure (ToolError) becomes a 'failed' result."""
    runner, make_tools = WIRED_RUNNERS[route(task)[1]]
    ctx = RunnerContext(run=run, task=task, lock_holder=holder, dry=dry)
    try:
        return runner(ctx, make_tools(ctx))
    except ToolError as error:
        if dry:
            raise
        return RunnerResult.failed(str(error))


def apply_result(run: dict, task: dict, result: RunnerResult) -> str:
    """Save what the runner returned on the task (and the brain review). Returns one report line."""
    if result.kind == "done":
        update_task(task["id"], status="done", waiting_for=None, achieved_number=result.achieved,
                    result={**result.output, "note": result.note}, finished_at=now_iso(), last_error=None)
        review = record_decision(run["id"], "brain_review", "done", "brain",
                                 f"code: achieved {result.achieved} of target {result.target}. {result.note}",
                                 question={"achieved": result.achieved, "target": result.target}, task_row_id=task["id"])
        return f"{task['task_id']} -> done: {result.note} (brain review #{review['id']})"
    if result.kind == "waiting":
        decision = open_decision(run["id"], result.decision_kind, result.question, task["id"])
        update_task(task["id"], status="waiting_human", waiting_for=result.decision_kind)
        allowed = " | ".join(sorted(ALLOWED_ANSWERS[result.decision_kind]))
        return (f"{task['task_id']} -> waiting for decision #{decision['id']} ({result.decision_kind}): "
                f"{result.question['text']}\n            answer: python orchestrator.py answer {decision['id']} <{allowed}> \"note\"")
    if result.kind == "stopped":
        update_task(task["id"], status="stopped", waiting_for=None, last_error=result.note, finished_at=now_iso())
        return f"{task['task_id']} -> stopped: {result.note}"
    if result.kind == "running":
        update_task(task["id"], status="running", waiting_for=None)
        return f"{task['task_id']} -> still running: {result.note}\n            next: python orchestrator.py run <YYYY-MM>"
    decision = open_decision(run["id"], "task_failed", {
        "text": f"{task['task_id']} failed: {result.note}. retry = run it again, skip = mark done without "
                "result, stop = stop this task."}, task["id"])
    update_task(task["id"], status="waiting_human", waiting_for="task_failed", last_error=result.note)
    return (f"{task['task_id']} -> FAILED: {result.note}\n            decision #{decision['id']}: "
            f"python orchestrator.py answer {decision['id']} <retry | skip | stop> \"note\"")


def cmd_run(customer: str, month: str) -> dict:
    """
    One step of the run, under the run lock: apply answers, finish half-done tasks, run at most ONE
    tool runner and start at most ONE new task, then settle the run status. Returns a summary (for tests).
    """
    run = find_run(customer, month)
    if run["status"] != "running":
        print(f"Run {run['run_key']} is '{run['status']}', nothing to do. {run.get('stop_reason') or ''}")
        return {"run_status": run["status"], "applied": [], "resumed": [], "started": None, "not_wired": [],
                "tool": None}
    holder = lock_holder()
    take_run_lock(run["id"], holder)
    try:
        return run_step(run, holder)
    finally:
        release_run_lock(run["id"], holder)   # also after an error; a killed process: the lock expires


def run_step(run: dict, holder: str, stop_before_new_paid: bool = False) -> dict:
    """
    The body of cmd_run (the caller holds the lock).
    stop_before_new_paid (--until-wait): do not START a new QuickICP task (it buys data); report it instead.
    """
    # Crash recovery FIRST: a human task left 'running' gets its decision (the same one if it exists,
    # even if it was already answered), so the answer below is applied in this same run.
    resumed = []
    for task in get_tasks(run["id"]):
        if task["status"] == "running" and route(task)[0] == "human":
            start_human_task(run, task)
            resumed.append(task["task_id"])

    applied = apply_answers(run) + apply_task_failed(run)

    # At most ONE tool runner per call: first a tool task to continue, else (below) a new one.
    tool_line, tool_kind, started, decision, not_wired, blocked_paid = None, None, None, None, [], None
    resume = tool_task_to_resume(run)
    if resume:
        result = invoke_runner(run, resume, holder)
        tool_kind, tool_line = result.kind, apply_result(run, resume, result)
    else:
        for task in ready_tasks(run["id"]):
            kind, runner = route(task)
            if kind == "human":
                decision, started = start_human_task(run, task), task
                break
            if runner in WIRED_RUNNERS:
                if stop_before_new_paid and task["agent"] in PAID_DATA_AGENTS:
                    blocked_paid = task["task_id"]
                    break
                started = update_task(task["id"], status="running", started_at=now_iso())
                result = invoke_runner(run, started, holder)
                tool_kind, tool_line = result.kind, apply_result(run, started, result)
                break
            not_wired.append(f"{task['task_id']} needs runner '{runner}' (not wired yet)")

    run = settle_run(run)

    # Report
    print(f"Run {run['run_key']} | status {run['status']}")
    for line in applied:
        print(f"  Applied:  {line}")
    for task_id in resumed:
        print(f"  Resumed:  {task_id} (half-done after a crash; its decision is open now)")
    for line in not_wired:
        print(f"  Waiting:  {line}")
    if tool_line:
        print(f"  Tool:     {tool_line}")
    if blocked_paid:
        print(f"  Next:     {blocked_paid} would buy data: run it without --until-wait "
              f"(python orchestrator.py run {month_of(run)})")
    if started and decision:
        allowed = " | ".join(sorted(ALLOWED_ANSWERS[decision["kind"]]))
        print(f"  Started:  {started['task_id']} ({started['agent']} / {started['output_type']}) "
              f"-> waiting for a person, decision #{decision['id']} ({decision['kind']})")
        print(f"            {human_question(started)['text']}")
        print(f'  Next:     do the task, then: python orchestrator.py answer {decision["id"]} <{allowed}> "note"')
    elif run["status"] == "running" and not tool_line and not blocked_paid:
        if list_open_decisions(run["id"]):
            print("  Next:     answer the open decision(s): python orchestrator.py decisions")
        elif not not_wired:
            print("  Next:     nothing to start now")
    if run["status"] == "stopped":
        print(f"  {run['stop_reason']}")
    if run["status"] == "done":
        print("  RUN DONE: every task is done.")
    return {"run_status": run["status"], "applied": applied, "resumed": resumed,
            "started": started["task_id"] if started else None, "not_wired": not_wired, "tool": tool_line,
            "tool_kind": tool_kind, "human_started": bool(decision), "blocked_paid": blocked_paid}


# --until-wait never STARTS a new task of these agents: they buy data without a human gate.
PAID_DATA_AGENTS = {"QuickICP"}
UNTIL_WAIT_MAX_STEPS = 20


def until_wait_stop(summary: dict) -> str | None:
    """Code decides when --until-wait stops (None = keep going)."""
    if summary["run_status"] in ("done", "stopped"):
        return f"run is {summary['run_status']}"
    if summary["blocked_paid"]:
        return f"next would buy data ({summary['blocked_paid']}): run it without --until-wait"
    if summary["human_started"]:
        return f"{summary['started']} needs a person (decision opened)"
    if summary["tool_kind"] in ("waiting", "failed"):
        return "a tool task needs a human decision"
    if summary["tool_kind"] == "running":
        return "a tool is still running (run again later)"
    if not (summary["applied"] or summary["resumed"] or summary["started"] or summary["tool"]):
        return "nothing left to do now (open decisions or no ready task)"
    return None


def cmd_run_until_wait(customer: str, month: str, max_steps: int = UNTIL_WAIT_MAX_STEPS) -> dict:
    """
    --until-wait: the normal one-step run, again and again under ONE lock, until a task needs a human,
    a tool is still running, nothing is left, the next task would buy data, or max_steps.
    """
    run = find_run(customer, month)
    if run["status"] != "running":
        print(f"Run {run['run_key']} is '{run['status']}', nothing to do.")
        return {"steps": 0, "stopped_because": f"run is {run['status']}"}
    holder = lock_holder()
    take_run_lock(run["id"], holder)
    try:
        for step in range(1, max_steps + 1):
            renew_run_lock(run["id"], holder)
            print(f"\n--- step {step} ---")
            summary = run_step(get_run(run["run_key"]), holder, stop_before_new_paid=True)
            reason = until_wait_stop(summary)
            if reason:
                print(f"\nSTOPPED after {step} step(s): {reason}")
                return {"steps": step, "stopped_because": reason, "last": summary}
        print(f"\nSTOPPED after {max_steps} steps (safety cap). Run again to continue.")
        return {"steps": max_steps, "stopped_because": "safety cap"}
    finally:
        release_run_lock(run["id"], holder)


def cmd_dry(customer: str, month: str, task_id: str | None = None) -> RunnerResult | None:
    """
    --dry: what a tool task WOULD do. Nothing is recorded, nothing is bought, no lock. It may use LLM
    calls (and, for QuickICP accounts, Prospeo lookups + data/quickicp_payload.json, old one archived).
    task_id given (e.g. T7): that wired tool task, even if its human gates are still open.
    No task_id: the tool task to continue, else the first ready one.
    """
    run = find_run(customer, month)
    if task_id:
        task = next((t for t in get_tasks(run["id"]) if t["task_id"] == task_id), None)
        if not task or route(task)[0] != "tool" or route(task)[1] not in WIRED_RUNNERS:
            raise ToolError(f"STOPPED: {task_id} is not a wired tool task in run {run['run_key']}.")
        if task["status"] == "done":
            raise ToolError(f"STOPPED: {task_id} is already done; nothing to dry-run.")
    else:
        task = tool_task_to_resume(run) or next(
            (t for t in ready_tasks(run["id"]) if route(t)[0] == "tool" and route(t)[1] in WIRED_RUNNERS), None)
    if not task:
        print(f"Run {run['run_key']}: no wired tool task is ready (dry run has nothing to check).")
        return None
    result = invoke_runner(run, task, None, dry=True)
    print(f"\nDRY RUN {run['run_key']} {task['task_id']} ({task['agent']} / {task['output_type']}) - nothing recorded, nothing bought")
    if result.note:
        print(f"  {result.note}")
    for line in result.output.get("lines", []):
        print(f"  {line}")
    return result


def cmd_review(customer: str, month: str, ask=None) -> dict:
    """
    review <YYYY-MM>: the brain reviews the run's done tool tasks against the plan (brain_review.py).
    Code computes every number; 1 LLM call writes the explanation + max 3 suggested actions (each an
    open decision: accept / reject). Under the run lock. Nothing runs automatically.
    """
    from brain_review import print_review, review_run
    from quickicp import ask_llm
    run = find_run(customer, month)
    holder = lock_holder()
    take_run_lock(run["id"], holder)
    try:
        result = review_run(run, ask or ask_llm)
    finally:
        release_run_lock(run["id"], holder)
    print_review(result)
    return result


def cmd_unlock(customer: str, month: str) -> None:
    """Free the run lock after a crash. Asks the human to type the run key."""
    run = find_run(customer, month)
    if not run.get("locked_by"):
        print(f"Run {run['run_key']} is not locked.")
        return
    print(f"Run {run['run_key']} is locked by {run['locked_by']} until {run['locked_until']}.")
    print("Only unlock if no other 'run' is still working (e.g. it crashed).")
    if input(f"Type the run key to unlock ({run['run_key']}): ").strip() != run["run_key"]:
        print("Not unlocked.")
        return
    force_unlock(run["id"])
    print("Unlocked.")


# ================= CLOSE (a new plan replaces this run) =================

CLOSED_PREFIX = "CLOSED by user: "


def is_closed(run: dict) -> bool:
    """True if 'close' already closed this run."""
    return run["status"] == "stopped" and (run.get("stop_reason") or "").startswith(CLOSED_PREFIX)


def close_answer(kind: str) -> str:
    """The answer that ends an open decision of this kind WITHOUT doing anything: stop, else no, else reject."""
    for answer in ("stop", "no", "reject"):
        if answer in ALLOWED_ANSWERS[kind]:
            return answer
    raise ToolError(f"STOPPED: no closing answer for decision kind '{kind}'; nothing was changed.")


def close_run(customer: str, month: str, reason: str, holder: str) -> dict:
    """
    Close a run so a new plan can be loaded as a revision (e.g. 2026-10-r2), under the run lock.
    Order (each step safe to repeat after a crash):
        checks (reason, not done, no tool running, a closing answer for every open decision)
        -> open decisions answered (stop / no / reject, decided_by 'user', note 'run closed: <reason>')
        -> every unfinished task -> 'stopped' (last_error 'closed: <reason>'; already stopped keeps its reason)
        -> run -> 'stopped', stop_reason 'CLOSED by user: <reason>'
    Never touched: done tasks, answered decisions, the ledger (brain_tool_calls), other runs.
    Returns {"closed_now", "decisions": [(id, kind, answer)], "tasks": [task ids stopped]}.
    """
    if not (reason or "").strip():
        raise ToolError(f'STOPPED: a reason is required, e.g. close {month} "replaced by the intake plan"')
    run = find_run(customer, check_month(month))
    if is_closed(run):
        print(f"Run {run['run_key']} is already closed: {run['stop_reason']}. Nothing changed.")
        return {"closed_now": False, "decisions": [], "tasks": []}
    take_run_lock(run["id"], holder)   # refuses while another 'run' holds it
    try:
        run = get_run(run["run_key"])
        if run["status"] == "done":
            raise ToolError(f"STOPPED: run {run['run_key']} is done; there is nothing to close.")
        tasks = get_tasks(run["id"])
        busy = [t["task_id"] for t in tasks if t["status"] == "running" and t["agent"] not in HUMAN_AGENTS]
        if busy:
            raise ToolError(f"STOPPED: tool task(s) {', '.join(busy)} still running (a tool may still be working "
                            "outside). Let them finish or stop them first; nothing was changed.")
        open_ones = list_open_decisions(run["id"])
        answers = {d["id"]: close_answer(d["kind"]) for d in open_ones}   # every kind checked before any write

        note = f"run closed: {reason}"
        for d in open_ones:
            answer_decision(d["id"], answers[d["id"]], decided_by="user", reason=note)
        stopped = []
        for t in tasks:
            if t["status"] in ("done", "stopped"):
                continue
            update_task(t["id"], status="stopped", waiting_for=None, last_error=f"closed: {reason}",
                        finished_at=now_iso())
            stopped.append(t["task_id"])
        run = update_run(run["id"], status="stopped", stop_reason=CLOSED_PREFIX + reason)
    finally:
        release_run_lock(run["id"], holder)
    return {"closed_now": True, "decisions": [(d["id"], d["kind"], answers[d["id"]]) for d in open_ones],
            "tasks": stopped}


def cmd_close(customer: str, month: str, reason: str, confirm=input) -> dict | None:
    """'close 2026-10 "reason"': show what closing changes, ask to type the run key, then close_run()."""
    run = find_run(customer, check_month(month))
    if is_closed(run):
        print(f"Run {run['run_key']} is already closed: {run['stop_reason']}. Nothing changed.")
        return None
    tasks = get_tasks(run["id"])
    unfinished = [f"{t['task_id']} ({t['status']})" for t in tasks if t["status"] not in ("done", "stopped")]
    opened = list_open_decisions(run["id"])
    print(f"Run {run['run_key']} | status {run['status']} | "
          f"{sum(t['status'] == 'done' for t in tasks)} of {len(tasks)} tasks done")
    print(f"  Will stop:    {', '.join(unfinished) or 'no task'}")
    answered = ", ".join("#{} {}".format(d["id"], d["kind"]) for d in opened)
    print(f"  Will answer:  {answered or 'no open decision'}")
    print("  Kept as is:   done tasks, answered decisions, every tool call in the ledger")
    if confirm(f"Type the run key to close it ({run['run_key']}): ").strip() != run["run_key"]:
        print("Not closed.")
        return None
    result = close_run(customer, month, reason, lock_holder())
    print(f"Run {run['run_key']} closed: {CLOSED_PREFIX}{reason}")
    for decision_id, kind, answer in result["decisions"]:
        print(f"  #{decision_id} {kind} -> {answer}")
    print(f"  Tasks stopped: {', '.join(result['tasks']) or 'none'}")
    print(f"Next: python orchestrator.py load {next_revision(customer, month)}")
    return result


def next_revision(customer: str, month: str) -> str:
    """The first revision of month with no run yet: '2026-10' -> '2026-10-r2' (or -r3 if r2 exists)."""
    base = check_month(month).split("-r")[0]
    number = 2
    while get_run(run_key_for(customer, f"{base}-r{number}")):
        number += 1
    return f"{base}-r{number}"


# ================= RUN =================

USAGE = """Usage:
  python orchestrator.py selftest
  python orchestrator.py load <YYYY-MM>
  python orchestrator.py status [YYYY-MM]
  python orchestrator.py next [YYYY-MM]
  python orchestrator.py decisions
  python orchestrator.py answer <decision_id | task id like T11> <answer> ["note"]
  python orchestrator.py run <YYYY-MM> [--dry [TASK] | --until-wait]
  python orchestrator.py review <YYYY-MM>
  python orchestrator.py unlock <YYYY-MM>
  python orchestrator.py close <YYYY-MM> "reason"
  <YYYY-MM> may be a revision: 2026-10-r2"""

if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        args = sys.argv[1:]
        command = args[0] if args else ""
        try:
            if command == "selftest":
                from orchestrator_selftest import selftest      # the selftests live in their own file
                sys.exit(0 if selftest() else 1)
            elif command == "load" and len(args) == 2:
                cmd_load(args[1])
            elif command == "status" and len(args) <= 2:
                show_status(find_run(current_customer(), args[1] if len(args) == 2 else None))
            elif command == "next" and len(args) <= 2:
                show_next(find_run(current_customer(), args[1] if len(args) == 2 else None))
            elif command == "run" and len(args) in (3, 4) and args[2] == "--dry":
                cmd_dry(current_customer(), check_month(args[1]), args[3] if len(args) == 4 else None)
            elif command == "run" and len(args) == 2:
                cmd_run(current_customer(), check_month(args[1]))
            elif command == "run" and len(args) == 3 and args[2] == "--until-wait":
                cmd_run_until_wait(current_customer(), check_month(args[1]))
            elif command == "review" and len(args) == 2:
                cmd_review(current_customer(), check_month(args[1]))
            elif command == "unlock" and len(args) == 2:
                cmd_unlock(current_customer(), check_month(args[1]))
            elif command == "close" and len(args) == 3:
                cmd_close(current_customer(), args[1], args[2])
            elif command == "decisions":
                cmd_decisions(current_customer())
            elif command == "answer" and len(args) in (3, 4) and args[1].isdigit():
                cmd_answer(current_customer(), int(args[1]), args[2], args[3] if len(args) == 4 else None)
            elif command == "answer" and len(args) in (3, 4) and re.fullmatch(r"T\d+", args[1]):
                cmd_answer_task(current_customer(), args[1], args[2], args[3] if len(args) == 4 else None)
            else:
                print(USAGE)
                sys.exit(1)
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli)
