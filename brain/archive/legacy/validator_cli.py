"""
archive/legacy/validator_cli.py
===============================

The old CLI parts of validator.py (paid / send / file-based commands that skipped the ledger and the
approvals), moved here word for word on 2026-10-13. The run path uses the orchestrator runners instead.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from validator import *          # noqa: F401,F403


def customer_name() -> str:
    return load_json("data/understanding.json").get("customer_name") or "customer"


def run_companies() -> None:
    check_settings("OPENAI_MODEL")
    companies = load_json("data/quickicp_raw.json")
    if not companies:
        raise ToolError("data/quickicp_raw.json is empty. Run 'python quickicp.py companies' first.")
    print(f"Judging {len(companies)} companies (pass score: {PASS_SCORE})")

    judged = judge_companies(companies)
    save_json("data/companies_judged.json", judged)
    save_json("data/companies_passed.json", [c for c in judged if c["validation"]["status"] == "pass"])

    for company in judged:
        v = company["validation"]
        print(f"{v['status'].upper():6} {str(v['score']):>4} [{v['checked_by']:4}] "
              f"{company.get('COMPANY')}  -  {v['reason']}")
    print_counts(judged)
    print("Saved data/companies_judged.json and data/companies_passed.json")


def passed_company_keys() -> set[str]:
    """Website domains and company LinkedIn pages of every company that passed step 1."""
    keys = set()
    for company in load_json("data/companies_passed.json"):
        keys.add(clean_domain(company.get("WEBSITE")))
        keys.add(linkedin_key(company.get("LINKEDIN")))
    keys.discard("")
    return keys


def run_people() -> None:
    check_settings("OPENAI_MODEL")
    people = load_json("data/quickicp_people_raw.json")
    if not people:
        raise ToolError("data/quickicp_people_raw.json is empty. Run 'python quickicp.py people' first.")
    print(f"Judging {len(people)} people (pass score: {PEOPLE_PASS_SCORE})")

    judged = judge_people(people)
    save_json("data/people_judged.json", judged)
    save_json("data/people_passed.json", [p for p in judged if p["validation"]["status"] == "pass"])

    for person in judged:
        v = person["validation"]
        print(f"{v['status'].upper():6} {str(v['score']):>4} [{v['checked_by']:4}] "
              f"{person.get('FULL NAME')} | {person.get('JOB POSITION')} | {person.get('COMPANY NAME')}  "
              f"-  {v.get('buying_role')}: {v['reason']}")
    print_counts(judged)
    print("Saved data/people_judged.json and data/people_passed.json")


def run_all() -> None:
    print("===== COMPANY JUDGE =====")
    run_companies()
    print("\n===== PEOPLE JUDGE =====")
    run_people()
