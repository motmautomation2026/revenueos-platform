"""
orch/show.py
============

Where a run stands: status, next tasks, open decisions and how a question is printed.
Moved unchanged from orchestrator.py (Stage C, 2026-10-15). Import it through orchestrator, which
re-exports every name; this file never imports orchestrator.
"""

from plan_approval import describe
from state_store import ALLOWED_ANSWERS, get_decisions, get_runs, get_tasks, list_open_decisions, ready_tasks
from orch.common import approval_of


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
