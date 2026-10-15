"""
orch/common.py
==============

Shared by every job: data file names, months and run keys, finding a run, a run's plan approval.
Moved unchanged from orchestrator.py (Stage C, 2026-10-15). Import it through orchestrator, which
re-exports every name; this file never imports orchestrator.
"""

import re
from quickicp import ToolError
from state_store import check_customer_key, get_decisions, get_run, get_runs

WORK_ORDERS_FILE = "data/work_orders.json"
PLAN_FILE = "data/month_plan.json"
REVENUE_FILE = "data/revenue_analysis.json"
UNDERSTANDING_FILE = "data/understanding.json"

# 2026-10, or a revision of that month that replaces a closed run: 2026-10-r2, 2026-10-r3, ... (no -r0 / -r1).
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-r([2-9]|[1-9]\d+))?$")


# ================= PLAN CHECKS (code, no LLM) =================

def check_month(month: str) -> str:
    """Return month if it looks like 2026-10 or 2026-10-r2; stop otherwise."""
    if not MONTH_PATTERN.match(month or ""):
        raise ToolError(f"STOPPED: month must look like 2026-10 (YYYY-MM) or 2026-10-r2 (a revision), got '{month}'.")
    return month


def month_of(run: dict) -> str:
    """The month part of the run key: 'anvixa-ai-product-2026-10-r2' -> '2026-10-r2'."""
    return run["run_key"][len(run["customer"]) + 1:]


def run_key_for(customer: str, month: str) -> str:
    """'anvixa-ai-product' + '2026-10' -> 'anvixa-ai-product-2026-10'."""
    return f"{check_customer_key(customer)}-{check_month(month)}"


def approval_of(run_id: int) -> dict | None:
    """The run's answered plan_approval 'yes' decision, or None."""
    approvals = [d for d in get_decisions(run_id)
                 if d["kind"] == "plan_approval" and d["status"] == "answered" and d["answer"] == "yes"]
    return approvals[-1] if approvals else None


# ================= READ-ONLY VIEWS =================

def find_run(customer: str, month: str | None) -> dict:
    """The run for month, or this customer's latest run when month is None."""
    if month:
        run = get_run(run_key_for(customer, month))
        if not run:
            raise ToolError(f"STOPPED: no run {run_key_for(customer, month)}. "
                            f"Load it first: python orchestrator.py load {month}")
        return run
    runs = get_runs(customer)
    if not runs:
        raise ToolError(f"STOPPED: no run for customer '{customer}'. Load one: python orchestrator.py load <YYYY-MM>")
    return runs[-1]
