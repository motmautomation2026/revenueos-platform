"""
runner_email.py
===============

Tool runner for Email Agent 'emails' tasks (T11). Called by orchestrator.py 'run'.
Wraps email_tool.py (same content rules, same API calls) with the brain's rules (runner_kit.py).

FLOW (each step recorded in brain_tool_calls)
----
    1. request   FREE  recipients = the people T4 recorded for THIS run (this customer only), with a
                       valid email; TEST_MODE: the first 3 people's details on new '+' addresses of
                       TEST_RECIPIENTS (nobody else is emailed). Content (LLM) with the code rules of
                       known issue 4 (no never_say phrase; signature 'Regards, Team <CUSTOMER_DISPLAY_NAME>').
                       Our reference is made ONCE here; the request is never rebuilt.
    2. launch    PAID  POST the SAVED request (key = our reference). 'pending' -> same request again.
                       Crash or failure -> the SAVED request is resent (the tool answers duplicate: true).
       TYPE      code (email_tool.choose_campaign_type): HYPER_PERSONALIZED, or STANDARD when that is the
                       customer's email style or most recipients have too few details. Shown to the approver.
    3. samples   FREE  HYPER: read the tool's sample emails; not ready yet -> 'running' (next run reads again).
                       The request names 3-4 DIFFERENT sample people (email_tool.pick_samples, code).
                       STANDARD: no call; the emails themselves are shown, filled in for the first recipient
    4. decision  email_approve shows every sample (+ our code checks, email_tool.sample_problems):
                       yes -> approve | no -> task stopped (the campaign stays a DRAFT, nothing is sent)
                       feedback + note -> 4a (sample batches only, at most FEEDBACK_ROUNDS times)
       4a. feedback PAID  POST the note for the batch shown (key <ref>:feedback:<round>, saved first; a crash
                       resends the SAME request, the tool de-duplicates by batch). Then read samples until the
                       NEW batch is ready ('running' meanwhile) and ask again: version N+1.
                       Only notes the tool ACCEPTED count as rounds. Refused (422 too long / invalid, 429 daily
                       limit): asked again, not counted. 409 batch_not_latest / conflict: fresh samples, asked
                       again. 409 state_conflict (no longer a DRAFT): the task fails (retry / skip / stop).
                       Old samples shape / STANDARD / no rounds left / no note: no call, asked again.
       stale / failed  samples the tool will not remake by itself: no 'yes'; feedback (rewrite) or no.
    5. approve   PAID  HYPER: POST approve with the batch + config digest of the version the person said yes
                       to (never a newer read). Failed checks -> refused, asked again. 409 batch_not_latest /
                       approval_stale -> never retried: fresh samples, asked again. STANDARD: POST start.
                       Crash -> sent again automatically (a second one answers 200 'already_started').
    6. done      when the campaign is RUNNING. achieved = recipients launched.
                 (Opens / replies need the developer's results endpoint: a later step.)

REQUIRED IN .env
----------------
    EMAIL_TOOL_URL, EMAIL_TOOL_KEY, EMAIL_TOOL_USER_EMAIL, CLIENT_REFERENCE (existing account: the
    request then never holds a mailbox password), CUSTOMER_DISPLAY_NAME; TEST_RECIPIENTS in test mode

PLAN DATA
---------
    brain_customer_runs.understanding (runner_kit.run_understanding) + THIS task's work order.
    Never data/understanding.json or data/work_orders.json: those belong to the latest plan.

DEBUGGING
---------
    "STOPPED: ... needs the people ids of a done QuickICP contacts task"  -> run T4 first
    "STOPPED: CLIENT_REFERENCE is missing"           -> the runner only works with an existing account
    "STOPPED: the reference emails still contain never_say phrases"      -> the LLM kept a forbidden phrase
    Every call: Supabase brain_tool_calls where idempotency_key like 'MOTM-%' or '<run_key>:T11:%'
"""

import os
import uuid

import email_tool
from quickicp import TEST_MODE, ToolError
from runner_kit import RunnerContext, RunnerResult, contacts_people_ids
from state_store import owned_rows_output

FEEDBACK_ROUNDS = 3              # feedback rounds on the samples; after that only yes or no
MAX_NOTE_LENGTH = 1000           # same as orchestrator.MAX_NOTE_LENGTH and lib/brain/answers.ts
# Sample batches that can never be approved; the tool does not remake them by itself (feedback does).
NOT_APPROVABLE = {"stale": "The samples are out of date (edited in the app): give feedback to rewrite them, or answer no.",
                  "failed": "The tool could not write the samples: give feedback to try again, or answer no."}


# ================= REAL TOOLS =================

class RealEmailTools:
    """The real calls: Supabase people, email_tool content + launch + samples + approve."""

    def __init__(self, understanding: dict, work_order: dict):
        """understanding: the run's (runner_kit.run_understanding); work_order: THIS task's. Never data/ files."""
        self.understanding, self.work_order = understanding, work_order

    def people(self, customer: str, ids: list[int]) -> list[dict]:
        """T4's people for THIS customer, each with its company joined (email_tool's row format)."""
        from supabase_store import COMPANY_TABLE, PEOPLE_TABLE, db
        rows = (db.table(PEOPLE_TABLE).select(f"*, {COMPANY_TABLE}(customer, company_name, website, location, industry)")
                  .eq("customer", customer).in_("id", ids).execute().data)
        for row in rows:
            row["company"] = row.pop(COMPANY_TABLE, None) or {}
        order = {person_id: i for i, person_id in enumerate(ids)}
        return sorted(rows, key=lambda r: order.get(r["id"], 10**9))

    def build_request(self, rows: list[dict]) -> dict:
        """The launch body (LLM content, recipients, NEW reference) + the person id of each recipient."""
        import email_tool
        if not os.getenv("CLIENT_REFERENCE"):
            raise ToolError("STOPPED: CLIENT_REFERENCE is missing in .env; the runner only uses an existing account.")
        body, person_ids = email_tool.build_launch_request(rows, understanding=self.understanding,
                                                           work_order=self.work_order)
        return {"body": body, "person_ids": person_ids}

    def launch(self, body: dict) -> dict:
        import email_tool
        status, reply = email_tool.send_launch(body)
        return reply

    def samples(self, state: dict) -> dict:
        import email_tool
        status, reply = email_tool.call_tool("GET", email_tool.campaign_url(state, email_tool.SAMPLES_ACTION))
        if status >= 400:
            raise ToolError(f"Samples request rejected ({status}): {str(reply)[:300]}")
        return reply

    def approve(self, state: dict, batch_id: str | None = None, config_digest: str | None = None) -> dict:
        """Approve the version shown (batch + digest); no body for the old samples shape."""
        import email_tool
        status, reply = email_tool.call_tool("POST", email_tool.campaign_url(state, email_tool.APPROVE_ACTION),
                                             email_tool.approve_body(batch_id, config_digest))
        if status >= 400:
            raise email_tool.refusal("approve", status, reply)
        return reply

    def feedback(self, state: dict, batch_id: str, text: str) -> dict:
        """Send the person's note for the batch they saw; the tool rewrites the samples (a new batch)."""
        import email_tool
        status, reply = email_tool.call_tool("POST", email_tool.campaign_url(state, email_tool.FEEDBACK_ACTION),
                                             email_tool.feedback_body(batch_id, text))
        if status >= 400:
            raise email_tool.refusal("feedback", status, reply)
        return reply

    def plain_text(self, body_html: str) -> str:
        import email_tool
        return email_tool.plain_text(body_html)

    def campaign_kind(self, body: dict) -> dict:
        import email_tool
        return email_tool.campaign_kind(body)

    def preview(self, body: dict) -> list[dict]:
        import email_tool
        return email_tool.preview(body)

    def start(self, state: dict) -> dict:
        """Start a STANDARD campaign that was stored as a draft (the tool's start route)."""
        import email_tool
        status, reply = email_tool.call_tool("POST", email_tool.campaign_url(state, "start"))
        if status >= 400:
            raise ToolError(f"Start rejected ({status}): {str(reply)[:300]}")
        return reply


# ================= FAKE TOOLS (selftest only: no network, no LLM) =================

class FakeEmailTools:
    """
    Canned email tool for the orchestrator selftest. Counts every call. Can crash on purpose.
        samples_after   samples are 'ready' from this samples call on (1 = at once)
        crash_launch / crash_approve / crash_feedback   crash INSIDE that call number
        new_shape       the tool's real samples shape: batch_id b<N>, config_digest d<N>, 4 people x 3 emails,
                        each body with the tool's hidden preheader <div>; feedback de-duplicated by batch
        approve_errors / feedback_errors   [(HTTP status, error code), ...]: the next calls answer these
                        (in the tool's error shape, mapped by email_tool.refusal like the real tools)
        statuses        samples_status for the next samples reads, e.g. ['stale'] or ['none']
        bad_sample      batch b1: 'guaranteed' in Rahul's email 2 body
        bad_preheader   batch b1: 'guaranteed' in Rahul's email 1 preheader
        email_state     state of Rahul's email 1 (default 'OK')
    """

    PEOPLE = [("Rahul", "CTO", "Acme"), ("Priya", "COO", "Brightline"), ("Arjun", "CFO", "Corvus"),
              ("Meera", "Head of IT", "Delta Works")]

    def __init__(self, rows, samples_after=1, crash_launch=None, crash_approve=None, standard=False,
                 new_shape=False, crash_feedback=None, approve_errors=(), feedback_errors=(), statuses=(),
                 bad_sample=False, bad_preheader=False, email_state="OK"):
        self.rows = rows
        self.standard = standard
        self.samples_after = samples_after
        self.crash_launch, self.crash_approve, self.crash_feedback = crash_launch, crash_approve, crash_feedback
        self.new_shape, self.bad_sample, self.bad_preheader, self.email_state = new_shape, bad_sample, bad_preheader, email_state
        self.approve_errors, self.feedback_errors, self.statuses = list(approve_errors), list(feedback_errors), list(statuses)
        self.calls = {"build": 0, "launch": 0, "samples": 0, "approve": 0, "start": 0, "feedback": 0}
        self.launch_bodies = []
        self.batch, self.generating, self.generations = 1, False, 0
        self.feedback_replies, self.feedback_bodies, self.approve_bodies = {}, [], []

    def people(self, customer, ids):
        return [dict(r) for r in self.rows if r["customer"] == customer and r["id"] in ids]

    def build_request(self, rows):
        self.calls["build"] += 1
        chosen = rows if self.new_shape else rows[:3] if TEST_MODE else rows
        body = {"reference": f"MOTM-SELFTEST-HYPER-{uuid.uuid4().hex[:12]}",
                "recipients": [{"email": f"team+hp{i}@test.example", "first_name": r["full_name"]}
                               for i, r in enumerate(chosen)],
                "emails": [{"subject": "Hello", "body_html": "<p>Hi {{first_name}},</p><p>Regards,<br>Team Anvixa</p>"}]}
        if not self.standard:
            body["campaign_type"] = "HYPER_PERSONALIZED"
        if self.new_shape:
            import email_tool
            body["recipients"] = [{"email": f"team+hp{i}@test.example", "first_name": r["full_name"].split()[0],
                                   "company": r["company"].get("company_name"), "title": r.get("job_position"),
                                   "city": r.get("city"), "company_industry": "Manufacturing"}
                                  for i, r in enumerate(chosen)]
            body["objective"] = {"cta": "Would a short call help?", "must_mention": ["SAP"], "never_say": ["synergy"]}
            body["sample_recipients"] = email_tool.pick_samples(body["recipients"])
        return {"body": body, "person_ids": [r["id"] for r in chosen]}

    def launch(self, body):
        from runner_quickicp_accounts import SimulatedCrash
        self.calls["launch"] += 1
        self.launch_bodies.append(body["reference"])
        if self.crash_launch == self.calls["launch"]:
            raise SimulatedCrash("crash during the email launch")
        return {"campaign_id": "c-1", "workspace_id": "w-1", "campaign_status": "DRAFT", "start_status": "awaiting_approval",
                "duplicate": len(self.launch_bodies) > 1}

    def samples(self, state):
        self.calls["samples"] += 1
        if self.new_shape:
            return self.batch_reply()
        ready = self.calls["samples"] >= self.samples_after
        return {"samples_status": "ready" if ready else "generating",
                "samples": [{"email_number": 1, "subject": "Faster SAP work at C1", "recipient_first_name": "A",
                             "recipient_company": "C1", "body_html": "<p>Hi A,</p><p>Regards,<br>Team Anvixa</p>"}]
                if ready else []}

    PREHEADER = ('<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all;font-size:1px;'
                 'line-height:1px;color:transparent">{text}' + "&zwnj;&nbsp;" * 40 + "</div>")

    def error(self, call, status, code):
        """The tool's error reply, mapped exactly like the real tools do."""
        import email_tool
        reply = {"error": {"code": code, "message": f"selftest {code}", "request_id": "req-1", "details": None}}
        return email_tool.refusal(call, status, reply)

    def batch_reply(self):
        """The tool's GET samples reply: 'generating' once right after feedback, then batch b<N> ready."""
        top = {"campaign_id": "c-1", "campaign_status": "DRAFT", "approval_status": "NONE"}
        status = self.statuses.pop(0) if self.statuses else None
        if status == "none":
            return {**top, "samples_status": "none", "batch_id": None, "config_digest": None, "recipients": [], "samples": []}
        if self.generating or self.calls["samples"] < self.samples_after:
            self.generating = False
            return {**top, "samples_status": "generating", "batch_id": f"b{self.batch}",
                    "config_digest": f"d{self.batch}", "recipients": [], "samples": []}
        people, flat = [], []
        for name, title, company in self.PEOPLE:
            emails = []
            for number in (1, 2, 3):
                first = self.batch == 1 and name == "Rahul"
                body_bad = " Results are guaranteed." if self.bad_sample and first and number == 2 else ""
                pre = ("Guaranteed results for " if self.bad_preheader and first and number == 1 else "SAP idea for ") + company
                email = {"email_number": number, "state": self.email_state if first and number == 1 else "OK",
                         "subject": f"SAP at {company} (v{self.batch})",
                         "body_html": self.PREHEADER.format(text=pre) + f"<p>Hi {name},</p><p>We help teams run SAP "
                                      f"faster.{body_bad}</p><p>Would a short call help?</p><p>Regards,<br>Team Anvixa</p>",
                         "fallback_used": False, "failure_codes": []}
                emails.append(email)
                flat.append({**email, "recipient_first_name": name, "recipient_company": company})
            people.append({"audience_member_id": f"a-{name}", "email": f"{name.lower()}@x.test", "first_name": name,
                           "company": company, "title": title, "emails": emails})
        return {**top, "samples_status": status or "ready", "batch_id": f"b{self.batch}",
                "config_digest": f"d{self.batch}", "recipients": people, "samples": flat}

    def feedback(self, state, batch_id, text):
        """The tool's feedback: de-duplicated by batch (same batch -> duplicate: true, the SAME new batch_id)."""
        from runner_quickicp_accounts import SimulatedCrash
        self.calls["feedback"] += 1
        if self.feedback_errors:
            raise self.error("feedback", *self.feedback_errors.pop(0))
        if batch_id in self.feedback_replies:
            reply = {**self.feedback_replies[batch_id], "duplicate": True}
        elif batch_id != f"b{self.batch}":
            raise self.error("feedback", 409, "batch_not_latest")
        else:
            self.batch += 1
            self.generations += 1
            self.generating = True
            self.feedback_bodies.append({"batch_id": batch_id, "feedback": text})
            reply = self.feedback_replies[batch_id] = {"campaign_id": "c-1", "batch_id": f"b{self.batch}",
                                                       "samples_status": "generating", "duplicate": False}
        if self.crash_feedback == self.calls["feedback"]:
            raise SimulatedCrash("crash during feedback (after the tool took it)")
        return reply

    def approve(self, state, batch_id=None, config_digest=None):
        import email_tool
        from runner_quickicp_accounts import SimulatedCrash
        self.calls["approve"] += 1
        if self.crash_approve == self.calls["approve"]:
            raise SimulatedCrash("crash during approve")
        self.approve_bodies.append(email_tool.approve_body(batch_id, config_digest))
        if self.approve_errors:
            status, code = self.approve_errors.pop(0)
            if code == "batch_not_latest":
                self.batch += 1                  # a newer batch exists in the tool
            if code == "approval_stale":
                self.statuses.append("stale")    # settings changed: the batch is stale and is NOT remade
            raise self.error("approve", status, code)
        return {"status": "already_started" if self.calls["approve"] > 1 else "started",
                "campaign_id": "c-1", "campaign_status": "RUNNING"}

    def plain_text(self, body_html):
        return body_html.replace("<p>", "").replace("</p>", "\n").replace("<br>", "\n")

    def campaign_kind(self, body):
        standard = body.get("campaign_type") != "HYPER_PERSONALIZED"
        return {"type": "STANDARD" if standard else "HYPER_PERSONALIZED",
                "note": "This customer's email style is: the same email for everyone." if standard else ""}

    def preview(self, body):
        first = body["recipients"][0]
        return [{"email": 1, "for": first["first_name"], "subject": body["emails"][0]["subject"],
                 "text": self.plain_text(body["emails"][0]["body_html"].replace("{{first_name}}", first["first_name"]))}]

    def start(self, state):
        self.calls["start"] += 1
        return {"status": "already_started" if self.calls["start"] > 1 else "started", "campaign_status": "RUNNING"}


# ================= THE RUNNER =================

def run_email(ctx: RunnerContext, tools) -> RunnerResult:
    """One call of the email runner. Returns done / waiting / stopped / running (or dry)."""
    task, customer = ctx.task, ctx.run["customer"]

    # 1. the request: recipients + content + reference, made ONCE (FREE, then reused)
    def build():
        rows = tools.people(customer, contacts_people_ids(ctx))
        if not rows:
            raise ToolError(f"STOPPED: none of T4's people belong to customer '{customer}'.")
        return tools.build_request(rows)
    request = ctx.free_call("request", build, tool="email_tool")
    body, person_ids = request["body"], request["person_ids"]
    reference = body["reference"]
    if not body.get("recipients"):
        raise ToolError("STOPPED: no recipient with a valid email among T4's people.")
    kind = tools.campaign_kind(body)            # read from the saved request: the same on every run
    standard = kind["type"] == "STANDARD"
    kind_words = "STANDARD (the same email for everyone)" if standard else "HYPER-PERSONALIZED (rewritten for each recipient)"

    if ctx.dry:
        subjects = [e.get("subject") for e in body.get("emails", [])]
        lines = [f"Recipients:             {len(body['recipients'])}"
                 + (" (TEST MODE: team '+' addresses only)" if TEST_MODE else ""),
                 f"Type:                   {kind_words}" + (f" - {kind['note']}" if kind["note"] else ""),
                 f"{'Emails' if standard else 'Reference emails'}:       {len(subjects)}: " + " | ".join(subjects),
                 "Would launch:           1 campaign (stored as DRAFT, samples, then your approval before any send)"]
        return RunnerResult("dry", output={"recipients": len(body["recipients"]), "reference_emails": len(subjects),
                                           "campaign_type": kind["type"], "lines": lines})

    # 2. launch the SAVED request (PAID; the tool de-duplicates by our reference)
    reply = ctx.resendable_call("launch", reference, lambda: tools.launch(body), {"reference": reference},
                                tool="email_tool")
    state = {"workspace_id": reply["workspace_id"], "campaign_id": reply["campaign_id"]}

    # 5. already approved (any version) -> done
    approved = [r for r in ctx.steps("approve") if r["status"] == "done"]
    decisions = ctx.decisions("email_approve")
    decision = decisions[-1] if decisions else None
    # Feedback rounds = notes the TOOL ACCEPTED (done ledger rows). A refused note (422 / 429 / 409) never counts.
    accepted = [r for r in ctx.steps("feedback") if r["status"] == "done"]

    def valid_note(d: dict) -> bool:
        return bool((d["reason"] or "").strip()) and len(d["reason"]) <= MAX_NOTE_LENGTH

    def ask(shown: list, batch: dict | None = None, checks: list | None = None, note: str = "",
            feedback_ok: bool = True, status: str = "ready"):
        """4. A new email_approve. Only the answers code will accept are offered (state_store enforces them)."""
        used = min(len(accepted), FEEDBACK_ROUNDS)
        left = FEEDBACK_ROUNDS - used
        can_yes = not checks and status not in NOT_APPROVABLE
        answers = (["no"] + (["yes"] if can_yes else [])
                   + (["feedback"] if batch and batch.get("batch_id") and feedback_ok and left > 0 else []))
        text = ((note + " ") if note else "") + (
            f"{task['task_id']}: approve these {'emails' if standard else 'sample emails'} and START sending to "
            f"{len(body['recipients'])} recipient(s)" + (" (TEST MODE: team inboxes only)" if TEST_MODE else "")
            + f"? Type: {kind_words}. " + (kind["note"] + " " if kind["note"] else ""))
        if checks:
            text += f"{len(checks)} sample(s) failed our checks, so they cannot be approved. "
        text += ("yes = start, " if "yes" in answers else "") + "no = stop (the campaign stays a draft)."
        if "feedback" in answers:
            text += f" feedback + a note = the tool rewrites the samples ({left} round(s) left)."
        question = {"text": text, "reference": reference, "samples": shown, "campaign_type": kind["type"],
                    "type_note": kind["note"], "allowed_answers": answers}
        if batch:
            question.update(round=used + 1, feedback_rounds_left=left, batch_id=batch["batch_id"],
                            config_digest=batch["config_digest"], checks=list(checks or []), samples_status=status,
                            previous_feedback=[(r["request"] or {}).get("feedback") for r in accepted[:used]])
        return RunnerResult.waiting("email_approve", question)

    def show(samples: dict, note: str = ""):
        """Ask about a NEW-shape batch: ready -> with our checks; stale / failed -> no yes (feedback or no).
        None when there is nothing to ask yet ('none', 'generating')."""
        status = samples.get("samples_status")
        if status in NOT_APPROVABLE:
            return ask(email_tool.flat_samples(samples), email_tool.batch_of(samples),
                       note=" ".join(filter(None, [note, NOT_APPROVABLE[status]])), status=status)
        if status != "ready":
            return None
        return ask(email_tool.flat_samples(samples), email_tool.batch_of(samples),
                   email_tool.sample_problems(samples, body), note)

    def ask_again(note: str, feedback: bool = True):
        """The same version again (same batch, digest, checks, status), with why; no call: the answer cannot be used."""
        q = decision["question"] or {}
        batch = email_tool.batch_of(q) if q.get("batch_id") else None
        return ask(q.get("samples", []), batch, q.get("checks"), note, feedback, q.get("samples_status") or "ready")

    def read_samples() -> dict:
        return ctx.free_call("samples", lambda: tools.samples(state), seq=ctx.next_seq("samples"), tool="email_tool")

    def changed_before(step: str, key: str) -> bool:
        """This exact call already got 409 (samples changed): it is never sent again."""
        return any(r["idempotency_key"] == key and r["status"] == "failed"
                   and (r.get("error") or "").startswith(email_tool.CHANGED) for r in ctx.steps(step))

    def review_again(why: str):
        """After a 409: the latest samples, asked again (never approved with values from this read)."""
        asked = show(read_samples(), "The samples changed after you saw them; please review again.")
        return asked or RunnerResult.running(f"campaign {reference}: {why}; waiting for the new samples.")

    if not approved:
        answer = decision["answer"] if decision and decision["status"] == "answered" else None
        if answer == "no":
            return RunnerResult.stopped(f"samples not approved (decision #{decision['id']}): {decision['reason']}. "
                                        "The campaign stays a DRAFT; nothing is sent.")
        if decision is not None and answer is None:
            return RunnerResult.waiting("email_approve", decision["question"])     # still open: the same question
        if decision is None:
            if standard:
                # 3. STANDARD: the tool writes no samples; show the emails as the first recipient reads them
                return ask(tools.preview(body))
            # 3. samples (FREE read); not ready yet -> try again on the next run
            samples = read_samples()
            if email_tool.samples_shape(samples) == "new":
                return show(samples) or RunnerResult.running(
                    f"campaign {reference} stored; the tool has not finished the samples "
                    f"('{samples.get('samples_status')}'). Run again in a minute.")
            if samples.get("samples_status") != "ready":
                return RunnerResult.running(f"campaign {reference} stored; samples are still "
                                            f"'{samples.get('samples_status')}'. Run again in a minute.")
            return ask([{"email": s.get("email_number"), "for": f"{s.get('recipient_first_name')} at "
                         f"{s.get('recipient_company')}", "subject": s.get("subject"),
                         "text": tools.plain_text(s.get("body_html"))} for s in samples.get("samples", [])])

        q = decision["question"] or {}
        if answer == "feedback":
            # 4a. feedback: only on a sample batch, with a note, while rounds are left (code refuses otherwise)
            if standard or not q.get("batch_id"):
                return ask_again("Feedback is not available for this campaign yet; answer yes or no.", feedback=False)
            if not valid_note(decision):
                return ask_again(f"Feedback needs a note (at most {MAX_NOTE_LENGTH} characters) saying what to change.")
            mine = f":d{decision['id']}"
            before = [r for r in accepted if not r["idempotency_key"].endswith(mine)]
            if len(before) >= FEEDBACK_ROUNDS:
                return ask_again("No feedback rounds left: answer yes or no.", feedback=False)
            key = f"{reference}:feedback:{len(before) + 1}{mine}"      # one answer = one call (a crash resends it)
            if changed_before("feedback", key):
                return review_again("the feedback was for samples that had changed")
            note = decision["reason"].strip()
            try:
                sent = ctx.resendable_call("feedback", key, lambda: tools.feedback(state, q["batch_id"], note),
                                           email_tool.feedback_body(q["batch_id"], note), tool="email_tool")
            except email_tool.SamplesChanged:
                return review_again("the feedback was for samples that had changed")
            except email_tool.FeedbackRefused as refused:
                return ask_again(str(refused)[len(email_tool.REFUSED):])
            accepted = [r for r in ctx.steps("feedback") if r["status"] == "done"]
            samples = read_samples()
            # never show an older batch as if it were the rewritten one
            asked = show(samples) if samples.get("batch_id") == email_tool.new_batch_id(sent) else None
            return asked or RunnerResult.running(
                f"campaign {reference}: rewriting samples after feedback round {len(before) + 1}.")

        # answer == "yes"
        if q.get("checks") or q.get("samples_status") in NOT_APPROVABLE:
            return ask_again("These samples failed checks or are out of date and cannot be approved; "
                             "give feedback or answer no.")
        if standard:
            # 5. STANDARD: start the draft (PAID; a second start answers 'already_started')
            reply_approve = ctx.resendable_call("approve", f"{reference}:approve", lambda: tools.start(state),
                                                {"reference": reference}, tool="email_tool")
        elif q.get("batch_id"):
            # 5. approve EXACTLY the version shown: batch + digest from THIS decision, never from a newer read
            batch = email_tool.batch_of(q)
            key = f"{reference}:approve:{batch['batch_id']}:{batch['config_digest']}"
            if changed_before("approve", key):
                return review_again("the approved samples had changed")
            try:
                reply_approve = ctx.resendable_call(
                    "approve", key, lambda: tools.approve(state, batch["batch_id"], batch["config_digest"]),
                    {"reference": reference, **email_tool.approve_body(batch["batch_id"], batch["config_digest"])},
                    tool="email_tool")
            except email_tool.SamplesChanged:
                return review_again("the approved samples had changed")
        else:
            # 5. old samples shape: approve with no body, as before (a second approve answers 'already_started')
            reply_approve = ctx.resendable_call("approve", f"{reference}:approve", lambda: tools.approve(state),
                                                {"reference": reference}, tool="email_tool")
    else:
        reply_approve = approved[-1]["response"]

    status = str(reply_approve.get("campaign_status") or "").upper()
    if status != "RUNNING":
        return RunnerResult.running(f"approved; campaign status is '{status}', waiting for RUNNING.")
    rejected = len(reply.get("recipients_rejected") or [])
    achieved = len(body["recipients"]) - rejected
    output = owned_rows_output(ctx.run, people_ids=person_ids, achieved=achieved, target=len(body["recipients"]),
                               reference=reference, campaign_id=state["campaign_id"],
                               workspace_id=state["workspace_id"], campaign_status=status,
                               recipients=len(body["recipients"]), rejected=rejected, test_mode=TEST_MODE,
                               campaign_type=kind["type"], type_note=kind["note"])
    note = (f"campaign {reference} RUNNING for {achieved} recipient(s)"
            + (" (TEST MODE: team inboxes)" if TEST_MODE else ""))
    return RunnerResult.done(achieved, len(body["recipients"]), output, note)
