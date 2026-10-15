"""
orch/close.py
=============

Close a run so a new plan for the same month can replace it.
Moved unchanged from orchestrator.py (Stage C, 2026-10-15). Import it through orchestrator, which
re-exports every name; this file never imports orchestrator.
"""

from quickicp import ToolError
from state_store import (
    ALLOWED_ANSWERS, answer_decision, get_run, get_tasks, list_open_decisions, lock_holder, now_iso,
    release_run_lock, take_run_lock, update_run, update_task,
)
from orch.common import check_month, find_run
from orch.load import next_revision
from orch.run import HUMAN_AGENTS


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
