"""
orch_tests/part_tools.py
========================

Parts 'tools' (QuickICP accounts runner) and 'contacts' (QuickICP contacts runner).
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from datetime import datetime, timezone
from orchestrator import *
from orch_tests.common import expect, raises, sweep_selftest_runs


def selftest_tools() -> bool:
    """quickicp_accounts runner + run lock + --dry, with FakeAccountsTools only (no network, no LLM)."""
    from runner_quickicp_accounts import FakeAccountsTools, SimulatedCrash
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator TOOL selftest (fake QuickICP): customer '{customer}'")
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 1, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    runs, ok = [], False
    saved_override = os.environ.pop("TEST_TARGET_OVERRIDE", None)
    real_entry = WIRED_RUNNERS["quickicp_accounts"]

    def new_run(month: str, target: str, fake: FakeAccountsTools) -> dict:
        wo = {"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "accounts",
              "task_type": "data_build", "week": 1, "depends_on": [], "target": target, "instructions": ["test"]}
        run, _ = load_run(customer, month, [wo], {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        runs.append(run)
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: fake)
        return run

    def t1(run: dict) -> dict:
        return get_tasks(run["id"])[0]

    def rows(run: dict, step: str) -> list[dict]:
        from state_store import get_steps
        return [r for r in get_steps(t1(run)["id"]) if r["step"] == step]

    def kinds(run: dict, kind: str) -> list[dict]:
        return [d for d in get_decisions(run["id"]) if d["kind"] == kind]

    def existing(n_good: int, n_bad: int = 0) -> list[dict]:
        return ([{**FakeAccountsTools.company(f"ex{i}.test", True), "_id": 500 + i} for i in range(1, n_good + 1)]
                + [{**FakeAccountsTools.company(f"bad{i}.test", False), "_id": 700 + i} for i in range(1, n_bad + 1)])

    def crash(month: str) -> bool:
        try:
            cmd_run(customer, month)
        except SimulatedCrash:
            return True
        return False

    try:
        # S0. industry keywords: no Prospeo names -> synonyms ONCE; only names Prospeo returned (fake lookup, no network)
        from quickicp import industry_options
        known = {"e-commerce": [], "retail": ["Retail"], "online retail": ["Retail", "Online Retail"],
                 "internet marketplace": [], "manufacturing": ["Manufacturing"], "logistics": []}
        looked_up = []

        def fake_lookup(field, keyword):
            looked_up.append((field, keyword))
            return list(known.get(keyword.lower(), []))
        found = industry_options(["E-commerce", "Manufacturing", "Logistics"], lookup=fake_lookup)
        expect("S0 'E-commerce' finds nothing -> synonyms retail / online retail / internet marketplace, once each",
               [k for _, k in looked_up] == ["E-commerce", "retail", "online retail", "internet marketplace",
                                             "Manufacturing", "Logistics"]
               and all(field == "industry_search" for field, _ in looked_up))
        expect("S0 synonym names listed under 'E-commerce', each once; a keyword with names gets no synonyms",
               found == {"E-commerce": ["Retail", "Online Retail"], "Manufacturing": ["Manufacturing"], "Logistics": []})

        # S1. reuse covers the target -> NO paid search
        fake = FakeAccountsTools(existing=existing(12))
        run = new_run("2027-01", "10 accounts", fake)
        cmd_run(customer, "2027-01")
        task = t1(run)
        expect("S1 reuse: enough companies already in Supabase -> 0 paid searches, task done, achieved 12",
               fake.calls["search"] == 0 and task["status"] == "done" and task["achieved_number"] == 12)
        expect("S1 company ids of this run recorded with customer + run_key; brain review 'done' recorded",
               task["result"]["company_ids"] == list(range(501, 513)) and task["result"]["customer"] == customer
               and task["result"]["run_key"] == run["run_key"] and kinds(run, "brain_review")[-1]["answer"] == "done")
        expect("S1 lock released after the run", get_run(run["run_key"])["locked_by"] is None)

        # S2. shortfall only; an existing domain in the reply is never counted again
        ledger_seen = []
        fake = FakeAccountsTools(existing=existing(6, 4))
        run = new_run("2027-02", "10 accounts", fake)
        fake.on_search = lambda: ledger_seen.append([r["status"] for r in rows(run, "search")][-1])
        original_search = fake.search

        def search_with_known_domain(payload):
            found = original_search(payload)
            return ([FakeAccountsTools.company("ex1.test", True)] + found[1:]) if fake.calls["search"] == 1 else found
        fake.search = search_with_known_domain
        cmd_run(customer, "2027-02")
        task = t1(run)
        first_request = rows(run, "search")[0]["request"]["payload"]["company_count"]
        expect("S2 buys only the shortfall: first search asks for 4 (= 10 target - 6 reused)", first_request == 4)
        expect("S2 the paid row was already 'running' INSIDE the call (saved before acting)",
               ledger_seen and all(status == "running" for status in ledger_seen))
        expect("S2 existing domain in the reply not counted -> 1 extra round -> done with 10 (6 reused + 4 new)",
               fake.calls["search"] == 2 and task["status"] == "done" and task["achieved_number"] == 10
               and task["result"]["bought"] == 4 and all(r["paid"] for r in rows(run, "search")))

        # S3. crash DURING the paid search -> unknown + human; never repeated automatically; rerun
        fake = FakeAccountsTools(crash_search=1)
        run = new_run("2027-03", "5 accounts", fake)
        expect("S3 crash during the paid search", crash("2027-03"))
        expect("S3 row left 'running', lock still released (finally)",
               rows(run, "search")[0]["status"] == "running" and get_run(run["run_key"])["locked_by"] is None)
        cmd_run(customer, "2027-03")
        expect("S3 next run: row 'unknown', 1 paid_call_unknown decision, task waiting, NOT searched again",
               rows(run, "search")[0]["status"] == "unknown" and len(kinds(run, "paid_call_unknown")) == 1
               and t1(run)["status"] == "waiting_human" and fake.calls["search"] == 1)
        cmd_run(customer, "2027-03")
        expect("S3 run again without an answer: still 1 decision, still no new search",
               len(kinds(run, "paid_call_unknown")) == 1 and fake.calls["search"] == 1)
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "rerun", "checked n8n: it did not run")
        cmd_run(customer, "2027-03")
        expect("S3 rerun: old row closed as 'failed' (human decided), a NEW search key, task done with 5",
               [r["status"] for r in rows(run, "search")] == ["failed", "done"] and fake.calls["search"] == 2
               and t1(run)["status"] == "done" and t1(run)["achieved_number"] == 5)
        expect("S3 payload built only once across the crash (reused from the ledger)", fake.calls["build_payload"] == 1)

        # S4. crash AFTER the search, before the judge -> saved reply judged, no new search
        fake = FakeAccountsTools(crash_judge=1)
        run = new_run("2027-04", "5 accounts", fake)
        expect("S4 crash before the judge", crash("2027-04"))
        cmd_run(customer, "2027-04")
        expect("S4 next run judges the SAVED reply: still 1 search, task done with 5",
               fake.calls["search"] == 1 and t1(run)["status"] == "done" and t1(run)["achieved_number"] == 5)

        # S5. goal check: more, more, then only continue / stop; continue -> done with shortfall
        fake = FakeAccountsTools(good_rate=0)
        run = new_run("2027-05", "5 accounts", fake)
        cmd_run(customer, "2027-05")
        g1 = kinds(run, "goal_check")
        expect("S5 short after round 0 + 2 extra rounds -> goal_check (continue / more / stop)",
               fake.calls["search"] == 3 and len(g1) == 1 and g1[0]["question"]["allowed_answers"] == ["continue", "more", "stop"]
               and t1(run)["waiting_for"] == "goal_check")
        cmd_answer(customer, g1[0]["id"], "more", "try other industries")
        cmd_run(customer, "2027-05")
        expect("S5 'more' -> exactly 2 more rounds, then a new goal_check", fake.calls["search"] == 5
               and len(kinds(run, "goal_check")) == 2)
        cmd_answer(customer, kinds(run, "goal_check")[-1]["id"], "more", "one last try")
        cmd_run(customer, "2027-05")
        final = kinds(run, "goal_check_final")
        expect("S5 after 2 'more': goal_check_final offers only continue / stop", fake.calls["search"] == 7
               and len(final) == 1 and final[0]["question"]["allowed_answers"] == ["continue", "stop"])
        expect("S5 'more' refused at the final check", raises(lambda: cmd_answer(customer, final[0]["id"], "more", "x")))
        cmd_answer(customer, final[0]["id"], "continue", "go on with what we have")
        cmd_run(customer, "2027-05")
        expect("S5 continue -> done with the shortfall recorded, no new search",
               t1(run)["status"] == "done" and t1(run)["result"]["shortfall"] == 5 and fake.calls["search"] == 7)

        # S6. stop at the goal check -> task stopped -> run stopped (nothing else can move)
        fake = FakeAccountsTools(good_rate=0)
        run = new_run("2027-06", "3 accounts", fake)
        cmd_run(customer, "2027-06")
        cmd_answer(customer, kinds(run, "goal_check")[0]["id"], "stop", "wrong segment")
        result = cmd_run(customer, "2027-06")
        expect("S6 stop -> task stopped, run stopped", t1(run)["status"] == "stopped" and result["run_status"] == "stopped")

        # S7. skip an unknown paid search -> that round counts as empty, the loop goes on
        fake = FakeAccountsTools(crash_search=1)
        run = new_run("2027-07", "4 accounts", fake)
        crash("2027-07")
        cmd_run(customer, "2027-07")
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "skip", "do not risk paying twice")
        cmd_run(customer, "2027-07")
        expect("S7 skip -> round treated as empty -> new filters (adjust) -> done with 4",
               fake.calls["adjust"] == 1 and fake.calls["search"] == 2 and t1(run)["status"] == "done"
               and t1(run)["achieved_number"] == 4)

        # S8. known failure (ToolError) in the search -> task_failed decision -> retry
        fake = FakeAccountsTools(fail_search=1)
        run = new_run("2027-08", "4 accounts", fake)
        cmd_run(customer, "2027-08")
        failed_q = kinds(run, "task_failed")
        expect("S8 known failure -> row 'failed', task_failed decision (retry / skip / stop), task waiting",
               rows(run, "search")[0]["status"] == "failed" and len(failed_q) == 1
               and t1(run)["waiting_for"] == "task_failed" and "502" in t1(run)["last_error"])
        cmd_answer(customer, failed_q[0]["id"], "retry", "n8n is back")
        cmd_run(customer, "2027-08")
        expect("S8 retry -> new search, done with 4", fake.calls["search"] == 2 and t1(run)["status"] == "done")

        # S9. run lock
        fake = FakeAccountsTools(existing=existing(5))
        run = new_run("2027-09", "5 accounts", fake)
        take_run_lock(run["id"], "other-process")
        expect("S9 a second 'run' while locked is refused", raises(lambda: cmd_run(customer, "2027-09"))
               and t1(run)["status"] == "pending")
        force_unlock(run["id"])
        expect("S9 unlock frees it", get_run(run["run_key"])["locked_by"] is None)
        take_run_lock(run["id"], "crashed-process", minutes=-1)            # an expired lease
        cmd_run(customer, "2027-09")
        expect("S9 an EXPIRED lock is taken over; released afterwards",
               t1(run)["status"] == "done" and get_run(run["run_key"])["locked_by"] is None)

        # S10. --dry: shows the shortfall, records nothing, buys nothing
        fake = FakeAccountsTools(existing=existing(3))
        run = new_run("2027-10", "8 accounts", fake)
        result = cmd_dry(customer, "2027-10")
        expect("S10 --dry: target 8, 3 reused, would buy 5; nothing recorded, task still pending",
               result.output["shortfall"] == 5 and result.output["would_request"] == 5 and fake.calls["search"] == 0
               and rows(run, "payload") == [] and t1(run)["status"] == "pending" and not get_decisions(run["id"])[1:])

        # TEST_TARGET_OVERRIDE (test mode only)
        from runner_quickicp_accounts import runner_target
        os.environ["TEST_TARGET_OVERRIDE"] = "7"
        expect("TEST_TARGET_OVERRIDE=7 used in test mode", runner_target(t1(run)) == 7)
        os.environ["TEST_TARGET_OVERRIDE"] = "seven"
        expect("TEST_TARGET_OVERRIDE must be a whole number", raises(lambda: runner_target(t1(run))))
        os.environ.pop("TEST_TARGET_OVERRIDE")
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        if saved_override is not None:
            os.environ["TEST_TARGET_OVERRIDE"] = saved_override
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {len(runs)} test runs, {total} rows deleted, "
              f"{'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (tools) OK" if ok else "ORCHESTRATOR SELFTEST (tools) FAILED")
    return ok


def selftest_contacts() -> bool:
    """quickicp_contacts runner (T4) with FakeContactsTools only, + save_people fill rule + phone types."""
    from runner_quickicp_accounts import SimulatedCrash
    from runner_quickicp_contacts import FakeContactsTools
    from supabase_store import (COMPANY_TABLE, PEOPLE_TABLE, db, delete_selftest_rows, phone_type,
                                save_companies, save_people, saved_people)
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    other = customer + "-other"
    print(f"\nOrchestrator CONTACTS selftest (fake QuickICP): customer '{customer}'")
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 2, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    runs, ok = [], False
    saved_override = os.environ.pop("TEST_TARGET_OVERRIDE", None)
    real_entry = WIRED_RUNNERS["quickicp_contacts"]
    person = FakeContactsTools.person

    def companies(n: int, first_id: int = 11) -> list[dict]:
        return [{"id": i, "customer": customer, "website": f"c{i}.test", "linkedin": None,
                 "company_name": f"C{i}", "industry": "x"} for i in range(first_id, first_id + n)]

    def new_run(month: str, target: str, fake: FakeContactsTools, company_ids: list[int]) -> dict:
        def wo(task_id, output, deps, tgt):
            return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": "QuickICP", "output_type": output,
                    "task_type": "data_build", "week": 1, "depends_on": deps, "target": tgt, "instructions": ["test"]}
        run, _ = load_run(customer, month, [wo("T1", "accounts", [], "40 accounts"), wo("T2", "contacts", ["T1"], target)],
                          {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        runs.append(run)
        t1 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T1"][0]
        update_task(t1["id"], status="done", result={"company_ids": company_ids, "customer": customer,
                                                     "run_key": run["run_key"]})
        WIRED_RUNNERS["quickicp_contacts"] = (real_entry[0], lambda ctx: fake)
        return run

    def t2(run: dict) -> dict:
        return [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]

    def rows(run: dict, step: str) -> list[dict]:
        from state_store import get_steps
        return [r for r in get_steps(t2(run)["id"]) if r["step"] == step]

    def kinds(run: dict, kind: str) -> list[dict]:
        return [d for d in get_decisions(run["id"]) if d["kind"] == kind]

    def crash(month: str) -> bool:
        try:
            cmd_run(customer, month)
        except SimulatedCrash:
            return True
        return False

    try:
        # phone types (code rule)
        expect("phone_type: +911126142451 landline, +91 98113 00501 mobile, 01126142451 landline, junk unknown",
               phone_type("+911126142451") == "landline" and phone_type("+91 98113 00501") == "mobile"
               and phone_type("9811300501") == "mobile" and phone_type("01126142451") == "landline"
               and phone_type("12345") == "unknown" and phone_type(None) == "unknown")

        # K1. reuse covers the target; enrich ONLY passed people missing email / phone
        cs = companies(3)
        existing = [person(f"e{i}", f"c{11 + i % 3}.test", True, email=None if i in (1, 2) else f"e{i}@x.test",
                           phone=None if i in (2, 3, 4) else "+919811300501", _id=600 + i, company_id=11 + i % 3)
                    for i in range(1, 13)]
        existing.append(person("lowlevel", "c11.test", False, _id=700, company_id=11))     # fails the judge
        existing.append(person("elsewhere", "c99.test", True, _id=701, company_id=99))    # not a T3 company
        fake = FakeContactsTools(cs, existing=existing)
        run = new_run("2028-01", "10 contacts", fake, [11, 12, 13])
        cmd_run(customer, "2028-01")
        task = t2(run)
        expect("K1 reuse covers target 10 (12 pass): 0 people searches, task done, achieved 12",
               fake.calls["search"] == 0 and task["status"] == "done" and task["achieved_number"] == 12)
        expect("K1 email finder got EXACTLY the 2 passed people without email",
               sorted(fake.sent["emails"]) == sorted(["https://linkedin.com/in/e1", "https://linkedin.com/in/e2"]))
        expect("K1 phone finder got EXACTLY the 3 passed people without phone",
               sorted(fake.sent["phones"]) == sorted([f"https://linkedin.com/in/e{i}" for i in (2, 3, 4)]))
        out = task["result"]
        expect("K1 output: 12 people ids, all with email + phone, mobile/landline counted, customer + run_key",
               len(out["people_ids"]) == 12 and out["with_email"] == 12 and out["with_phone"] == 12
               and out["mobile"] + out["landline"] == 12 and out["landline"] >= 1
               and out["customer"] == customer and out["run_key"] == run["run_key"])
        expect("K1 brain review 'done' recorded", kinds(run, "brain_review")[-1]["answer"] == "done")

        # K2. shortfall only, best companies first, another customer's company ignored, known person not recounted
        cs = companies(8) + [{"id": 99, "customer": other, "website": "c99.test", "linkedin": None,
                              "company_name": "C99", "industry": "x"}]
        scores = {11: 10, 12: 90, 13: 80, 14: 70, 15: 60, 16: 50, 17: 40, 18: 30, 99: 100}
        existing = [person(f"k{i}", "c11.test", True, email=f"k{i}@x.test", phone="+919811300501",
                           _id=610 + i, company_id=11) for i in range(1, 5)]
        ledger_seen = []
        fake = FakeContactsTools(cs, scores=scores, existing=existing, repeat_social="https://linkedin.com/in/k1")
        run = new_run("2028-02", "10 contacts", fake, [11, 12, 13, 14, 15, 16, 17, 18, 99])
        fake.on_search = lambda: ledger_seen.append(rows(run, "people")[-1]["status"])
        cmd_run(customer, "2028-02")
        task = t2(run)
        expect("K2 company of ANOTHER customer ignored (1 id dropped)",
               rows(run, "companies")[0]["output"]["dropped_other_customer"] == 1)
        expect("K2 first batch: best 5 companies by score (c12, c13, c14, c15, c16), 2 people each, for a shortfall of 6",
               fake.payloads[0]["domains"] == ["c12.test", "c13.test", "c14.test", "c15.test", "c16.test"]
               and fake.payloads[0]["count_per_company"] == 2)
        expect("K2 paid batch row already 'running' INSIDE the call", ledger_seen == ["running"])
        expect("K2 a person we already had is not counted again: 4 reused + 9 new = 13, 1 search",
               task["status"] == "done" and task["achieved_number"] == 13 and task["result"]["bought"] == 9
               and fake.calls["search"] == 1)

        # K3. crash in BATCH 2 of round 0 -> unknown -> rerun: batch 1 replayed, never bought again
        os.environ["TEST_TARGET_OVERRIDE"] = "40"
        fake = FakeContactsTools(companies(30), crash_search=2)
        run = new_run("2028-03", "350 contacts", fake, list(range(11, 41)))
        expect("K3 crash during batch 2 of round 0", crash("2028-03"))
        cmd_run(customer, "2028-03")
        expect("K3 next run: batch 2 'unknown', 1 paid_call_unknown decision, no new search",
               [r["status"] for r in rows(run, "people")] == ["done", "unknown"]
               and len(kinds(run, "paid_call_unknown")) == 1 and fake.calls["search"] == 2)
        cmd_run(customer, "2028-03")
        expect("K3 run again without an answer: still 1 decision, still no new search",
               len(kinds(run, "paid_call_unknown")) == 1 and fake.calls["search"] == 2)
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "rerun", "n8n log shows it did not run")
        cmd_run(customer, "2028-03")
        keys = {r["idempotency_key"].split(":T2:")[1]: r["status"] for r in rows(run, "people")}
        expect("K3 rerun: batch 1 REPLAYED (not bought again), batch 2 attempt a2 bought, task done",
               fake.calls["search"] == 3 and keys == {"people:r0:b1:a1": "done", "people:r0:b2:a1": "failed",
                                                      "people:r0:b2:a2": "done"} and t2(run)["status"] == "done")
        expect("K3 test cap: email and phone finders got at most 6 people each",
               len(fake.sent["emails"]) == 6 and len(fake.sent["phones"]) == 6)
        os.environ.pop("TEST_TARGET_OVERRIDE")

        # K4. crash in the email finder -> skip -> those people stay without email; phones still run
        existing = [person(f"m{i}", "c11.test", True, _id=620 + i, company_id=11) for i in range(1, 11)]
        fake = FakeContactsTools(companies(1), existing=existing, crash_enrich=("emails", 1))
        run = new_run("2028-04", "10 contacts", fake, [11])
        expect("K4 crash during the email finder", crash("2028-04"))
        cmd_run(customer, "2028-04")
        cmd_answer(customer, kinds(run, "paid_call_unknown")[0]["id"], "skip", "do not pay twice")
        cmd_run(customer, "2028-04")
        task = t2(run)
        expect("K4 skip -> no emails, email finder not called again, phone finder ran, task done",
               fake.calls["emails"] == 1 and task["result"]["with_email"] == 0 and fake.calls["phones"] == 1
               and task["status"] == "done")

        # K5. goal check: more, then 'nothing left to try' -> final (continue / stop) -> continue
        fake = FakeContactsTools(companies(3), good_rate=0)
        run = new_run("2028-05", "10 contacts", fake, [11, 12, 13])
        cmd_run(customer, "2028-05")
        g = kinds(run, "goal_check")
        expect("K5 short after round 0 + 2 extra rounds (more companies -> more per company) -> goal_check",
               fake.calls["search"] == 3 and len(g) == 1 and g[0]["question"]["allowed_answers"] == ["continue", "more", "stop"])
        cmd_answer(customer, g[0]["id"], "more", "try more per company")
        cmd_run(customer, "2028-05")
        final = kinds(run, "goal_check_final")
        expect("K5 'more' -> 1 round until nothing is left to try -> goal_check_final (continue / stop)",
               fake.calls["search"] == 4 and len(final) == 1 and final[0]["question"]["allowed_answers"] == ["continue", "stop"])
        cmd_answer(customer, final[0]["id"], "continue", "go on")
        cmd_run(customer, "2028-05")
        expect("K5 continue -> done with the shortfall recorded", t2(run)["status"] == "done"
               and t2(run)["result"]["shortfall"] == 10)

        # K6. --dry for contacts
        existing = [person(f"d{i}", "c11.test", True, email=None if i == 1 else f"d{i}@x.test",
                           phone="+919811300501", _id=640 + i, company_id=11) for i in range(1, 4)]
        fake = FakeContactsTools(companies(5), existing=existing)
        run = new_run("2028-06", "10 contacts", fake, [11, 12, 13, 14, 15])
        result = cmd_dry(customer, "2028-06")
        expect("K6 --dry: shortfall 7 -> ~10 people (5 companies x 2), 6 emails, 6 phones (test cap); nothing recorded",
               result.output["shortfall"] == 7 and result.output["would_people"] == 10
               and result.output["would_emails"] == 6 and result.output["would_phones"] == 6
               and fake.calls["search"] == 0 and rows(run, "titles") == [] and t2(run)["status"] == "pending")

        # K7. save_people fills an EMPTY email / phone, never overwrites (real QuickICP tables, selftest customer)
        save_companies([{"COMPANY": "Fill Test", "WEBSITE": "filltest.test"}], customer)
        lookups = __import__("supabase_store").company_lookups(customer)
        base = {"FULL NAME": "Fill Test", "USER SOCIAL": "https://linkedin.com/in/fill-test",
                "COMPANY URL": "filltest.test"}
        save_people([{**base, "email": "first@x.test", "phone": None}], lookups, customer)
        save_people([{**base, "email": "second@x.test", "phone": "+919811300501"}], lookups, customer)
        row = saved_people(customer)["https://linkedin.com/in/fill-test"]
        expect("K7 save_people: empty phone filled, existing email NOT overwritten",
               row["email"] == "first@x.test" and row["phone_number"] == "+919811300501")
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_contacts"] = real_entry
        os.environ.pop("TEST_TARGET_OVERRIDE", None)
        if saved_override is not None:
            os.environ["TEST_TARGET_OVERRIDE"] = saved_override
        quickicp_deleted = delete_selftest_rows(customer)
        left_rows = (len(db.table(COMPANY_TABLE).select("id").eq("customer", customer).execute().data)
                     + len(db.table(PEOPLE_TABLE).select("id").eq("customer", customer).execute().data))
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left or left_rows else 'PASS'}  cleanup: {len(runs)} test runs, {total} state rows and "
              f"{quickicp_deleted} QuickICP rows deleted, {'SOME' if left or left_rows else 'none'} left")
        ok = ok and not left and not left_rows
    print("ORCHESTRATOR SELFTEST (contacts) OK" if ok else "ORCHESTRATOR SELFTEST (contacts) FAILED")
    return ok
