"""
pipeline.py
===========

The full QuickICP flow, connected end to end, with validation and shortfall loops.

STEPS (in order)
----------------
    companies   company search + company judge + shortfall loop   -> data/companies_passed.json
    people      people search + people judge + shortfall loop     -> data/people_passed.json
                (only in passed companies)
    emails      email finder on PASSED people only                -> data/quickicp_emails_raw.json
    phones      phone finder on people with emails                -> data/quickicp_phones_raw.json
    save        Supabase: passed companies + enriched people

WHY THIS ORDER
--------------
Validation and the shortfall loops run BEFORE the paid enrichment steps, so emails and phones
are only bought for companies and people that fit the customer, and in the right quantity.

RUN
---
    python pipeline.py              run every step from the start
    python pipeline.py emails       resume from a step (earlier steps are NOT rerun)

DEBUGGING
---------
    - The step that failed is printed with the exact command to resume.
    - Every step's output is a JSON file in data/.
    - Round-by-round details: data/company_loop_log.json, data/people_loop_log.json
    - Verdicts with reasons:  data/companies_judged.json, data/people_judged.json
"""

import sys
import traceback

from company_loop import run_loop
from people_loop import run_people_loop
from quickicp import ToolError, check_settings, load_json, run_emails, run_phones


# ================= STEPS =================

def step_companies() -> None:
    """Company search, judged, with the shortfall loop."""
    run_loop()


def step_people() -> None:
    """People search in passed companies, judged, with the shortfall loop."""
    run_people_loop()
    if not load_json("data/people_passed.json"):
        raise ToolError("No people passed validation. Check data/people_judged.json for the reasons.")


def step_emails() -> None:
    """Email finder on passed people only."""
    run_emails(input_path="data/people_passed.json")


def step_phones() -> None:
    """Phone finder on people who went through the email finder."""
    run_phones()


def step_save() -> None:
    """Save passed companies and enriched people to Supabase."""
    check_settings("SUPABASE_URL", "SUPABASE_KEY")
    # Imported here so the pipeline can run the other steps even if Supabase is not configured.
    from supabase_store import current_customer, save_companies, save_people
    customer = current_customer()   # fixed CUSTOMER_KEY from .env: rows never mix between customers
    lookups = save_companies(load_json("data/companies_passed.json"), customer)
    save_people(load_json("data/quickicp_phones_raw.json"), lookups, customer)


STEPS = [
    ("companies", step_companies),
    ("people", step_people),
    ("emails", step_emails),
    ("phones", step_phones),
    ("save", step_save),
]


# ================= RUN =================

def run(start_step: str) -> None:
    """Run every step from start_step to the end; stop with a resume command on any failure."""
    names = [name for name, _ in STEPS]
    start = names.index(start_step)

    for number, (name, step) in enumerate(STEPS[start:], start=start + 1):
        print(f"\n{'=' * 20} STEP {number}/{len(STEPS)}: {name.upper()} {'=' * 20}")
        try:
            step()
        except ToolError as error:
            print(f"\nSTOPPED at step '{name}': {error}")
            print(f"Fix the issue, then resume with:  python pipeline.py {name}")
            sys.exit(1)
        except Exception:
            print(f"\nUNEXPECTED ERROR at step '{name}' (details below):")
            traceback.print_exc()
            print(f"\nFix the issue, then resume with:  python pipeline.py {name}")
            sys.exit(1)

    print("\nPIPELINE COMPLETE: validated companies and people are enriched and saved.")


if __name__ == "__main__":
    names = [name for name, _ in STEPS]
    start_step = sys.argv[1] if len(sys.argv) > 1 else names[0]
    if start_step not in names:
        print("Usage: python pipeline.py [" + " | ".join(names) + "]")
        sys.exit(1)
    run(start_step)