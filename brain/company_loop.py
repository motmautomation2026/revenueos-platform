"""
company_loop.py
===============

The company search ROUNDS used by the orchestrator's accounts runner (runner_quickicp_accounts.py):
one round = search -> keep only new companies -> judge; extra rounds fetch the next page or let the
brain adjust the filters. No standalone CLI any more: the old 'python company_loop.py' loop and its
goal_check.py prompt are in archive/legacy/ (the goal check is now an orchestrator decision).

PAGINATION (agreed with the n8n developer)
------------------------------------------
    - Every payload has company_count and coveredpages.
    - company_count = how many NEW companies we want in this request.
    - coveredpages  = how many pages of THIS SAME SEARCH we already fetched (0 = new search).
    - n8n fetches pages coveredpages+1 onwards, ceil(company_count / 25) pages in total.
    - After a successful request: coveredpages += ceil(company_count / 25).
    - If the filters change (brain adjustment), it is a NEW search: coveredpages = 0.

FLOW (run_round / run_extra_rounds)
----
    Round 0   filters from the work order, coveredpages 0
    Round 1+  only while passed < target, at most MAX_EXTRA_ROUNDS:
              NEXT PAGE  - same filters, coveredpages = pages already fetched
                           (used while the last round returned companies AND some passed)
              ADJUST     - brain changes the filters, coveredpages = 0
                           (used when the last page was empty, or nothing in it passed)
    Still short after the rounds -> the runner opens a goal_check decision (continue / more / stop).

WHO DECIDES WHAT
----------------
    Code : target, shortfall, pages, next-page vs adjust, de-duplication
    LLM  : how to change the filters when the current search is exhausted (brain_adjust)
    Human: continue / more / stop, through the orchestrator's goal_check decision

READS / WRITES
--------------
    Through the LoopTools the runner passes: every paid search recorded in brain_tool_calls before it is
    made, the state saved after every round (LoopState.to_json / from_json). The run's understanding and
    the task's work order come from the run (brain_customer_runs), never data/.

TEST MODE
---------
    With TEST_MODE on (in quickicp.py), the target is TEST_TARGET.

DEBUGGING
---------
    brain_tool_calls rows of the task: step 'state' (output.loop.log: each round's strategy, payload,
    counts) and the round log file data/runs/<run_key>/<task>_company_loop_log.json.
"""

import json
import math
from collections import Counter
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from quickicp import (
    PAGE_SIZE,
    TEST_MODE,
    CompanyPayload,
    CompanySize,
    ToolError,
    ask_llm,
    clean_domain,
    industry_options,
    max_number,
    pick_exact_names,
    prospeo_suggestions,
    save_json,
    search_companies,
)
from validator import company_context, judge_companies

MAX_EXTRA_ROUNDS = 2      # extra requests after round 0 (and per "try more")
MAX_USER_RETRIES = 2      # how many times the human may choose "try more"
# In test mode: 40 = more than one page, to force an extra round. The platform's Staging switch
# passes the person's own number as STAGING_COMPANIES (run_for.py).
from quickicp import staging_number
TEST_TARGET = staging_number("STAGING_COMPANIES", 40)
OVERSAMPLE = 2            # real runs: ask for 2x the shortfall, since some will fail the judge


# ================= WHAT THE BRAIN RETURNS =================

class SearchAdjustment(BaseModel):
    industry_keywords: list[str] = Field(description="Short industry keywords, 1-3 words each")
    location_keywords: list[str] = Field(description="Plain city or state names inside the target regions")
    company_size: list[CompanySize]
    explanation: str = Field(description="One or two sentences: what changed and why")


# ================= LOOP STATE =================

@dataclass
class LoopState:
    """Everything the rounds share, so 'try more' can continue exactly where it stopped."""
    work_order: dict
    target: int
    payload: CompanyPayload | None = None
    covered: int = 0                  # pages of the CURRENT search fetched so far
    received: int = 0                 # companies returned by the last round
    last_passed: int = 0              # companies that passed in the last round
    rounds: int = 0                   # rounds sent (0 = only round 0 so far)
    seen: set = field(default_factory=set)           # domains found, across all rounds
    all_raw: list = field(default_factory=list)      # every company found, no duplicates
    all_judged: list = field(default_factory=list)   # every company with its verdict
    log: list = field(default_factory=list)

    def to_json(self) -> dict:
        """Everything needed to continue later (saved by the orchestrator after every round)."""
        return {"work_order": self.work_order, "target": self.target,
                "payload": self.payload.model_dump() if self.payload else None,
                "covered": self.covered, "received": self.received, "last_passed": self.last_passed,
                "rounds": self.rounds, "seen": sorted(self.seen), "all_raw": self.all_raw,
                "all_judged": self.all_judged, "log": self.log}

    @classmethod
    def from_json(cls, data: dict) -> "LoopState":
        """Rebuild a LoopState saved with to_json()."""
        payload = CompanyPayload.model_validate(data["payload"]) if data.get("payload") else None
        return cls(work_order=data["work_order"], target=data["target"], payload=payload,
                   covered=data["covered"], received=data["received"], last_passed=data["last_passed"],
                   rounds=data["rounds"], seen=set(data["seen"]), all_raw=data["all_raw"],
                   all_judged=data["all_judged"], log=data["log"])


# ================= EXTERNAL CALLS OF ONE ROUND =================

class LoopTools:
    """
    The calls one round makes (the base the runners build on; the real search / judge).
    The orchestrator's runner passes its own version: every search saved in
    brain_tool_calls BEFORE it is made, and a fake version in its selftest (no network, no LLM).
    """

    def search(self, payload: CompanyPayload) -> list[dict]:
        """The PAID company search (n8n)."""
        return search_companies(payload)

    def judge(self, companies: list[dict]) -> list[dict]:
        """The company judge (LLM only for industries not yet in the map)."""
        return judge_companies(companies)

    def adjust(self, work_order: dict, log: list[dict], summary: dict, shortfall: int) -> tuple[str, CompanyPayload]:
        """New filters from the brain (LLM) + Prospeo exact names -> (explanation, payload)."""
        adjust = brain_adjust(work_order, log, summary, shortfall)
        return adjust.explanation, resolve_payload(adjust, work_order, shortfall)

    def save_payload(self, payload: CompanyPayload) -> None:
        """The judge reads target cities and minimum size from this file, so it is saved first."""
        save_json("data/quickicp_payload.json", payload.model_dump())

    def save_log(self, log: list[dict]) -> None:
        """Round-by-round log file."""
        save_json("data/company_loop_log.json", log)

    def after_round(self, state: "LoopState") -> None:
        """Called after every round; the orchestrator's runner saves the state here."""


REAL_TOOLS = LoopTools()


# ================= NUMBERS (CODE) =================

def passed_target(work_order: dict) -> int:
    """How many companies must pass. Real runs: the largest number in the work order target."""
    return TEST_TARGET if TEST_MODE else max_number(work_order.get("target", ""), 25)


def count_passed(judged: list[dict]) -> int:
    return sum(1 for company in judged if company["validation"]["status"] == "pass")


def pages_for(company_count: int) -> int:
    """Pages n8n will fetch for this many companies: 100 -> 4, 20 -> 1, 40 -> 2."""
    return math.ceil(company_count / PAGE_SIZE)


def request_size(shortfall: int) -> int:
    """
    How many NEW companies to ask for in an extra round. We send the full number;
    the n8n function splits it into pages of 25 itself.
    Test runs ask for exactly the shortfall; real runs ask for 2x, since some will fail the judge.
    """
    return shortfall if TEST_MODE else shortfall * OVERSAMPLE


# ================= REJECTION SUMMARY (CODE) =================

def rejection_summary(judged: list[dict]) -> dict:
    """Turn all verdicts so far into facts the brain (and the human) can act on."""
    failed = [c for c in judged if c["validation"]["status"] == "fail"]
    passed = [c for c in judged if c["validation"]["status"] == "pass"]
    return {
        "total_judged": len(judged),
        "passed": len(passed),
        "outside_geography": sum(1 for c in failed if c["validation"]["reason"] == "Outside target geography"),
        "below_min_size": sum(1 for c in failed if c["validation"]["reason"].startswith("Headcount")),
        "low_fit_industries": dict(Counter(c.get("INDUSTRY") for c in failed
                                           if c["validation"]["checked_by"] in ("llm", "cam"))),
        "passed_industries": dict(Counter(c.get("INDUSTRY") for c in passed)),
    }


# ================= BRAIN ADJUSTS THE SEARCH (LLM) =================

def brain_adjust(work_order: dict, log: list[dict], summary: dict, shortfall: int,
                 context: dict | None = None) -> SearchAdjustment:
    """
    Ask the brain for new filters, used only when the current search is exhausted.
    context: company_context of the run (orchestrator); None = the CLI files.
    """
    previous_searches = [entry["payload"] for entry in log if entry.get("payload")]
    prompt = (
        "You are the Master Agent of MOTM Revenue OS. The current company search has run out of "
        f"good results. We still need {shortfall} more companies that pass the judge.\n"
        "Design a NEW search. Rules:\n"
        "- Stay inside the customer's priority segments (P1 first, then P2) and the work order's regions.\n"
        "- Do not repeat industries that mostly failed. Keep what worked, and add adjacent industries "
        "or more cities inside the same regions, so the search returns NEW companies.\n"
        "- Only widen company size if many companies failed for being too small.\n"
        "- location_keywords must be plain city or state names.\n"
        "- Explain the change in one or two sentences.\n\n"
        "Customer:\n" + json.dumps(context if context is not None else company_context(), ensure_ascii=False)
        + "\n\nWork order:\n" + json.dumps(work_order, ensure_ascii=False)
        + "\n\nResults so far:\n" + json.dumps(summary, ensure_ascii=False)
        + "\n\nSearches already done:\n" + json.dumps(previous_searches, ensure_ascii=False)
    )
    return ask_llm(SearchAdjustment, prompt, "Brain adjustment")


def resolve_payload(adjust: SearchAdjustment, work_order: dict, shortfall: int) -> CompanyPayload:
    """New filters -> Prospeo exact names -> payload for a NEW search (coveredpages = 0)."""
    industries_found = industry_options(adjust.industry_keywords)   # synonyms once if a keyword finds nothing
    location_options = {kw: prospeo_suggestions("location_search", kw) for kw in adjust.location_keywords}

    picked = pick_exact_names(work_order, industries_found, location_options)
    valid_industries = {name for names in industries_found.values() for name in names}
    valid_locations = {name for names in location_options.values() for name in names}
    industries = [name for name in picked.industries if name in valid_industries]
    locations = [name for name in picked.locations if name in valid_locations]
    if not industries or not locations:
        raise ToolError("Brain adjustment produced no valid industries or locations.")

    return CompanyPayload(industries=industries, locations=locations, company_size=adjust.company_size,
                          company_count=request_size(shortfall), coveredpages=0)


# ================= ONE ROUND =================

def run_round(number: int, strategy: str, payload: CompanyPayload, state: LoopState,
              explanation: str, tools: LoopTools = REAL_TOOLS) -> None:
    """Send one payload, keep only companies not seen before, judge them, update the state."""
    # The judge reads target cities and minimum size from this file, so save it first.
    tools.save_payload(payload)

    companies = tools.search(payload)
    new = []
    for company in companies:
        domain = clean_domain(company.get("WEBSITE"))
        if domain and domain not in state.seen:
            state.seen.add(domain)
            new.append(company)
    state.all_raw.extend(new)

    judged = tools.judge(new) if new else []
    state.all_judged += judged
    passed = count_passed(judged)
    first_page = payload.coveredpages + 1
    last_page = payload.coveredpages + pages_for(payload.company_count)

    state.log.append({
        "round": number,
        "strategy": strategy,
        "explanation": explanation,
        "pages": f"{first_page}-{last_page}",
        "payload": payload.model_dump(),
        "received": len(companies),
        "new": len(new),
        "passed": passed,
    })
    tools.save_log(state.log)

    # Update the state for the next round.
    state.payload = payload
    state.covered = payload.coveredpages + pages_for(payload.company_count)
    state.received = len(companies)
    state.last_passed = passed
    state.rounds = number

    print(f"\nRound {number} [{strategy}, pages {first_page}-{last_page}]: "
          f"received {len(companies)}, new {len(new)}, passed {passed}")
    for company in judged:
        v = company["validation"]
        print(f"  {v['status'].upper():6} {str(v['score']):>4} [{v['checked_by']:4}] "
              f"{company.get('COMPANY')}  -  {v['reason']}")
    tools.after_round(state)


def run_extra_rounds(state: LoopState, how_many: int, tools: LoopTools = REAL_TOOLS) -> None:
    """Up to how_many extra rounds, stopping early when the target is met or a round fails."""
    for _ in range(how_many):
        passed = count_passed(state.all_judged)
        if passed >= state.target:
            return
        number = state.rounds + 1
        shortfall = state.target - passed
        print(f"\n===== ROUND {number}: short by {shortfall} =====")

        try:
            if state.received > 0 and state.last_passed > 0:
                # The current search still gives good results: fetch its next page(s).
                strategy = "next_page"
                explanation = f"Same search, continuing after page {state.covered}"
                payload = state.payload.model_copy(update={"company_count": request_size(shortfall),
                                                           "coveredpages": state.covered})
            else:
                # The current search is exhausted (empty page, or nothing passed): new filters.
                strategy = "adjust"
                explanation, payload = tools.adjust(state.work_order, state.log,
                                                    rejection_summary(state.all_judged), shortfall)
            print(f"Strategy: {strategy} - {explanation}")
            run_round(number, strategy, payload, state, explanation, tools)

        except ToolError as error:
            # A failed extra round never loses earlier results: stop these rounds, keep what we have.
            print(f"Round {number} stopped: {error}")
            state.log.append({"round": number, "error": str(error)})
            tools.save_log(state.log)
            state.rounds = number
            # Force "adjust" next time: the current search just failed.
            state.received, state.last_passed = 0, 0
            return
