"""
runner_calls.py
===============

Tool runner for Quickhowl 'calls' tasks (T7). Called by orchestrator.py 'run'.
Wraps quickhowl.py (same profile, same safety checks, same API calls) with the brain's rules.

FLOW (each step recorded in brain_tool_calls)
----
    1. contacts  FREE  the people T4 recorded for THIS run (this customer only). Only MOBILE numbers
                       (supabase_store.phone_type); landlines / unknown numbers are skipped and recorded.
                       TEST_MODE: real details, but the phone is one of TEST_CALL_NUMBERS (max 3).
                       REAL MODE needs REAL_CALLS_ENABLED = True (stays False until go-live) AND
                       QUICKHOWL_DND_HANDLED=true (DND is handled by the Quickhowl platform).
    2. request   FREE  company profile (LLM) + contacts + our reference, made ONCE; quickhowl.assert_safe()
    3. store     PAID  the SAVED request with auto_start = false: stored, nobody is called.
                       Crash or failure -> resent automatically (same reference -> duplicate: true).
    4. decision  call_start: who will be called, which numbers. yes -> start | no -> task stopped
                       (the campaign stays stored, nobody is called)
    5. start     PAID  the IDENTICAL request with auto_start = true. Crash -> resent ('already_started').
    6. poll      FREE  status + results about every 60 s, at most 10 minutes per 'run'
                       - still a DRAFT after start -> stops at once (known issue 3) -> task_failed
                       - outside the calling window, or paused -> 'running' at once (run again later)
                       - not finished after 10 minutes -> 'running' (the next 'run' continues)
    7. done      finished (status finished, 0 still to call, every call finished).
                 achieved = calls completed; outcomes per contact in the task output;
                 full transcripts in data/runs/<run_key>/<task>_call_results.json (backup)

PLAN DATA
---------
    brain_customer_runs.understanding (runner_kit.run_understanding) + THIS task's work order.
    Never data/understanding.json or data/work_orders.json: those belong to the latest plan.

DEBUGGING
---------
    "STOPPED: no MOBILE number among T4's people"   -> T4 found only landlines; see the output 'skipped'
    "STOPPED: Real calls are off"                   -> by design until go-live (REAL_CALLS_ENABLED)
    "STOPPED: QUICKHOWL_DND_HANDLED is not true"    -> add QUICKHOWL_DND_HANDLED=true to .env (DND by Quickhowl)
    contacts_rejected                               -> numbers Quickhowl refused (bad, duplicate, do-not-call);
                                                       recorded in the T7 output and counted by the review
    "STOPPED: the campaign is still a DRAFT after start" -> the start did not take; check Quickhowl
    Every call: Supabase brain_tool_calls where idempotency_key like 'motm-%-call-%' or '<run_key>:T7:%'
"""

import json
import os
import time
import uuid

from quickicp import TEST_MODE, ToolError, save_json
from runner_kit import RunnerContext, RunnerResult, contacts_people_ids
from state_store import owned_rows_output, renew_run_lock
from supabase_store import phone_type

POLL_LIMIT_SECONDS = 600          # at most 10 minutes of polling per 'run'
POLL_EVERY_SECONDS = 60
FINISHED_STATUSES = {"completed", "failed", "paused"}


# ================= REAL TOOLS =================

class RealCallTools:
    """The real calls: Supabase people, quickhowl profile + launch + status + results."""

    def __init__(self, run_key: str, task_id: str, understanding: dict, work_order: dict):
        """understanding: the run's (runner_kit.run_understanding); work_order: THIS task's. Never data/ files."""
        self.understanding, self.work_order = understanding, work_order
        self.results_file = os.path.join("data", "runs", run_key, f"{task_id}_call_results.json")

    def people(self, customer: str, ids: list[int]) -> list[dict]:
        """T4's people for THIS customer, each with its company joined (quickhowl's row format)."""
        from supabase_store import COMPANY_TABLE, PEOPLE_TABLE, db
        people = (db.table(PEOPLE_TABLE).select("*").eq("customer", customer).in_("id", ids).execute().data)
        company_ids = sorted({p["company_id"] for p in people if p.get("company_id")})
        companies = ({c["id"]: c for c in db.table(COMPANY_TABLE).select("*").eq("customer", customer)
                      .in_("id", company_ids).execute().data} if company_ids else {})
        for person in people:
            person["company"] = companies.get(person.get("company_id")) or {}
        order = {person_id: i for i, person_id in enumerate(ids)}
        return sorted(people, key=lambda p: order.get(p["id"], 10**9))

    def test_cap(self) -> int:
        """TEST_MODE: how many contacts at most (our test numbers, max quickhowl.MAX_TEST_CALLS)."""
        import quickhowl
        return min(len(quickhowl.test_numbers()), quickhowl.MAX_TEST_CALLS)

    def contacts(self, people: list[dict]) -> list[dict]:
        import quickhowl
        reason = quickhowl.real_calls_block_reason()
        if reason:
            raise ToolError(reason)
        return quickhowl.contacts_from_people(people)

    def dnd(self) -> tuple[bool, str]:
        """(DND confirmed as handled by Quickhowl?, the note recorded for the call start)."""
        import quickhowl
        return quickhowl.dnd_handled(), quickhowl.dnd_note()

    def build_request(self, contacts: list[dict]) -> dict:
        import quickhowl
        request = quickhowl.build_request(contacts, understanding=self.understanding, work_order=self.work_order)
        quickhowl.assert_safe(request)
        return request

    def launch(self, request: dict, auto_start: bool) -> dict:
        import quickhowl
        quickhowl.assert_safe(request)                  # checked again right before every send
        reply = quickhowl.call_api("POST", "/launch", body=quickhowl.with_secrets(request, auto_start=auto_start))
        if reply.get("start_status") == "blocked":
            raise ToolError(f"Quickhowl {'start' if auto_start else 'store'} blocked: {reply.get('start_reason')}")
        return reply

    def status(self, request: dict) -> dict:
        import quickhowl
        return quickhowl.call_api("GET", f"/campaigns/{request['reference']}",
                                  params={"client_reference": request["client_reference"]}, timeout=60)

    def results(self, request: dict, since: str | None) -> tuple[list[dict], str | None]:
        """New / changed calls since 'since' (all pages). Returns (calls, next since)."""
        import quickhowl
        calls = []
        while True:
            params = {"client_reference": request["client_reference"], "limit": quickhowl.PAGE_LIMIT}
            if since:
                params["since"] = since
            reply = quickhowl.call_api("GET", f"/campaigns/{request['reference']}/calls", params=params, timeout=60)
            for item in reply.get("items", []):
                item["transcript"] = quickhowl.clean_transcript(item.get("transcript"))
                calls.append(item)
            since = reply.get("next_since") or since
            if not reply.get("has_more"):
                return calls, since

    def save_results(self, calls: dict) -> None:
        save_json(self.results_file, calls)

    def clock(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ================= FAKE TOOLS (selftest only: no network, no LLM) =================

class FakeCallTools:
    """
    Canned Quickhowl for the orchestrator selftest. Counts every call; time is simulated.
        statuses     the campaign status for each status poll (the last one repeats)
        crash_store / crash_start   crash INSIDE that launch call
    """

    def __init__(self, rows, statuses=("running", "completed"), test_numbers=("+919000000001", "+919000000002"),
                 crash_store=None, crash_start=None, in_window=True, dnd_handled=True, rejected=None):
        self.dnd_handled, self.rejected = dnd_handled, rejected or []
        self.rows, self.statuses = rows, list(statuses)
        self.numbers, self.in_window = list(test_numbers), in_window
        self.crash_store, self.crash_start = crash_store, crash_start
        self.calls = {"build": 0, "store": 0, "start": 0, "status": 0, "results": 0}
        self.now = 0.0
        self.saved = None

    def people(self, customer, ids):
        return [dict(r) for r in self.rows if r["customer"] == customer and r["id"] in ids]

    def test_cap(self):
        return min(len(self.numbers), 3)

    def contacts(self, people):
        if not TEST_MODE:
            raise ToolError("STOPPED: Real calls are off (REAL_CALLS_ENABLED = False; switched on at go-live).")
        return [{"name": p["full_name"], "phone": self.numbers[i], "company": p["company"].get("company_name")}
                for i, p in enumerate(people)]

    def build_request(self, contacts):
        self.calls["build"] += 1
        return {"client_reference": "selftest", "reference": f"motm-selftest-call-{uuid.uuid4().hex[:12]}",
                "contacts": contacts, "company_profile": {"company_name": "Anvixa"}}

    def launch(self, request, auto_start):
        from runner_quickicp_accounts import SimulatedCrash
        kind = "start" if auto_start else "store"
        self.calls[kind] += 1
        if getattr(self, f"crash_{kind}") == self.calls[kind]:
            raise SimulatedCrash(f"crash during the Quickhowl {kind}")
        return {"reference": request["reference"], "duplicate": self.calls[kind] > 1,
                "start_status": ("already_started" if self.calls[kind] > 1 else "started") if auto_start else "pending",
                "contacts_rejected": list(self.rejected),
                "campaign": {"status": "running" if auto_start else "draft"}}

    def status(self, request):
        self.calls["status"] += 1
        status = self.statuses[min(self.calls["status"] - 1, len(self.statuses) - 1)]
        return {"status": status, "in_calling_window_now": self.in_window,
                "contacts_still_to_call": 0 if status == "completed" else len(request["contacts"])}

    def results(self, request, since):
        self.calls["results"] += 1
        done = self.statuses[min(self.calls["status"] - 1, len(self.statuses) - 1)] == "completed"
        calls = [{"call_id": f"call-{i}", "contact": {"name": c["name"], "company": c.get("company"), "phone": c["phone"]},
                  "status": "completed" if done else "in_progress", "finished": done,
                  "outcome": ("interested" if i == 0 else "no_answer") if done else None,
                  "duration_seconds": 95 if done else None, "summary": "fake call", "transcript": [{"speaker": "agent"}]}
                 for i, c in enumerate(request["contacts"])]
        return calls, f"since-{self.calls['results']}"

    def save_results(self, calls):
        self.saved = calls

    def dnd(self):
        return self.dnd_handled, ("DND: handled by the Quickhowl platform (QUICKHOWL_DND_HANDLED=true)"
                                  if self.dnd_handled else "DND: not confirmed (test mode: our own numbers only)")

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


# ================= THE RUNNER =================

def finished(status: dict, calls: dict) -> bool:
    """Code decides: finished status, nobody still to call, every call finished."""
    return (status.get("status") in FINISHED_STATUSES and status.get("contacts_still_to_call") == 0
            and bool(calls) and all(c.get("finished") for c in calls.values()))


DO_NOT_CALL_WORDS = ("dnd", "do_not_call", "do-not-call", "do not call", "ndnc")


def is_do_not_call(entry) -> bool:
    """Code: a rejected contact whose reason names do-not-call (any field, any case)."""
    text = json.dumps(entry, ensure_ascii=False).lower()
    return any(word in text for word in DO_NOT_CALL_WORDS)


def rejected_contacts(ctx: RunnerContext) -> list:
    """Every contact Quickhowl rejected, from the saved store / start replies (no duplicates)."""
    found, seen = [], set()
    for step in ("store", "start"):
        for row in ctx.steps(step):
            if row["status"] == "done":
                for entry in (row["response"] or {}).get("contacts_rejected") or []:
                    key = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                    if key not in seen:
                        seen.add(key)
                        found.append(entry)
    return found


def run_calls(ctx: RunnerContext, tools) -> RunnerResult:
    """One call of the calls runner. Returns done / waiting / stopped / failed / running (or dry)."""
    task, customer = ctx.task, ctx.run["customer"]

    # 1. contacts: MOBILE numbers only (FREE, done once)
    def pick():
        people = tools.people(customer, contacts_people_ids(ctx))
        mobile = [p for p in people if phone_type(p.get("phone_number")) == "mobile"]
        skipped = [{"person_id": p["id"], "name": p.get("full_name"), "phone_type": phone_type(p.get("phone_number"))}
                   for p in people if phone_type(p.get("phone_number")) != "mobile"]
        if not mobile:
            raise ToolError(f"STOPPED: no MOBILE number among T4's {len(people)} people "
                            f"({len(skipped)} landline / unknown).")
        if TEST_MODE:
            mobile = mobile[:tools.test_cap()]           # each one gets one of OUR test numbers
        return {"contacts": tools.contacts(mobile), "person_ids": [p["id"] for p in mobile], "skipped": skipped}
    picked = ctx.free_call("contacts", pick, tool="supabase")

    # 2. the request (profile by the LLM), made ONCE
    request = ctx.free_call("request", lambda: tools.build_request(picked["contacts"]), tool="quickhowl")
    reference = request["reference"]

    if ctx.dry:
        lines = [f"Contacts:               {len(request['contacts'])} (mobile only"
                 + (", TEST MODE: our TEST_CALL_NUMBERS only" if TEST_MODE else "") + ")",
                 f"Skipped:                {len(picked['skipped'])} landline / unknown number(s)",
                 "Would do:               store the campaign (nobody called), then ask you before dialling"]
        lines += [f"  {c.get('name')} | {c.get('company')} | phone {c.get('phone')}" for c in request["contacts"]]
        return RunnerResult("dry", output={"contacts": len(request["contacts"]), "skipped": len(picked["skipped"]),
                                           "lines": lines})

    # 3. store (PAID, auto_start false; the tool de-duplicates by our reference)
    ctx.resendable_call("store", f"{reference}:store", lambda: tools.launch(request, auto_start=False),
                        {"reference": reference, "auto_start": False}, tool="quickhowl")

    # 4. a human decides before anyone is called
    started = [r for r in ctx.steps("start") if r["status"] == "done"]
    if not started:
        decisions = ctx.decisions("call_start")
        decision = decisions[-1] if decisions else None
        if decision and decision["status"] == "answered" and decision["answer"] == "no":
            return RunnerResult.stopped(f"calls not started (decision #{decision['id']}): {decision['reason']}. "
                                        "The campaign stays stored; nobody is called.")
        if not (decision and decision["status"] == "answered" and decision["answer"] == "yes"):
            return RunnerResult.waiting("call_start", {
                "text": f"{task['task_id']}: START calling {len(request['contacts'])} contact(s)"
                        + f" [{tools.dnd()[1]}]"
                        + (" (TEST MODE: only our own test numbers ring)" if TEST_MODE else "")
                        + "? Calls run 09:00-19:00 Mon-Sat, one at a time. yes = start, no = stop (nobody is called).",
                "reference": reference,
                "contacts": [{"name": c.get("name"), "company": c.get("company"), "phone": c.get("phone")}
                             for c in request["contacts"]]})
        # 5. start (PAID; the identical request with auto_start true)
        handled, note = tools.dnd()
        ctx.resendable_call("start", f"{reference}:start", lambda: tools.launch(request, auto_start=True),
                            {"reference": reference, "auto_start": True, "dnd_handled": handled, "dnd_note": note},
                            tool="quickhowl")

    # 6. poll (FREE reads), at most POLL_LIMIT_SECONDS in this run
    polls = [r for r in ctx.steps("poll") if r["status"] == "done"]
    seen = polls[-1]["output"] if polls else {"since": None, "calls": {}}
    calls, since = dict(seen["calls"]), seen["since"]
    deadline = tools.clock() + POLL_LIMIT_SECONDS
    while True:
        status = tools.status(request)
        new_calls, since = tools.results(request, since)
        for call in new_calls:
            calls[call["call_id"]] = call
        snapshot = {"since": since, "calls": calls, "status": status.get("status")}
        ctx.free_call("poll", lambda s=snapshot: s, seq=ctx.next_seq("poll"), tool="quickhowl")
        tools.save_results(calls)
        if status.get("status") == "draft":
            # Known issue 3: a draft never starts by itself; stop polling at once.
            raise ToolError(f"STOPPED: campaign {reference} is still a DRAFT after start; nobody is being called. "
                            "Check it in Quickhowl, then answer retry.")
        if finished(status, calls):
            break
        if not status.get("in_calling_window_now", True):
            return RunnerResult.running(f"campaign {reference}: outside the calling window "
                                        f"({status.get('calling_window') or '09:00-19:00 Mon-Sat'}); run again later.")
        if status.get("status") == "paused":
            return RunnerResult.running(f"campaign {reference} is PAUSED with {status.get('contacts_still_to_call')} "
                                        "still to call.")
        if tools.clock() + POLL_EVERY_SECONDS > deadline:
            return RunnerResult.running(f"campaign {reference}: calls still going "
                                        f"({len(calls)} so far). Run again later.")
        if ctx.lock_holder:
            renew_run_lock(ctx.run["id"], ctx.lock_holder)
        tools.sleep(POLL_EVERY_SECONDS)

    # 7. done: outcomes per contact (code counts)
    by_phone = {c.get("phone"): pid for c, pid in zip(request["contacts"], picked["person_ids"])}
    outcomes = [{"person_id": by_phone.get((c.get("contact") or {}).get("phone")),
                 "name": (c.get("contact") or {}).get("name"), "company": (c.get("contact") or {}).get("company"),
                 "status": c.get("status"), "outcome": c.get("outcome"), "duration_seconds": c.get("duration_seconds"),
                 "summary": c.get("summary")} for c in calls.values()]
    completed = sum(1 for c in calls.values() if c.get("status") == "completed")
    counts = {}
    for c in calls.values():
        counts[c.get("outcome")] = counts.get(c.get("outcome"), 0) + 1
    rejected = rejected_contacts(ctx)
    start_rows = [r for r in ctx.steps("start") if r["status"] == "done"]
    dnd_confirmation = (start_rows[-1]["request"] or {}).get("dnd_note") if start_rows else None
    output = owned_rows_output(ctx.run, people_ids=picked["person_ids"], achieved=completed,
                               contacts_rejected=rejected, rejected_by_quickhowl=len(rejected),
                               rejected_do_not_call=sum(1 for r in rejected if is_do_not_call(r)),
                               dnd_confirmation=dnd_confirmation,
                               target=len(request["contacts"]), reference=reference, outcomes=outcomes,
                               outcome_counts=counts, skipped_landlines=picked["skipped"], test_mode=TEST_MODE)
    note = (f"campaign {reference} finished: {completed} of {len(request['contacts'])} call(s) completed, "
            f"outcomes {counts}" + (" (TEST MODE: our test numbers)" if TEST_MODE else ""))
    return RunnerResult.done(completed, len(request["contacts"]), output, note)
