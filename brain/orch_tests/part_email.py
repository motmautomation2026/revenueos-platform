"""
orch_tests/part_email.py
========================

Part 'email' (email runner, sample review with feedback rounds).
Moved unchanged from orchestrator_selftest.py (2026-10-16). Run through: python orchestrator.py selftest.
"""

from datetime import datetime, timezone
from orchestrator import *
from orch_tests.common import expect, outreach_fixture, raises, sweep_selftest_runs


def selftest_email() -> bool:
    """Email runner (T11) with FakeEmailTools, + known issue 4 and send_launch 'pending' (no network, no LLM)."""
    import email_tool
    from runner_email import FakeEmailTools
    from runner_quickicp_accounts import SimulatedCrash
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    print(f"\nOrchestrator EMAIL selftest (fake email tool): customer '{customer}'")
    real_entry = WIRED_RUNNERS["email"]
    ok = False
    rows = [{"id": 901 + i, "customer": customer, "full_name": f"Person {i}", "email": f"p{i}@prospect.test",
             "company": {"company_name": f"C{i}"}} for i in range(5)]

    def setup(month, fake):
        WIRED_RUNNERS["email"] = (real_entry[0], lambda ctx: fake)
        return outreach_fixture(customer, month, "Email Agent", "emails", [r["id"] for r in fake.rows])

    def t2(run):
        return [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]

    def rows_of(run, step):
        from state_store import get_steps
        return [r for r in get_steps(t2(run)["id"]) if r["step"] == step]

    def decision(run):
        found = [d for d in get_decisions(run["id"]) if d["kind"] == "email_approve"]
        return found[-1] if found else None

    def crash(month):
        try:
            cmd_run(customer, month)
        except SimulatedCrash:
            return True
        return False

    saved_env = {name: os.environ.get(name) for name in ("CUSTOMER_DISPLAY_NAME", "CUSTOMER_LONG_NAMES")}
    real_call_tool = email_tool.call_tool
    real_load_json = getattr(email_tool, "load_json", None)    # none any more: email_tool reads no files
    try:
        # E1. request built ONCE; launch keyed by OUR reference; samples shown in the decision; yes -> RUNNING
        fake = FakeEmailTools(rows)
        run = setup("2029-01", fake)
        cmd_run(customer, "2029-01")
        launch = rows_of(run, "launch")
        reference = rows_of(run, "request")[0]["output"]["body"]["reference"]
        d = decision(run)
        expect("E1 request built once; launch row keyed by our reference, paid, done; task waits for email_approve",
               fake.calls["build"] == 1 and len(launch) == 1 and launch[0]["idempotency_key"] == reference
               and launch[0]["paid"] and launch[0]["status"] == "done" and t2(run)["waiting_for"] == "email_approve")
        expect("E1 the decision shows the samples and the recipient count (TEST MODE: 3 team addresses)",
               d["question"]["samples"][0]["subject"] == "Faster SAP work at C1" and "3 recipient(s)" in d["question"]["text"])
        cmd_run(customer, "2029-01")
        expect("E1 run again without an answer: no new launch, still 1 decision",
               fake.calls["launch"] == 1 and len([x for x in get_decisions(run["id"]) if x["kind"] == "email_approve"]) == 1)
        from state_store import start_step
        t1_row = [t for t in get_tasks(run["id"]) if t["task_id"] == "T1"][0]
        expect("E1 a key owned by ANOTHER task is refused (never reuse another task's reply)",
               raises(lambda: start_step(t1_row["id"], "launch", reference, "email_tool", True, {})))
        cmd_answer(customer, d["id"], "yes", "samples read, good")
        cmd_run(customer, "2029-01")
        task = t2(run)
        expect("E1 yes -> approve -> done when RUNNING; achieved 3; the 3 person ids recorded",
               fake.calls["approve"] == 1 and task["status"] == "done" and task["achieved_number"] == 3
               and task["result"]["people_ids"] == [901, 902, 903] and task["result"]["campaign_status"] == "RUNNING")

        # E2. crash DURING the launch -> the SAVED request is resent (same reference), never rebuilt
        fake = FakeEmailTools(rows, crash_launch=1)
        run = setup("2029-02", fake)
        expect("E2 crash during the launch", crash("2029-02"))
        cmd_run(customer, "2029-02")
        expect("E2 next run resends the SAVED request: same reference, content not rebuilt, no human asked",
               fake.calls["launch"] == 2 and len(set(fake.launch_bodies)) == 1 and fake.calls["build"] == 1
               and t2(run)["waiting_for"] == "email_approve"
               and not [x for x in get_decisions(run["id"]) if x["kind"] == "paid_call_unknown"])

        # E3. crash DURING approve -> approve sent again automatically ('already_started') -> done
        fake = FakeEmailTools(rows, crash_approve=1)
        run = setup("2029-03", fake)
        cmd_run(customer, "2029-03")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        expect("E3 crash during approve", crash("2029-03"))
        cmd_run(customer, "2029-03")
        expect("E3 next run approves again automatically -> done", fake.calls["approve"] == 2
               and t2(run)["status"] == "done")

        # E4. no -> task stopped, nothing approved (campaign stays a draft)
        fake = FakeEmailTools(rows)
        run = setup("2029-04", fake)
        cmd_run(customer, "2029-04")
        cmd_answer(customer, decision(run)["id"], "no", "subject lines too salesy")
        cmd_run(customer, "2029-04")
        expect("E4 no -> task stopped, approve never called", t2(run)["status"] == "stopped"
               and fake.calls["approve"] == 0 and "DRAFT" in t2(run)["last_error"])

        # E5. samples not ready -> 'running'; the next run reads them again
        fake = FakeEmailTools(rows, samples_after=2)
        run = setup("2029-05", fake)
        result = cmd_run(customer, "2029-05")
        expect("E5 samples still generating -> task stays 'running', no decision yet",
               t2(run)["status"] == "running" and decision(run) is None and "still running" in result["tool"])
        cmd_run(customer, "2029-05")
        expect("E5 next run: samples ready -> email_approve decision", decision(run) is not None
               and fake.calls["samples"] == 2 and fake.calls["launch"] == 1)

        # E6. --dry: nothing recorded, nothing launched
        fake = FakeEmailTools(rows)
        run = setup("2029-06", fake)
        result = cmd_dry(customer, "2029-06")
        expect("E6 --dry: 3 recipients, nothing recorded, no launch", result.output["recipients"] == 3
               and fake.calls["launch"] == 0 and rows_of(run, "request") == [] and t2(run)["status"] == "pending")

        # E7. send_launch: 'pending' -> the SAME request again (call_tool stubbed, no network)
        sent = []

        def stub(method, url, body=None):
            sent.append(body)
            return 200, ({"start_status": "pending"} if len(sent) < 3 else
                         {"start_status": "awaiting_approval", "campaign_id": "c", "workspace_id": "w"})
        email_tool.call_tool = stub
        os.environ.setdefault("EMAIL_TOOL_URL", "https://example.invalid/api/v1/x")
        body = {"reference": "R-1"}
        email_tool.send_launch(body, wait_seconds=0)
        expect("E7 send_launch: pending twice -> the same request sent 3 times", len(sent) == 3
               and all(b is body for b in sent))
        email_tool.call_tool = real_call_tool

        # E8. known issue 4: never_say in OUR reference emails -> one rewrite; signature from the display name.
        # Its own understanding + work order: reading ANY file through email_tool here is a test failure.
        from email_tool import CampaignContent, Objective, ReferenceEmail
        os.environ["CUSTOMER_DISPLAY_NAME"] = "Anvixa"
        os.environ["CUSTOMER_LONG_NAMES"] = "Anvixa AI, Anvixa AI Product"   # shorter first on purpose
        understanding = {"customer_name": "Anvixa AI", "business_summary": "selftest"}
        work_order = {"task_id": "T11", "agent": "Email Agent", "output_type": "emails", "target": "3 emails"}
        files_read = []

        def no_files(path, *args, **kwargs):
            files_read.append(path)
            raise AssertionError(f"E8 read a file: {path}")
        email_tool.load_json = no_files

        def content(subject, body_text, never_say=("guaranteed",)):
            return CampaignContent(
                campaign_name="x", campaign_description="x",
                objective=Objective(objective="Book a demo", offer="AI automation", cta="Would a short demo help?",
                                    target="Ops leaders", problem_solved="manual work", tone="direct",
                                    must_mention=[], never_say=list(never_say)),
                emails=[ReferenceEmail(subject=subject, preheader="Why ANVIXA AI teams save hours",
                                       body_html=f"<p>Hi {{{{first_name}}}},</p><p>{body_text}</p>"
                                                 "<p>Would a short demo help?</p>", wait_days_before=0)])

        def write(answers, asked):
            return email_tool.write_content(ask=lambda s, p, n: (asked.append(p), answers[len(asked) - 1])[1],
                                            understanding=understanding, work_order=work_order)

        expect("E8 long names: .env list + understanding, duplicates once, LONGEST first, display name left out",
               email_tool.long_names(understanding, "Anvixa") == ["Anvixa AI Product", "Anvixa AI"]
               and email_tool.long_names({"customer_name": "anvixa"}, "Anvixa") == ["Anvixa AI Product", "Anvixa AI"])
        asked = []
        fixed = write([content("Anvixa AI Product for {{company}}", "This is your last chance to try Anvixa AI Product."),
                       content("Anvixa AI Product for {{company}}",
                               "Anvixa AI Product saves hours. Anvixa AI works in your browser.")], asked)
        email = fixed.emails[0]
        expect("E8 'last chance' found -> exactly one rewrite, asked with the forbidden phrase named",
               len(asked) == 2 and "last chance" in asked[1].lower())
        expect("E8 the prompt holds the GIVEN understanding and work order",
               '"business_summary": "selftest"' in asked[0] and '"target": "3 emails"' in asked[0])
        expect("E8 signature '<p>Regards,<br>Team Anvixa</p>' added",
               email.body_html.endswith("<p>Regards,<br>Team Anvixa</p>"))
        expect("E8 'Anvixa AI Product' and 'Anvixa AI' (any case) -> 'Anvixa' in subject, preheader and body",
               email.subject == "Anvixa for {{company}}" and email.preheader == "Why Anvixa teams save hours"
               and "<p>Anvixa saves hours. Anvixa works in your browser.</p>" in email.body_html)
        everything = f"{email.subject} {email.preheader} {email.body_html}".lower()
        expect("E8 never 'Anvixa Product' or 'Anvixa AI' left anywhere",
               "anvixa product" not in everything and "anvixa ai" not in everything)
        os.environ.pop("CUSTOMER_LONG_NAMES")
        only = write([content("Anvixa AI for {{company}}", "Anvixa AI saves hours.")], [])
        expect("E8 without CUSTOMER_LONG_NAMES: the understanding's name is still replaced",
               only.emails[0].subject == "Anvixa for {{company}}" and "<p>Anvixa saves hours.</p>" in only.emails[0].body_html)
        expect("E8 still a forbidden phrase after the rewrite -> STOPPED",
               raises(lambda: write([content("s", "last chance"), content("s", "guaranteed results")], [])))

        # E8b. never_say: fixed list + self-ban rule in the FIRST prompt, whole phrases (strict), where it was found
        expect("E8b the first prompt names all 4 fixed phrases and the self-ban rule",
               all(f'"{phrase}"' in asked[0] for phrase in email_tool.NEVER_SAY_DEFAULTS)
               and "must not appear in YOUR OWN emails" in asked[0])

        def found(text, never_say=("free",)):
            return email_tool.hit_phrases(email_tool.never_say_hits(content("s", text, never_say)))
        expect("E8b whole phrases: 'freedom' and 'carefree' are NOT hits for 'free'",
               found("Enjoy the freedom of a carefree team.") == [])
        expect("E8b strict: 'free demo', 'Free trial', 'hassle-free', 'feel free' ARE hits for 'free'",
               all(found(text) == ["free"] for text in
                   ("Book a free demo.", "Start a Free trial.", "A hassle-free setup.", "Feel free to reply.")))
        expect("E8b fixed phrases any case: 'LAST CHANCE', '100% sure' (but not '1100%')",
               found("LAST CHANCE to join.", ()) == ["last chance"] and found("We are 100% sure.", ()) == ["100%"]
               and found("Growth of 1100% is rare.", ()) == [])
        hits = email_tool.never_say_hits(content("Free for {{company}}", "Hi there. Feel free to reply anytime.", ["free"]))
        expect("E8b each hit says where: email number, field and the text around it",
               {(h["email"], h["field"]) for h in hits} == {(1, "subject"), (1, "body")}
               and any("Feel free to reply" in h["snippet"] for h in hits if h["field"] == "body")
               and all("\n" not in h["snippet"] for h in hits))
        asked, message = [], ""
        try:
            write([content("s", "Try it risk-free today.", ["risk-free"]),
                   content("s", "A risk-free start for your team.", ["risk-free"])], asked)
        except ToolError as error:
            message = str(error)
        expect("E8b an LLM phrase ('risk-free') used twice -> STOPPED naming the email, field and sentence",
               message.startswith("STOPPED: the reference emails still contain never_say phrases ['risk-free']")
               and "'risk-free' in email 1 body:" in message and "A risk-free start for your team" in message)
        expect("E8b the rewrite request quotes the exact sentence and the whole-word rule",
               len(asked) == 2 and "Try it risk-free today" in asked[1] and "whole words" in asked[1])

        # E8d. the LLM bans an ordinary word ('free') for itself -> dropped by code; its longer phrases stay
        asked = []
        kept_free = write([content("s", "Feel free to reply.", ["free", "risk-free"])], asked)
        expect("E8d the LLM's own 'free' is DROPPED ('risk-free' and our fixed phrases stay): 'Feel free' passes, "
               "no rewrite (1 LLM call)",
               len(asked) == 1 and "free" not in [p.lower() for p in kept_free.objective.never_say]
               and "risk-free" in kept_free.objective.never_say
               and kept_free.objective.never_say[:4] == email_tool.NEVER_SAY_DEFAULTS)
        expect("E8d 'risk-free trial' (the LLM's longer phrase) is still caught",
               raises(lambda: write([content("s", "Start a risk-free trial.", ["free", "risk-free"]),
                                     content("s", "Start a risk-free trial today.", ["free", "risk-free"])], [])))
        expect("E8d our fixed 'last chance' stays strict, even with an empty LLM list",
               raises(lambda: write([content("s", "Your last chance.", []), content("s", "Last chance again.", [])], [])))
        expect("E8d the prompt: the LLM's list is for hype only, never ordinary words like 'free' or 'help'; "
               "the cold-email rule; email 3 follows up 'our earlier emails'",
               "Never add ordinary words you may need for polite writing" in asked[0] and "'free'" in asked[0]
               and email_tool.COLD_EMAIL_RULE in asked[0] and "never spoken with us" in asked[0]
               and "Never imply an earlier conversation, meeting, call or reply" in asked[0]
               and "polite final follow-up to our earlier emails" in asked[0])
        many = content("s", "ok", ["Cheapest", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8"])
        kept = email_tool.enforce_rules(many).objective.never_say
        expect("E8b our 4 fixed phrases come FIRST and are never cut (10 max, 'Cheapest' once)",
               kept[:4] == email_tool.NEVER_SAY_DEFAULTS and len(kept) == email_tool.MAX_PHRASES
               and [p.lower() for p in kept].count("cheapest") == 1)

        # E8c. email 3 is a calm follow-up; the rewrite names every hit field and says ALL fields count
        def three(subject3="A last idea for {{company}}", preheader3="One more thought", body3="Happy to help later."):
            def mail(subject, preheader, body, wait):
                return ReferenceEmail(subject=subject, preheader=preheader, wait_days_before=wait,
                                      body_html=f"<p>Hi {{{{first_name}}}},</p><p>{body}</p><p>Would a short demo help?</p>")
            return CampaignContent(
                campaign_name="x", campaign_description="x",
                objective=Objective(objective="Book a demo", offer="AI automation", cta="Would a short demo help?",
                                    target="Ops leaders", problem_solved="manual work", tone="direct",
                                    must_mention=[], never_say=["guaranteed"]),
                emails=[mail("Hello {{company}}", "Quick intro", "Intro.", 0),
                        mail("An idea for {{company}}", "How teams save time", "Value.", 3),
                        mail(subject3, preheader3, body3, 4)])
        import json
        schema_text = json.dumps(CampaignContent.model_json_schema())
        asked = []
        write([three(), three()], asked)
        expect("E8c the first prompt has the email 3 rule (calm, never 'last chance' in subject, preheader or body); "
               "the emails format says 'polite final follow-up', no longer 'final note'",
               email_tool.EMAIL3_RULE in asked[0] and "polite final follow-up (calm, no urgency)" in schema_text
               and "short final note" not in schema_text and len(asked) == 1)
        asked, message = [], ""
        try:
            write([three(subject3="Last chance to automate at {{company}}"),
                   three(preheader3="Your last chance to save hours")], asked)
        except ToolError as error:
            message = str(error)
        expect("E8c the r5 case: 'last chance' in the email 3 SUBJECT -> the rewrite names 'email 3 subject', says ALL "
               "fields (subject, preheader AND body) must be free of EVERY phrase, lists them, repeats the email 3 rule",
               len(asked) == 2 and "- email 3 subject: 'last chance'" in asked[1]
               and "ALL fields of ALL emails (subject, preheader AND body)" in asked[1]
               and all(f'"{p}"' in asked[1] for p in email_tool.NEVER_SAY_DEFAULTS)
               and "Moving a phrase to another field still fails" in asked[1]
               and asked[1].rstrip().endswith(email_tool.EMAIL3_RULE))
        expect("E8c ... moved to the email 3 PREHEADER after the rewrite -> the SAME STOPPED as before, naming the field",
               message.startswith("STOPPED: the reference emails still contain never_say phrases ['last chance'] after one rewrite")
               and "'last chance' in email 3 preheader:" in message)
        asked = []
        fixed = write([three(subject3="Last chance to automate at {{company}}"), three()], asked)
        expect("E8c a clean rewrite -> the content is used (one rewrite, signature added, no 'last chance' left)",
               len(asked) == 2 and fixed.emails[2].subject == "A last idea for {{company}}"
               and fixed.emails[2].body_html.endswith("<p>Regards,<br>Team Anvixa</p>")
               and not email_tool.never_say_hits(fixed))
        asked = []
        write([three(subject3="Last chance for {{company}}", body3="Guaranteed results, act today."), three()], asked)
        expect("E8c hits in two fields (subject + body) -> both listed in the rewrite request",
               "- email 3 body: 'guaranteed'" in asked[1] and "- email 3 subject: 'last chance'" in asked[1])
        expect("E8 no file in data/ was read", files_read == [])
        os.environ.pop("CUSTOMER_DISPLAY_NAME")
        expect("E8 CUSTOMER_DISPLAY_NAME missing -> STOPPED", raises(email_tool.display_name))

        # ---- F. sample review with feedback rounds (new samples shape; old shape + STANDARD unchanged) ----
        from state_store import DECISIONS, backend, now_iso

        def platform_answer(d, answer, note):
            """What the platform does: it writes the answer itself (no cmd_answer, no answer_decision checks)."""
            backend().update(DECISIONS, {"status": "answered", "answer": answer, "decided_by": "platform",
                                         "reason": note, "answered_at": now_iso()}, {"id": d["id"], "status": "open"})

        def approve_count(run):
            return len([d for d in get_decisions(run["id"]) if d["kind"] == "email_approve"])

        # F0. pick_samples: different companies first, then titles, then cities; thin people last; deterministic
        people = [{"email": "a1", "first_name": "A", "company": "Acme", "title": "CTO", "city": "Pune", "company_industry": "x"},
                  {"email": "a2", "first_name": "B", "company": "Acme", "title": "VP", "city": "Pune", "company_industry": "x"},
                  {"email": "t1", "first_name": "T", "company": "Zed"},                         # thin: no title / industry / site
                  {"email": "b1", "first_name": "C", "company": "Bolt", "title": "CTO", "city": "Pune", "company_industry": "x"},
                  {"email": "c1", "first_name": "D", "company": "Core", "title": "CFO", "city": "Delhi", "company_industry": "x"},
                  {"email": "d1", "first_name": "E", "company": "Dune", "title": "CTO", "city": "Pune", "company_industry": "x"}]
        picked = email_tool.pick_samples(people)
        expect("F0 pick_samples: 4 people from 4 different companies (new title + city preferred: c1 2nd), ties in "
               "input order, the thin one last, the same answer twice, fewer than 3 people -> all of them",
               picked == ["a1", "c1", "b1", "d1"] and email_tool.pick_samples(people) == picked
               and email_tool.pick_samples(people, n=6)[-1] == "t1"
               and email_tool.pick_samples(people[:2]) == ["a1", "a2"])

        # F0b. build_launch_request: sample_recipients for HYPER only (real function, content given, no LLM)
        saved = {name: os.environ.get(name) for name in ("EMAIL_TOOL_USER_EMAIL", "CLIENT_REFERENCE", "EMAIL_CAMPAIGN_TYPE")}
        saved_test_mode = email_tool.TEST_MODE
        try:
            os.environ.update({"EMAIL_TOOL_USER_EMAIL": "selftest@test.example", "CLIENT_REFERENCE": "selftest-client"})
            os.environ.pop("EMAIL_CAMPAIGN_TYPE", None)
            email_tool.TEST_MODE = False                 # real recipients (only 3 '+' copies in test mode)
            people_rows = [{"id": 970 + i, "full_name": f"Name{i} X", "email": f"n{i}@prospect.test", "job_position": title,
                            "location": "Pune, Maharashtra, India",
                            "company": {"company_name": company, "website": f"{company.lower()}.test", "industry": "x"}}
                           for i, (company, title) in enumerate([("Acme", "CTO"), ("Acme", "VP"), ("Bolt", "CFO"),
                                                                 ("Core", "COO"), ("Dune", "CTO")])]
            hyper, _ = email_tool.build_launch_request(people_rows, understanding={"customer_name": "Selftest"},
                                                       work_order={}, write=lambda: three())
            os.environ["EMAIL_CAMPAIGN_TYPE"] = "STANDARD"
            plain, _ = email_tool.build_launch_request(people_rows, understanding={"customer_name": "Selftest"},
                                                       work_order={}, write=lambda: three())
            expect("F0b build_launch_request: HYPER names 4 sample people from 4 companies; STANDARD names none",
                   len(hyper["sample_recipients"]) == 4
                   and len({r["company"] for r in hyper["recipients"] if r["email"] in hyper["sample_recipients"]}) == 4
                   and "sample_recipients" not in plain)
        finally:
            email_tool.TEST_MODE = saved_test_mode
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        # F0c. with_sender / without_password: the workspace login password is added at send time
        # (so the saved request never holds it) and masked in logs; no password set -> nothing added.
        saved_secrets = {name: os.environ.get(name) for name in ("EMAIL_TOOL_USER_PASSWORD", "SMTP_HOST")}
        try:
            os.environ.pop("SMTP_HOST", None)                                # isolate from mailbox injection
            body = {"user": {"email": "u@x.test", "workspace_name": "X", "role": "ADMIN"}, "reference": "R"}
            os.environ["EMAIL_TOOL_USER_PASSWORD"] = "Client@2026Secure"
            sent = email_tool.with_sender(body)
            expect("F0c the login password is added to the sent user block but not to the saved body",
                   sent["user"]["password"] == "Client@2026Secure" and "password" not in body["user"]
                   and email_tool.with_sender(body)["user"]["password"] == "Client@2026Secure")   # same every send
            expect("F0c the login password is masked in any logged reply",
                   "Client@2026Secure" not in email_tool.without_password('{"echo": "Client@2026Secure"}')
                   and "********" in email_tool.without_password("Client@2026Secure"))
            os.environ.pop("EMAIL_TOOL_USER_PASSWORD", None)
            expect("F0c no password set -> the user block is sent unchanged",
                   "password" not in email_tool.with_sender(body)["user"])
        finally:
            for name, value in saved_secrets.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        rows_f = [{"id": 951 + i, "customer": customer, "full_name": f"Name{i} X", "email": f"f{i}@prospect.test",
                   "job_position": title, "company": {"company_name": company}}
                  for i, (company, title) in enumerate([("Acme", "CTO"), ("Acme", "VP"), ("Bolt", "CFO"),
                                                        ("Core", "COO"), ("Dune", "CTO"), ("Echo", "CEO")])]

        # F1. the request names 4 sample people from different companies, saved once
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-01", fake)
        cmd_run(customer, "2031-01")
        body = rows_of(run, "request")[0]["output"]["body"]
        picks = body["sample_recipients"]
        cmd_run(customer, "2031-01")
        d = decision(run)
        q = d["question"]
        expect("F1 launch body: 4 sample_recipients, all different companies; the same on a re-run (built once)",
               len(picks) == 4 and len({r["company"] for r in body["recipients"] if r["email"] in picks}) == 4
               and fake.calls["build"] == 1 and rows_of(run, "request")[0]["output"]["body"]["sample_recipients"] == picks)
        expect("F1 version 1 shown grouped per person ('Rahul, CTO at Acme', 4 x 3 emails), batch b1/d1, no failed "
               "checks, 3 rounds left, answers yes / no / feedback",
               q["round"] == 1 and q["batch_id"] == "b1" and q["config_digest"] == "d1" and q["checks"] == []
               and q["feedback_rounds_left"] == 3 and len(q["samples"]) == 12 and q["samples"][0]["for"] == "Rahul, CTO at Acme"
               and q["samples"][3]["for"].startswith("Priya") and q["allowed_answers"] == ["feedback", "no", "yes"]
               and "feedback + a note" in q["text"])

        # F2. feedback -> keyed <ref>:feedback:1, saved with batch b1 + the note; 'running' while rewriting; version 2
        reference = body["reference"]
        cmd_answer(customer, d["id"], "feedback", "Shorter, and mention the Pune plant")
        cmd_run(customer, "2031-01")
        fb = rows_of(run, "feedback")
        expect("F2 feedback: ONE call keyed '<ref>:feedback:1:d<decision id>', paid, request = batch b1 + the note; "
               "while the new batch is generating the task is 'running' (no decision yet)",
               len(fb) == 1 and fb[0]["idempotency_key"] == f"{reference}:feedback:1:d{d['id']}" and fb[0]["paid"]
               and fb[0]["request"] == {"batch_id": "b1", "feedback": "Shorter, and mention the Pune plant"}
               and fake.calls["feedback"] == 1 and t2(run)["status"] == "running" and approve_count(run) == 1)
        cmd_run(customer, "2031-01")
        q = decision(run)["question"]
        expect("F2 next run: batch b2 ready -> a new email_approve: version 2, batch b2, 2 rounds left, the earlier feedback listed",
               approve_count(run) == 2 and q["round"] == 2 and q["batch_id"] == "b2" and q["feedback_rounds_left"] == 2
               and q["previous_feedback"] == ["Shorter, and mention the Pune plant"] and "(v2)" in q["samples"][0]["subject"]
               and fake.calls["feedback"] == 1)

        # F3. a crash inside the feedback call -> the SAME saved request is resent; the tool rewrites once
        fake = FakeEmailTools(rows_f, new_shape=True, crash_feedback=1)
        run = setup("2031-03", fake)
        cmd_run(customer, "2031-03")
        cmd_answer(customer, decision(run)["id"], "feedback", "Less formal")
        expect("F3 crash during feedback", crash("2031-03"))
        fb = rows_of(run, "feedback")
        saved_first = len(fb) == 1 and fb[0]["status"] == "running" and fb[0]["request"]["batch_id"] == "b1"
        cmd_run(customer, "2031-03")
        cmd_run(customer, "2031-03")
        fb = rows_of(run, "feedback")
        expect("F3 the row was saved BEFORE the call (left 'running' by the crash); the next run resends the SAME "
               "request (batch b1, same note); the tool rewrote ONCE; then version 2 is asked",
               saved_first and fake.calls["feedback"] == 2 and fake.generations == 1 and len(fb) == 1
               and fb[0]["status"] == "done" and fake.feedback_bodies == [{"batch_id": "b1", "feedback": "Less formal"}]
               and decision(run)["question"]["batch_id"] == "b2")
        expect("F3 the resend got the tool's duplicate reply: duplicate true, the SAME new batch b2",
               fb[0]["response"] == {"campaign_id": "c-1", "batch_id": "b2", "samples_status": "generating", "duplicate": True})

        # F4. 3 rounds -> a 4th 'feedback' is not offered, refused when answered, and makes NO call
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-04", fake)
        cmd_run(customer, "2031-04")
        for number in (1, 2, 3):
            cmd_answer(customer, decision(run)["id"], "feedback", f"change {number}")
            cmd_run(customer, "2031-04")
            cmd_run(customer, "2031-04")
        q = decision(run)["question"]
        offered_ok = q["round"] == 4 and q["feedback_rounds_left"] == 0 and q["allowed_answers"] == ["no", "yes"]
        refused = raises(lambda: cmd_answer(customer, decision(run)["id"], "feedback", "change 4"))
        platform_answer(decision(run), "feedback", "change 4")                 # the platform writes it anyway
        cmd_run(customer, "2031-04")
        q = decision(run)["question"]
        expect("F4 after 3 rounds: version 4 offers only yes / no; 'feedback' is refused by the answer rules; written "
               "anyway (platform) -> NO call, the same version again with 'No feedback rounds left'",
               offered_ok and refused and fake.calls["feedback"] == 3 and q["batch_id"] == "b4"
               and q["text"].startswith("No feedback rounds left") and q["allowed_answers"] == ["no", "yes"])

        # F5. yes -> approve EXACTLY the version shown (b3/d3), even when the tool already has a newer batch
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-05", fake)
        cmd_run(customer, "2031-05")
        for number in (1, 2):
            cmd_answer(customer, decision(run)["id"], "feedback", f"change {number}")
            cmd_run(customer, "2031-05")
            cmd_run(customer, "2031-05")
        reference = rows_of(run, "request")[0]["output"]["body"]["reference"]
        fake.batch = 4                                                        # a newer batch exists in the tool
        cmd_answer(customer, decision(run)["id"], "yes", "version 3 is good")
        cmd_run(customer, "2031-05")
        approve = rows_of(run, "approve")
        expect("F5 yes -> approve body is exactly {batch b3, digest d3} of the version shown (not the newer b4); "
               "key '<ref>:approve:b3:d3'; done",
               fake.approve_bodies == [{"batch_id": "b3", "config_digest": "d3"}] and len(approve) == 1
               and approve[0]["idempotency_key"] == f"{reference}:approve:b3:d3" and t2(run)["status"] == "done")

        # F6. approve 409 batch_not_latest -> never retried or auto-approved; fresh samples asked again; yes approves those
        fake = FakeEmailTools(rows_f, new_shape=True, approve_errors=[(409, "batch_not_latest")])
        run = setup("2031-06", fake)
        cmd_run(customer, "2031-06")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2031-06")
        q = decision(run)["question"]
        failed = rows_of(run, "approve")
        first_ok = (fake.calls["approve"] == 1 and q["batch_id"] == "b2" and q["text"].startswith("The samples changed")
                    and len(failed) == 1 and failed[0]["status"] == "failed"
                    and failed[0]["error"].startswith(email_tool.CHANGED) and t2(run)["status"] == "waiting_human")
        cmd_answer(customer, decision(run)["id"], "yes", "the new ones are fine")
        cmd_run(customer, "2031-06")
        expect("F6 409 -> not retried, not approved; a new email_approve with the fresh batch b2; a later yes approves "
               "b2/d2 -> done",
               first_ok and fake.calls["approve"] == 2 and fake.approve_bodies[-1] == {"batch_id": "b2", "config_digest": "d2"}
               and t2(run)["status"] == "done")

        # F7. a sample with 'guaranteed' -> listed in checks; 'yes' not offered, refused, and never approved
        fake = FakeEmailTools(rows_f, new_shape=True, bad_sample=True)
        run = setup("2031-07", fake)
        cmd_run(customer, "2031-07")
        q = decision(run)["question"]
        checks_ok = (q["checks"] == ["Rahul (email 2): never_say 'guaranteed' in body"]
                     and q["allowed_answers"] == ["feedback", "no"] and "failed our checks" in q["text"])
        refused = raises(lambda: cmd_answer(customer, decision(run)["id"], "yes", "looks fine"))
        platform_answer(decision(run), "yes", "looks fine")
        cmd_run(customer, "2031-07")
        q = decision(run)["question"]
        expect("F7 'guaranteed' -> checks list it; yes not offered and refused; written anyway -> NOT approved, asked "
               "again ('failed checks')",
               checks_ok and refused and fake.calls["approve"] == 0 and q["text"].startswith("These samples failed checks")
               and q["batch_id"] == "b1" and t2(run)["status"] == "waiting_human")

        # F8. old samples shape -> feedback not offered; written anyway -> no call, yes / no again; approve without a body
        fake = FakeEmailTools(rows)
        run = setup("2031-08", fake)
        cmd_run(customer, "2031-08")
        q = decision(run)["question"]
        old_ok = "batch_id" not in q and q["allowed_answers"] == ["no", "yes"] and "feedback" not in q["text"]
        platform_answer(decision(run), "feedback", "shorter")
        cmd_run(customer, "2031-08")
        q2 = decision(run)["question"]
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2031-08")
        expect("F8 old shape: yes / no only; feedback written anyway -> no call, the same samples with 'Feedback is not "
               "available'; yes -> approve with NO body, as before",
               old_ok and fake.calls["feedback"] == 0 and q2["text"].startswith("Feedback is not available")
               and q2["samples"] == q["samples"] and fake.approve_bodies == [None] and t2(run)["status"] == "done")

        # F9. STANDARD -> feedback behaves like the old shape (no call), start is not sent
        fake = FakeEmailTools(rows, standard=True)
        run = setup("2031-09", fake)
        cmd_run(customer, "2031-09")
        offered = decision(run)["question"]["allowed_answers"]
        platform_answer(decision(run), "feedback", "shorter")
        cmd_run(customer, "2031-09")
        expect("F9 STANDARD: yes / no only; feedback -> no call, asked again; nothing started",
               offered == ["no", "yes"] and fake.calls["feedback"] == 0 and fake.calls["start"] == 0
               and decision(run)["question"]["text"].startswith("Feedback is not available"))

        # F10. 'feedback' needs a note (max 1000 characters); yes / no still need none
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2031-10", fake)
        cmd_run(customer, "2031-10")
        d = decision(run)
        expect("F10 feedback without a note, or with more than 1000 characters -> refused by cmd_answer; 'no' with "
               "no note -> accepted",
               raises(lambda: cmd_answer(customer, d["id"], "feedback", "  "))
               and raises(lambda: cmd_answer(customer, d["id"], "feedback", "x" * 1001))
               and cmd_answer(customer, d["id"], "no", None)["answer"] == "no")

        # ---- G. the email tool's real contract (error codes, stale / failed / none, preheader, PENDING / FAILED) ----
        def opened(run):
            return decision(run)["question"]

        def feedback_round(run, month, note):
            """Answer feedback, then run until the next version is asked (2 runs: rewriting, then ready)."""
            cmd_answer(customer, decision(run)["id"], "feedback", note)
            cmd_run(customer, month)
            if decision(run)["status"] == "answered":
                cmd_run(customer, month)

        # G1. approve 409 approval_stale -> the batch is stale (never remade by the tool): no yes; feedback rewrites it
        fake = FakeEmailTools(rows_f, new_shape=True, approve_errors=[(409, "approval_stale")])
        run = setup("2032-01", fake)
        cmd_run(customer, "2032-01")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2032-01")
        q = opened(run)
        stale_ok = (q["samples_status"] == "stale" and q["allowed_answers"] == ["feedback", "no"] and q["batch_id"] == "b1"
                    and q["text"].startswith("The samples changed after you saw them; please review again. The samples "
                                             "are out of date (edited in the app)"))
        platform_answer(decision(run), "yes", "approve anyway")
        cmd_run(customer, "2032-01")
        refused_yes = fake.calls["approve"] == 1 and opened(run)["text"].startswith("These samples failed checks or are out of date")
        feedback_round(run, "2032-01", "Rewrite them, please")
        cmd_answer(customer, decision(run)["id"], "yes", "the rewrite is good")
        cmd_run(customer, "2032-01")
        expect("G1 approve 409 approval_stale -> not retried; the samples are 'stale' -> asked with feedback / no only; "
               "a yes written anyway is refused; feedback on the stale batch b1 rewrites it (b2); yes approves b2/d2",
               stale_ok and refused_yes and fake.feedback_bodies == [{"batch_id": "b1", "feedback": "Rewrite them, please"}]
               and fake.approve_bodies[-1] == {"batch_id": "b2", "config_digest": "d2"} and t2(run)["status"] == "done")

        # G2. approve 409 previews_incomplete -> as before: the task fails (retry / skip / stop)
        fake = FakeEmailTools(rows_f, new_shape=True, approve_errors=[(409, "previews_incomplete")])
        run = setup("2032-02", fake)
        cmd_run(customer, "2032-02")
        cmd_answer(customer, decision(run)["id"], "yes", "ok")
        cmd_run(customer, "2032-02")
        task = t2(run)
        expect("G2 approve 409 previews_incomplete -> task_failed with the known message; no new email_approve",
               task["waiting_for"] == "task_failed" and "Samples are still being written" in (task["last_error"] or "")
               and approve_count(run) == 1)

        # G3. feedback 409 state_conflict -> no retry, a clear STOPPED, task_failed; not a round
        fake = FakeEmailTools(rows_f, new_shape=True, feedback_errors=[(409, "state_conflict")])
        run = setup("2032-03", fake)
        cmd_run(customer, "2032-03")
        cmd_answer(customer, decision(run)["id"], "feedback", "shorter")
        cmd_run(customer, "2032-03")
        task = t2(run)
        expect("G3 feedback 409 state_conflict -> task_failed 'STOPPED: the campaign is no longer a DRAFT'; sent once, "
               "no new email_approve, no round used",
               task["waiting_for"] == "task_failed" and "no longer a DRAFT" in (task["last_error"] or "")
               and fake.calls["feedback"] == 1 and approve_count(run) == 1
               and not [r for r in rows_of(run, "feedback") if r["status"] == "done"])

        # G4. feedback 409 conflict / batch_not_latest -> fresh samples, asked again; not retried, not a round
        fake = FakeEmailTools(rows_f, new_shape=True, feedback_errors=[(409, "conflict"), (409, "batch_not_latest")])
        run = setup("2032-04", fake)
        cmd_run(customer, "2032-04")
        texts = []
        for note in ("shorter", "shorter again"):
            cmd_answer(customer, decision(run)["id"], "feedback", note)
            cmd_run(customer, "2032-04")
            texts.append(opened(run)["text"])
        q = opened(run)
        expect("G4 feedback 409 conflict, then batch_not_latest -> each: one call, samples read again, asked again "
               "('samples changed'); still version 1 with 3 rounds left; nothing rewritten",
               fake.calls["feedback"] == 2 and fake.generations == 0 and approve_count(run) == 3
               and all(t.startswith("The samples changed after you saw them") for t in texts)
               and q["round"] == 1 and q["feedback_rounds_left"] == 3 and q["allowed_answers"] == ["feedback", "no", "yes"])

        # G5. refused notes (422 too long, 422 invalid, 429 daily limit) -> asked again with the reason; never a round
        fake = FakeEmailTools(rows_f, new_shape=True, feedback_errors=[(422, "feedback_too_long"), (422, "validation_error"),
                                                                       (429, "preview_budget_exhausted")])
        run = setup("2032-05", fake)
        cmd_run(customer, "2032-05")
        texts = []
        for note in ("long note", "odd note", "third note"):
            cmd_answer(customer, decision(run)["id"], "feedback", note)
            cmd_run(customer, "2032-05")
            texts.append(opened(run)["text"])
        left_after_refusals = opened(run)["feedback_rounds_left"]
        for number in (1, 2, 3):
            feedback_round(run, "2032-05", f"accepted {number}")
        q = opened(run)
        refused = raises(lambda: cmd_answer(customer, decision(run)["id"], "feedback", "one more"))
        expect("G5 422 feedback_too_long / validation_error / 429 preview_budget_exhausted -> asked again with the "
               "reason, 3 rounds still left; then 3 ACCEPTED notes -> version 4, no rounds left, feedback refused",
               texts[0].startswith("The feedback is too long") and texts[1].startswith("The email tool did not accept that note")
               and texts[2].startswith("The daily sample limit") and left_after_refusals == 3
               and q["round"] == 4 and q["feedback_rounds_left"] == 0 and q["allowed_answers"] == ["no", "yes"]
               and q["previous_feedback"] == ["accepted 1", "accepted 2", "accepted 3"] and refused
               and fake.calls["feedback"] == 6 and fake.generations == 3)

        # G6. stale / failed on the first read -> no waiting: feedback or no; stale with no rounds left -> no only
        fake = FakeEmailTools(rows_f, new_shape=True, statuses=["stale"])
        run = setup("2032-06", fake)
        cmd_run(customer, "2032-06")
        stale_q = opened(run)
        fake = FakeEmailTools(rows_f, new_shape=True, statuses=["failed"])
        run = setup("2032-07", fake)
        cmd_run(customer, "2032-07")
        failed_q = opened(run)
        fake = FakeEmailTools(rows_f, new_shape=True)
        run = setup("2032-08", fake)
        cmd_run(customer, "2032-08")
        for number in (1, 2):
            feedback_round(run, "2032-08", f"change {number}")
        cmd_answer(customer, decision(run)["id"], "feedback", "change 3")
        cmd_run(customer, "2032-08")                     # rewriting
        fake.statuses = ["stale"]                       # edited in the app before the rewrite was read
        cmd_run(customer, "2032-08")
        last_q = opened(run)
        expect("G6 'stale' -> asked at once (no waiting): 'out of date (edited in the app)', feedback / no; 'failed' -> "
               "'could not write the samples', feedback / no; stale with no rounds left -> no only",
               stale_q["samples_status"] == "stale" and stale_q["allowed_answers"] == ["feedback", "no"]
               and stale_q["text"].startswith("The samples are out of date (edited in the app)")
               and failed_q["allowed_answers"] == ["feedback", "no"]
               and failed_q["text"].startswith("The tool could not write the samples")
               and last_q["samples_status"] == "stale" and last_q["allowed_answers"] == ["no"])

        # G7. 'none' (batch_id null) -> keep waiting with a clear message; never mistaken for the old shape
        fake = FakeEmailTools(rows_f, new_shape=True, statuses=["none"])
        run = setup("2032-09", fake)
        result = cmd_run(customer, "2032-09")
        waiting_ok = t2(run)["status"] == "running" and decision(run) is None and "('none')" in result["tool"]
        cmd_run(customer, "2032-09")
        expect("G7 'none' (batch_id null) -> 'running' with 'the tool has not finished the samples (none)', no decision; "
               "the next read (ready) asks version 1",
               waiting_ok and email_tool.samples_shape({"samples_status": "none", "batch_id": None}) == "new"
               and opened(run)["batch_id"] == "b1")

        # G8. state PENDING / FAILED -> listed; only OK passes
        problems = {state: email_tool.sample_problems(FakeEmailTools(rows_f, new_shape=True, email_state=state).samples({}),
                                                      {"objective": {"cta": "Would a short call help?"}})
                    for state in ("OK", "PENDING", "FAILED")}
        expect("G8 sample state: OK passes; PENDING and FAILED are each listed as a problem",
               problems["OK"] == [] and problems["PENDING"] == ["Rahul (email 1): the tool reports state 'PENDING', issues []"]
               and problems["FAILED"] == ["Rahul (email 1): the tool reports state 'FAILED', issues []"])

        # G9. the preheader: split from body_html (the tool's real markup); never_say + cold on subject, preheader AND
        # body; greeting, CTA and must_mention on the BODY only
        real_body = ('<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;'
                     'line-height:1px;color:transparent">A quick idea for Acme' + "&zwnj;&nbsp;" * 46 + '</div>'
                     '<p>Hi Sarah,</p><p>I noticed this about your work: Company: Acme.</p><p>We help SaaS companies '
                     'improve their outbound process by automating manual prospecting work.</p><p>Would you be open to a '
                     'quick conversation?</p><p>Best,<br>John</p>')
        preheader, rest = email_tool.split_preheader(real_body)
        plain = "<p>Hi Sarah,</p><p>Would you be open to a quick conversation?</p>"

        def sarah(body_html, **extra):
            return {"batch_id": "b1", "recipients": [{"first_name": "Sarah", "company": "Acme", "title": "CEO", "emails": [
                {"email_number": 1, "state": "OK", "subject": "A quick idea for Acme", "body_html": body_html,
                 "fallback_used": False, "failure_codes": [], **extra}]}]}
        request = {"objective": {"cta": "Would you be open to a quick conversation?", "must_mention": ["outbound"]}}
        bad_pre = real_body.replace("A quick idea for Acme", "As discussed, guaranteed idea for Acme")
        shown = email_tool.flat_samples(sarah(real_body))[0]
        expect("G9 split_preheader: the tool's real sample -> preheader 'A quick idea for Acme' (padding removed), body "
               "starts '<p>Hi Sarah,'; no <div> -> no preheader, body unchanged; the person sees the body + the preheader",
               preheader == "A quick idea for Acme" and rest.startswith("<p>Hi Sarah,</p>")
               and email_tool.split_preheader(plain) == ("", plain)
               and shown["preheader"] == "A quick idea for Acme" and shown["text"].startswith("Hi Sarah,"))
        expect("G9 checks: the real sample passes (greeting / CTA / must_mention on the body); never_say + cold phrase in "
               "the PREHEADER are caught; a preheader FIELD (if one ever appears) is checked too; without a <div> the "
               "greeting still passes",
               email_tool.sample_problems(sarah(real_body), request) == []
               and email_tool.sample_problems(sarah(bad_pre), request)
               == ["Sarah (email 1): never_say 'guaranteed' in preheader",
                   "Sarah (email 1): implies an earlier conversation ('as discussed') in preheader"]
               and email_tool.sample_problems(sarah(rest, preheader="Last chance for Acme"), request)
               == ["Sarah (email 1): never_say 'last chance' in preheader"]
               and email_tool.sample_problems(sarah(plain), {"objective": {"cta": request["objective"]["cta"]}}) == [])

        # G10. the REAL tool calls (call_tool stubbed, no network): each error code maps to the right outcome
        from runner_email import RealEmailTools
        sent, answers = [], []

        def stub(method, url, body=None):
            sent.append((url.rsplit("/", 1)[-1], body))
            return answers.pop(0)
        email_tool.call_tool = stub
        saved_key = os.environ.get("EMAIL_TOOL_KEY")
        os.environ.setdefault("EMAIL_TOOL_URL", "https://example.invalid/api/v1/x")
        os.environ.setdefault("EMAIL_TOOL_KEY", "selftest")
        try:
            real = RealEmailTools({}, {})
            where = {"workspace_id": "w", "campaign_id": "c"}

            def outcome(call, status, code):
                answers.append((status, {"error": {"code": code, "message": "m", "request_id": "r", "details": None}}))
                try:
                    real.feedback(where, "b1", "note") if call == "feedback" else real.approve(where, "b1", "d1")
                except email_tool.SamplesChanged:
                    return "changed"
                except email_tool.FeedbackRefused:
                    return "refused"
                except ToolError as error:
                    return "failed: " + str(error)
                return "ok"
            results = {(call, code): outcome(call, status, code) for call, status, code in [
                ("feedback", 409, "batch_not_latest"), ("feedback", 409, "conflict"), ("feedback", 409, "state_conflict"),
                ("feedback", 422, "feedback_too_long"), ("feedback", 422, "validation_error"),
                ("feedback", 429, "preview_budget_exhausted"), ("approve", 409, "batch_not_latest"),
                ("approve", 409, "approval_stale"), ("approve", 409, "previews_failed"), ("approve", 409, "samples_not_ready")]}
            answers.append((200, {"campaign_id": "c", "batch_id": "b2", "samples_status": "generating", "duplicate": False}))
            reply = real.feedback(where, "b1", "Shorter, please.")
            expect("G10 the real tools read error.code: feedback batch_not_latest / conflict -> samples changed; "
                   "state_conflict -> STOPPED (task fails); 422 / 429 -> refused (asked again); approve batch_not_latest / "
                   "approval_stale -> samples changed; previews_failed / samples_not_ready -> fails as before; the bodies "
                   "sent are exactly {batch_id, feedback} and {batch_id, config_digest}",
               [results[k] for k in [("feedback", "batch_not_latest"), ("feedback", "conflict")]] == ["changed", "changed"]
               and results[("feedback", "state_conflict")].startswith("failed: STOPPED: the campaign is no longer a DRAFT")
               and [results[("feedback", c)] for c in ("feedback_too_long", "validation_error", "preview_budget_exhausted")]
               == ["refused"] * 3
               and [results[("approve", c)] for c in ("batch_not_latest", "approval_stale")] == ["changed", "changed"]
               and "The sample failed and cannot be approved" in results[("approve", "previews_failed")]
               and "Samples are not ready yet" in results[("approve", "samples_not_ready")]
               and reply["batch_id"] == "b2" and sent[0] == ("feedback", {"batch_id": "b1", "feedback": "note"})
               and sent[6] == ("approve", {"batch_id": "b1", "config_digest": "d1"}))
        finally:
            email_tool.call_tool = real_call_tool
            if saved_key is None:
                os.environ.pop("EMAIL_TOOL_KEY", None)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["email"] = real_entry
        email_tool.call_tool = real_call_tool
        if real_load_json is None:
            email_tool.__dict__.pop("load_json", None)
        else:
            email_tool.load_json = real_load_json
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        total, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {total} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (email) OK" if ok else "ORCHESTRATOR SELFTEST (email) FAILED")
    return ok
