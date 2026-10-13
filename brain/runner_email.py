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
    3. samples   FREE  read the sample emails; not ready yet -> 'running' (next run reads again)
    4. decision  email_approve shows every sample: yes -> approve | no -> task stopped
                       (the campaign stays a DRAFT, nothing is sent)
    5. approve   PAID  POST approve. Crash -> sent again automatically (a second approve answers
                       200 'already_started', developer guide step 3).
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

from quickicp import TEST_MODE, ToolError
from runner_kit import RunnerContext, RunnerResult, contacts_people_ids
from state_store import owned_rows_output


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
        status, reply = email_tool.call_tool("GET", email_tool.campaign_url(state, "samples"))
        if status >= 400:
            raise ToolError(f"Samples request rejected ({status}): {str(reply)[:300]}")
        return reply

    def approve(self, state: dict) -> dict:
        import email_tool
        status, reply = email_tool.call_tool("POST", email_tool.campaign_url(state, "approve"))
        if status >= 400:
            error = reply.get("error") if isinstance(reply.get("error"), dict) else {}
            raise ToolError(f"Approve rejected ({status}). "
                            + email_tool.APPROVE_MESSAGES.get(error.get("code"), str(reply)[:300]))
        return reply

    def plain_text(self, body_html: str) -> str:
        import email_tool
        return email_tool.plain_text(body_html)


# ================= FAKE TOOLS (selftest only: no network, no LLM) =================

class FakeEmailTools:
    """
    Canned email tool for the orchestrator selftest. Counts every call. Can crash on purpose.
        samples_after   samples are 'ready' from this samples call on (1 = at once)
        crash_launch / crash_approve   crash INSIDE that call number
    """

    def __init__(self, rows, samples_after=1, crash_launch=None, crash_approve=None):
        self.rows = rows
        self.samples_after = samples_after
        self.crash_launch, self.crash_approve = crash_launch, crash_approve
        self.calls = {"build": 0, "launch": 0, "samples": 0, "approve": 0}
        self.launch_bodies = []

    def people(self, customer, ids):
        return [dict(r) for r in self.rows if r["customer"] == customer and r["id"] in ids]

    def build_request(self, rows):
        self.calls["build"] += 1
        chosen = rows[:3] if TEST_MODE else rows
        body = {"reference": f"MOTM-SELFTEST-HYPER-{uuid.uuid4().hex[:12]}",
                "recipients": [{"email": f"team+hp{i}@test.example", "first_name": r["full_name"]}
                               for i, r in enumerate(chosen)],
                "emails": [{"subject": "Hello", "body_html": "<p>Hi {{first_name}},</p><p>Regards,<br>Team Anvixa</p>"}]}
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
        ready = self.calls["samples"] >= self.samples_after
        return {"samples_status": "ready" if ready else "generating",
                "samples": [{"email_number": 1, "subject": "Faster SAP work at C1", "recipient_first_name": "A",
                             "recipient_company": "C1", "body_html": "<p>Hi A,</p><p>Regards,<br>Team Anvixa</p>"}]
                if ready else []}

    def approve(self, state):
        from runner_quickicp_accounts import SimulatedCrash
        self.calls["approve"] += 1
        if self.crash_approve == self.calls["approve"]:
            raise SimulatedCrash("crash during approve")
        return {"status": "already_started" if self.calls["approve"] > 1 else "started", "campaign_status": "RUNNING"}

    def plain_text(self, body_html):
        return body_html.replace("<p>", "").replace("</p>", "\n").replace("<br>", "\n")


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

    if ctx.dry:
        subjects = [e.get("subject") for e in body.get("emails", [])]
        lines = [f"Recipients:             {len(body['recipients'])}"
                 + (" (TEST MODE: team '+' addresses only)" if TEST_MODE else ""),
                 f"Reference emails:       {len(subjects)}: " + " | ".join(subjects),
                 "Would launch:           1 campaign (stored as DRAFT, samples, then your approval before any send)"]
        return RunnerResult("dry", output={"recipients": len(body["recipients"]), "reference_emails": len(subjects),
                                           "lines": lines})

    # 2. launch the SAVED request (PAID; the tool de-duplicates by our reference)
    reply = ctx.resendable_call("launch", reference, lambda: tools.launch(body), {"reference": reference},
                                tool="email_tool")
    state = {"workspace_id": reply["workspace_id"], "campaign_id": reply["campaign_id"]}

    # 5. already approved -> done
    approved = [r for r in ctx.steps("approve") if r["status"] == "done"]
    decisions = ctx.decisions("email_approve")
    decision = decisions[-1] if decisions else None
    if not approved:
        if decision and decision["status"] == "answered" and decision["answer"] == "no":
            return RunnerResult.stopped(f"samples not approved (decision #{decision['id']}): {decision['reason']}. "
                                        "The campaign stays a DRAFT; nothing is sent.")
        if not (decision and decision["status"] == "answered" and decision["answer"] == "yes"):
            if decision is None:
                # 3. samples (FREE read); not ready yet -> try again on the next run
                samples = ctx.free_call("samples", lambda: tools.samples(state), seq=ctx.next_seq("samples"),
                                        tool="email_tool")
                if samples.get("samples_status") != "ready":
                    return RunnerResult.running(f"campaign {reference} stored; samples are still "
                                                f"'{samples.get('samples_status')}'. Run again in a minute.")
                shown = [{"email": s.get("email_number"), "for": f"{s.get('recipient_first_name')} at "
                          f"{s.get('recipient_company')}", "subject": s.get("subject"),
                          "text": tools.plain_text(s.get("body_html"))} for s in samples.get("samples", [])]
            else:
                shown = decision["question"].get("samples", [])
            # 4. a human approves the samples
            return RunnerResult.waiting("email_approve", {
                "text": f"{task['task_id']}: approve these sample emails and START sending to "
                        f"{len(body['recipients'])} recipient(s)" + (" (TEST MODE: team inboxes only)" if TEST_MODE else "")
                        + "? yes = start, no = stop (the campaign stays a draft).",
                "reference": reference, "samples": shown})
        # 5. approve (PAID; a second approve answers 'already_started')
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
                               recipients=len(body["recipients"]), rejected=rejected, test_mode=TEST_MODE)
    note = (f"campaign {reference} RUNNING for {achieved} recipient(s)"
            + (" (TEST MODE: team inboxes)" if TEST_MODE else ""))
    return RunnerResult.done(achieved, len(body["recipients"]), output, note)
