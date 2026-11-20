"""
auto_approve.py
===============

Timed auto-approval of routine gate decisions (opt-in per project).

A project may set "auto-approve gates after N seconds"
(revenueos_projects.auto_approve_seconds, read by supabase_store.auto_approve_seconds). When it is set,
a routine gate decision that has been OPEN longer than that window is answered automatically, so the run
does not park in the platform's "Needs a person" column while no one objects. When it is unset the
behaviour is unchanged (every gate waits for a person).

Only ROUTINE gates are auto-answered, and each with its safe answer:
  - human_task (a person's work: CAM, Content & Training, tele-calling, field visit) -> "skip"
        never "done": "done" would claim work a person never actually did. "skip" marks it done-but-
        skipped so the pipeline is not blocked, without pretending the work happened.
  - human_gate_task (a content / readiness / approval gate)                          -> "done" (approved)
  - email_approve (send the sample emails)                                           -> "yes"
  - call_start (start calling the contacts)                                          -> "yes"

Exception decisions are NEVER auto-answered -- they need real judgement:
  goal_check / goal_check_final (target missed: continue / more / stop),
  paid_call_unknown (a paid call may or may not have gone through),
  task_failed (a tool kept failing: retry / skip / stop).

This module is pure (only `datetime`): the orchestrator's run loop (orch/run.py, auto_resolve_overdue)
reads the per-project window and calls auto_answer_for for each open decision, then answers the ones it
returns via state_store.answer_decision. Kept dependency-free so it runs in a plain selftest.
"""

from datetime import datetime, timezone

# Routine gate kind -> the answer to auto-apply. Any kind not here is left for a human.
AUTO_APPROVE_ANSWERS = {
    "human_task": "skip",
    "human_gate_task": "done",
    "email_approve": "yes",
    "call_start": "yes",
}


def decision_age_seconds(created_at, now: datetime) -> float:
    """
    Seconds since a decision was opened. An unknown or unparseable timestamp returns 0.0, so such a
    decision is treated as brand-new and is NOT auto-approved yet (fail-safe toward asking a human).
    """
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return max(0.0, (now - created).total_seconds())
    except (ValueError, TypeError):
        return 0.0


def auto_answer_for(decision: dict, timeout_seconds: int, now: datetime) -> str | None:
    """
    The answer to auto-apply to one OPEN decision, or None to leave it for a human. Pure.
    Returns a value only when: the kind is a routine gate, that answer is actually offered by the
    decision, and the decision has been open at least `timeout_seconds`.
    """
    if not timeout_seconds or timeout_seconds <= 0:
        return None
    answer = AUTO_APPROVE_ANSWERS.get(decision.get("kind"))
    if not answer:
        return None
    offered = (decision.get("question") or {}).get("allowed_answers")
    if offered and answer not in offered:
        return None
    if decision_age_seconds(decision.get("created_at"), now) < timeout_seconds:
        return None
    return answer


def selftest() -> None:
    """Pure checks: no database, no LLM, no paid tool. Run: python auto_approve.py selftest"""
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    old = "2026-01-01T11:00:00+00:00"   # 1 hour before now
    fresh = "2026-01-01T11:59:58+00:00"  # 2 seconds before now

    def check(label: str, condition: bool) -> None:
        if not condition:
            raise AssertionError(label)
        print(f"PASS  {label}")

    # Each routine gate, once overdue, gets its safe answer.
    check("human_task overdue -> skip", auto_answer_for({"kind": "human_task", "created_at": old}, 5, now) == "skip")
    check("human_gate_task overdue -> done", auto_answer_for({"kind": "human_gate_task", "created_at": old}, 5, now) == "done")
    check("email_approve overdue -> yes", auto_answer_for({"kind": "email_approve", "created_at": old}, 5, now) == "yes")
    check("call_start overdue -> yes", auto_answer_for({"kind": "call_start", "created_at": old}, 5, now) == "yes")

    # Exception decisions are never auto-answered.
    for kind in ("goal_check", "goal_check_final", "paid_call_unknown", "task_failed", "plan_approval"):
        check(f"{kind} is left for a human", auto_answer_for({"kind": kind, "created_at": old}, 5, now) is None)

    # A decision younger than the window is left alone.
    check("fresh decision waits", auto_answer_for({"kind": "human_task", "created_at": fresh}, 5, now) is None)

    # 5-second window: a decision exactly 5s old is due.
    five = "2026-01-01T11:59:55+00:00"
    check("exactly 5s old with a 5s window -> due", auto_answer_for({"kind": "call_start", "created_at": five}, 5, now) == "yes")

    # Off (window unset / <= 0) never auto-answers.
    check("window 0 -> off", auto_answer_for({"kind": "human_task", "created_at": old}, 0, now) is None)

    # An answer the decision does not offer is not forced.
    check("not forced when the answer is not offered",
          auto_answer_for({"kind": "email_approve", "created_at": old, "question": {"allowed_answers": ["no", "feedback"]}}, 5, now) is None)
    check("allowed when the answer is offered",
          auto_answer_for({"kind": "email_approve", "created_at": old, "question": {"allowed_answers": ["yes", "no"]}}, 5, now) == "yes")

    # A missing / unparseable timestamp is treated as brand-new (ask a human).
    check("unknown timestamp waits", auto_answer_for({"kind": "human_task", "created_at": None}, 5, now) is None)
    check("junk timestamp waits", auto_answer_for({"kind": "human_task", "created_at": "not-a-date"}, 5, now) is None)

    print("\nauto_approve selftest: all checks passed.")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        selftest()
    else:
        print("usage: python auto_approve.py selftest")
