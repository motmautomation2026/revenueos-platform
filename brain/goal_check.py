"""
goal_check.py
=============

One shared "goal check" used by every loop (companies, people, and later AI calling).

WHY
---
Each loop retries on its own (max 2 extra rounds). If it is STILL short of its
target after that, it must not silently move on. It stops and asks a human:

    GOAL CHECK: companies - 32 of 40 passed (short by 8) after 3 round(s)
      1  Continue with 32 (shortfall recorded for the CAM)
      2  Try 2 more rounds
      3  Stop here (results are saved)

The answer is saved in data/decisions.json, so there is always a record of who
decided what, and when. (Later this becomes the core.decisions table in Supabase.)

WHO DECIDES WHAT
----------------
    Code : whether the goal is met, how many "try more" are still allowed
    Human: continue / try more / stop

SETTINGS
--------
    ASK_USER = True    ask in the terminal (normal local runs)
    ASK_USER = False   never ask: always "continue" and record it as decided by "auto"
                       (for unattended runs later, e.g. Temporal workflows)
    If the terminal cannot take input (no keyboard), it also records "continue" as "auto".

USED BY
-------
    company_loop.py (now), people_loop.py and the AI calling step (next)

    from goal_check import ask_on_shortfall, StopRequested
    decision = ask_on_shortfall("companies", target=40, achieved=32, rounds_done=3,
                                can_retry=True, details={...})
    # decision is "continue", "more" or "stop"

WRITES
------
    data/decisions.json   list of every goal-check decision (appended, never overwritten)

DEBUGGING
---------
    - Not asked even though short? Check ASK_USER, and that the loop calls ask_on_shortfall.
    - decisions.json broken by a hand-edit: it is moved aside to decisions.json.bad and a
      new list is started (nothing crashes).
"""

import json
import os
from datetime import datetime, timezone

from quickicp import ToolError

ASK_USER = True
DECISIONS_FILE = "data/decisions.json"

CHOICES = {"1": "continue", "2": "more", "3": "stop"}


class StopRequested(ToolError):
    """The human chose 'stop' at a goal check. Results were saved before asking."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record_decision(entry: dict) -> None:
    """Append one decision to data/decisions.json (never loses earlier decisions)."""
    decisions = []
    if os.path.exists(DECISIONS_FILE):
        try:
            with open(DECISIONS_FILE, encoding="utf-8") as file:
                decisions = json.load(file)
            if not isinstance(decisions, list):
                raise ValueError("not a list")
        except (json.JSONDecodeError, ValueError):
            os.replace(DECISIONS_FILE, DECISIONS_FILE + ".bad")
            print(f"  Note: {DECISIONS_FILE} was broken; moved to {DECISIONS_FILE}.bad")
            decisions = []
    decisions.append(entry)
    os.makedirs(os.path.dirname(DECISIONS_FILE), exist_ok=True)
    with open(DECISIONS_FILE, "w", encoding="utf-8") as file:
        json.dump(decisions, file, indent=2, ensure_ascii=False)


def read_choice(can_retry: bool) -> tuple[str, str]:
    """Ask until a valid choice is typed. Returns (decision, decided_by)."""
    allowed = ["1", "2", "3"] if can_retry else ["1", "3"]
    while True:
        try:
            answer = input(f"Choose {' / '.join(allowed)}: ").strip()
        except EOFError:
            print("  No keyboard input available: continuing (recorded as auto).")
            return "continue", "auto"
        if answer in allowed:
            return CHOICES[answer], "user"
        print(f"  Please type {' or '.join(allowed)}.")


def ask_on_shortfall(step: str, target: int, achieved: int, rounds_done: int,
                     can_retry: bool, details: dict | None = None,
                     more_label: str = "Try 2 more rounds") -> str:
    """
    Show the shortfall, get a decision, record it, and return it:
    'continue', 'more' or 'stop'. 'more' is only offered when can_retry is True.
    """
    shortfall = max(0, target - achieved)
    print(f"\n========== GOAL CHECK: {step} ==========")
    print(f"{achieved} of {target} passed (short by {shortfall}) after {rounds_done} round(s)")
    if details:
        for key, value in details.items():
            print(f"  {key}: {value}")
    print(f"  1  Continue with {achieved} (shortfall recorded for the CAM)")
    if can_retry:
        print(f"  2  {more_label}")
    else:
        print("  -  (no more retries allowed for this step)")
    print("  3  Stop here (results are saved)")

    if ASK_USER:
        decision, decided_by = read_choice(can_retry)
    else:
        decision, decided_by = "continue", "auto"
        print("  ASK_USER is off: continuing (recorded as auto).")

    record_decision({
        "step": step,
        "target": target,
        "achieved": achieved,
        "shortfall": shortfall,
        "rounds_done": rounds_done,
        "decision": decision,
        "decided_by": decided_by,
        "decided_at": now_iso(),
        "details": details or {},
    })
    print(f"  Decision recorded: {decision} ({decided_by}) in {DECISIONS_FILE}")
    return decision