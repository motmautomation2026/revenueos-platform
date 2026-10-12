"""
runner_kit.py
=============

Shared pieces for tool runners (one runner per tool task: quickicp_accounts now; quickicp_contacts,
email and calls later). The orchestrator calls a runner; the runner returns a RunnerResult.

WHY
---
One place enforces the reliability rules, so every runner gets them the same way:
    - SAVE BEFORE ACTING: every external call gets a brain_tool_calls row BEFORE it is made.
    - PAID calls (company search, later: people/email/phone search, launches): a row still
      'running' on the next run means we crashed during the call. It becomes 'unknown' and a human
      decides (paid_call_unknown: rerun / skip / stop). It is NEVER repeated automatically.
    - FREE calls (LLM, Prospeo suggestion lookups, judging, Supabase saves): recorded with
      paid = false. A crash during one simply repeats it. A finished one is reused, not repeated.
    - The run lock is renewed before every paid call.

FLOW (inside a runner)
----
    ctx.free_call("payload", fn)        -> fixed key: done once, reused on every later run
    ctx.free_call("adjust", fn, seq=n)  -> new key each time (repeatable, recorded)
    ctx.paid_call("search", fn, seq=n)  -> new key each time; 'running' row BEFORE the call
    ctx.paid_batch("people:r0:b2", ...) -> a batch a later run may reach again: finished -> replayed
    ctx.resendable_call(step, reference, ...) -> the tool de-duplicates by reference: a crashed or
                                           failed call resends the SAVED request (email, Quickhowl)
    return RunnerResult.done(...) / .waiting(kind, question) / .stopped(note) / .failed(error)

KEYS (brain_tool_calls.idempotency_key)
----
    <run_key>:<task_id>:<step>            e.g. anvixa-ai-product-2026-10:T3:payload
    <run_key>:<task_id>:<step>:<seq>      e.g. anvixa-ai-product-2026-10:T3:search:2

DRY MODE (python orchestrator.py run <YYYY-MM> --dry)
---------
    ctx.dry = True: free calls run but are NOT recorded, paid calls are refused, no lock.

DEBUGGING
---------
    "STOPPED: paid call refused in --dry mode"  -> a runner tried to buy in a dry run (bug in the runner)
    "STOPPED: paid call key ... already used"   -> a runner reused a sequence number (bug in the runner)
    "STOPPED: lost the run lock"                -> the lock expired and another run took it over
    See every call: Supabase table brain_tool_calls, filtered on idempotency_key like '<run_key>:T3:%'.
"""

from dataclasses import dataclass, field
from typing import Callable

from quickicp import ToolError
from state_store import finish_step, get_decisions, get_steps, renew_run_lock, start_step


@dataclass
class RunnerResult:
    """What a runner tells the orchestrator. Code decides which one."""
    kind: str                         # 'done' | 'waiting' | 'stopped' | 'failed' | 'running' | 'dry'
    achieved: int | None = None
    target: int | None = None
    output: dict = field(default_factory=dict)
    decision_kind: str | None = None  # for 'waiting': goal_check, goal_check_final, paid_call_unknown
    question: dict | None = None
    note: str | None = None           # for 'stopped' / 'failed' / 'dry'

    @classmethod
    def done(cls, achieved: int, target: int, output: dict, note: str) -> "RunnerResult":
        return cls("done", achieved, target, output, note=note)

    @classmethod
    def waiting(cls, kind: str, question: dict) -> "RunnerResult":
        return cls("waiting", decision_kind=kind, question=question)

    @classmethod
    def stopped(cls, note: str) -> "RunnerResult":
        return cls("stopped", note=note)

    @classmethod
    def failed(cls, error: str) -> "RunnerResult":
        return cls("failed", note=error)

    @classmethod
    def running(cls, note: str) -> "RunnerResult":
        """Still working (e.g. calls in progress): the task stays 'running', the next 'run' continues it."""
        return cls("running", note=note)


@dataclass
class RunnerContext:
    """What a runner gets: the run, its task, the lock holder, and the ledger helpers."""
    run: dict
    task: dict
    lock_holder: str | None           # None in --dry mode (no lock is taken)
    dry: bool = False

    def key(self, step: str, seq: int | None = None) -> str:
        """Idempotency key for one call of this task."""
        base = f"{self.run['run_key']}:{self.task['task_id']}:{step}"
        return base if seq is None else f"{base}:{seq}"

    def steps(self, step: str | None = None) -> list[dict]:
        """This task's brain_tool_calls rows (optionally one step name), oldest first."""
        rows = get_steps(self.task["id"])
        return [row for row in rows if step is None or row["step"] == step]

    def next_seq(self, step: str) -> int:
        """The next unused sequence number for a repeatable step."""
        return len(self.steps(step)) + 1

    def decisions(self, kind: str) -> list[dict]:
        """This task's decisions of one kind, oldest first."""
        return [d for d in get_decisions(self.run["id"]) if d["task_row_id"] == self.task["id"] and d["kind"] == kind]

    def free_call(self, step: str, fn: Callable[[], object], seq: int | None = None,
                  tool: str = "quickicp", request: dict | None = None):
        """
        A FREE, repeatable call (LLM, Prospeo lookups, judge, Supabase save), recorded with paid = false.
        Finished before -> its saved output is returned (not repeated). Crashed before -> repeated.
        """
        if self.dry:
            return fn()
        row, _ = start_step(self.task["id"], step, self.key(step, seq), tool, False, request)
        if row["status"] == "done":
            return row["output"]
        try:
            output = fn()
        except ToolError as error:
            finish_step(row["id"], "failed", error=str(error))
            raise
        finish_step(row["id"], "done", output=output)
        return output

    def paid_call(self, step: str, seq: int, fn: Callable[[], object], request: dict, tool: str = "quickicp"):
        """
        A PAID call. The 'running' row is saved BEFORE the call. A known failure (ToolError) is saved
        as 'failed'. A crash leaves it 'running' -> next run: 'unknown' + a human decides.
        """
        if self.dry:
            raise ToolError("STOPPED: paid call refused in --dry mode.")
        if self.lock_holder:
            renew_run_lock(self.run["id"], self.lock_holder)
        row, is_new = start_step(self.task["id"], step, self.key(step, seq), tool, True, request)
        if not is_new:
            raise ToolError(f"STOPPED: paid call key {self.key(step, seq)} already used; refusing to call again.")
        try:
            response = fn()
        except ToolError as error:
            finish_step(row["id"], "failed", error=str(error))
            raise
        finish_step(row["id"], "done", response={"items": response} if isinstance(response, list) else response)
        return response

    def resendable_call(self, step: str, key: str, fn: Callable[[], object], request: dict, tool: str):
        """
        A PAID call the TOOL itself de-duplicates (same reference + same content -> duplicate / already
        started): email launch + approve, Quickhowl store + start. Saved BEFORE the call under our
        reference. Finished before -> the saved reply (not called again). Crashed or failed before ->
        the SAVED request is sent again automatically (no human needed, nothing is bought twice).
        """
        if self.dry:
            raise ToolError("STOPPED: paid call refused in --dry mode.")
        if self.lock_holder:
            renew_run_lock(self.run["id"], self.lock_holder)
        row, _ = start_step(self.task["id"], step, key, tool, True, request)
        if row["status"] == "done":
            return row["response"]
        try:
            response = fn()
        except ToolError as error:
            finish_step(row["id"], "failed", error=str(error))
            raise
        finish_step(row["id"], "done", response=response)
        return response

    def paid_batch(self, base: str, fn: Callable[[], list], request: dict, skipped: list[str],
                   tool: str = "quickicp") -> list:
        """
        A PAID batch that a later run may reach again (after a crash, the same round is redone):
            a finished attempt of this batch -> its SAVED reply (never bought again)
            a batch a human chose to skip    -> [] (treated as empty)
            otherwise                        -> a new attempt: <base>:a<n>, saved BEFORE the call
        base: '<step>:<batch id>', e.g. 'people:r0:b2' or 'emails:b1'.
        """
        step, batch_id = base.split(":", 1)
        rows = [r for r in self.steps(step) if r["idempotency_key"].startswith(self.key(base) + ":a")]
        done = [r for r in rows if r["status"] == "done"]
        if done:
            return done[-1]["response"]["items"]
        if base in skipped:
            return []
        return self.paid_call(step, f"{batch_id}:a{len(rows) + 1}", fn, request, tool)


def contacts_people_ids(ctx: RunnerContext) -> list[int]:
    """The person ids recorded by this run's done QuickICP contacts task (T4). Stops if there are none."""
    from state_store import get_tasks
    done = [t for t in get_tasks(ctx.run["id"]) if t["agent"] == "QuickICP" and t["output_type"] == "contacts"
            and t["status"] == "done" and (t["result"] or {}).get("people_ids")]
    if not done:
        raise ToolError(f"STOPPED: {ctx.task['task_id']} needs the people ids of a done QuickICP contacts task "
                        "in this run (e.g. T4). Run that task first.")
    return done[-1]["result"]["people_ids"]


def target_for(task: dict, test_cap: int) -> int:
    """
    How many results a tool task needs (code):
        TEST_TARGET_OVERRIDE in .env (test mode only) -> that number
        TEST_MODE                                     -> min(target_number, test_cap)
        real                                          -> target_number
    """
    from quickicp import TEST_MODE
    import os
    override = (os.getenv("TEST_TARGET_OVERRIDE") or "").strip()
    if override:
        if not TEST_MODE:
            raise ToolError("STOPPED: TEST_TARGET_OVERRIDE is only allowed in TEST_MODE. Remove it from .env.")
        if not override.isdigit() or int(override) < 1:
            raise ToolError(f"STOPPED: TEST_TARGET_OVERRIDE must be a whole number > 0, got '{override}'.")
        return int(override)
    if task["target_number"] is None:
        raise ToolError(f"STOPPED: {task['task_id']} target has no number: '{task['work_order'].get('target')}'.")
    return min(task["target_number"], test_cap) if TEST_MODE else task["target_number"]
