"""
people_loop.py
==============

People search with validation, an automatic shortfall loop, and a GOAL CHECK that
asks a human when the loop is still short.

WHY
---
The people judge drops off-target people (wrong function, too junior). If too few pass,
this loop searches for more, NEW people only, up to MAX_EXTRA_ROUNDS times.
If it is STILL short after that, it saves everything and asks you (goal_check.py):
continue, try more, or stop.

FLOW
----
    Round 0   people search in the best passed companies (highest company score first)
              -> keep new people -> people judge
    Round 1+  only while passed people < target, at most MAX_EXTRA_ROUNDS:
              MORE_COMPANIES   - search unused passed companies (used while any are left)
              MORE_PER_COMPANY - all companies used: ask for more people per company
              NOTHING LEFT     - all companies used AND per company already at MAX_PER_COMPANY:
                                 no round is sent (it would only return repeats)
    GOAL CHECK  still short -> results saved -> you choose:
              1 continue  - move on with who passed (shortfall recorded)
              2 more      - run MAX_EXTRA_ROUNDS more rounds (allowed MAX_USER_RETRIES times,
                            and only while there is still something left to try)
              3 stop      - stop here; everything is already saved

HOW MUCH TO ASK FOR
-------------------
    We ask for target x OVERSAMPLE people (e.g. 100 x 1.5 = 150), because some will fail.
    count_per_company is capped by the tool; we never ask for more than MAX_PER_COMPANY.

WHO DECIDES WHAT
----------------
    Code : target, shortfall, which companies to use, people per company, de-duplication,
           when nothing is left to try, when to ask
    LLM  : job titles to search (once), and each title's function (people judge in validator.py)
    Human: continue / more / stop when the goal is not met after the retries

READS
-----
    data/companies_passed.json     companies that passed the company judge
    data/work_orders.json          QuickICP contacts work order (target, roles)

WRITES
------
    data/quickicp_people_raw.json  every person found, across all rounds (no duplicates)
    data/people_judged.json        every person with their verdict
    data/people_passed.json        only passed people (used by the email finder)
    data/people_loop_log.json      one entry per round: strategy, companies, counts
    data/decisions.json            the goal-check decision, if one was needed (via goal_check.py)

RUN
---
    python people_loop.py

ORCHESTRATOR
------------
    The orchestrator's runner (runner_quickicp_contacts.py) reuses run_round() / run_extra_rounds()
    with its own PeopleLoopTools: every paid people-search BATCH recorded in brain_tool_calls before
    it is made, the state saved after every round (LoopState.to_json / from_json). This CLI uses
    REAL_TOOLS and behaves exactly as before.
    (normally run by pipeline.py as the 'people' step)

TEST MODE
---------
    To see the goal check with NO extra credits: set TEST_PEOPLE_TARGET = 50 and
    MAX_EXTRA_ROUNDS = 0 (only round 0 runs), then choose 1 or 3 when asked.
    Put both values back afterwards (10 and 2).

DEBUGGING
---------
    Open data/people_loop_log.json: each round shows its strategy, how many companies and
    people per company were used, how many people were new, and how many passed.
    Goal-check entries show the decision. Open data/decisions.json for every decision.
"""

import math
import sys
import traceback
from dataclasses import dataclass, field

from goal_check import StopRequested, ask_on_shortfall
from quickicp import (
    DOMAIN_BATCH_SIZE,
    PEOPLE_LOCATIONS,
    TEST_MODE,
    TEST_PEOPLE_PER_COMPANY,
    PeoplePayload,
    ToolError,
    check_settings,
    clean_domain,
    load_json,
    load_quickicp_work_order,
    make_job_titles,
    max_number,
    save_json,
    search_people,
)
from validator import PEOPLE_PASS_SCORE, judge_people

MAX_EXTRA_ROUNDS = 2      # extra searches after round 0 (and per "try more")
MAX_USER_RETRIES = 2      # how many times the human may choose "try more"
OVERSAMPLE = 1.5          # ask for 1.5x the people needed, since some will fail the judge
MAX_PER_COMPANY = 10      # upper limit for count_per_company
# Test runs: a small target keeps credits low. The platform's Staging switch passes the person's
# own number as STAGING_PEOPLE (run_for.py).
from quickicp import staging_number
TEST_PEOPLE_TARGET = staging_number("STAGING_PEOPLE", 10)


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
    The calls one round makes. 'python people_loop.py' uses these real ones (same behaviour as
    before). The orchestrator's runner passes its own version: every people-search batch saved in
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
        """Called after every round; the orchestrator saves the state here. Nothing in the CLI."""


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


# ================= COMPANY QUEUE =================

def company_queue() -> list[str]:
    """Domains of passed companies, best company score first, no duplicates."""
    companies = load_json("data/companies_passed.json")
    if not companies:
        raise ToolError("No companies passed validation. Run the company step first.")
    companies.sort(key=lambda c: c.get("validation", {}).get("score") or 0, reverse=True)
    queue = []
    for company in companies:
        domain = clean_domain(company.get("WEBSITE"))
        if domain and domain not in queue:
            queue.append(domain)
    return queue


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
        except StopRequested:
            raise
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


def save_results(state: LoopState) -> list[dict]:
    """Save every output file. Called before asking the human, and at the end."""
    passed_people = [p for p in state.all_judged if p["validation"]["status"] == "pass"]
    save_json("data/quickicp_people_raw.json", state.all_raw)
    save_json("data/people_judged.json", state.all_judged)
    save_json("data/people_passed.json", passed_people)
    save_json("data/people_loop_log.json", state.log)
    return passed_people


# ================= THE LOOP =================

def run_people_loop() -> None:
    check_settings("OPENAI_MODEL", "QUICKICP_PEOPLE_WEBHOOK_URL")
    work_order = load_quickicp_work_order("contacts")
    target = people_target(work_order)
    queue = company_queue()
    per_company = starting_per_company(target, len(queue))
    print(f"Target: {target} passed people (pass score {PEOPLE_PASS_SCORE}, max extra rounds {MAX_EXTRA_ROUNDS})")
    print(f"Passed companies available: {len(queue)} | starting people per company: {per_company}")

    job_titles = make_job_titles(work_order)       # generated once, reused every round
    print("Job titles:", job_titles)
    state = LoopState(target=target, queue=queue, job_titles=job_titles, per_company=per_company)

    # Round 0: the best passed companies.
    print("\n===== ROUND 0 =====")
    domains = queue[:companies_needed(target, per_company)]
    state.used += domains
    run_round(0, "initial", domains, state)

    # Extra rounds, then a goal check if still short.
    user_retries = 0
    run_extra_rounds(state, MAX_EXTRA_ROUNDS)
    while count_passed(state.all_judged) < state.target:
        save_results(state)   # never ask before everything is safely saved
        left = something_left_to_try(state)
        decision = ask_on_shortfall(
            step="people",
            target=state.target,
            achieved=count_passed(state.all_judged),
            rounds_done=state.rounds + 1,
            can_retry=left and user_retries < MAX_USER_RETRIES,
            details=round_summary(state),
            more_label=f"Try {MAX_EXTRA_ROUNDS} more rounds ({MAX_USER_RETRIES - user_retries} left)",
        )
        state.log.append({"goal_check": "people", "decision": decision,
                          "passed": count_passed(state.all_judged), "target": state.target,
                          "something_left_to_try": left})
        save_json("data/people_loop_log.json", state.log)

        if decision == "more":
            user_retries += 1
            run_extra_rounds(state, MAX_EXTRA_ROUNDS)
            continue
        if decision == "stop":
            raise StopRequested("Stopped at the people goal check. Everyone found so far is saved "
                                "in data/people_passed.json.")
        break   # continue with who we have

    passed_people = save_results(state)
    shortfall = max(0, state.target - len(passed_people))
    print(f"\nDONE: {len(passed_people)} passed of target {state.target} "
          f"across {len([e for e in state.log if 'strategy' in e])} round(s)")
    if shortfall:
        print(f"Continuing with a shortfall of {shortfall} (decision in data/decisions.json).")
    print("Saved quickicp_people_raw.json, people_judged.json, people_passed.json, people_loop_log.json")


# ================= RUN =================

if __name__ == "__main__":
    try:
        run_people_loop()
    except ToolError as error:
        print("\nSTOPPED:", error)
        sys.exit(1)
    except Exception:
        print("\nUNEXPECTED ERROR (details below for debugging):")
        traceback.print_exc()
        sys.exit(1)