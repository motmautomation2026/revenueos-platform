"""
archive/legacy/supabase_store_cli.py
====================================

The old CLI parts of supabase_store.py (paid / send / file-based commands that skipped the ledger and the
approvals), moved here word for word on 2026-10-13. The run path uses the orchestrator runners instead.
Kept for reference only: NOT runnable as is and never imported by the run path.
"""

from supabase_store import *          # noqa: F401,F403


# Input files written by quickicp.py
COMPANIES_FILE = "data/quickicp_raw.json"             # all companies found; only used by quickicp.py 'all'


# The old 'save' command of supabase_store.py: wrote data/ files to Supabase outside the ledger.
def old_save_command(customer):
    # Step 1: PASSED companies first (same as pipeline.py save), so people can be linked to them.
    lookups = save_companies(load_json(PASSED_COMPANIES_FILE), customer)
    # Step 2: people, linked to this customer's companies by domain, LinkedIn or name.
    save_people(load_json(PEOPLE_FILE), lookups, customer)
