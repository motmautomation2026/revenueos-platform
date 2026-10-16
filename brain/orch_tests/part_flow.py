"""
orch_tests/part_flow.py
=======================

Parts '5a', '5b', 'close', 'run data' and 'percent'.
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from datetime import datetime, timezone
from orchestrator import *
from orch_tests.common import expect, raises, run_work_orders, sweep_selftest_runs


def selftest_5a() -> bool:
    """--until-wait, 'answer T<n>', full 'decisions' questions. Fake QuickICP only; no network, no LLM."""
    import contextlib
    import io
    from runner_quickicp_accounts import FakeAccountsTools
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator 5a selftest (--until-wait, answer T<n>, decisions): customer '{customer}'")
    real_entry = WIRED_RUNNERS["quickicp_accounts"]
    ok = False
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 4, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output, deps, target="test"):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "data_build" if agent == "QuickICP" else "coordination", "week": 1,
                "depends_on": deps, "target": target, "instructions": ["test"], "done_when": f"{task_id} done"}

    def task(run, task_id):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == task_id][0]

    try:
        fake = FakeAccountsTools(existing=[{**FakeAccountsTools.company(f"ex{i}.test", True), "_id": 500 + i}
                                           for i in range(1, 7)])
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: fake)
        run, _ = load_run(customer, "2030-02", [wo("T1", "CAM", "coordination", []),
                                                wo("T2", "CAM", "coordination", ["T1"]),
                                                wo("T3", "QuickICP", "accounts", ["T2"], "5 accounts"),
                                                wo("T4", "CAM", "handover", ["T3"])],
                          {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)

        # U1. a fresh run: step 1 starts T1 (a person is needed) -> stop
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U1 --until-wait stops after 1 step: T1 needs a person",
               out["steps"] == 1 and "T1 needs a person" in out["stopped_because"]
               and task(run, "T1")["status"] == "waiting_human")

        # U2. answer by TASK ID, then --until-wait applies it and stops at the next person
        cmd_answer_task(customer, "T1", "done", "kick-off held")
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U2 'answer T1 done' works; --until-wait applies it and stops at T2 (person)",
               task(run, "T1")["status"] == "done" and "T2 needs a person" in out["stopped_because"])

        # U3. next ready task is QuickICP (buys data) -> --until-wait stops BEFORE starting it
        cmd_answer_task(customer, "T2", "done", "ok")
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U3 --until-wait stops before a NEW QuickICP task: 'next would buy data', T3 not started, 0 calls",
               "would buy data (T3)" in out["stopped_because"] and task(run, "T3")["status"] == "pending"
               and fake.calls["build_payload"] == 0 and fake.calls["search"] == 0)

        # U4. a plain 'run' starts T3 (reuse covers it here); then --until-wait goes on to T4 (person)
        cmd_run(customer, "2030-02")
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U4 plain run did T3; --until-wait then stops at T4 (person)",
               task(run, "T3")["status"] == "done" and "T4 needs a person" in out["stopped_because"])

        # U5. nothing to do -> stops at once; the lock is free afterwards; a held lock is refused
        out = cmd_run_until_wait(customer, "2030-02")
        expect("U5 nothing to do -> stops after 1 step ('nothing left to do now')",
               out["steps"] == 1 and "nothing left" in out["stopped_because"])
        expect("U5 lock released after --until-wait", get_run(run["run_key"])["locked_by"] is None)
        take_run_lock(run["id"], "other-process")
        expect("U5 --until-wait refused while another run holds the lock",
               raises(lambda: cmd_run_until_wait(customer, "2030-02")))
        force_unlock(run["id"])

        # U6. answer by task id: refused with 0 or 2 open decisions
        expect("U6 'answer T3' refused: T3 has no open decision", raises(lambda: cmd_answer_task(customer, "T3", "done", "x")))
        t4 = task(run, "T4")
        email_q = open_decision(run["id"], "email_approve", {
            "text": "Approve these sample emails?",
            "samples": [{"email": 1, "for": "Asha at C1", "subject": "Hello Anvixa", "text": "Hi Asha,\nShort demo?"}]},
            t4["id"])
        expect("U6 'answer T4' refused: T4 has 2 open decisions (lists them)",
               raises(lambda: cmd_answer_task(customer, "T4", "done", "x")))

        # U7. 'decisions' prints the full email samples and the call numbers
        call_q = open_decision(run["id"], "call_start", {
            "text": "Start calling?", "contacts": [{"name": "Ravi", "company": "C2", "phone": "+919000000001"}]},
            t4["id"])
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            cmd_decisions(customer)
        text = printed.getvalue()
        expect("U7 decisions shows the email sample (subject + text) and who will be called on which number",
               "Subject: Hello Anvixa" in text and "Short demo?" in text and "Ravi | C2 | number +919000000001" in text)

        # U8. the stop rules themselves (pure code)
        base = {"run_status": "running", "blocked_paid": None, "human_started": False, "tool_kind": None,
                "applied": [], "resumed": [], "started": None, "tool": None}
        expect("U8 stop rules: done/stopped, paid next, person, tool waiting/failed, tool running, no progress",
               until_wait_stop({**base, "run_status": "done"}) == "run is done"
               and "buy data" in until_wait_stop({**base, "blocked_paid": "T9"})
               and "a tool task needs a human" in until_wait_stop({**base, "tool_kind": "waiting", "tool": "x"})
               and "still running" in until_wait_stop({**base, "tool_kind": "running", "tool": "x"})
               and "nothing left" in until_wait_stop(base))
        expect("U8 keep going after progress with no human needed (answer applied / tool done)",
               until_wait_stop({**base, "applied": ["T1 -> done"]}) is None
               and until_wait_stop({**base, "tool_kind": "done", "tool": "T3 -> done", "started": "T3"}) is None)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (5a) OK" if ok else "ORCHESTRATOR SELFTEST (5a) FAILED")
    return ok


def selftest_5b() -> bool:
    """review: code numbers, data quality, objections, fake LLM text, checks, action decisions. No network, no real LLM."""
    import contextlib
    import io
    from brain_review import (NOT_AVAILABLE, TEST_CAP_LINE, TRANSCRIPT_CHARS, CallLabel, CallLabels, NextAction,
                              ReviewText, call_text)
    from state_store import finish_step, start_step
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator 5b selftest (review, fake LLM): customer '{customer}'")
    ok = False
    proof = {"approved_at": "2026-10-09T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 5, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    rule = "If lead response rate is below 10% then refine outreach messaging"
    plan = {"month": "Month 1", "objectives": ["Build the account list"], "revenue_analysis": {},
            "kpis_to_track": ["Number of accounts built", "Number of AI calls made and outcomes"],
            "optimization_rules": [rule]}

    def wo(task_id, agent, output, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "x", "week": 1, "depends_on": [], "target": target, "instructions": ["test"]}

    def fake_ask(texts, labels=None):
        """A fake LLM: CallLabels -> the given labels; ReviewText -> the given texts in order. Records prompts."""
        review_prompts, label_prompts = [], []

        def ask(schema, prompt, name):
            if schema is CallLabels:
                label_prompts.append(prompt)
                return CallLabels(labels=labels or [])
            review_prompts.append(prompt)
            return texts[len(review_prompts) - 1]
        ask.review_prompts, ask.label_prompts = review_prompts, label_prompts
        return ask

    def action(text_, kind="quality", follows="new", task_id="T7"):
        return NextAction(action=text_, why="from the numbers", task_id=task_id, kind=kind, follows_rule=follows)

    def text(worked, actions):
        return ReviewText(what_worked=[worked], whats_short=["Email opens are not available yet"], next_actions=actions)

    def answer_open_actions(run):
        for d in get_decisions(run["id"]):
            if d["kind"].startswith("review_action_") and d["status"] == "open":
                cmd_answer(customer, d["id"], "reject", "test")

    labels3 = [CallLabel(index=0, label="building_own_solution", quote="we are building our own AI"),
               CallLabel(index=1, label="call_later", quote="call me next month"),
               CallLabel(index=2, label="made_up_label", quote="hmm")]

    try:
        run, _ = load_run(customer, "2030-03", [wo("T3", "QuickICP", "accounts", "150-200 accounts"),
                                                wo("T4", "QuickICP", "contacts", "250-350 contacts"),
                                                wo("T11", "Email Agent", "emails", "Emails sent to 250-350 contacts"),
                                                wo("T7", "Quickhowl", "calls", "Calls to 250-350 contacts"),
                                                wo("T8", "Human Tele Calling", "qualified_leads", "10-12 enquiries")],
                          plan, {}, True, proof)
        tasks = {t["task_id"]: t for t in get_tasks(run["id"])}
        results = {
            "T3": (181, {"target": 40, "reused": 181, "bought": 0, "company_ids": [1]}),
            "T4": (15, {"target": 10, "reused": 15, "bought": 0, "with_email": 15, "with_phone": 15, "mobile": 12,
                        "landline": 3, "unknown_phone": 0, "people_ids": [5]}),
            "T11": (3, {"target": 3, "recipients": 3, "rejected": 0, "campaign_status": "RUNNING"}),
            "T7": (3, {"target": 3, "outcome_counts": {"not_interested": 2, "callback": 1},
                       "skipped_landlines": [{"person_id": 9}],
                       "outcomes": [{"name": "Ravi", "company": "C2", "outcome": "not_interested",
                                     "duration_seconds": 61, "summary": "Not interested right now."}]}),
        }
        for task_id, (achieved, result) in results.items():
            update_task(tasks[task_id]["id"], status="done", achieved_number=achieved, result=result)
        # Ledger rows the review reads: judge counts (reuse) and call transcripts (poll)
        for task_id, output in (("T3", {"existing": 192, "passed": 181}),
                                ("T4", {"existing": 22, "passed": [{"x": i} for i in range(15)]})):
            row, _ = start_step(tasks[task_id]["id"], "reuse", f"{run['run_key']}:{task_id}:reuse", "supabase", False, {})
            finish_step(row["id"], "done", output=output)
        long_line = "word " * 600
        calls = {f"call-{i}": {"call_id": f"call-{i}", "contact": {"name": f"P{i}"}, "outcome": "not_interested",
                               "summary": f"summary {i}", "transcript": [{"speaker": "agent", "text": "Hello"},
                                                                         {"speaker": "user", "text": long_line}]}
                 for i in range(3)}
        row, _ = start_step(tasks["T7"]["id"], "poll", f"{run['run_key']}:T7:poll:1", "quickhowl", False, {})
        finish_step(row["id"], "done", output={"since": None, "calls": calls, "status": "completed"})

        # R1. numbers (code)
        ask = fake_ask([text("181 accounts were reused, 0 bought",
                             [action("Ask about in-house AI plans early in the script", "script", rule),
                              action("Scale calls towards the plan target", "volume"),
                              action("Remove landlines before calling", "data_quality"),
                              action("Re-check job titles that failed", "targeting"),
                              action("A fifth action", "process")])], labels3)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            out = cmd_review(customer, "2030-03", ask=ask)
        n = {t["task_id"]: t for t in out["numbers"]["tool_tasks"]}
        expect("R1 4 done tool tasks reviewed (T8 is a human task, not reviewed)", sorted(n) == ["T11", "T3", "T4", "T7"])
        expect("R1 T4: 15 of effective 10 = 150%, of plan 350 = 4%",
               n["T4"]["pct_of_effective_target"] == 150 and n["T4"]["pct_of_plan_target"] == 4)
        expect("R1 T11: opens and replies 'not available yet'",
               n["T11"]["email_opens"] == NOT_AVAILABLE and n["T11"]["email_replies"] == NOT_AVAILABLE)

        # R6. data quality (code): % and flags at the thresholds (landline >= 20, people judge >= 30)
        dq = out["numbers"]["data_quality"]
        flagged = sorted(f["signal"] for f in dq["flagged"])
        expect("R6 data quality: landline 20%, without email 0%, companies failed 6%, people failed 32%",
               dq["landline_pct"] == 20 and dq["without_email_pct"] == 0 and dq["companies_failed_judge_pct"] == 6
               and dq["people_failed_judge_pct"] == 32)
        expect("R6 flagged exactly: landline (20 >= 20) and people judge (32 >= 30); not email, not companies",
               flagged == ["landline_pct", "people_failed_judge_pct"])

        # R7. objections: the LLM labels, code counts; unknown label -> other; quotes kept; text cut
        obj = out["numbers"]["objections"]
        expect("R7 3 calls labelled in 1 LLM call; counts by code; 'made_up_label' -> other; quotes kept",
               len(ask.label_prompts) == 1 and obj["calls_labelled"] == 3
               and obj["counts"] == {"building_own_solution": 1, "call_later": 1, "other": 1}
               and obj["quotes"][0]["quote"] == "we are building our own AI")
        short = call_text({"summary": "s", "outcome": "x", "transcript": [{"speaker": "agent", "text": "Hello sir"},
                                                                           {"speaker": "user", "text": "No thanks"}]})
        expect(f"R7 each call's text cut to {TRANSCRIPT_CHARS} characters; prospect lines come before the caller's",
               len(call_text(calls["call-0"])) == TRANSCRIPT_CHARS
               and short.index("Prospect: No thanks") < short.index("Caller: Hello sir"))
        expect("R7 the review prompt gets the objection counts, the quotes and the flags",
               "building_own_solution" in ask.review_prompts[0] and "we are building our own AI" in ask.review_prompts[0]
               and "people_failed_judge_pct" in ask.review_prompts[0] and "TEST MODE IS ON" in ask.review_prompts[0])

        # R8. TEST_MODE: volume action dropped; 'test caps' said once; max 3 actions; rules shown
        decisions = get_decisions(run["id"])
        actions = sorted([d for d in decisions if d["kind"].startswith("review_action_") and d["status"] == "open"],
                         key=lambda d: d["kind"])
        expect("R8 TEST_MODE: the 'volume' action dropped, 3 of the remaining 4 kept (2 LLM calls in all)",
               out["llm_calls"] == 2 and len(actions) == 3
               and [d["question"]["kind"] for d in actions] == ["script", "data_quality", "targeting"])
        expect("R8 'volume shortfalls are test caps' said exactly once",
               out["text"].whats_short.count(TEST_CAP_LINE) == 1)
        expect("R8 each action names its plan rule (exact) or 'new'",
               actions[0]["question"]["follows_rule"] == rule and actions[1]["question"]["follows_rule"] == "new"
               and "follows plan rule:" in actions[0]["question"]["text"])

        # R2. saved: run_review + task results tagged, runner fields untouched
        review = [d for d in decisions if d["kind"] == "run_review"]
        t4 = [t for t in get_tasks(run["id"]) if t["task_id"] == "T4"][0]
        expect("R2 run_review recorded (brain) with numbers, text and the 3 action ids; T4 result tagged, fields kept",
               len(review) == 1 and review[0]["decided_by"] == "brain"
               and sorted(review[0]["question"]["action_decision_ids"]) == sorted(d["id"] for d in actions)
               and t4["result"]["review"]["review_decision_id"] == review[0]["id"] and t4["result"]["mobile"] == 12)

        # R3. open actions block a new review; answers need a note; answering only records
        expect("R3 a new review is refused while review actions are open",
               raises(lambda: cmd_review(customer, "2030-03", ask=fake_ask([text("x", [])], labels3))))
        expect("R3 accept without a note refused", raises(lambda: cmd_answer(customer, actions[0]["id"], "accept", None)))
        before = {t["task_id"]: t["status"] for t in get_tasks(run["id"])}
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            cmd_answer(customer, actions[0]["id"], "accept", "yes, change the opener")
        expect("R9 answering a review action prints 'Recorded. Nothing runs automatically.'",
               "Recorded. Nothing runs automatically." in printed.getvalue() and "applied on the next" not in printed.getvalue())
        answer_open_actions(run)
        expect("R3 accepting / rejecting only records the choice: no task changed",
               {t["task_id"]: t["status"] for t in get_tasks(run["id"])} == before)

        # R10. follows_rule not a plan rule -> one retry naming it -> fixed
        ask = fake_ask([text("ok", [action("Do X", "script", "If calls fail then call more")]),
                        text("ok", [action("Do X", "script", rule)])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R10 invented rule -> one retry naming it -> the exact plan rule is saved",
               len(ask.review_prompts) == 2 and "If calls fail then call more" in ask.review_prompts[1]
               and out["text"].next_actions[0].follows_rule == rule and out["note"] == "")
        answer_open_actions(run)
        ask = fake_ask([text("ok", [action("Do X", "script", "made-up rule")]),
                        text("ok", [action("Do X", "script", "still made up")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R10 still not a plan rule after the retry -> kept, marked 'new (rule not matched)', with a note",
               out["text"].next_actions[0].follows_rule == "new (rule not matched)" and "not matched" in out["note"])
        answer_open_actions(run)

        # R4 / R5. invented numbers -> one retry -> fixed, or numbers-only
        ask = fake_ask([text("We reached 999 decision makers", [action("Do X")]),
                        text("Accounts were fully reused", [action("Do X")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R4 invented '999' -> one retry naming it -> the second text is saved (1 label + 2 review calls)",
               out["llm_calls"] == 3 and "999" in ask.review_prompts[1]
               and out["text"].what_worked == ["Accounts were fully reused"])
        answer_open_actions(run)
        ask = fake_ask([text("777 calls", [action("Do Y")]), text("still 777", [action("Do Y")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R5 invented twice -> numbers-only review: no text, no actions, the note says why",
               out["text"] is None and out["action_ids"] == [] and "invented numbers ['777']" in out["note"])
        expect("R5 the lock is free after a review", get_run(run["run_key"])["locked_by"] is None)

        # R11 / R12. claims RESULTS do not show (outcomes here: no booked demo) -> one retry -> fixed, or numbers-only
        from brain_review import unsupported_claims
        def calls_data(outcomes, completed=None, summary=""):
            """RESULTS with one calls task whose calls have these outcomes (the outcome data the guard reads)."""
            return {"tool_tasks": [{"task_id": "T7", "outcome_counts": {o: 1 for o in outcomes},
                                    "calls_completed": len(outcomes) if completed is None else completed,
                                    "calls": [{"outcome": o, "summary": summary} for o in outcomes]}],
                    "objections": {"counts": {}, "quotes": []}}
        expect("R11 code: negated claims and suggested actions are fine; evidence in RESULTS allows the claim",
               unsupported_claims(text("No demo was booked yet", [action("Get a demo booked with the prospect")]), {}) == []
               and unsupported_claims(text("A demo was scheduled", []), calls_data(["demo_scheduled"])) == []
               and len(unsupported_claims(text("A demo was scheduled", []), calls_data(["interested"]))) == 1)
        ask = fake_ask([text("A demo was scheduled with the interested prospect", [action("Do Z")]),
                        text("Calls reached decision makers", [action("Do Z")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R11 'demo was scheduled' (outcome only 'interested') -> one retry naming it -> the second text is saved",
               out["llm_calls"] == 3 and "A demo was scheduled" in ask.review_prompts[1]
               and out["text"].what_worked == ["Calls reached decision makers"])
        answer_open_actions(run)
        ask = fake_ask([text("Meeting booked with the prospect", [action("Do Z")]),
                        text("We signed a deal this week", [action("Do Z")])], labels3)
        out = cmd_review(customer, "2030-03", ask=ask)
        expect("R12 claims twice -> numbers-only review: no text, no actions, the note says 'claims not in the data'",
               out["text"] is None and out["action_ids"] == [] and "claims not in the data" in out["note"]
               and "We signed a deal this week" in out["note"])

        # R13. the wider guard: every outcome claim needs outcome data (never the plan); negations stay fine
        cases = [("prospect replied", "Two prospects replied to the emails", calls_data(["call_later"]),
                  calls_data(["call_later"], summary="the prospect replied by email asking for pricing")),
                 ("meeting or demo held", "A demo was held with the CIO", calls_data(["interested"]),
                  calls_data(["demo_held"])),
                 ("call completed", "Three calls were completed", calls_data([], completed=0),
                  calls_data(["interested", "call_later", "not_interested"])),
                 ("lead qualified", "We got 2 qualified leads", calls_data(["interested"]), calls_data(["qualified"])),
                 ("prospect interested", "One prospect is interested", calls_data(["not_interested"]),
                  calls_data(["interested"])),
                 ("proposal or RFQ", "A proposal was sent to Tata Steel", calls_data(["interested"]),
                  calls_data(["interested"], summary="asked us to share a proposal")),
                 ("POC or pilot", "The pilot started at C1", calls_data(["interested"]),
                  calls_data(["interested"], summary="agreed to a pilot")),
                 ("deal or order won", "A purchase order was received", calls_data(["interested"]),
                  calls_data(["order_received"]))]
        caught = {name: [c.split(":")[0] for c in unsupported_claims(text(sentence, []), without)]
                  for name, sentence, without, _ in cases}
        allowed = {name: unsupported_claims(text(sentence, []), with_) for name, sentence, _, with_ in cases}
        expect("R13 caught WITHOUT outcome data: replied, demo held, calls completed, lead qualified, interested, "
               "proposal sent, pilot started, purchase order received",
               all(name in caught[name] for name in caught))
        expect("R13 allowed WITH matching outcome data (call outcomes, summaries, calls completed)",
               all(found == [] for found in allowed.values()))
        expect("R13 negations stay fine: 'nobody replied yet', 'no meeting was held', 'not qualified'",
               unsupported_claims(text("Nobody replied yet", []), calls_data([])) == []
               and unsupported_claims(text("No meeting was held", []), calls_data([])) == []
               and unsupported_claims(text("The leads were not qualified", []), calls_data([])) == [])
        plan_only = {**calls_data(["call_later"]), "tool_tasks": [{**calls_data(["call_later"])["tool_tasks"][0],
                                                                   "plan_target": "10-12 qualified enquiries"}]}
        expect("R13 the plan's own text is never evidence ('qualified' in the plan target); a target sentence "
               "is not a claim; 'not interested' is not evidence of interest",
               caught["lead qualified"] and len(unsupported_claims(text("We got 2 qualified leads", []), plan_only)) == 1
               and unsupported_claims(text("We are short of the 10-12 qualified enquiries target", []), plan_only) == []
               and len(unsupported_claims(text("One prospect is interested", []), calls_data(["not_interested"]))) == 1)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (5b) OK" if ok else "ORCHESTRATOR SELFTEST (5b) FAILED")
    return ok


def selftest_close() -> bool:
    """
    close + revision months: the old run is closed honestly (ledger, done tasks, answers kept), the new plan
    loads as <month>-r2 next to it, and nothing runs or is answered on the old run afterwards. No tool, no LLM.
    """
    from state_store import finish_step, get_steps, start_step
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator CLOSE selftest: customer '{customer}'")
    proof = {"approved_at": "2026-10-12T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 6, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}
    plan = {"month": "Month 1", "revenue_analysis": {}}
    reason = "replaced by the intake plan"
    ok = False

    def task(run, task_id):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == task_id][0]

    def snapshot(run) -> tuple:
        """Everything close may change or must keep: run, tasks, decisions, ledger rows."""
        row = get_run(run["run_key"])
        tasks = get_tasks(run["id"])
        return ((row["status"], row["stop_reason"], row["locked_by"]),
                [(t["task_id"], t["status"], t["last_error"], t["finished_at"], t["waiting_for"]) for t in tasks],
                [(d["id"], d["status"], d["answer"], d["decided_by"], d["reason"]) for d in get_decisions(run["id"])],
                [(s["idempotency_key"], s["status"], s["response"]) for t in tasks for s in get_steps(t["id"])])

    try:
        # Month pattern (pure code)
        expect("month 2026-10 and revisions 2026-10-r2 / -r12 accepted",
               all(check_month(m) == m for m in ("2026-10", "2026-10-r2", "2026-10-r12")))
        expect("bad revisions refused: -r0, -r1, -r02, -x2, 2026-13-r2, 2026-10-r",
               all(raises(lambda m=m: check_month(m))
                   for m in ("2026-10-r0", "2026-10-r1", "2026-10-r02", "2026-10-x2", "2026-13-r2", "2026-10-r")))
        expect("month_of reads the month back from the run key",
               month_of({"run_key": "anvixa-ai-product-2026-10-r2", "customer": "anvixa-ai-product"}) == "2026-10-r2"
               and month_of({"run_key": "anvixa-ai-product-2026-10", "customer": "anvixa-ai-product"}) == "2026-10")
        expect("closing answers: gate/human/goal -> stop, email/call -> no, review action -> reject",
               close_answer("human_gate_task") == "stop" and close_answer("goal_check") == "stop"
               and close_answer("email_approve") == "no" and close_answer("call_start") == "no"
               and close_answer("review_action_1") == "reject")

        # The old run: T1 done, T2 gate + T3 human waiting, T4 email waiting for approval (+ a ledger row),
        # T5 stopped earlier for its own reason, T6 pending, and an open review action on the run.
        old, _ = load_run(customer, "2026-10", run_work_orders(), plan, {}, True, proof)
        update_task(task(old, "T1")["id"], status="done", finished_at=now_iso())
        for task_id, kind in (("T2", "human_gate_task"), ("T3", "human_task"), ("T4", "email_approve")):
            row = task(old, task_id)
            update_task(row["id"], status="waiting_human", waiting_for=kind)
            open_decision(old["id"], kind, {"text": f"selftest {task_id}"}, row["id"])
        update_task(task(old, "T5")["id"], status="stopped", last_error="stopped by user: client cancelled",
                    finished_at=now_iso())
        open_decision(old["id"], "review_action_1", {"text": "selftest action"})
        step, _ = start_step(task(old, "T4")["id"], "launch", f"{old['run_key']}:T4:launch", "email_tool", False,
                             {"campaign": "selftest"})
        finish_step(step["id"], "done", response={"campaign_id": "selftest-campaign"}, external_ref="selftest-campaign")
        before = snapshot(old)

        # Refusals: nothing changes
        expect("close without a reason refused, nothing changed",
               raises(lambda: close_run(customer, "2026-10", "  ", "selftest-holder")) and snapshot(old) == before)
        take_run_lock(old["id"], "someone-else")
        locked = snapshot(old)
        expect("close while another 'run' holds the lock refused, nothing changed, the lock stays theirs",
               raises(lambda: close_run(customer, "2026-10", reason, "selftest-holder"))
               and snapshot(old) == locked and locked[0][2] == "someone-else")
        force_unlock(old["id"])
        update_task(task(old, "T4")["id"], status="running", waiting_for=None)
        held = snapshot(old)
        expect("close while a TOOL task is running refused, nothing changed, lock released",
               raises(lambda: close_run(customer, "2026-10", reason, "selftest-holder"))
               and snapshot(old) == held and get_run(old["run_key"])["locked_by"] is None)
        update_task(task(old, "T4")["id"], status="waiting_human", waiting_for="email_approve")
        expect("the test run is back as it was", snapshot(old) == before)
        expect("typing the wrong run key does not close",
               cmd_close(customer, "2026-10", reason, confirm=lambda _: "anvixa-ai-product-2026-10") is None
               and snapshot(old) == before)

        # Close
        result = cmd_close(customer, "2026-10", reason, confirm=lambda _: old["run_key"])
        after = get_run(old["run_key"])
        answers = {(task(old, t)["id"] if t else None): a for t, a in
                   (("T2", "stop"), ("T3", "stop"), ("T4", "no"), (None, "reject"))}
        decided = {d["task_row_id"]: d for d in get_decisions(old["id"]) if d["kind"] != "plan_approval"}
        expect("close answers the 4 open decisions: T2 gate stop, T3 stop, T4 email 'no', review action reject",
               result["closed_now"] and len(result["decisions"]) == 4
               and all(decided[k]["status"] == "answered" and decided[k]["answer"] == a for k, a in answers.items()))
        expect("each answer: decided by 'user', note 'run closed: <reason>'",
               all(decided[k]["decided_by"] == "user" and decided[k]["reason"] == f"run closed: {reason}"
                   for k in answers))
        expect("unfinished tasks T2, T3, T4, T6 -> stopped with 'closed: <reason>'",
               sorted(result["tasks"]) == ["T2", "T3", "T4", "T6"]
               and all(task(old, t)["status"] == "stopped" and task(old, t)["last_error"] == f"closed: {reason}"
                       and task(old, t)["waiting_for"] is None for t in ("T2", "T3", "T4", "T6")))
        expect("done T1 untouched; T5 (stopped earlier) keeps its own reason",
               before[1][0] == snapshot(old)[1][0] and task(old, "T5")["last_error"] == "stopped by user: client cancelled")
        expect("ledger row of T4 unchanged; plan approval unchanged",
               snapshot(old)[3] == before[3]
               and [d for d in get_decisions(old["id"]) if d["kind"] == "plan_approval"][0]["answer"] == "yes")
        expect("run -> stopped, 'CLOSED by user: <reason>', lock free",
               after["status"] == "stopped" and after["stop_reason"] == f"CLOSED by user: {reason}"
               and after["locked_by"] is None and is_closed(after))
        closed = snapshot(old)
        expect("closing twice changes nothing",
               not close_run(customer, "2026-10", reason, "selftest-holder")["closed_now"]
               and cmd_close(customer, "2026-10", reason, confirm=lambda _: old["run_key"]) is None
               and snapshot(old) == closed)
        expect("a closed run never runs: run + --until-wait do nothing, no open decisions",
               cmd_run(customer, "2026-10")["started"] is None
               and cmd_run_until_wait(customer, "2026-10")["steps"] == 0
               and list_open_decisions(old["id"]) == [] and snapshot(old) == closed)
        show_status(get_run(old["run_key"]))

        # The new plan: same task ids T1..T3, loaded as a revision next to the old run
        new_orders = [{"work_order_id": f"WO-N{i}", "task_id": f"T{i}", "agent": "CAM", "output_type": "coordination",
                       "task_type": "coordination", "week": 1, "depends_on": [], "target": "test",
                       "instructions": ["new plan"], "done_when": f"T{i} done"} for i in (1, 2, 3)]
        new_proof = {**proof, "work_orders_count": 3}
        expect("next free revision is 2026-10-r2", next_revision(customer, "2026-10") == "2026-10-r2")
        expect("loading the new plan as 2026-10 is still refused (the closed run is never replaced)",
               raises(lambda: load_run(customer, "2026-10", new_orders, plan, {}, True, new_proof))
               and snapshot(old) == closed)
        expect("2026-10-r2 without proof refused, writes nothing",
               raises(lambda: load_run(customer, "2026-10-r2", new_orders, plan, {}, True, None, "STOPPED: no proof (test)"))
               and get_run(run_key_for(customer, "2026-10-r2")) is None)
        new, created = load_run(customer, "2026-10-r2", new_orders, plan, {}, True, new_proof)
        expect("2026-10-r2 is a separate run: own key, 3 pending tasks, own approval, running",
               created and new["id"] != old["id"] and new["run_key"] == f"{customer}-2026-10-r2"
               and [t["status"] for t in get_tasks(new["id"])] == ["pending"] * 3
               and approval_of(new["id"])["question"]["proof"] == new_proof and new["status"] == "running")
        expect("commands without a month use the new run; the old one is unchanged",
               find_run(customer, None)["id"] == new["id"] and snapshot(old) == closed)
        cmd_run(customer, "2026-10-r2")
        cmd_answer_task(customer, "T1", "done", "selftest: new plan")
        new_t1 = task(new, "T1")
        expect("'answer T1' without a month answers the NEW run's T1, never the old one",
               [d["answer"] for d in get_decisions(new["id"]) if d["task_row_id"] == new_t1["id"]] == ["done"]
               and snapshot(old) == closed)
        expect("the next free revision is now 2026-10-r3", next_revision(customer, "2026-10-r2") == "2026-10-r3")
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (close) OK" if ok else "ORCHESTRATOR SELFTEST (close) FAILED")
    return ok


def selftest_run_data() -> bool:
    """
    The REAL toolsets (LLM and paid parts faked) use the run's understanding + the task's own work order,
    never data/understanding.json / data/work_orders.json; the company judge uses the task's LEDGER payload,
    never data/quickicp_payload.json, also after a dry run and after a crash. No LLM, no paid call, no file written.
    """
    import email_tool
    import quickhowl
    import quickicp
    import runner_quickicp_accounts as accounts_module
    import validator
    from email_tool import CampaignContent, Objective, ReferenceEmail
    from quickicp import CompanyPayload
    from runner_calls import RealCallTools
    from runner_email import RealEmailTools
    from runner_quickicp_accounts import RealAccountsTools, SimulatedCrash
    from runner_quickicp_contacts import RealContactsTools
    from state_store import get_steps

    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator RUN DATA selftest (real toolsets, LLM + paid parts faked): customer '{customer}'")
    plan_files = {"data/understanding.json", "data/work_orders.json", "data/quickicp_payload.json"}
    changed = {"data/understanding.json": {"customer_name": "CHANGED Corp", "business_summary": "CHANGED"},
               "data/work_orders.json": [{"agent": "QuickICP", "output_type": "accounts", "tier": "CHANGED"},
                                         {"agent": "QuickICP", "output_type": "contacts", "instructions": ["CHANGED"]},
                                         {"agent": "Email Agent", "output_type": "emails", "target": "CHANGED"},
                                         {"agent": "Quickhowl", "output_type": "calls", "target": "CHANGED"}],
               "data/quickicp_payload.json": {"locations": ["Delhi, India"], "company_size": ["5001-10000"]}}
    run_understanding_ = {"customer_name": "Run Customer", "business_summary": "browser automation"}
    accounts_wo = {"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "accounts",
                   "task_type": "data_build", "week": 1, "depends_on": [], "target": "2 accounts", "tier": "P1-RUN",
                   "instructions": ["run"]}
    contacts_wo = {"task_id": "T2", "agent": "QuickICP", "output_type": "contacts", "instructions": ["RUN roles"],
                   "target": "6 contacts"}
    email_wo = {"task_id": "T3", "agent": "Email Agent", "output_type": "emails", "target": "RUN 3 emails"}
    calls_wo = {"task_id": "T4", "agent": "Quickhowl", "output_type": "calls", "target": "RUN 2 calls"}
    proof = {"approved_at": "2026-10-12T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 1, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    # ---- swaps (all restored in finally) ----
    reads, writes, contexts = [], [], []
    real = {"quickicp.load_json": quickicp.load_json, "validator.load_json": getattr(validator, "load_json", None),
            "email_tool.load_json": getattr(email_tool, "load_json", None),
            "quickhowl.load_json": getattr(quickhowl, "load_json", None),
            "quickicp.save_json": quickicp.save_json,
            "validator.load_map": validator.load_map, "validator.fill_map": validator.fill_map}
    patched_methods = {name: getattr(RealAccountsTools, name)
                       for name in ("build_payload", "existing_companies", "search", "save_companies", "save_log")}
    other = {"email_tool.write_content": email_tool.write_content, "email_tool.check_settings": email_tool.check_settings,
             "email_tool.test_inboxes": email_tool.test_inboxes, "quickhowl.check_settings": quickhowl.check_settings,
             "quickhowl.make_profile": quickhowl.make_profile, "quickhowl.assert_safe": quickhowl.assert_safe}
    saved_ref = os.environ.get("CLIENT_REFERENCE")

    def reader(module_load):
        def load(path, *args, **kwargs):
            if path in plan_files or module_load is None:      # a module without a reader must read nothing
                reads.append(path)
                return changed.get(path)
            return module_load(path, *args, **kwargs)
        return load

    def recorder(path, *args, **kwargs):
        if path in plan_files:
            writes.append(path)
        return real["quickicp.save_json"](path, *args, **kwargs)

    maps = {"industry": {"items": {"good": {"score": 90, "source": "llm", "reason": "fits"}}, "company_overrides": {}},
            "title": {"items": {"head procurement": {"function": "procurement", "function_relevant": True,
                                                       "source": "llm"}}, "company_overrides": {}}}
    payload_file = "data/quickicp_payload.json"
    file_before = open(payload_file, "rb").read() if os.path.exists(payload_file) else None

    def company(domain):
        return {"COMPANY": domain, "WEBSITE": domain, "LOCATION": "Surat, Gujarat, India", "INDUSTRY": "good",
                "HEADCOUNT": 500}

    built, searched, judge_crash = [], [], {"armed": False}
    payload_locations = ["Pune, India", "Surat Metropolitan Area, India"]    # 1st build: the dry run; 2nd: the real run

    def build_payload(self, work_order):
        built.append({"understanding": self.understanding, "work_order": work_order})
        return CompanyPayload(industries=["good"], locations=[payload_locations[min(len(built), 2) - 1]],
                              company_size=["201-500"], company_count=0, coveredpages=0)

    def search(self, payload):
        searched.append(payload.model_dump())
        return [company("s1.test"), company("s2.test")]

    real_judge = RealAccountsTools.judge

    def judge(self, companies):
        if judge_crash["armed"] and companies:
            judge_crash["armed"] = False
            raise SimulatedCrash("crash after the paid search, before the judge")
        return real_judge(self, companies)

    def toolset(ctx):
        return RealAccountsTools(ctx.run["run_key"], ctx.task["task_id"], run_understanding(ctx.run), ctx.task["work_order"])

    real_entry = WIRED_RUNNERS["quickicp_accounts"]
    ok = False
    try:
        for module, name in ((quickicp, "quickicp"), (validator, "validator"), (email_tool, "email_tool"),
                             (quickhowl, "quickhowl")):
            module.load_json = reader(real[f"{name}.load_json"])
        quickicp.save_json = recorder
        accounts_module.save_json = recorder
        validator.load_map = lambda kind: {**maps[kind], "items": dict(maps[kind]["items"])}
        validator.fill_map = lambda kind, data, labels, context: contexts.append((kind, context))
        RealAccountsTools.build_payload = build_payload
        RealAccountsTools.existing_companies = lambda self, c: []
        RealAccountsTools.search = search
        RealAccountsTools.save_companies = lambda self, cs, c: {d["WEBSITE"]: 700000 + i for i, d in enumerate(cs)}
        RealAccountsTools.save_log = lambda self, log: None
        RealAccountsTools.judge = judge
        WIRED_RUNNERS["quickicp_accounts"] = (run_quickicp_accounts, toolset)

        run, _ = load_run(customer, "2026-10", [accounts_wo], {"month": "Month 1", "revenue_analysis": {}},
                          run_understanding_, True, proof)
        t1 = get_tasks(run["id"])[0]

        # 1. A dry run first (payload Pune): records nothing, writes no file
        dry = invoke_runner(run, t1, None, dry=True)
        expect("D1 dry run: own payload (Pune), nothing in the ledger, no file written",
               dry.kind == "dry" and get_steps(t1["id"]) == [] and writes == [])

        # 2. The real run: payload Surat (ledger); crash after the paid search, before the judge
        judge_crash["armed"] = True
        crashed = False
        try:
            invoke_runner(run, t1, None)
        except SimulatedCrash:
            crashed = True
        steps = {s["step"]: s for s in get_steps(t1["id"])}
        expect("D2 real run: payload Surat saved in the ledger, the paid search done, then a crash before the judge",
               crashed and steps["payload"]["output"]["locations"] == ["Surat Metropolitan Area, India"]
               and steps["search"]["status"] == "done" and len(searched) == 1)

        # 3. Restart (a new toolset = a new process; the 'changed' file still says Delhi)
        result = invoke_runner(run, t1, None)
        judged = (([s for s in get_steps(t1["id"]) if s["step"] == "state"][-1]["output"] or {}).get("loop") or {}).get("all_judged", [])
        expect("D3 restart: the payload comes back from the LEDGER (no new build, no new search); "
               "the same Surat companies still pass",
               result.kind == "done" and result.achieved == 2 and len(built) == 2 and len(searched) == 1
               and [c["validation"]["status"] for c in judged] == ["pass", "pass"])
        expect("D4 the dry run (Pune) and the changed file (Delhi) never reached the judge",
               all("Outside target geography" not in c["validation"]["reason"] for c in judged))
        expect("D5 build_payload got the RUN's understanding and THIS task's work order",
               built[-1] == {"understanding": run_understanding_, "work_order": t1["work_order"]})
        industry_ctx = [c for kind, c in contexts if kind == "industry"]
        expect("D6 the industry judge's context: the run's customer + this task's tier (not 'CHANGED')",
               industry_ctx and all(c["customer"] == "Run Customer" and c["target_tier"] == "P1-RUN" for c in industry_ctx))
        tools = RealAccountsTools(run["run_key"], "T1", run_understanding_, accounts_wo)
        expect("D7 the judge without a ledger payload -> STOPPED (never the file)",
               raises(lambda: real_judge(tools, [company("x.test")])))

        # 4. Contacts: the title judge's context
        people = RealContactsTools(run["run_key"], "T2", run_understanding_, contacts_wo)
        people.company_keys = {"s1.test"}
        verdicts = people.judge([{"FULL NAME": "A", "JOB POSITION": "Head Procurement", "COMPANY URL": "s1.test"}])
        title_ctx = [c for kind, c in contexts if kind == "title"]
        expect("D8 contacts judge: the run's customer + THIS task's roles; the person passes",
               verdicts[0]["validation"]["status"] == "pass" and title_ctx[-1]["customer"] == "Run Customer"
               and title_ctx[-1]["target_roles_work_order"]["instructions"] == ["RUN roles"])

        # 5. Email: content from the run's understanding + this task's work order
        given = {}

        def write_content(ask=None, understanding=None, work_order=None):
            given.update(understanding=understanding, work_order=work_order)
            return CampaignContent(campaign_name="x", campaign_description="x",
                                   objective=Objective(objective="o", offer="o", cta="Demo?", target="t",
                                                       problem_solved="p", tone="t", must_mention=[], never_say=[]),
                                   emails=[ReferenceEmail(subject="s", preheader="p", body_html="<p>b</p>",
                                                          wait_days_before=0)])
        email_tool.write_content = write_content
        email_tool.check_settings = lambda *names: None
        email_tool.test_inboxes = lambda: ["team@test.example"]
        os.environ["CLIENT_REFERENCE"] = saved_ref or "selftest-reference"
        request = RealEmailTools(run_understanding_, email_wo).build_request(
            [{"id": 1, "customer": customer, "full_name": "Asha Rao", "email": "a@prospect.test", "company": {}}])
        expect("D9 email: write_content got the run's understanding + THIS task's work order; reference 'RUN-CUSTOMER'",
               given == {"understanding": run_understanding_, "work_order": email_wo}
               and "MOTM-RUN-CUSTOMER-HYPER-" in request["body"]["reference"])

        # 6. Calls: the profile from the run's understanding + this task's work order
        profiled = {}

        def make_profile(understanding, work_order):
            profiled.update(understanding=understanding, work_order=work_order)
            return {"profile": {"company_name": "Run Customer"}}
        quickhowl.make_profile, quickhowl.check_settings = make_profile, lambda *names: None
        quickhowl.assert_safe = lambda request_: None
        RealCallTools(run["run_key"], "T4", run_understanding_, calls_wo).build_request(
            [{"name": "Asha", "phone": "+919000000001", "company": "C"}])
        expect("D10 calls: make_profile got the run's understanding + THIS task's work order",
               profiled == {"understanding": run_understanding_, "work_order": calls_wo})

        expect("D11 not one read of data/understanding.json, data/work_orders.json or data/quickicp_payload.json "
               "on the run path; nothing written to them", reads == [] and writes == [])
        expect("D12 data/quickicp_payload.json is byte-for-byte unchanged",
               (open(payload_file, "rb").read() if os.path.exists(payload_file) else None) == file_before)
        expect("D13 a run without a saved understanding -> STOPPED (no fallback to the file)",
               raises(lambda: run_understanding({"run_key": "x", "understanding": None})))
        no_fallback = []
        for call in (lambda: validator.company_context(), lambda: validator.people_context(),
                     lambda: validator.judge_companies([]), lambda: other["email_tool.write_content"](),
                     lambda: quickhowl.build_request([])):
            try:
                call()
            except TypeError:
                no_fallback.append(True)
        expect("D14 no file fallback any more: the judges, email content and call request REQUIRE the run's inputs",
               len(no_fallback) == 5 and reads == [])
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        for module, name in ((quickicp, "quickicp"), (validator, "validator"), (email_tool, "email_tool"),
                             (quickhowl, "quickhowl")):
            if real[f"{name}.load_json"] is None:
                module.__dict__.pop("load_json", None)
            else:
                module.load_json = real[f"{name}.load_json"]
        quickicp.save_json = real["quickicp.save_json"]
        accounts_module.save_json = real["quickicp.save_json"]
        validator.load_map, validator.fill_map = real["validator.load_map"], real["validator.fill_map"]
        for name, method in patched_methods.items():
            setattr(RealAccountsTools, name, method)
        RealAccountsTools.judge = real_judge
        for key, value in other.items():
            module_name, attr = key.split(".")
            setattr({"email_tool": email_tool, "quickhowl": quickhowl}[module_name], attr, value)
        if saved_ref is None:
            os.environ.pop("CLIENT_REFERENCE", None)
        else:
            os.environ["CLIENT_REFERENCE"] = saved_ref
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (run data) OK" if ok else "ORCHESTRATOR SELFTEST (run data) FAILED")
    return ok


# ================= PERCENT TARGETS: a share of the base task's achieved number =================

def selftest_percent() -> bool:
    """'100% of T1 contacts' -> T1's achieved number (runner_kit.target_for + the review); no tool, no LLM."""
    from brain_review import task_numbers
    from runner_kit import percent_target, target_for
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator PERCENT selftest: customer '{customer}'")
    proof = {"approved_at": "2026-10-13T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
             "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
             "work_orders_count": 4, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
             "revenue_analysis_sha256": "z" * 64}

    def wo(task_id, agent, output, deps, target):
        return {"work_order_id": f"WO-{task_id}", "task_id": task_id, "agent": agent, "output_type": output,
                "task_type": "x", "week": 1, "depends_on": deps, "target": target, "instructions": ["test"]}
    orders = [wo("T1", "QuickICP", "contacts", [], "6 contacts"),
              wo("T2", "QuickICP", "contacts", ["T1"], "50% of T1 contacts"),
              wo("T3", "Email Agent", "emails", ["T1"], "Emails to 100% of validated contacts"),
              wo("T4", "Quickhowl", "calls", ["T1", "T2"], "Calls to 100% of contacts")]
    saved_override = os.environ.pop("TEST_TARGET_OVERRIDE", None)
    ok = False
    try:
        import quickicp
        run, _ = load_run(customer, "2026-10", orders, {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        task = {t["task_id"]: t for t in get_tasks(run["id"])}
        expect("P1 a % is never stored as a count: '50% of T1 contacts' / '100% of validated contacts' -> no "
               "target_number; '6 contacts' -> 6",
               task["T1"]["target_number"] == 6 and task["T2"]["target_number"] is None
               and task["T3"]["target_number"] is None)
        expect("P2 the base task not done yet -> STOPPED (never 100)", raises(lambda: target_for(task["T2"], 40)))
        update_task(task["T1"]["id"], status="done", achieved_number=7)
        expect("P3 '50% of T1 contacts' with T1 done at 7 -> ceil(3.5) = 4 (named task); '100% of validated "
               "contacts' -> 7 (the only counted dependency)",
               percent_target(task["T2"]) == (4, "T1") and percent_target(task["T3"]) == (7, "T1")
               and target_for(task["T2"], 40) == 4)
        expect("P4 TEST_MODE still caps it: cap 3 -> 3", quickicp.TEST_MODE and target_for(task["T3"], 3) == 3)
        expect("P5 two counted dependencies and no task named -> STOPPED (not clear which)",
               raises(lambda: target_for(task["T4"], 40)))
        update_task(task["T3"]["id"], status="done", achieved_number=3, result={"target": 3})
        numbers = task_numbers(get_tasks(run["id"])[2])
        expect("P6 the review shows the resolved plan target: 7, '100% of T1' (not 100)",
               numbers["plan_target_number"] == 7 and numbers["plan_target_basis"] == "100% of T1"
               and numbers["pct_of_plan_target"] == 43)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        if saved_override is not None:
            os.environ["TEST_TARGET_OVERRIDE"] = saved_override
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (percent) OK" if ok else "ORCHESTRATOR SELFTEST (percent) FAILED")
    return ok
