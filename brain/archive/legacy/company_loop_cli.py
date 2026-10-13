"""
archive/legacy/company_loop_cli.py
==================================

OLD standalone CLI, moved here from company_loop.py on 2026-10-13 (word for word). Replaced by the orchestrator
runners (runner_quickicp_accounts.py), which save every paid call in the ledger and ask decisions through the orchestrator.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from company_loop import *          # the shared round code it used  # noqa: F401,F403
from goal_check import StopRequested, ask_on_shortfall          # archive/legacy/goal_check.py  # noqa: F401

def save_results(state: LoopState) -> list[dict]:
    """Save every output file. Called before asking the human, and at the end."""
    passed_companies = [c for c in state.all_judged if c["validation"]["status"] == "pass"]
    save_json("data/quickicp_raw.json", state.all_raw)
    save_json("data/companies_judged.json", state.all_judged)
    save_json("data/companies_passed.json", passed_companies)
    save_json("data/company_loop_log.json", state.log)
    return passed_companies


# ================= THE LOOP =================

def run_loop() -> None:
    check_settings("OPENAI_MODEL", "PROSPEO_API_KEY", "QUICKICP_WEBHOOK_URL")
    work_order = load_quickicp_work_order("accounts")
    understanding = load_json("data/understanding.json")
    state = LoopState(work_order=work_order, target=passed_target(work_order))
    print(f"Target: {state.target} passed companies (pass score {PASS_SCORE}, "
          f"max extra rounds {MAX_EXTRA_ROUNDS})")

    # Round 0: the normal search from the work order (coveredpages = 0).
    print("\n===== ROUND 0 =====")
    payload = build_company_payload(work_order, understanding)
    run_round(0, "initial", payload, state, "Initial search from the work order")

    # Extra rounds, then a goal check if still short.
    user_retries = 0
    run_extra_rounds(state, MAX_EXTRA_ROUNDS)
    while count_passed(state.all_judged) < state.target:
        save_results(state)   # never ask before everything is safely saved
        decision = ask_on_shortfall(
            step="companies",
            target=state.target,
            achieved=count_passed(state.all_judged),
            rounds_done=state.rounds + 1,
            can_retry=user_retries < MAX_USER_RETRIES,
            details=rejection_summary(state.all_judged),
            more_label=f"Try {MAX_EXTRA_ROUNDS} more rounds ({MAX_USER_RETRIES - user_retries} left)",
        )
        state.log.append({"goal_check": "companies", "decision": decision,
                          "passed": count_passed(state.all_judged), "target": state.target})
        save_json("data/company_loop_log.json", state.log)

        if decision == "more":
            user_retries += 1
            run_extra_rounds(state, MAX_EXTRA_ROUNDS)
            continue
        if decision == "stop":
            raise StopRequested("Stopped at the companies goal check. Everything found so far is saved "
                                "in data/companies_passed.json.")
        break   # continue with what we have

    passed_companies = save_results(state)
    shortfall = max(0, state.target - len(passed_companies))
    print(f"\nDONE: {len(passed_companies)} passed of target {state.target} "
          f"across {len([e for e in state.log if 'payload' in e])} round(s)")
    if shortfall:
        print(f"Continuing with a shortfall of {shortfall} (decision in data/decisions.json).")
    print("Saved quickicp_raw.json, companies_judged.json, companies_passed.json, company_loop_log.json")


# ================= RUN =================

if __name__ == "__main__":
    try:
        run_loop()
    except ToolError as error:
        print("\nSTOPPED:", error)
        sys.exit(1)
    except Exception:
        print("\nUNEXPECTED ERROR (details below for debugging):")
        traceback.print_exc()
        sys.exit(1)