"""
orch_tests/part_calls.py
========================

Part 'calls' (Quickhowl runner).
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from datetime import datetime, timezone
from orchestrator import *
from orch_tests.common import expect, outreach_fixture, raises, sweep_selftest_runs


def selftest_calls() -> bool:
    """Calls runner (T7) with FakeCallTools, + Quickhowl safety checks (no network, no LLM, no calls)."""
    import contextlib
    import io
    import quickhowl
    from runner_calls import POLL_LIMIT_SECONDS, POLL_EVERY_SECONDS, FakeCallTools
    from runner_quickicp_accounts import SimulatedCrash
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator CALLS selftest (fake Quickhowl): customer '{customer}'")
    real_entry = WIRED_RUNNERS["calls"]
    ok = False
    phones = ["+919811300501", "+911126142451", "+918126409111", "+919694757222"]   # 2nd is a landline
    rows = [{"id": 911 + i, "customer": customer, "full_name": f"Caller {i}", "phone_number": phone,
             "company": {"company_name": f"C{i}"}} for i, phone in enumerate(phones)]

    def setup(month, fake):
        WIRED_RUNNERS["calls"] = (real_entry[0], lambda ctx: fake)
        return outreach_fixture(customer, month, "Quickhowl", "calls", [r["id"] for r in rows])

    def t2(run):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]

    def decision(run, kind="call_start"):
        found = [d for d in get_decisions(run["id"]) if d["kind"] == kind]
        return found[-1] if found else None

    def started(month, fake):
        run = setup(month, fake)
        cmd_run(customer, month)
        cmd_answer(customer, decision(run)["id"], "yes", "call our test phones")
        return run

    saved_flags = (quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED)
    try:
        # Q1-Q3. mobile only; test numbers only; stored -> call_start -> start -> poll -> done with outcomes
        fake = FakeCallTools(rows)
        run = setup("2029-07", fake)
        cmd_run(customer, "2029-07")
        q = decision(run)["question"]
        expect("Q1 landline skipped; TEST MODE: 2 contacts (2 test numbers), our numbers only; stored, nobody called",
               [c["phone"] for c in q["contacts"]] == ["+919000000001", "+919000000002"]
               and fake.calls["store"] == 1 and fake.calls["start"] == 0 and t2(run)["waiting_for"] == "call_start")
        cmd_answer(customer, decision(run)["id"], "yes", "call our test phones")
        cmd_run(customer, "2029-07")
        task = t2(run)
        expect("Q3 yes -> start -> polled until finished -> done: 2 completed, outcomes per person, landline recorded",
               fake.calls["start"] == 1 and task["status"] == "done" and task["achieved_number"] == 2
               and [o["person_id"] for o in task["result"]["outcomes"]] == [911, 913]
               and task["result"]["skipped_landlines"][0]["phone_type"] == "landline"
               and task["result"]["outcome_counts"] == {"interested": 1, "no_answer": 1})

        # Q2. Quickhowl's own safety checks (module flags switched for this check only)
        quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = False, False
        expect("Q2 real mode refused by quickhowl.assert_safe (REAL_CALLS_ENABLED = False)",
               raises(lambda: quickhowl.assert_safe({"contacts": [{"phone": "+919811300501"}]})))
        quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = saved_flags
        if quickhowl.TEST_MODE and os.getenv("TEST_CALL_NUMBERS"):
            expect("Q2 test mode refuses a phone that is not a TEST_CALL_NUMBER",
                   raises(lambda: quickhowl.assert_safe({"contacts": [{"phone": "+910000000000"}]})))

        # Q4. no -> stopped, nobody called
        fake = FakeCallTools(rows)
        run = setup("2029-08", fake)
        cmd_run(customer, "2029-08")
        cmd_answer(customer, decision(run)["id"], "no", "not today")
        cmd_run(customer, "2029-08")
        expect("Q4 no -> task stopped, start never sent", t2(run)["status"] == "stopped" and fake.calls["start"] == 0)

        # Q5. crash DURING start -> sent again automatically ('already_started') -> done
        fake = FakeCallTools(rows, crash_start=1)
        run = setup("2029-09", fake)
        cmd_run(customer, "2029-09")
        cmd_answer(customer, decision(run)["id"], "yes", "go")
        try:
            cmd_run(customer, "2029-09")
            crashed = False
        except SimulatedCrash:
            crashed = True
        cmd_run(customer, "2029-09")
        expect("Q5 crash during start -> next run resends the SAME request (no human) -> done",
               crashed and fake.calls["start"] == 2 and fake.calls["build"] == 1 and t2(run)["status"] == "done")

        # Q6. still a DRAFT after start -> polling stops at once (known issue 3) -> task_failed
        fake = FakeCallTools(rows, statuses=("draft",))
        run = started("2029-10", fake)
        cmd_run(customer, "2029-10")
        expect("Q6 draft after start -> stops after ONE poll, task_failed decision (retry / skip / stop)",
               fake.calls["status"] == 1 and t2(run)["waiting_for"] == "task_failed" and "DRAFT" in t2(run)["last_error"])

        # Q7. calls still going after 10 minutes -> 'running'; the next run continues polling
        fake = FakeCallTools(rows, statuses=("running",))
        run = started("2029-11", fake)
        cmd_run(customer, "2029-11")
        first = fake.calls["status"]
        expect(f"Q7 still running after {POLL_LIMIT_SECONDS // 60} min -> task stays 'running' "
               f"({first} polls, {POLL_EVERY_SECONDS}s apart)",
               t2(run)["status"] == "running" and first == POLL_LIMIT_SECONDS // POLL_EVERY_SECONDS + 1)
        cmd_run(customer, "2029-11")
        expect("Q7 the next run continues polling (not started again)", fake.calls["status"] > first
               and fake.calls["start"] == 1)

        # Q8. outside the calling window -> 'running' at once
        fake = FakeCallTools(rows, statuses=("running",), in_window=False)
        run = started("2029-12", fake)
        cmd_run(customer, "2029-12")
        expect("Q8 outside the calling window -> 'running' after one poll", fake.calls["status"] == 1
               and t2(run)["status"] == "running")

        # Q9. --dry
        fake = FakeCallTools(rows)
        run = setup("2030-01", fake)
        result = cmd_dry(customer, "2030-01")
        expect("Q9 --dry: 2 contacts, 1 skipped landline; nothing stored, nothing recorded",
               result.output["contacts"] == 2 and result.output["skipped"] == 1 and fake.calls["store"] == 0
               and t2(run)["status"] == "pending")
        # Q10. the real-call gate: REAL_CALLS_ENABLED (go-live) AND QUICKHOWL_DND_HANDLED=true (flags in-process only)
        saved_dnd = os.environ.get("QUICKHOWL_DND_HANDLED")
        try:
            real_contact = {"contacts": [{"phone": "+919811300501"}]}
            quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = False, False
            os.environ["QUICKHOWL_DND_HANDLED"] = "true"
            expect("Q10 real mode + REAL_CALLS_ENABLED False -> blocked ('off ... go-live'), even with DND handled",
                   raises(lambda: quickhowl.assert_safe(real_contact))
                   and "go-live" in quickhowl.real_calls_block_reason())
            quickhowl.REAL_CALLS_ENABLED = True
            os.environ.pop("QUICKHOWL_DND_HANDLED")
            expect("Q10 real mode + REAL on + QUICKHOWL_DND_HANDLED missing -> blocked (names the setting)",
                   raises(lambda: quickhowl.assert_safe(real_contact))
                   and "QUICKHOWL_DND_HANDLED" in quickhowl.real_calls_block_reason())
            os.environ["QUICKHOWL_DND_HANDLED"] = "yes"
            expect("Q10 QUICKHOWL_DND_HANDLED=yes is not 'true' -> still blocked", raises(lambda: quickhowl.assert_safe(real_contact)))
            os.environ["QUICKHOWL_DND_HANDLED"] = "TRUE"
            quickhowl.assert_safe(real_contact)
            expect("Q10 real mode + REAL on + QUICKHOWL_DND_HANDLED=TRUE -> allowed (no call is made here)",
                   quickhowl.real_calls_block_reason() is None)
            quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = True, False
            os.environ.pop("QUICKHOWL_DND_HANDLED")
            expect("Q10 test mode unchanged: never blocked by the DND setting; the note says 'own numbers only'",
                   quickhowl.real_calls_block_reason() is None and "own numbers only" in quickhowl.dnd_note())
        finally:
            quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = saved_flags
            if saved_dnd is None:
                os.environ.pop("QUICKHOWL_DND_HANDLED", None)
            else:
                os.environ["QUICKHOWL_DND_HANDLED"] = saved_dnd

        # Q11 / Q12. DND note on every call start; contacts Quickhowl rejected -> T7 output + review counts
        rejected = [{"phone": "+919811300501", "reason": "Number is on the DND registry"},
                    {"phone": "+918126409111", "reason": "duplicate"}]
        fake = FakeCallTools(rows, rejected=rejected)
        run = setup("2030-02", fake)
        cmd_run(customer, "2030-02")
        q = decision(run)["question"]
        cmd_answer(customer, decision(run)["id"], "yes", "go")
        cmd_run(customer, "2030-02")
        from state_store import get_steps
        start_row = [r for r in get_steps(t2(run)["id"]) if r["step"] == "start"][0]
        result = t2(run)["result"]
        expect("Q11 call_start question shows the DND confirmation; the ledger start row saves it; T7 output keeps it",
               "DND: handled by the Quickhowl platform" in q["text"] and start_row["request"]["dnd_handled"] is True
               and "Quickhowl platform" in start_row["request"]["dnd_note"]
               and "Quickhowl platform" in result["dnd_confirmation"])
        expect("Q12 T7 output: 2 contacts rejected by Quickhowl (store + start replies, no duplicates), 1 do-not-call",
               result["rejected_by_quickhowl"] == 2 and result["rejected_do_not_call"] == 1
               and len(result["contacts_rejected"]) == 2)
        from brain_review import task_numbers
        numbers = task_numbers(t2(run))
        expect("Q12 the review counts them: rejected_by_quickhowl 2, rejected_do_not_call 1",
               numbers["rejected_by_quickhowl"] == 2 and numbers["rejected_do_not_call"] == 1)

        # Q13. check_system: Quickhowl DND PASS when true, WARN when missing; real calls reported as set.
        # The flag is set here (in-process, restored with saved_flags), never read from the shared quickhowl.py value.
        import check_system
        saved_dnd = os.environ.get("QUICKHOWL_DND_HANDLED")
        try:
            lines = {}
            for value, real in (("true", False), (None, True)):
                if value is None:
                    os.environ.pop("QUICKHOWL_DND_HANDLED", None)
                else:
                    os.environ["QUICKHOWL_DND_HANDLED"] = value
                quickhowl.REAL_CALLS_ENABLED = real
                check_system.results.clear()
                captured = io.StringIO()
                with contextlib.redirect_stdout(captured):
                    check_system.check_quickhowl()
                lines[value] = captured.getvalue()
            expect("Q13 check_system: 'Quickhowl DND' PASS with true, WARN when missing; 'Real calls' PASS "
                   "'off until go-live' when off, WARN 'real prospects can be called' when on",
                   "PASS  Quickhowl DND" in lines["true"] and "WARN  Quickhowl DND" in lines[None]
                   and "PASS  Real calls" in lines["true"] and "off until go-live" in lines["true"]
                   and "WARN  Real calls" in lines[None] and "real prospects can be called" in lines[None])
        finally:
            check_system.results.clear()
            if saved_dnd is None:
                os.environ.pop("QUICKHOWL_DND_HANDLED", None)
            else:
                os.environ["QUICKHOWL_DND_HANDLED"] = saved_dnd

        # Q14. ONE login per client: QUICKHOWL_USER_* when set, else the email tool's; never saved, never in an error
        names = ("QUICKHOWL_USER_EMAIL", "QUICKHOWL_USER_PASSWORD", "EMAIL_TOOL_USER_EMAIL", "EMAIL_TOOL_USER_PASSWORD")
        saved_login = {name: os.environ.get(name) for name in names}
        try:
            for name in names:
                os.environ.pop(name, None)
            os.environ.update({"EMAIL_TOOL_USER_EMAIL": "owner@client.test", "EMAIL_TOOL_USER_PASSWORD": "Fake6x"})
            request = {"reference": "R-14", "contacts": []}
            shared = quickhowl.with_secrets(request, auto_start=False)["user"]
            os.environ.update({"QUICKHOWL_USER_EMAIL": "calls@client.test", "QUICKHOWL_USER_PASSWORD": "FakeCalls9"})
            own = quickhowl.with_secrets(request, auto_start=False)["user"]

            class EchoReply:
                status_code, text = 422, ""

                def json(self):
                    return {"detail": "invalid", "user": {"password": "FakeCalls9"}, "also": "Fake6x"}
            error = quickhowl.explain_error(EchoReply())
            for name in names:
                os.environ.pop(name, None)
            nothing = raises(quickhowl.check_login)
            try:
                quickhowl.check_login()
            except ToolError as stop:
                said = str(stop)
        finally:
            for name, value in saved_login.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        expect("Q14 no calling login set -> the email tool's login (the one the admin set) is used; a calling login "
               "that is set wins; the saved request never gets a user block; an echoed password is masked in the "
               "error; no login at all -> STOPPED naming the settings",
               (shared["email"], shared["password"]) == ("owner@client.test", "Fake6x")
               and (own["email"], own["password"]) == ("calls@client.test", "FakeCalls9") and "user" not in request
               and "FakeCalls9" not in error and "Fake6x" not in error and "********" in error
               and nothing and "QUICKHOWL_USER_PASSWORD or EMAIL_TOOL_USER_PASSWORD" in said)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["calls"] = real_entry
        quickhowl.TEST_MODE, quickhowl.REAL_CALLS_ENABLED = saved_flags
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (calls) OK" if ok else "ORCHESTRATOR SELFTEST (calls) FAILED")
    return ok
