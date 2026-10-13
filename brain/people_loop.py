"""
people_loop.py
==============

The people search ROUNDS used by the orchestrator's contacts runner (runner_quickicp_contacts.py):
one round = search people in a batch of companies -> keep only new people -> judge; extra rounds use
more companies or more people per company. No standalone CLI any more: the old 'python people_loop.py'
loop, its company queue and its goal_check.py prompt are in archive/legacy/ (the goal check is now an
orchestrator decision).

FLOW (run_round / run_extra_rounds)
----
    Round 0   people search in the best passed companies (highest company score first)
              -> keep new people -> people judge
    Round 1+  only while passed people < target, at most MAX_EXTRA_ROUNDS:
              MORE_COMPANIES   - search unused passed companies (used while any are left)
              MORE_PER_COMPANY - all companies used: ask for more people per company
              NOTHING LEFT     - all companies used AND per company already at MAX_PER_COMPANY:
                                 no round is sent (it would only return repeats)
    Still short after the rounds -> the runner opens a goal_check decision (continue / more / stop).

HOW MUCH TO ASK FOR
-------------------
    We ask for target x OVERSAMPLE people (e.g. 100 x 1.5 = 150), because some will fail.
    count_per_company is capped by the tool; we never ask for more than MAX_PER_COMPANY.

WHO DECIDES WHAT
----------------
    Code : target, shortfall, which companies to use, people per company, de-duplication,
           when nothing is left to try
    LLM  : job titles to search (once), and each title's function (people judge in validator.py)
    Human: continue / more / stop, through the orchestrator's goal_check decision

READS / WRITES
--------------
    Through the PeopleLoopTools the runner passes: every paid people-search BATCH recorded in
    brain_tool_calls before it is made, the state saved after every round.

TEST MODE
---------
    With TEST_MODE on (in quickicp.py), the target is TEST_PEOPLE_TARGET.

DEBUGGING
---------
    brain_tool_calls rows of the task: step 'state' (each round's strategy, companies, counts) and
    data/runs/<run_key>/<task>_people_loop_log.json.
"""

import math
from dataclasses import dataclass, field

from quickicp import (
    DOMAIN_BATCH_SIZE,
    PEOPLE_LOCATIONS,
    TEST_MODE,
    TEST_PEOPLE_PER_COMPANY,
    PeoplePayload,
    ToolError,
    max_number,
    save_json,
    search_people,
)
from validator import judge_people

MAX_EXTRA_ROUNDS = 2      # extra searches after round 0 (and per "try more")
MAX_USER_RETRIES = 2      # how many times the human may choose "try more"
OVERSAMPLE = 1.5          # ask for 1.5x the people needed, since some will fail the judge
MAX_PER_COMPANY = 10      # upper limit for count_per_company
TEST_PEOPLE_TARGET = 10   # test runs: small target to keep credits low


# ================= LOOP STATE =================

@dataclass
class LoopState:
    """Everything the rounds share, so 'try more' can continue exactly where it stopped."""
    target: int
    queue: list                   # passed company domains, best first
    job_titles: list
    per_company: int
    used: list = field(default_factory=list)         # companies already searched
    seen: set = field(default_factory=set)           # people already found (LinkedIn URL)
    all_raw: list = field(default_factory=list)
    all_judged: list = field(default_factory=list)
    log: list = field(default_factory=list)
    rounds: int = 0                                  # rounds sent (0 = only round 0 so far)

    def to_json(self) -> dict:
        """Everything needed to continue later (saved by the orchestrator after every round)."""
        return {"target": self.target, "queue": self.queue, "job_titles": self.job_titles,
                "per_company": self.per_company, "used": self.used, "seen": sorted(self.seen),
                "all_raw": self.all_raw, "all_judged": self.all_judged, "log": self.log, "rounds": self.rounds}

    @classmethod
    def from_json(cls, data: dict) -> "LoopState":
        """Rebuild a LoopState saved with to_json()."""
        return cls(target=data["target"], queue=data["queue"], job_titles=data["job_titles"],
                   per_company=data["per_company"], used=data["used"], seen=set(data["seen"]),
                   all_raw=data["all_raw"], all_judged=data["all_judged"], log=data["log"], rounds=data["rounds"])


# ================= EXTERNAL CALLS OF ONE ROUND =================

class PeopleLoopTools:
    """
    The calls one round makes (the base the runners build on; the real search / judge).
    The orchestrator's runner passes its own version: every people-search batch saved in
    brain_tool_calls BEFORE it is made, and a fake version in its selftest (no network, no LLM).
    """

    def search_batch(self, payload: PeoplePayload, round_number: int, batch_number: int) -> list[dict]:
        """The PAID people search for one batch of companies (n8n)."""
        return search_people(payload)

    def judge(self, people: list[dict]) -> list[dict]:
        """The people judge (LLM only for job titles not yet in the map)."""
        return judge_people(people)

    def save_raw(self, people: list[dict]) -> None:
        save_json("data/quickicp_people_raw.json", people)

    def save_log(self, log: list[dict]) -> None:
        save_json("data/people_loop_log.json", log)

    def after_round(self, state: "LoopState") -> None:
        """Called after every round; the orchestrator's runner saves the state here."""


REAL_TOOLS = PeopleLoopTools()


# ================= NUMBERS (CODE) =================

def people_target(work_order: dict) -> int:
    """How many people must pass. Real runs: the largest number in the work order target."""
    return TEST_PEOPLE_TARGET if TEST_MODE else max_number(work_order.get("target", ""), 50)


def count_passed(judged: list[dict]) -> int:
    return sum(1 for person in judged if person["validation"]["status"] == "pass")


def companies_needed(people_wanted: int, per_company: int) -> int:
    """How many companies to search to get this many people: 150 people / 3 each -> 50 companies."""
    return max(1, math.ceil(people_wanted * OVERSAMPLE / per_company))


def starting_per_company(target: int, company_count: int) -> int:
    """People per company for round 0: spread the target over the passed companies."""
    if TEST_MODE:
        return TEST_PEOPLE_PER_COMPANY
    return min(MAX_PER_COMPANY, max(1, math.ceil(target * OVERSAMPLE / company_count)))


def something_left_to_try(state: LoopState) -> bool:
    """False when every company was searched AND people per company is already at the maximum."""
    unused = [d for d in state.queue if d not in state.used]
    return bool(unused) or state.per_company < MAX_PER_COMPANY


# ================= ONE ROUND =================

def search_round(domains: list[str], job_titles: list[str], per_company: int,
                 tools: PeopleLoopTools = REAL_TOOLS, round_number: int = 0) -> tuple[list[dict], int]:
    """
    Search people in these domains, in batches. A failed batch is skipped, the rest continue.
    Returns (people found, number of failed batches).
    """
    people, failed = [], 0
    for batch_number, start in enumerate(range(0, len(domains), DOMAIN_BATCH_SIZE), start=1):
        batch = domains[start:start + DOMAIN_BATCH_SIZE]
        payload = PeoplePayload(domains=batch, job_titles=job_titles, locations=PEOPLE_LOCATIONS,
                                count_per_company=per_company)
        try:
            people.extend(tools.search_batch(payload, round_number, batch_number))
        except ToolError as error:
            failed += 1
            print(f"  Batch of {len(batch)} companies failed: {error}")
    return people, failed


def run_round(number: int, strategy: str, domains: list[str], state: LoopState,
              tools: PeopleLoopTools = REAL_TOOLS) -> None:
    """Search, keep only people not seen before, judge them, log the round, update the state."""
    found, failed = search_round(domains, state.job_titles, state.per_company, tools, number)

    new = []
    for person in found:
        key = person.get("USER SOCIAL") or f"{person.get('FULL NAME')}|{person.get('COMPANY URL')}"
        if key not in state.seen:
            state.seen.add(key)
            new.append(person)
    state.all_raw.extend(new)
    tools.save_raw(state.all_raw)

    judged = tools.judge(new) if new else []
    state.all_judged += judged
    passed = count_passed(judged)
    state.rounds = number

    state.log.append({
        "round": number,
        "strategy": strategy,
        "companies": len(domains),
        "per_company": state.per_company,
        "received": len(found),
        "new": len(new),
        "passed": passed,
        "failed_batches": failed,
    })
    tools.save_log(state.log)

    print(f"\nRound {number} [{strategy}, {len(domains)} companies x {state.per_company}]: "
          f"received {len(found)}, new {len(new)}, passed {passed}")
    for person in judged:
        v = person["validation"]
        print(f"  {v['status'].upper():6} {str(v['score']):>4} [{v['checked_by']:4}] "
              f"{person.get('FULL NAME')} | {person.get('JOB POSITION')} | {person.get('COMPANY NAME')}"
              f"  -  {v.get('buying_role')}: {v['reason']}")
    tools.after_round(state)


def run_extra_rounds(state: LoopState, how_many: int, tools: PeopleLoopTools = REAL_TOOLS) -> None:
    """Up to how_many extra rounds; stops early when the target is met, nothing is left, or a round fails."""
    for _ in range(how_many):
        passed = count_passed(state.all_judged)
        if passed >= state.target:
            return
        if not something_left_to_try(state):
            print(f"\nNo more ways to find people: all {len(state.queue)} passed companies searched "
                  f"at the maximum of {MAX_PER_COMPANY} people each.")
            return
        number = state.rounds + 1
        shortfall = state.target - passed
        print(f"\n===== ROUND {number}: short by {shortfall} =====")

        unused = [d for d in state.queue if d not in state.used]
        if unused:
            # Unused passed companies left: search people there, same people per company.
            strategy = "more_companies"
            domains = unused[:companies_needed(shortfall, state.per_company)]
            state.used += domains
        else:
            # Every company used: ask for more people per company (new ones are kept, repeats skipped).
            strategy = "more_per_company"
            state.per_company = min(MAX_PER_COMPANY, state.per_company * 2)
            domains = list(state.used)

        try:
            run_round(number, strategy, domains, state, tools)
        except ToolError as error:
            # A failed extra round never loses earlier results: stop these rounds, keep what we have.
            print(f"Round {number} stopped: {error}")
            state.log.append({"round": number, "error": str(error)})
            tools.save_log(state.log)
            state.rounds = number
            return


def round_summary(state: LoopState) -> dict:
    """Facts for the human at the goal check."""
    judged = state.all_judged
    return {
        "people_judged": len(judged),
        "passed": count_passed(judged),
        "failed_company_check": sum(1 for p in judged if p["validation"]["checked_by"] == "code"
                                    and p["validation"]["status"] == "fail"),
        "failed_role": sum(1 for p in judged if p["validation"]["checked_by"] != "code"
                           and p["validation"]["status"] == "fail"),
        "review": sum(1 for p in judged if p["validation"]["status"] == "review"),
        "companies_searched": f"{len(state.used)} of {len(state.queue)}",
        "people_per_company": f"{state.per_company} (max {MAX_PER_COMPANY})",
    }
