"""
platform_approval.py
====================

Lets a person approve the month plan IN THE PLATFORM instead of at the keyboard, so a plan can
be written in the background. Used by master_agent.py when it was started for a platform request
(run_for.py sets BRAIN_REQUEST_ID); without that setting nothing here is used and the typed
prompt works exactly as before.

WHY
---
master_agent.py stops and waits for a typed answer ("yes", changes, or "override" + a reason).
A background process has no keyboard. plan_approval.ask_approval() already takes the function
that supplies the answer; PlatformReader is that function. It publishes the plan to the request
row, waits for the answer recorded in the platform, and returns it as the same typed text.

THE RULES DO NOT CHANGE
-----------------------
    Every answer still goes through plan_approval.decide(), here first:
        yes        refused while plan rules are broken
        override   only when rules are broken, and only with a reason
        changes    sent to the brain, which plans again
    A refused answer never reaches the graph: its reason is shown in the platform and the reader
    keeps waiting. The proof file is written by master_agent.py exactly as before; approved_by is
    the platform user who approved.

THE REQUEST ROW (revenueos_brain_requests; see supabase/migrations/0003_project_brain_key.sql)
---------------
    This is the ONE revenueos_* table the brain writes to. Status:
        requested          the platform asked for a month plan
        planning           the brain is reading the plan and writing the month plan
        awaiting_approval  the month plan is published; waiting for a person
        finishing          approved; writing work orders, checking and loading the run
        loaded             the run exists; the orchestrator takes over
        failed / cancelled

RUN
---
    python platform_approval.py selftest       fake request store: no network, no LLM, no waiting
"""

import os
import re
import time

REQUESTS = "revenueos_brain_requests"
ACTIVE = ("requested", "planning", "awaiting_approval", "finishing")
POLL_SECONDS = 10
HEARTBEAT_SECONDS = 60
WAIT_HOURS = 72                      # an unanswered plan is given up after this long
PENDING_APPROVER = "platform (pending)"
APPROVER_MAX = 80                    # same limit as plan_approval.APPROVER_MAX


# Every month plan must use all four connected tools, in this order: find companies -> find people
# (email + phone) -> email campaign and AI calls; and no task may be one that can never run. Such a
# plan is never approved, not even with "override". The platform's lib/brain/requests.ts
# (compulsoryProblems) has the same rule.
NEEDS_PEOPLE = ("Quickhowl", "Email Agent")
REQUIRED_STEPS = (("QuickICP", "accounts", "find companies (QuickICP)"),
                  ("QuickICP", "contacts", "find people with email and phone (QuickICP contacts)"),
                  ("Email Agent", "emails", "an email campaign (Email Agent)"),
                  ("Quickhowl", "calls", "AI calls (Quickhowl)"))
COMPULSORY_ERRORS = re.compile(r"has no number\. Tool tasks run by a count|which does not exist|"
                               r"is not enabled for this customer|only has:")


def compulsory_problems(plan: dict | None, errors: list[str]) -> list[str]:
    """One sentence per compulsory problem of a published month plan; empty when it may be approved."""
    tasks = {str(t.get("task_id")): t for t in ((plan or {}).get("tasks") or []) if isinstance(t, dict)}

    def comes_after(task: dict, agent: str, output_type: str) -> bool:
        seen, stack = set(), list(task.get("depends_on") or [])
        while stack:
            task_id = str(stack.pop())
            if task_id in seen:
                continue
            seen.add(task_id)
            before = tasks.get(task_id)
            if not before:
                continue
            if before.get("agent") == agent and before.get("output_type") == output_type:
                return True
            stack.extend(before.get("depends_on") or [])
        return False

    def finds_people_before(task: dict) -> bool:
        return comes_after(task, "QuickICP", "contacts")

    def has(agent: str, output_type: str) -> bool:
        return any(t.get("agent") == agent and t.get("output_type") == output_type for t in tasks.values())

    steps = [f"The plan has no step to {words}. Every month plan must have it."
             for agent, output_type, words in REQUIRED_STEPS if not has(agent, output_type)]
    order = [f"{task_id} (find people) must come after the step that finds companies."
             for task_id, task in tasks.items()
             if task.get("agent") == "QuickICP" and task.get("output_type") == "contacts"
             and has("QuickICP", "accounts") and not comes_after(task, "QuickICP", "accounts")]
    missing = [f"{task_id} ({'email campaign' if task.get('agent') == 'Email Agent' else 'AI calls'}) has no earlier "
               "step that finds people's email and phone. A QuickICP contacts task must come before it."
               for task_id, task in tasks.items() if task.get("agent") in NEEDS_PEOPLE and not finds_people_before(task)]
    return steps + order + missing + [error for error in errors or [] if COMPULSORY_ERRORS.search(error)]


class Stop(Exception):
    """Planning must stop (cancelled in the platform, or no answer in time)."""


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ================= THE REQUEST STORE =================

class Requests:
    """Read and write request rows. Every write names the statuses it may start from."""

    def __init__(self, client):
        self._client = client

    def get(self, request_id: str) -> dict | None:
        rows = self._client.table(REQUESTS).select("*").eq("id", request_id).execute().data or []
        return rows[0] if rows else None

    def requested(self) -> list[dict]:
        """Requests nobody has picked up yet, oldest first."""
        rows = (self._client.table(REQUESTS).select("id, project_id, customer_key, failed_step, created_at")
                .eq("status", "requested").execute().data) or []
        return sorted(rows, key=lambda row: row.get("created_at") or "")

    def in_progress(self, worker: str) -> list[dict]:
        """Requests this worker had claimed and not finished (found after a restart)."""
        return (self._client.table(REQUESTS).select("id, customer_key, status").eq("worker", worker)
                .in_("status", ["planning", "awaiting_approval", "finishing"]).execute().data) or []

    def update(self, request_id: str, fields: dict, only_from: tuple[str, ...] = ACTIVE) -> bool:
        """Change a request, but only if it is still in one of 'only_from'. True if it was changed."""
        rows = (self._client.table(REQUESTS).update(fields).eq("id", request_id)
                .in_("status", list(only_from)).execute().data) or []
        return bool(rows)

    def claim(self, request_id: str, worker: str) -> bool:
        """requested -> planning, for exactly one worker."""
        return self.update(request_id, {"status": "planning", "worker": worker, "heartbeat_at": utc_now(),
                                        "error": None, "brain_message": None}, only_from=("requested",))


# ================= STEPS THAT BUY DATA =================

PAID_STEPS = "revenueos_brain_paid_steps"
PAID_OPEN = ("waiting", "approved", "running")


class PaidSteps:
    """
    The worker never starts a task that buys data on its own. It OFFERS the step here; a person
    starts it in the platform; the worker then runs that one step.
        waiting -> approved (by the platform) -> running -> done | failed;  withdrawn = no longer next
    """

    def __init__(self, client):
        self._client = client

    def offer(self, project_id: str, customer: str, month: str, task_id: str) -> bool:
        """Show the step in the platform, once. True if a new offer was made."""
        open_rows = (self._client.table(PAID_STEPS).select("id").eq("customer_key", customer).eq("month", month)
                     .eq("task_id", task_id).in_("status", list(PAID_OPEN)).execute().data) or []
        if open_rows:
            return False
        self._client.table(PAID_STEPS).insert({"project_id": project_id, "customer_key": customer,
                                               "month": month, "task_id": task_id}).execute()
        return True

    def approved(self) -> list[dict]:
        """Steps a person has started in the platform and no worker has taken yet, oldest first."""
        rows = (self._client.table(PAID_STEPS).select("id, customer_key, month, task_id, created_at")
                .eq("status", "approved").execute().data) or []
        return sorted(rows, key=lambda row: row.get("created_at") or "")

    def update(self, step_id: str, fields: dict, only_from: tuple[str, ...]) -> bool:
        rows = (self._client.table(PAID_STEPS).update(fields).eq("id", step_id)
                .in_("status", list(only_from)).execute().data) or []
        return bool(rows)

    def withdraw(self, customer: str) -> None:
        """The run moved on some other way: an offer nobody answered is no longer the next step."""
        (self._client.table(PAID_STEPS).update({"status": "withdrawn"}).eq("customer_key", customer)
         .eq("status", "waiting").execute())

    def available(self) -> bool:
        """False until supabase/migrations/0007_brain_paid_steps.sql has been run."""
        try:
            self._client.table(PAID_STEPS).select("id").limit(1).execute()
            return True
        except Exception:
            return False


def connect() -> Requests:
    """The request store, from SUPABASE_URL + SUPABASE_KEY. Imported here so 'selftest' needs no packages."""
    from supabase import create_client
    return Requests(create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")))


# ================= WHAT IS PUBLISHED =================

def plain(value):
    """A pydantic object (or anything else) as plain JSON data."""
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def plan_payload(state: dict, errors: list[str]) -> dict:
    """The month plan as the platform shows it. Copied from the graph state; nothing is added."""
    return {"plan": plain(state.get("month_plan")), "revenue_analysis": plain(state.get("revenue_analysis")),
            "plan_errors": list(errors or [])}


# ================= THE READER =================

class PlatformReader:
    """
    read(prompt) -> text, for plan_approval.ask_approval(errors, read=...).

    Publishes the plan, then waits for the answer recorded in the platform and returns it as the
    text a person would have typed. Refused answers are reported back and never returned.
    """

    def __init__(self, requests, request_id: str, payload: dict, errors: list[str], decide,
                 sleep=time.sleep, clock=time.time, poll_seconds: float = POLL_SECONDS,
                 wait_hours: float = WAIT_HOURS):
        self._requests, self._id = requests, request_id
        self._payload, self._errors, self._decide = payload, list(errors or []), decide
        self._sleep, self._clock = sleep, clock
        self._poll, self._deadline = poll_seconds, clock() + wait_hours * 3600
        self._queued: list[str] = []          # the override reason, handed over on the next call
        self._compulsory = compulsory_problems(payload.get("plan"), self._errors)
        self._published = False
        self.approved_by: str | None = None   # who approved, for the proof

    def _publish(self, message: str | None) -> None:
        """Show the plan (and why the last answer was refused, if it was) and clear any old answer."""
        fields = {**self._payload, "status": "awaiting_approval", "brain_message": message,
                  "answer": None, "answer_text": None, "answered_by": None, "answered_at": None,
                  "heartbeat_at": utc_now()}
        if not self._requests.update(self._id, fields, only_from=("planning", "awaiting_approval")):
            self._check_still_wanted()
        self._published = True

    def _check_still_wanted(self) -> dict:
        request = self._requests.get(self._id)
        if not request or request.get("status") == "cancelled":
            raise Stop("STOPPED: this request was cancelled in the platform. Nothing was approved.")
        return request

    def __call__(self, prompt: str = "") -> str:
        if self._queued:
            return self._queued.pop(0)
        if not self._published:
            self._publish(None)

        last_heartbeat = self._clock()
        while True:
            request = self._check_still_wanted()
            answer = (request.get("answer") or "").strip()
            if not answer:
                if self._clock() > self._deadline:
                    self._requests.update(self._id, {"status": "failed", "failed_step": "plan",
                                                     "error": "Nobody answered the month plan in time."})
                    raise Stop("STOPPED: no answer to the month plan in time. Nothing was approved.")
                if self._clock() - last_heartbeat >= HEARTBEAT_SECONDS:
                    self._requests.update(self._id, {"heartbeat_at": utc_now()}, only_from=("awaiting_approval",))
                    last_heartbeat = self._clock()
                self._sleep(self._poll)
                continue

            text = (request.get("answer_text") or "").strip()
            who = ((request.get("answered_by") or "").strip() or "platform user")[:APPROVER_MAX]
            typed = {"yes": "yes", "override": "override"}.get(answer, text)
            choice = self._decide(typed, self._errors)

            if self._compulsory and choice.action in ("approve", "override"):
                # A task in this plan could never run: it is changed, never approved (not even anyway).
                self._publish("This plan is missing a compulsory step and cannot be approved: "
                              + " ".join(self._compulsory[:3]) + " Ask for changes.")
                continue
            if answer == "changes" and choice.action != "feedback":
                # "yes" or "override" sent as changes must never be read as an approval.
                self._publish("Describe the changes in a sentence. Nothing was approved.")
                continue
            if choice.action == "override" and not text:
                self._publish("An override needs a reason. Nothing was approved.")
                continue
            if choice.action == "refuse":
                self._publish(choice.message)      # shown in the platform; keep waiting
                continue
            if choice.action == "feedback":
                self._requests.update(self._id, {"status": "planning", "answer": None, "answer_text": None,
                                                 "brain_message": f"Changes from {who} sent to the brain; it is planning again."},
                                      only_from=("awaiting_approval",))
                return typed

            # approve, or override with a reason
            self.approved_by = who
            self._requests.update(self._id, {"status": "finishing", "approved_by": who, "approved_at": utc_now(),
                                             "brain_message": None}, only_from=("awaiting_approval",))
            if choice.action == "override":
                self._queued.append(text)          # ask_approval asks for the reason next
                return "override"
            return "yes"


class Platform:
    """What master_agent.py uses when it was started for a platform request."""
    PENDING = PENDING_APPROVER

    def __init__(self, requests: Requests, request_id: str):
        self._requests, self._id = requests, request_id

    def reader(self, state: dict, errors: list[str]) -> PlatformReader:
        from plan_approval import decide           # the one set of answer rules
        return PlatformReader(self._requests, self._id, plan_payload(state, errors), errors, decide)


def from_environment() -> Platform | None:
    """The platform approval for this process, or None when it was not started for a request."""
    request_id = (os.getenv("BRAIN_REQUEST_ID") or "").strip()
    return Platform(connect(), request_id) if request_id else None


# ================= SELFTEST (no network, no LLM, no waiting) =================

class MemoryRequests:
    """An in-memory request store with the same methods and the same 'only_from' rule."""

    def __init__(self, row: dict):
        self.row, self.writes = dict(row), []

    def get(self, request_id):
        return dict(self.row) if self.row.get("id") == request_id else None

    def update(self, request_id, fields, only_from=ACTIVE):
        if self.row.get("id") != request_id or self.row.get("status") not in only_from:
            return False
        self.row.update(fields)
        self.writes.append(dict(fields))
        return True


def expect(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"FAIL: {label}")
    print(f"PASS  {label}")


def selftest() -> None:
    from collections import namedtuple
    Choice = namedtuple("Choice", "action message")

    def decide(answer, errors):                    # the same rules as plan_approval.decide
        word = (answer or "").strip().lower()
        if not word:
            return Choice("refuse", "empty")
        if word in ("yes", "y", "approve", "approved"):
            return Choice("refuse", f"{len(errors)} plan rule(s) are still broken") if errors else Choice("approve", "ok")
        if word == "override":
            return Choice("override", "reason required") if errors else Choice("refuse", "no override needed")
        return Choice("feedback", "changes sent")

    class Model:
        def __init__(self, data):
            self._data = data
        def model_dump(self, mode="json"):
            return self._data

    # A complete plan: all four tool steps in order, so only the answer rules are being tested here.
    complete = [{"task_id": "T1", "agent": "QuickICP", "output_type": "accounts", "depends_on": []},
                {"task_id": "T2", "agent": "QuickICP", "output_type": "contacts", "depends_on": ["T1"]},
                {"task_id": "T3", "agent": "Email Agent", "output_type": "emails", "depends_on": ["T2"]},
                {"task_id": "T4", "agent": "Quickhowl", "output_type": "calls", "depends_on": ["T2"]}]
    state = {"month_plan": Model({"month": "Month 1", "tasks": complete}),
             "revenue_analysis": Model({"revenue_goal": "2 crore"})}
    expect("the plan is published as plain data, with the rule errors",
           plan_payload(state, ["rule A"]) == {"plan": {"month": "Month 1", "tasks": complete},
                                               "revenue_analysis": {"revenue_goal": "2 crore"}, "plan_errors": ["rule A"]})

    def reader_for(row, errors, answers):
        """A reader over an in-memory request; 'answers' are recorded one per poll, like a person would."""
        store = MemoryRequests({"id": "r1", "status": "planning", **row})
        pending, slept = list(answers), []
        def sleep(seconds):
            slept.append(seconds)
            if pending:
                store.row.update(pending.pop(0))
        return store, PlatformReader(store, "r1", plan_payload(state, errors), errors, decide, sleep=sleep), slept

    # approve a clean plan
    store, read, slept = reader_for({}, [], [{"answer": "yes", "answered_by": "Asha Rao"}])
    expect("a plan with no broken rules is approved by 'yes'", read("Approve?") == "yes")
    expect("it waited for the person, then moved to 'finishing' with their name",
           slept == [POLL_SECONDS] and store.row["status"] == "finishing" and store.row["approved_by"] == "Asha Rao"
           and read.approved_by == "Asha Rao")
    expect("the plan was published before waiting",
           store.writes[0]["status"] == "awaiting_approval" and store.writes[0]["plan"]["month"] == "Month 1")

    # 'yes' refused while rules are broken, then a real override
    store, read, _ = reader_for({}, ["rule A"], [{"answer": "yes", "answered_by": "Asha"},
                                                 {"answer": "override", "answer_text": "  ", "answered_by": "Asha"},
                                                 {"answer": "override", "answer_text": "agreed with BD", "answered_by": "Asha"}])
    first = read("Approve?")
    expect("'yes' is refused while rules are broken, and an override without a reason is refused too",
           first == "override" and store.row["status"] == "finishing")
    expect("the refusals were shown in the platform, and the old answer was cleared each time",
           [w.get("brain_message") for w in store.writes if w.get("status") == "awaiting_approval"]
           == [None, "1 plan rule(s) are still broken", "An override needs a reason. Nothing was approved."])
    expect("the override reason is handed over when ask_approval asks for it",
           read("Reason for override (required): ") == "agreed with BD")

    # changes -> planning again
    store, read, _ = reader_for({}, [], [{"answer": "changes", "answer_text": "plan: fewer calls in week 1", "answered_by": "Asha"}])
    expect("changes are returned as typed text and the request goes back to 'planning'",
           read("Approve?") == "plan: fewer calls in week 1" and store.row["status"] == "planning"
           and store.row["answer"] is None and read.approved_by is None)

    # 'yes' typed into the changes box is not an approval
    store, read, _ = reader_for({}, [], [{"answer": "changes", "answer_text": "yes", "answered_by": "A"},
                                         {"answer": "changes", "answer_text": "plan: add Pune", "answered_by": "A"}])
    expect("'yes' sent as changes is never read as an approval",
           read("Approve?") == "plan: add Pune" and read.approved_by is None and store.row["status"] == "planning")

    # override refused when nothing is broken
    store, read, _ = reader_for({}, [], [{"answer": "override", "answer_text": "why not", "answered_by": "A"},
                                         {"answer": "yes", "answered_by": "A"}])
    expect("an override is refused when no rule is broken; a later 'yes' approves", read("Approve?") == "yes")

    # cancelled while waiting
    store, read, _ = reader_for({}, [], [{"status": "cancelled"}])
    try:
        read("Approve?")
        cancelled = False
    except Stop as stop:
        cancelled = "cancelled" in str(stop)
    expect("cancelling in the platform stops planning without approving", cancelled and read.approved_by is None)

    # nobody answers
    clock = [0.0]
    store = MemoryRequests({"id": "r1", "status": "planning"})
    read = PlatformReader(store, "r1", plan_payload(state, []), [], decide, wait_hours=1,
                          sleep=lambda seconds: clock.__setitem__(0, clock[0] + 1800), clock=lambda: clock[0])
    try:
        read("Approve?")
        timed_out = False
    except Stop:
        timed_out = store.row["status"] == "failed" and store.row["failed_step"] == "plan"
    expect("an unanswered plan is given up after the waiting time and marked failed", timed_out)

    # a compulsory step missing: never approved, not even with override; changes still go through
    plan = {"tasks": [{"task_id": "T1", "agent": "QuickICP", "output_type": "accounts", "depends_on": []},
                      {"task_id": "T4", "agent": "Quickhowl", "output_type": "calls", "depends_on": ["T1"]}]}
    expect("calls with no people-search step before them are a compulsory problem",
           any(p.startswith("T4 (AI calls) has no earlier step") for p in compulsory_problems(plan, [])))
    expect("a plan missing a tool step is a compulsory problem",
           any("no step to an email campaign" in p for p in compulsory_problems(plan, [])))
    fixed = {"tasks": plan["tasks"][:1] + [{"task_id": "T2", "agent": "QuickICP", "output_type": "contacts", "depends_on": ["T1"]},
                                           {"task_id": "T4", "agent": "Quickhowl", "output_type": "calls", "depends_on": ["T2"]},
                                           {"task_id": "T6", "agent": "Email Agent", "output_type": "emails", "depends_on": ["T2"]}]}
    expect("all four tools, in order, means nothing is compulsory", compulsory_problems(fixed, []) == [])
    loose = {"tasks": [{**t, "depends_on": []} if t["task_id"] == "T2" else t for t in fixed["tasks"]]}
    expect("people found before the companies are a compulsory problem",
           compulsory_problems(loose, []) == ["T2 (find people) must come after the step that finds companies."])
    expect("a tool task with no number is compulsory too",
           compulsory_problems(fixed, ["T4 (Quickhowl / calls) target 'x' has no number. Tool tasks run by a count: give one"]) != [])
    store = MemoryRequests({"id": "r1", "status": "planning"})
    answers = [{"answer": "override", "answer_text": "go anyway", "answered_by": "A"},
               {"answer": "changes", "answer_text": "plan: add a contacts task", "answered_by": "A"}]
    def feed(seconds):
        if answers:
            store.row.update(answers.pop(0))
    read = PlatformReader(store, "r1", {"plan": plan, "plan_errors": ["rule"]}, ["rule"], decide, sleep=feed)
    expect("approving anyway is refused while a compulsory step is missing; changes still go through",
           read("Approve?") == "plan: add a contacts task" and read.approved_by is None)
    expect("the refusal is shown in the platform",
           any("compulsory step" in (w.get("brain_message") or "") for w in store.writes))

    # the store's guard
    store = MemoryRequests({"id": "r1", "status": "cancelled"})
    expect("a cancelled request cannot be changed", store.update("r1", {"status": "planning"}) is False)
    expect("a long name is cut to the proof's limit", len(("x" * 200)[:APPROVER_MAX]) == 80)
    expect("without BRAIN_REQUEST_ID nothing here is used", os.environ.pop("BRAIN_REQUEST_ID", None) is None
           and from_environment() is None)
    print("\nSELFTEST PASSED: no network, no LLM, no waiting.")


if __name__ == "__main__":
    selftest()
