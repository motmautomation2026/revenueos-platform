"""
orch_tests/common.py
====================

Helpers every part uses (e2e_test.py imports some through orchestrator_selftest): expect, raises, the network guard, the selftest-* sweep, the fixtures.
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from contextlib import contextmanager
from orchestrator import *
from state_store import delete_test_run

# ================= SELFTEST (throwaway selftest-* run) =================

def expect(label: str, condition: bool) -> None:
    """Print PASS, or stop the test with the label."""
    if not condition:
        raise AssertionError(label)
    print(f"  PASS  {label}")


def raises(function) -> bool:
    """True if calling function() raises ToolError."""
    try:
        function()
    except ToolError:
        return True
    return False


def fake_work_orders() -> list[dict]:
    """4 small work orders: T1 and T2 (no deps, weeks 2 and 1), T3 after T1+T2, T4 after T3."""
    def wo(task_id, week, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": "QuickICP", "output_type": "accounts",
                "week": week, "depends_on": deps, "target": target, "instructions": ["test"]}
    return [wo("T1", 2, [], "40 accounts"), wo("T2", 1, [], "no numeric target"),
            wo("T3", 3, ["T1", "T2"], "10 contacts"), wo("T4", 4, ["T3"], "5 calls")]


def no_real_toolset(ctx):
    """Stands in for every REAL toolset during the selftest: using one is a test failure."""
    raise AssertionError(f"a real toolset was requested in the selftest ({ctx.task['task_id']})")


def sweep_selftest_runs(customer: str) -> tuple[int, bool]:
    """
    Delete EVERY run of a selftest-* customer, also one a failed load left half-done (not in the test's
    own list). delete_test_run() refuses anything not named selftest-*. Returns (rows deleted, any left).
    """
    deleted = sum(delete_test_run(run["id"]) for run in get_runs(customer))
    return deleted, bool(get_runs(customer))


def run_work_orders() -> list[dict]:
    """
    6 tasks for the run test:
        T1 CAM requirements (human)            T4 Email Agent after T1 (tool, NOT wired yet, week 1)
        T2 Content & Training after T1 (GATE)  T5 Human Tele Calling after T2
        T3 CAM review after T1 (human)         T6 CAM handover after T5
    """
    def wo(task_id, agent, output, types, week, deps):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": types, "week": week, "depends_on": deps, "target": "test",
                "instructions": ["test"], "done_when": f"{task_id} done", "escalate_if": "never"}
    return [wo("T1", "CAM", "coordination", "requirements, coordination", 1, []),
            wo("T2", "Content & Training", "client_training", "client_training, content", 2, ["T1"]),
            wo("T3", "CAM", "review", "review", 2, ["T1"]),
            wo("T4", "Email Agent", "emails", "outreach", 1, ["T1"]),
            wo("T5", "Human Tele Calling", "qualified_leads", "qualification", 3, ["T2"]),
            wo("T6", "CAM", "handover", "handover", 4, ["T5"])]


@contextmanager
def network_guard():
    """Selftest safety net: any HTTP call to a host other than Supabase is blocked and listed."""
    import httpx
    from urllib.parse import urlparse
    allowed = urlparse(os.getenv("SUPABASE_URL") or "").hostname
    blocked: list[str] = []
    original = httpx.Client.send

    def guarded(self, request, *args, **kwargs):
        if request.url.host != allowed:
            blocked.append(str(request.url.host))
            raise RuntimeError(f"selftest network guard: blocked a call to {request.url.host}")
        return original(self, request, *args, **kwargs)

    import requests
    original_requests = requests.Session.send

    def guarded_requests(self, request, *args, **kwargs):      # quickhowl.py uses 'requests'
        host = urlparse(request.url).hostname
        if host != allowed:
            blocked.append(str(host))
            raise RuntimeError(f"selftest network guard: blocked a call to {host}")
        return original_requests(self, request, *args, **kwargs)

    httpx.Client.send = guarded
    requests.Session.send = guarded_requests
    try:
        yield blocked
    finally:
        httpx.Client.send = original
        requests.Session.send = original_requests


def outreach_fixture(customer: str, month: str, agent: str, output: str, person_ids: list[int]) -> dict:
    """A selftest run: T1 = done QuickICP contacts task with person ids; T2 = the outreach task after it."""
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 2, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent_, output_, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent_, "output_type": output_,
                "task_type": "outreach", "week": 1, "depends_on": deps, "target": target, "instructions": ["test"]}
    run, _ = load_run(customer, month, [wo("T1", "QuickICP", "contacts", [], "10 contacts"),
                                        wo("T2", agent, output, ["T1"], "contacts reached")],
                      {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
    t1 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T1"][0]
    update_task(t1["id"], status="done", result={"people_ids": person_ids, "customer": customer, "run_key": run["run_key"]})
    return run
