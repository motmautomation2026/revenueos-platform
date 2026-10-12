"""
runner_quickicp_accounts.py
===========================

Tool runner for QuickICP 'accounts' tasks (T3: "150-200 accounts built"). Called by
orchestrator.py 'run'. Wraps company_loop.py (same rounds, same judge), with the brain's rules.

FLOW (each step recorded in brain_tool_calls; see runner_kit.py)
----
    1. payload   FREE  keywords (LLM) -> Prospeo exact names -> payload. Done once, then reused.
    2. reuse     FREE  read THIS customer's companies already in Supabase, judge them with this
                       payload. Done once, then reused.
    3. shortfall = target - reused companies that pass. 0 -> NO paid search at all.
    4. rounds    PAID  company search for the shortfall only (round 0 + up to 2 extra rounds,
                       company_loop's next-page / adjust rules). Each search: 'running' row BEFORE
                       the call, reply saved right after. Companies we already have are never
                       counted again (their domains start in LoopState.seen).
                 FREE  judge + a state snapshot after every round.
    5. goal      still short after the extra rounds -> goal_check decision:
                       continue (done with shortfall) | more (2 more rounds) | stop
                       'more' at most 2 times; then goal_check_final: continue | stop
    6. save      FREE  passed NEW companies -> supabase_store.save_companies(customer); the ids of
                       reused + new companies are recorded for this run (owned_rows_output)
    7. done      the orchestrator records the brain review (code: achieved vs target)

CRASH SAFETY
------------
    during payload / reuse / judge / save (free)  -> repeated on the next run
    during the paid search (row 'running')        -> 'unknown' + paid_call_unknown decision:
                                                     rerun (new search) | skip (round = empty) | stop
                                                     never repeated automatically
    after the search, before the judge            -> the SAVED reply is judged; no new search
    during the rounds                             -> continue from the last state snapshot

TARGET (code)
------
    real:       task target_number (largest number in the target text, e.g. 200)
    TEST_MODE:  min(target_number, company_loop.TEST_TARGET = 40)
    TEST_TARGET_OVERRIDE in .env (test mode only): use that number instead, e.g. to force a shortfall

READS / WRITES
--------------
    Supabase quickicp_revenueos_client_company_data (this customer): read for reuse; new passed companies saved
    brain_tool_calls / brain_decisions via runner_kit + orchestrator
    data/quickicp_payload.json   written (the judge reads it); the old file is moved to data/archive/ first
    data/runs/<run_key>/<task>_company_loop_log.json   round log (backup)
    data/maps/*_industry_map.json   the judge may ADD industries it has not seen (never removes)

DEBUGGING
---------
    "STOPPED: <task> target has no number"     -> the work order target has no number; fix the plan
    "STOPPED: TEST_TARGET_OVERRIDE ..."         -> must be a whole number > 0, and only in TEST_MODE
    Rounds and reasons: brain_tool_calls rows step 'state' (output.loop.log), and the log file above.
"""

import json
import os
from datetime import datetime

from company_loop import (MAX_EXTRA_ROUNDS, MAX_USER_RETRIES, TEST_TARGET, LoopState, LoopTools,
                          count_passed, rejection_summary, request_size, run_extra_rounds, run_round)
from quickicp import PAGE_SIZE, TEST_MODE, CompanyPayload, ToolError, clean_domain, load_json, save_json
from runner_kit import RunnerContext, RunnerResult, target_for
from state_store import finish_step, mark_unknown, owned_rows_output

GOAL_KINDS = ("goal_check", "goal_check_final")


# ================= TARGET (code) =================

def runner_target(task: dict) -> int:
    """How many passed companies this task needs (see TARGET in the header; rule in runner_kit.target_for)."""
    return target_for(task, TEST_TARGET)


# ================= REAL TOOLS =================

class RealAccountsTools(LoopTools):
    """The real calls: company_loop's search / judge / adjust, plus payload, Supabase read and save."""

    def __init__(self, run_key: str, task_id: str):
        self.log_file = os.path.join("data", "runs", run_key, f"{task_id}_company_loop_log.json")

    def build_payload(self, work_order: dict) -> CompanyPayload:
        """Keywords (LLM) -> Prospeo suggestions -> exact names (LLM) -> payload."""
        from quickicp import build_company_payload
        return build_company_payload(work_order, load_json("data/understanding.json"))

    def existing_companies(self, customer: str) -> list[dict]:
        """This customer's companies in Supabase, in QuickICP field names (+ '_id' = row id)."""
        from supabase_store import COMPANY_TABLE, db
        rows = (db.table(COMPANY_TABLE).select("id, company_name, website, location, industry, headcount, phone, linkedin")
                  .eq("customer", customer).execute().data)
        return [{"_id": r["id"], "COMPANY": r["company_name"], "WEBSITE": r["website"], "LOCATION": r["location"],
                 "INDUSTRY": r["industry"], "HEADCOUNT": r["headcount"], "PHONE": r["phone"],
                 "LINKEDIN": r["linkedin"]} for r in rows]

    def save_payload(self, payload: CompanyPayload) -> None:
        """Write data/quickicp_payload.json (the judge reads it); an older, different file is archived first."""
        path = "data/quickicp_payload.json"
        if os.path.exists(path) and load_json(path) != payload.model_dump():
            folder = os.path.join("data", "archive", datetime.now().strftime("%Y%m%d-%H%M%S") + "-payload")
            number, base = 1, folder
            while os.path.exists(folder):
                number += 1
                folder = f"{base}-{number}"
            os.makedirs(folder)
            os.replace(path, os.path.join(folder, "quickicp_payload.json"))
        save_json(path, payload.model_dump())

    def save_log(self, log: list[dict]) -> None:
        """Round log as a backup file for this run and task."""
        save_json(self.log_file, log)

    def save_companies(self, companies: list[dict], customer: str) -> dict[str, int]:
        """Save passed new companies for this customer; returns {domain: company row id}."""
        from supabase_store import save_companies
        lookups = save_companies(companies, customer)
        domains = [clean_domain(c.get("WEBSITE")) for c in companies]
        return {d: lookups["domain"][d] for d in domains if d and d in lookups["domain"]}


# ================= FAKE TOOLS (selftest only: no network, no LLM) =================

class SimulatedCrash(BaseException):
    """Stands in for a killed process in the selftest (not an Exception, so nothing catches it)."""


class FakeAccountsTools(LoopTools):
    """
    Canned companies for the orchestrator selftest. Counts every call. Can crash on purpose.
        existing      companies 'already in Supabase' (QuickICP fields + '_id')
        good_rate     every n-th searched company passes (1 = all pass, 0 = none pass)
        crash_search  crash INSIDE search call number n (1-based); crash_judge: inside judge call n
    """

    def __init__(self, existing=None, good_rate=1, crash_search=None, crash_judge=None,
                 fail_search=None, on_search=None):
        self.existing = existing or []
        self.good_rate, self.crash_search, self.crash_judge = good_rate, crash_search, crash_judge
        self.fail_search, self.on_search = fail_search, on_search
        self.calls = {"build_payload": 0, "search": 0, "judge": 0, "adjust": 0, "save_companies": 0}
        self.made = 0          # companies 'found' so far (unique domains)

    @staticmethod
    def company(domain: str, good: bool) -> dict:
        return {"COMPANY": domain, "WEBSITE": domain, "LOCATION": "Delhi, India",
                "INDUSTRY": "good" if good else "bad", "HEADCOUNT": 500}

    def build_payload(self, work_order: dict) -> CompanyPayload:
        self.calls["build_payload"] += 1
        return CompanyPayload(industries=["good"], locations=["Delhi, India"], company_size=["201-500"],
                              company_count=0, coveredpages=0)

    def existing_companies(self, customer: str) -> list[dict]:
        return [dict(c) for c in self.existing]

    def search(self, payload: CompanyPayload) -> list[dict]:
        self.calls["search"] += 1
        if self.on_search:
            self.on_search()                      # e.g. check the ledger row is already 'running'
        if self.crash_search == self.calls["search"]:
            raise SimulatedCrash("crash during the paid search")
        if self.fail_search == self.calls["search"]:
            raise ToolError("Company search failed after 4 attempts: server error 502")
        found = []
        for _ in range(payload.company_count):
            self.made += 1
            good = self.good_rate and self.made % self.good_rate == 0
            found.append(self.company(f"new{self.made}.test", bool(good)))
        return found

    def judge(self, companies: list[dict]) -> list[dict]:
        self.calls["judge"] += 1
        if self.crash_judge == self.calls["judge"]:
            raise SimulatedCrash("crash after the search, before the judge")
        return [{**c, "validation": {"status": "pass" if c.get("INDUSTRY") == "good" else "fail",
                                     "score": 90 if c.get("INDUSTRY") == "good" else 10,
                                     "reason": "fake", "checked_by": "llm"}} for c in companies]

    def adjust(self, work_order, log, summary, shortfall):
        self.calls["adjust"] += 1
        return "fake adjust", CompanyPayload(industries=["adjusted"], locations=["Delhi, India"],
                                             company_size=["201-500"], company_count=request_size(shortfall),
                                             coveredpages=0)

    def save_payload(self, payload) -> None:
        pass

    def save_log(self, log) -> None:
        pass

    def save_companies(self, companies: list[dict], customer: str) -> dict[str, int]:
        self.calls["save_companies"] += 1
        return {clean_domain(c["WEBSITE"]): 900000 + i for i, c in enumerate(companies)}


# ================= LEDGER AROUND THE LOOP =================

class LedgeredTools(LoopTools):
    """
    What company_loop's rounds see. search -> PAID ledger row; adjust -> FREE ledger row;
    after every round -> state snapshot (FREE row 'state'). A saved reply can be replayed once.
    """

    def __init__(self, inner, ctx: RunnerContext, progress: dict):
        self.inner, self.ctx, self.progress = inner, ctx, progress
        self.replay: list[dict] | None = None

    def search(self, payload: CompanyPayload) -> list[dict]:
        if self.replay is not None:
            companies, self.replay = self.replay, None
            return companies
        return self.ctx.paid_call("search", self.ctx.next_seq("search"), lambda: self.inner.search(payload),
                                  {"payload": payload.model_dump()})

    def judge(self, companies: list[dict]) -> list[dict]:
        return self.inner.judge(companies)

    def adjust(self, work_order, log, summary, shortfall):
        def call():
            explanation, payload = self.inner.adjust(work_order, log, summary, shortfall)
            return {"explanation": explanation, "payload": payload.model_dump()}
        out = self.ctx.free_call("adjust", call, seq=self.ctx.next_seq("adjust"))
        return out["explanation"], CompanyPayload.model_validate(out["payload"])

    def save_payload(self, payload) -> None:
        self.inner.save_payload(payload)

    def save_log(self, log) -> None:
        self.inner.save_log(log)

    def after_round(self, state: LoopState) -> None:
        self.save_snapshot(state)

    def save_snapshot(self, state: LoopState | None) -> None:
        """Save the loop state + progress (decisions used, 'more' count) as a new 'state' row."""
        snapshot = {"loop": state.to_json() if state else None, **self.progress}
        self.ctx.free_call("state", lambda: snapshot, seq=self.ctx.next_seq("state"))


# ================= THE RUNNER =================

def latest_unconsumed(ctx: RunnerContext, kinds: tuple[str, ...], consumed: list[int]) -> dict | None:
    """The newest decision of these kinds for this task that this runner has not acted on yet."""
    found = [d for kind in kinds for d in ctx.decisions(kind) if d["id"] not in consumed]
    return max(found, key=lambda d: d["id"]) if found else None


def reuse_check(tools, customer: str) -> dict:
    """FREE: this customer's companies already in Supabase, judged with the current payload."""
    existing = tools.existing_companies(customer)
    judged = tools.judge(existing) if existing else []
    passed = [c for c in judged if c["validation"]["status"] == "pass"]
    return {"existing": len(existing), "passed": len(passed), "passed_ids": [c["_id"] for c in passed],
            "existing_domains": sorted({clean_domain(c.get("WEBSITE")) for c in existing if c.get("WEBSITE")})}


def run_quickicp_accounts(ctx: RunnerContext, tools) -> RunnerResult:
    """One call of the accounts runner. Returns done / waiting / stopped / failed (or dry)."""
    task, customer = ctx.task, ctx.run["customer"]
    target = runner_target(task)

    # A paid search left 'running' by a crash: it becomes 'unknown' (never repeated automatically).
    for row in ctx.steps("search"):
        if row["status"] == "running" and not ctx.dry:
            mark_unknown(row["id"], "crash during the paid company search (no reply saved)")
    unknown = [row for row in ctx.steps("search") if row["status"] in ("unknown", "running")]
    if ctx.dry and unknown:
        return RunnerResult("dry", note=f"{task['task_id']} has an unresolved paid search ({unknown[-1]['idempotency_key']}); "
                                        "answer its paid_call_unknown decision first.")

    # 1-2. payload and reuse check (FREE, done once, then reused)
    payload = CompanyPayload.model_validate(
        ctx.free_call("payload", lambda: tools.build_payload(task["work_order"]).model_dump()))
    tools.save_payload(payload)
    reuse = ctx.free_call("reuse", lambda: reuse_check(tools, customer), tool="supabase")
    shortfall = max(0, target - reuse["passed"])

    if ctx.dry:
        pages = -(-request_size(shortfall) // PAGE_SIZE) if shortfall else 0
        would = request_size(shortfall) if shortfall else 0
        lines = [f"Target:                 {target}",
                 f"Already in Supabase:    {reuse['existing']} companies for this customer, {reuse['passed']} pass the judge",
                 f"Shortfall:              {shortfall}",
                 f"Would buy:              {would} companies (about {pages} page(s) of 25)" if shortfall
                 else "Would buy:              NOTHING (reuse covers the target)"]
        return RunnerResult("dry", achieved=reuse["passed"], target=target,
                            output={"existing": reuse["existing"], "reused_passing": reuse["passed"],
                                    "shortfall": shortfall, "would_request": would, "pages": pages, "lines": lines})

    # Progress and loop state from the latest snapshot
    snapshots = [row for row in ctx.steps("state") if row["status"] == "done"]
    last = snapshots[-1]["output"] if snapshots else {}
    progress = {"consumed": list(last.get("consumed", [])), "mores": last.get("mores", 0)}
    state = LoopState.from_json(last["loop"]) if last.get("loop") else None
    ledgered = LedgeredTools(tools, ctx, progress)

    def new_state() -> LoopState:
        return LoopState(work_order=task["work_order"], target=shortfall, seen=set(reuse["existing_domains"]))

    # 3. an unknown paid search: a human decides (rerun / skip / stop)
    if unknown:
        row = unknown[-1]
        decision = latest_unconsumed(ctx, ("paid_call_unknown",), progress["consumed"])
        if decision is None or decision["status"] == "open":
            return RunnerResult.waiting("paid_call_unknown", {
                "text": f"{task['task_id']}: the paid company search {row['idempotency_key']} was interrupted; "
                        "we cannot know if it ran (and was charged). rerun = search again, skip = treat that "
                        "round as empty, stop = stop this task.",
                "call": row["idempotency_key"], "request": row["request"]})
        answer = decision["answer"]
        progress["consumed"].append(decision["id"])
        finish_step(row["id"], "failed", error=f"outcome unknown; human chose '{answer}' (decision #{decision['id']})")
        if answer == "skip":
            state = state or new_state()
            number = state.rounds + 1 if state.log else 0
            state.log.append({"round": number, "skipped": True, "reason": f"unknown paid search skipped (decision #{decision['id']})"})
            state.rounds, state.received, state.last_passed = number, 0, 0
        ledgered.save_snapshot(state)
        if answer == "stop":
            return RunnerResult.stopped(f"stopped after an interrupted paid search (decision #{decision['id']}): {decision['reason']}")

    # 4. buy only the shortfall
    if shortfall > 0:
        state = state or new_state()
        rounds_logged = len([entry for entry in state.log if "payload" in entry])
        done_searches = [row for row in ctx.steps("search") if row["status"] == "done"]
        if len(done_searches) > rounds_logged:
            # Crash after the search, before the judge: judge the SAVED reply, no new search.
            ledgered.replay = done_searches[-1]["response"]["items"]
            run_round(state.rounds + 1 if state.log else 0, "replay",
                      CompanyPayload.model_validate(done_searches[-1]["request"]["payload"]), state,
                      "saved reply judged after a crash", ledgered)
        if not state.log:
            try:
                run_round(0, "initial", payload.model_copy(update={"company_count": request_size(shortfall),
                                                                   "coveredpages": 0}),
                          state, f"Initial search for the shortfall of {shortfall}", ledgered)
            except ToolError as error:
                ledgered.save_snapshot(None if not state.log else state)
                return RunnerResult.failed(str(error))

        while True:
            rounds_allowed = MAX_EXTRA_ROUNDS * (1 + progress["mores"])
            if count_passed(state.all_judged) < shortfall and state.rounds < rounds_allowed:
                run_extra_rounds(state, rounds_allowed - state.rounds, ledgered)
                ledgered.save_snapshot(state)
            if count_passed(state.all_judged) >= shortfall:
                break

            # 5. goal check: still short after the allowed rounds
            decision = latest_unconsumed(ctx, GOAL_KINDS, progress["consumed"])
            if decision is None or decision["status"] == "open":
                achieved = reuse["passed"] + count_passed(state.all_judged)
                kind = "goal_check_final" if progress["mores"] >= MAX_USER_RETRIES else "goal_check"
                ledgered.save_snapshot(state)
                return RunnerResult.waiting(kind, {
                    "text": f"{task['task_id']} companies: {achieved} of {target} (short by {target - achieved}) after "
                            f"{state.rounds + 1} round(s). continue = go on with {achieved}"
                            + (", more = 2 more rounds" if kind == "goal_check" else "") + ", stop = stop this task.",
                    "achieved": achieved, "target": target, "details": rejection_summary(state.all_judged)})
            progress["consumed"].append(decision["id"])
            ledgered.save_snapshot(state)
            if decision["answer"] == "more":
                progress["mores"] += 1
                continue
            if decision["answer"] == "stop":
                return RunnerResult.stopped(f"stopped at the goal check (decision #{decision['id']}): {decision['reason']}")
            break   # continue with what we have

    # 6. save passed NEW companies for this customer; record which rows belong to this run
    new_passed = [c for c in (state.all_judged if state else []) if c["validation"]["status"] == "pass"]
    saved = ctx.free_call("save", lambda: {"ids": tools.save_companies(new_passed, customer)}, tool="supabase")
    achieved = reuse["passed"] + len(new_passed)
    output = owned_rows_output(ctx.run, company_ids=reuse["passed_ids"] + list(saved["ids"].values()),
                               achieved=achieved, target=target, reused=reuse["passed"], bought=len(new_passed),
                               shortfall=max(0, target - achieved), searches=len(ctx.steps("search")))
    note = (f"{achieved} of {target}: {reuse['passed']} reused, {len(new_passed)} new"
            + (f", short by {target - achieved} (continued by a human)" if achieved < target else ""))
    return RunnerResult.done(achieved, target, output, note)
