"""
orch/run.py
===========

The run loop: one step per 'run' (answers, human tasks, ONE tool runner, settle), --until-wait, --dry, review, unlock, and WIRED_RUNNERS (the one dict of runners every caller shares).
Moved unchanged from orchestrator.py (Stage C, 2026-10-15). Import it through orchestrator, which
re-exports every name; this file never imports orchestrator.
"""

from quickicp import ToolError
from state_store import (
    ALLOWED_ANSWERS, force_unlock, get_decisions, get_run, get_tasks, list_open_decisions, lock_holder,
    now_iso, open_decision, ready_tasks, record_decision, release_run_lock, renew_run_lock, take_run_lock,
    update_run, update_task,
)
from runner_kit import RunnerContext, RunnerResult, run_understanding
from runner_quickicp_accounts import RealAccountsTools, run_quickicp_accounts
from runner_quickicp_contacts import RealContactsTools, run_quickicp_contacts
from runner_email import RealEmailTools, run_email
from runner_calls import RealCallTools, run_calls
from orch.common import find_run, month_of


# ================= RUN (part 1: human tasks; tool runners plug in later) =================

# People do these tasks: the orchestrator opens a decision and the person answers it.
HUMAN_AGENTS = {"CAM", "Content & Training", "Human Tele Calling", "LinkedIn Agent", "Sales Engineer"}
# Gate tasks can never be skipped: only done or stop (code rule, enforced by the decision kind).
GATE_TYPES = {"data_approval", "content", "client_training", "readiness"}
HUMAN_KINDS = {"human_task", "human_gate_task"}

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
