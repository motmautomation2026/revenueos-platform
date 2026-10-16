"""
orch_tests/part_manual.py
=========================

Part 'manual tools'.
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from datetime import datetime, timezone
from orchestrator import *
from orch_tests.common import expect, raises, sweep_selftest_runs


# ================= MANUAL TOOLS: campaign from the ledger; pause never hits the wrong campaign =================

def selftest_manual_tools() -> bool:
    """quickhowl status/results/watch/pause and email_tool samples with --run: ledger only, fake API, no network."""
    import email_tool
    import quickhowl
    from state_store import finish_step, get_steps, start_step
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    tag = customer.split("-")[-1]                   # ledger keys are unique across ALL runs: one set per test run
    ref_a, ref_b, email_ref = f"motm-st{tag}-call-a", f"motm-st{tag}-call-b", f"MOTM-ST{tag}-HYPER-1"
    print(f"\nOrchestrator MANUAL TOOLS selftest (ledger lookups, fake Quickhowl / email tool): customer '{customer}'")
    proof = {"approved_at": "2026-10-13T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 3, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "outreach", "week": 1, "depends_on": [], "target": "3", "instructions": ["test"]}
    orders = [wo("T1", "Quickhowl", "calls"), wo("T2", "Email Agent", "emails"), wo("T3", "CAM", "coordination")]
    saved_key = os.environ.get("CUSTOMER_KEY")
    real_call_api, real_call_tool = quickhowl.call_api, email_tool.call_tool
    api, fake = [], {"status": "running", "reply_reference": None}

    def call_api(method, path, params=None, body=None, timeout=120):
        api.append((method, path, dict(params or {})))
        ref = path.split("/")[2]
        if method == "POST":
            return {"paused": True, "status": "paused", "reason": "selftest"}
        if path.endswith("/calls"):
            return {"items": [{"call_id": "c1", "contact": {"name": "Asha"}, "outcome": "interested", "finished": True}],
                    "has_more": False, "campaign_status": fake["status"]}
        return {"reference": fake["reply_reference"] or ref, "status": fake["status"],
                "contacts_still_to_call": 0 if fake["status"] == "completed" else 2,
                "contacts_total": 3, "calls_total": 1}

    tool_urls = []

    def call_tool(method, url, body=None):
        tool_urls.append((method, url))
        return 200, {"samples_status": "ready", "approval_status": "NONE",
                     "samples": [{"email_number": 1, "subject": "Hello", "body_html": "<p>Hi</p>"}]}

    def stored(task_row, ref, step="store", response=None):
        row, _ = start_step(task_row["id"], step, f"{ref}:{step}" if step == "store" else ref,
                            "quickhowl" if step == "store" else "email_tool", True, {"reference": ref, "auto_start": False})
        finish_step(row["id"], "done", response=response or {"client_reference": "selftest-client"})

    ok = False
    try:
        os.environ["CUSTOMER_KEY"] = customer
        quickhowl.call_api, email_tool.call_tool = call_api, call_tool
        run, _ = load_run(customer, "2026-10", orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        tasks = {t["task_id"]: t for t in get_tasks(run["id"])}
        load_run(customer, "2026-11", orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        stored(tasks["T1"], ref_a)
        stored(tasks["T2"], email_ref, step="launch",
               response={"campaign_id": "c-9", "workspace_id": "w-9", "campaign_status": "DRAFT"})

        def steps_now():
            return sum(len(get_steps(t["id"])) for t in tasks.values())
        before_steps = steps_now()

        expect("M1 --run is required; a missing task id or extra words -> usage, nothing done",
               raises(lambda: quickhowl.parse_manual_args(["pause"]))
               and raises(lambda: quickhowl.parse_manual_args(["pause", "--run", "2026-10"]))
               and raises(lambda: quickhowl.parse_manual_args(["pause", "--run", "2026-10", "T1", "now"]))
               and raises(lambda: quickhowl.parse_manual_args(["launch", "--run", "2026-10", "T1"]))
               and quickhowl.parse_manual_args(["status", "--run", "2026-10", "T1"]) == ("status", "2026-10", "T1", None)
               and api == [])
        campaign = quickhowl.ledger_campaign("2026-10", "T1")
        quickhowl.cmd_status(campaign)
        expect("M2 status: the reference comes from the task's ledger 'store' step (not data/call_campaign_state.json); "
               "one GET for it with its client_reference; nothing written to the ledger",
               campaign["reference"] == ref_a and campaign["client_reference"] == "selftest-client"
               and api == [("GET", f"/campaigns/{ref_a}", {"client_reference": "selftest-client"})]
               and steps_now() == before_steps)
        expect("M3 refused: an email task as a calls task, an unknown task, a calls task with no stored campaign",
               raises(lambda: quickhowl.ledger_campaign("2026-10", "T2"))
               and raises(lambda: quickhowl.ledger_campaign("2026-10", "T9"))
               and raises(lambda: quickhowl.ledger_campaign("2026-11", "T1")))
        stored(tasks["T1"], ref_b)
        message = ""
        try:
            quickhowl.ledger_campaign("2026-10", "T1")
        except ToolError as error:
            message = str(error)
        expect("M4 two campaigns in the task -> STOPPED listing both; --reference picks one exactly; an unknown one is refused",
               ref_a in message and ref_b in message
               and quickhowl.ledger_campaign("2026-10", "T1", ref_b)["reference"] == ref_b
               and raises(lambda: quickhowl.ledger_campaign("2026-10", "T1", "motm-other")))
        campaign = quickhowl.ledger_campaign("2026-10", "T1", ref_a)
        posts = lambda: [c for c in api if c[0] == "POST"]
        fake["reply_reference"] = "motm-somebody-else"
        expect("M5 pause: Quickhowl answers for ANOTHER campaign -> STOPPED, nothing POSTed",
               raises(lambda: quickhowl.cmd_pause(campaign, confirm=lambda _: ref_a)) and posts() == [])
        fake["reply_reference"] = None
        results = []
        for status in ("completed", "draft", "paused"):
            fake["status"] = status
            results.append(quickhowl.cmd_pause(campaign, confirm=lambda _: ref_a))
        expect("M6 pause: a finished, draft or already paused campaign -> nothing to pause, nothing POSTed",
               results == [None, None, None] and posts() == [])
        fake["status"] = "running"

        def eof(_):
            raise EOFError
        answers = [quickhowl.cmd_pause(campaign, confirm=lambda _: "yes"),
                   quickhowl.cmd_pause(campaign, confirm=lambda _: ref_b),
                   quickhowl.cmd_pause(campaign, confirm=lambda _: ""), quickhowl.cmd_pause(campaign, confirm=eof)]
        expect("M7 pause: 'yes', ANOTHER task reference, empty, end of input -> not paused, nothing POSTed",
               answers == [None, None, None, None] and posts() == [])
        reply = quickhowl.cmd_pause(campaign, confirm=lambda _: f" {ref_a} ")
        expect("M8 pause: the exact reference typed -> exactly ONE POST, for that campaign, with its client_reference",
               reply["paused"] and posts() == [("POST", f"/campaigns/{ref_a}/pause",
                                                 {"client_reference": "selftest-client"})])
        fake["status"] = "completed"
        outcome = quickhowl.cmd_results(campaign)
        slept = []
        quickhowl.cmd_watch(campaign, sleep=slept.append)
        expect("M9 results / watch: read-only (GETs only), finished campaign -> watch stops at once; ledger untouched",
               len(outcome["calls"]) == 1 and slept == [] and all(c[0] == "GET" for c in api if c not in posts())
               and steps_now() == before_steps + 1)       # +1 = the second store row added for M4
        email_campaign = email_tool.ledger_email_campaign("2026-10", "T2")
        email_tool.run_samples(email_campaign)
        expect("M10 email samples: campaign + workspace ids from the email task's 'launch' step; one GET; "
               "a calls task as an email task is refused",
               (email_campaign["campaign_id"], email_campaign["workspace_id"]) == ("c-9", "w-9")
               and len(tool_urls) == 1 and tool_urls[0][0] == "GET"
               and "/workspaces/w-9/" in tool_urls[0][1] and tool_urls[0][1].endswith("/campaigns/c-9/samples")
               and raises(lambda: email_tool.ledger_email_campaign("2026-10", "T1")))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        quickhowl.call_api, email_tool.call_tool = real_call_api, real_call_tool
        if saved_key is None:
            os.environ.pop("CUSTOMER_KEY", None)
        else:
            os.environ["CUSTOMER_KEY"] = saved_key
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (manual tools) OK" if ok else "ORCHESTRATOR SELFTEST (manual tools) FAILED")
    return ok
