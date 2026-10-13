"""
archive/legacy/people_loop_cli.py
=================================

OLD standalone CLI, moved here from people_loop.py on 2026-10-13 (word for word). Replaced by the orchestrator
runners (runner_quickicp_contacts.py), which save every paid call in the ledger and ask decisions through the orchestrator.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from people_loop import *          # the shared round code it used  # noqa: F401,F403
from goal_check import StopRequested, ask_on_shortfall          # archive/legacy/goal_check.py  # noqa: F401

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