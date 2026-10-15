"""
orch/load.py
============

Load an approved plan as a run (plan checks in code), and the next free revision of a month.
Moved unchanged from orchestrator.py (Stage C, 2026-10-15). Import it through orchestrator, which
re-exports every name; this file never imports orchestrator.
"""

import os
from plan_approval import describe, read_proof
from quickicp import TEST_MODE, ToolError, load_json
from state_store import (
    add_tasks, create_run, current_customer, get_run, get_tasks, record_decision, update_run,
)
from orch.common import PLAN_FILE, REVENUE_FILE, UNDERSTANDING_FILE, WORK_ORDERS_FILE, approval_of, check_month, run_key_for


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


def next_revision(customer: str, month: str) -> str:
    """The first revision of month with no run yet: '2026-10' -> '2026-10-r2' (or -r3 if r2 exists)."""
    base = check_month(month).split("-r")[0]
    number = 2
    while get_run(run_key_for(customer, f"{base}-r{number}")):
        number += 1
    return f"{base}-r{number}"
