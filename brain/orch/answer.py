"""
orch/answer.py
==============

Record a person's answer to an open decision of THIS customer (note rules).
Moved unchanged from orchestrator.py (Stage C, 2026-10-15). Import it through orchestrator, which
re-exports every name; this file never imports orchestrator.
"""

from quickicp import ToolError
from state_store import answer_decision, get_decisions, get_runs, get_tasks, list_open_decisions
from orch.common import find_run
from orch.run import HUMAN_KINDS


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
# A note is required when a person answers these.
NOTE_REQUIRED_KINDS = HUMAN_KINDS | {"review_action_1", "review_action_2", "review_action_3"}
# ...and for these answers of other kinds (the note is what gets sent on). Same rules: lib/brain/answers.ts.
NOTE_REQUIRED_ANSWERS = {("email_approve", "feedback")}
MAX_NOTE_LENGTH = 1000
