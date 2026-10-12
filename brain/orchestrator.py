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
from contextlib import contextmanager
from datetime import datetime, timezone

from plan_approval import describe, read_proof
from quickicp import TEST_MODE, ToolError, load_json
from state_store import (
    ALLOWED_ANSWERS, answer_decision, add_tasks, check_customer_key, create_run, current_customer,
    delete_test_run, force_unlock, get_decisions, get_run, get_runs, get_tasks, list_open_decisions, lock_holder,
    now_iso, open_decision, ready_tasks, record_decision, release_run_lock, renew_run_lock, take_run_lock, update_run,
    update_task,
)
from runner_kit import RunnerContext, RunnerResult
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
    if own_decisions[decision_id]["kind"] in NOTE_REQUIRED_KINDS and not (reason or "").strip():
        raise ToolError(f'STOPPED: a note is required, e.g. answer {decision_id} done "meeting held 10 Oct"')
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

# Tool tasks: (agent, output_type) -> runner name. A runner is wired when it is in WIRED_RUNNERS.
TOOL_ROUTES = {
    ("QuickICP", "accounts"): "quickicp_accounts",   # company_loop -> save_companies
    ("QuickICP", "contacts"): "quickicp_contacts",   # people_loop, emails, phones -> save_people
    ("Email Agent", "emails"): "email",              # email_tool launch -> samples -> approve
    ("Quickhowl", "calls"): "calls",                 # quickhowl launch -> start -> results
}
# Wired runners: name -> (runner function, toolset factory). The selftest swaps the factory for fakes.
WIRED_RUNNERS: dict = {
    "quickicp_accounts": (run_quickicp_accounts, lambda ctx: RealAccountsTools(ctx.run["run_key"], ctx.task["task_id"])),
    "quickicp_contacts": (run_quickicp_contacts, lambda ctx: RealContactsTools(ctx.run["run_key"], ctx.task["task_id"])),
    "email": (run_email, lambda ctx: RealEmailTools()),
    "calls": (run_calls, lambda ctx: RealCallTools(ctx.run["run_key"], ctx.task["task_id"])),
}
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
                       selftest_email(), selftest_calls(), selftest_5a(), selftest_5b(), selftest_close()]
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
        return outreach_fixture(customer, month, "Email Agent", "emails", [r["id"] for r in rows])

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
    real_load_json = email_tool.load_json
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
            write([content("s", "Feel free to reply.", ["free"]), content("s", "Feel free to write back.", ["free"])], asked)
        except ToolError as error:
            message = str(error)
        expect("E8b 'feel free' twice -> STOPPED naming the email, field and sentence",
               message.startswith("STOPPED: the reference emails still contain never_say phrases ['free']")
               and "'free' in email 1 body:" in message and "Feel free to write back" in message)
        expect("E8b the rewrite request quotes the exact sentence and the whole-word rule",
               len(asked) == 2 and "Feel free to reply" in asked[1] and "whole words" in asked[1])
        many = content("s", "ok", ["Cheapest", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8"])
        kept = email_tool.enforce_rules(many).objective.never_say
        expect("E8b our 4 fixed phrases come FIRST and are never cut (10 max, 'Cheapest' once)",
               kept[:4] == email_tool.NEVER_SAY_DEFAULTS and len(kept) == email_tool.MAX_PHRASES
               and [p.lower() for p in kept].count("cheapest") == 1)
        expect("E8 no file in data/ was read", files_read == [])
        os.environ.pop("CUSTOMER_DISPLAY_NAME")
        expect("E8 CUSTOMER_DISPLAY_NAME missing -> STOPPED", raises(email_tool.display_name))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["email"] = real_entry
        email_tool.call_tool = real_call_tool
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

        # Q13. check_system: Quickhowl DND PASS when true, WARN when missing; real calls 'off until go-live'
        import check_system
        saved_dnd = os.environ.get("QUICKHOWL_DND_HANDLED")
        try:
            lines = {}
            for value in ("true", None):
                if value is None:
                    os.environ.pop("QUICKHOWL_DND_HANDLED", None)
                else:
                    os.environ["QUICKHOWL_DND_HANDLED"] = value
                check_system.results.clear()
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    check_system.check_quickhowl()
                lines[value] = captured.getvalue()
            expect("Q13 check_system: 'Quickhowl DND' PASS with true, WARN when missing; 'Real calls' off until go-live",
                   "PASS  Quickhowl DND" in lines["true"] and "WARN  Quickhowl DND" in lines[None]
                   and "off until go-live" in lines["true"])
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
        expect("R11 code: negated claims and suggested actions are fine; evidence in RESULTS allows the claim",
               unsupported_claims(text("No demo was booked yet", [action("Get a demo booked with the prospect")]), {}) == []
               and unsupported_claims(text("A demo was scheduled", []), {"outcome": "demo_scheduled"}) == []
               and len(unsupported_claims(text("A demo was scheduled", []), {"outcome": "interested"})) == 1)
        ask = fake_ask([text("A demo was scheduled with the interested prospect", [action("Do Z")]),
                        text("One prospect said they are interested", [action("Do Z")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R11 'demo was scheduled' (outcome only 'interested') -> one retry naming it -> the second text is saved",
               out["llm_calls"] == 3 and "A demo was scheduled" in ask.review_prompts[1]
               and out["text"].what_worked == ["One prospect said they are interested"])
        answer_open_actions(run)
        ask = fake_ask([text("Meeting booked with the prospect", [action("Do Z")]),
                        text("We signed a deal this week", [action("Do Z")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R12 claims twice -> numbers-only review: no text, no actions, the note says 'claims not in the data'",
               out["text"] is None and out["action_ids"] == [] and "claims not in the data" in out["note"]
               and "We signed a deal this week" in out["note"])
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
    args = sys.argv[1:]
    command = args[0] if args else ""
    try:
        if command == "selftest":
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
