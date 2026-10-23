"""
campaign_monitor/selftest.py  -  python -m campaign_monitor selftest. A fake ledger (runs, tasks, steps, decisions),
fake tools and a fixed clock: no network, no tool, no Supabase; the log goes to a temp folder.
Mon 19 Oct 2026 = day 1, Tue 20 Oct = day 2 (the developers' report).
"""

import contextlib
import datetime
import io
import os
import tempfile
from types import SimpleNamespace

from campaign_monitor import check, cli, config, expect, find

KEY = "selftest-monitor"


def ist(day: int, hour: int, minute: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 10, day, hour, minute, tzinfo=expect.IST)


def utc(day: int, hour: int, minute: int = 0) -> str:
    return ist(day, hour, minute).astimezone(datetime.timezone.utc).isoformat()


class FakeLedger:
    """brain_customer_runs / brain_plan_tasks / brain_tool_calls / brain_decisions in memory."""

    def __init__(self):
        self.runs = [{"id": 1, "run_key": f"{KEY}-2026-10", "customer": KEY, "status": "done"}]
        self.tasks = [{"id": 10, "run_id": 1, "task_id": "T8", "agent": "Quickhowl", "output_type": "calls"},
                      {"id": 11, "run_id": 1, "task_id": "T9", "agent": "Email Agent", "output_type": "emails"},
                      {"id": 12, "run_id": 1, "task_id": "T10", "agent": "Email Agent", "output_type": "emails"},
                      {"id": 13, "run_id": 1, "task_id": "T4", "agent": "QuickICP", "output_type": "contacts"}]
        body = {"schedule": {"weekdays": [1, 2, 3, 4, 5], "window_start_local": "09:30:00", "window_end_local": "17:30:00",
                             "daily_limit": 10},
                "emails": [{"wait_days_before": 0}, {"wait_days_before": 2}],
                "recipients": [{"email": "a@x.in"}, {"email": "b@x.in"}, {"email": "c@x.in"}]}
        self.steps, self.decisions = [], []
        for task, step, key, extra in [
                (10, "store", "REF-CALL:store", {"request": {"reference": "REF-CALL"}, "response": {"client_reference": "cr-1"}}),
                (10, "start", "REF-CALL:start", {"finished_at": utc(19, 10)}),
                (11, "request", "run:T9:request", {"output": {"body": body}}),
                (11, "launch", "REF-MAIL", {"response": {"campaign_id": "c-1", "workspace_id": "w-1"}}),
                (11, "approve", "REF-MAIL:approve:b1", {"finished_at": utc(19, 10)}),
                (12, "launch", "REF-NOT-APPROVED", {"response": {"campaign_id": "c-2", "workspace_id": "w-1"}}),
                (13, "save", "run:T4:save", {})]:
            self.steps.append({"id": len(self.steps) + 1, "task_row_id": task, "step": step, "idempotency_key": key,
                               "status": "done", "started_at": utc(19, 9), "finished_at": None, "request": None,
                               "response": None, "output": None, **extra})
        self.reads = SimpleNamespace(get_runs=lambda key: self.runs if key == KEY else [],
                                     get_tasks=lambda run_id: [t for t in self.tasks if t["run_id"] == run_id],
                                     get_steps=lambda row: [s for s in self.steps if s["task_row_id"] == row])
        self.writes = SimpleNamespace(start_step=self.start_step, finish_step=self.finish_step,
                                      get_decisions=lambda run_id: [d for d in self.decisions if d["run_id"] == run_id],
                                      record_decision=self.record_decision, open_decision=self.open_decision)
        self.opened = []

    def start_step(self, task_row_id, step, key, tool, paid, request=None):
        old = next((s for s in self.steps if s["idempotency_key"] == key), None)
        if old:
            return old, False
        self.steps.append({"id": len(self.steps) + 1, "task_row_id": task_row_id, "step": step, "idempotency_key": key,
                           "tool": tool, "paid": paid, "status": "running", "request": request, "started_at": "now"})
        return self.steps[-1], True

    def finish_step(self, step_id, status, response=None, output=None, external_ref=None, error=None):
        row = next(s for s in self.steps if s["id"] == step_id)
        row.update(status=status, output=output, error=error)
        return row

    def record_decision(self, run_id, kind, answer, decided_by, reason=None, question=None, task_row_id=None):
        self.decisions.append({"id": 900 + len(self.decisions), "run_id": run_id, "kind": kind, "status": "answered",
                               "answer": answer, "decided_by": decided_by, "reason": reason, "question": question,
                               "task_row_id": task_row_id})
        return self.decisions[-1]

    def open_decision(self, run_id, kind, question, task_row_id=None):
        self.opened.append((run_id, kind, question, task_row_id))
        return {"id": 999}

    def monitor_rows(self, reference):
        return [s for s in self.steps if s["step"] == "monitor" and s["idempotency_key"].startswith(f"monitor:{reference}:")]


def call(day, hour, outcome="not_interested", finished=True, name="Asha"):
    return {"call_id": f"call-{day}-{hour}-{name}", "started_at": utc(day, hour), "outcome": outcome, "finished": finished,
            "contact": {"name": name, "company": "Acme"}, "summary": f"{outcome} on {day} Oct"}


class FakeTools:
    def __init__(self, status=None, calls=None, results=None, broken=False):
        self.status = status or {"status": "running", "contacts_still_to_call": 5, "calling_window": "09:00-19:00 Mon-Sat"}
        self.call_list, self.results, self.broken = calls or [], results, broken

    def calls_status(self, campaign):
        if self.broken:
            raise ConnectionError("down")
        return self.status

    def calls(self, campaign):
        return self.call_list

    def email_status(self, campaign):
        return None

    def email_results(self, campaign):
        return self.results


def selftest() -> bool:
    checks = []

    def expect_(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    saved = {k: os.environ.get(k) for k in ("BRAIN_LOG_DIR", "BRAIN_LOG")}
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.environ["BRAIN_LOG_DIR"] = folder
            os.environ.pop("BRAIN_LOG", None)
            ledger = FakeLedger()

            def run_check(tools, now):
                with contextlib.redirect_stdout(io.StringIO()) as said:
                    code = cli.check_command(KEY, None, tools=tools, reads=ledger.reads, writes=ledger.writes, now=now)
                return code, said.getvalue()

            found = find.campaigns(KEY, None, ledger.reads)
            expect_("campaigns come from the ledger (any run status, here 'done'): the calls 'start' step (+ its client "
                    "reference) and the APPROVED email launch (schedule, wait_days, recipients from 'request'); a launch "
                    "not approved yet and other tasks are left out",
                    [(c["kind"], c["reference"]) for c in found] == [("calls", "REF-CALL"), ("email", "REF-MAIL")]
                    and found[0]["client_reference"] == "cr-1" and found[1]["waits"] == [0, 2]
                    and found[1]["recipients"] == ["a@x.in", "b@x.in", "c@x.in"] and all(c["tracked"] for c in found)
                    and find.campaigns(KEY, "2026-11", ledger.reads) == [])

            day1 = [call(19, 11), call(19, 12, "callback_requested", name="Ravi"), call(19, 13)]
            code, said = run_check(FakeTools(calls=day1), ist(19, 20))
            first = ledger.monitor_rows("REF-CALL")
            out = first[0]["output"] if first else {}
            mail = (ledger.monitor_rows("REF-MAIL") or [{}])[0].get("output") or {}
            expect_("day 1 (Mon 19 Oct): 3 calls in the window -> on track; ONE free ledger step 'monitor:REF-CALL:1' "
                    "on the calls task (paid false); no alert; exit 0; the last line starts with 'MONITOR:'",
                    code == 0 and len(first) == 1 and first[0]["idempotency_key"] == "monitor:REF-CALL:1"
                    and first[0]["task_row_id"] == 10 and first[0]["paid"] is False and first[0]["status"] == "done"
                    and out["days"] == [{"day": "2026-10-19", "expected": True, "actual": 3, "expected_count": 5,
                                         "stall": False, "why": "calls expected"}]
                    and ledger.decisions == [] and said.strip().splitlines()[-1].startswith("MONITOR: 2 campaign(s) checked"))
            expect_("callback_requested calls are listed for the lead inbox (name, company, call id, day)",
                    [(c["name"], c["call_id"], c["day"]) for c in out.get("callbacks", [])]
                    == [("Ravi", "call-19-12-Ravi", "2026-10-19")])
            expect_("email results not available (no endpoint yet) -> said so, never a stall; still tracked",
                    mail.get("note", "").startswith("email results not available") and mail.get("stalled_days") == []
                    and mail.get("finished") is False)

            code, said = run_check(FakeTools(calls=day1), ist(20, 20))
            alert = ledger.decisions[0] if ledger.decisions else {}
            log = open(os.path.join(folder, "brain-" + datetime.date.today().isoformat() + ".log"), encoding="utf-8").read()
            expect_("day 2 (Tue 20 Oct) silent inside the calling window -> STALL: one campaign_stalled row, ANSWERED by "
                    "the brain (answer 'raised', never open), on the calls task, with day / expected / actual; exit 2; "
                    "a WARNING line in the brain log",
                    code == 2 and len(ledger.decisions) == 1
                    and (alert["kind"], alert["status"], alert["answer"], alert["decided_by"], alert["task_row_id"])
                    == ("campaign_stalled", "answered", "raised", "brain", 10)
                    and alert["question"]["day"] == "2026-10-20" and alert["question"]["actual"] == 0
                    and "stalled on 2026-10-20" in alert["reason"] and ledger.opened == []
                    and "WARNING run=selftest-monitor-2026-10 task=T8 step=monitor monitor REF-CALL (calls)" in log
                    and "stalled on 2026-10-20; 1 alert(s) raised" in log)
            code, said = run_check(FakeTools(calls=day1), ist(20, 21))
            expect_("a second check the same day -> still stalled, but NO new alert (one per campaign per day); the "
                    "monitor keys count up (monitor:REF-CALL:3)",
                    code == 0 and len(ledger.decisions) == 1 and "1 stalled, 0 new alert(s)" in said
                    and [s["idempotency_key"] for s in ledger.monitor_rows("REF-CALL")][-1] == "monitor:REF-CALL:3")

            weekend = expect.calls_days(utc(24, 10), ist(26, 8), {"status": "running", "contacts_still_to_call": 4},
                                        [call(24, 11)])
            expect_("weekend: Sat calls fine, Sun is not a calling day (Mon-Sat) -> no stall; Mon not judged before its "
                    "window closes",
                    [(d["day"], d["why"]) for d in weekend["days"]] == [("2026-10-24", "calls expected"),
                                                                         ("2026-10-25", "not a calling day")]
                    and not any(d["stall"] for d in weekend["days"]))
            paused = expect.calls_days(utc(19, 10), ist(21, 20), {"status": "paused", "contacts_still_to_call": 2}, day1)
            late = expect.calls_days(utc(19, 18, 30), ist(19, 20), {"status": "running", "contacts_still_to_call": 2}, [])
            expect_("paused -> noted, no stall; launched at 18:30 (window ends 19:00) -> that day is not judged",
                    [d["why"] for d in paused["days"]] == ["calls expected", "paused", "paused"]
                    and not any(d["stall"] for d in paused["days"]) and late["days"][0]["why"] == "launched late that day")

            done = {"status": "completed", "contacts_still_to_call": 0, "calling_window": "09:00-19:00 Mon-Sat"}
            code, said = run_check(FakeTools(status=done, calls=day1 + [call(21, 10)]), ist(21, 20))
            again = len(ledger.monitor_rows("REF-CALL"))
            run_check(FakeTools(status=done, calls=day1), ist(22, 20))
            expect_("finished (completed, 0 left, every call finished, nothing scheduled) -> 'FINISHED: tracking stops'; "
                    "the next check skips it (no new monitor step)",
                    "FINISHED: tracking stops" in said and len(ledger.monitor_rows("REF-CALL")) == again
                    and not find.campaigns(KEY, None, ledger.reads)[0]["tracked"]
                    and not expect.calls_finished({**done, "callbacks_scheduled": 1}, day1))

            schedule = {"weekdays": [1, 2, 3, 4, 5], "window_end_local": "17:30:00", "daily_limit": 10}
            step1 = [{"recipient": p, "step": 1, "sent_at": utc(19, 10), "delivered": True,
                      "bounced": p == "b@x.in", "replied": p == "c@x.in"} for p in ("a@x.in", "b@x.in", "c@x.in")]
            people = ["a@x.in", "b@x.in", "c@x.in"]
            wait = expect.email_days(utc(19, 10), ist(21, 18), schedule, [0, 2], people, step1)
            sent2 = expect.email_days(utc(19, 10), ist(21, 18), schedule, [0, 2], people,
                                      step1 + [{"recipient": "a@x.in", "step": 2, "sent_at": utc(21, 11)}])
            expect_("email: step 2 is due 2 days after step 1 (Wed); a bounced and a replied person leave the sequence -> "
                    "Wed expects 1 email; none sent = STALL; once sent: no stall, nothing left = finished; per-step counts",
                    [(d["day"], d["expected_count"], d["stall"]) for d in wait["days"]]
                    == [("2026-10-19", 3, False), ("2026-10-20", 0, False), ("2026-10-21", 1, True)]
                    and not any(d["stall"] for d in sent2["days"]) and sent2["finished"] and not wait["finished"]
                    and sent2["steps"][0] == {"step": 1, "sent": 3, "delivered": 3, "bounced": 1, "opened": 0,
                                              "replied": 1, "unsubscribed": 0})
            friday = [{"recipient": p, "step": 1, "sent_at": utc(23, 10)} for p in people]
            weekend_mail = expect.email_days(utc(23, 10), ist(26, 8), schedule, [0, 1], people, friday)
            monday_mail = expect.email_days(utc(23, 10), ist(26, 18), schedule, [0, 1], people, friday)
            limited = expect.email_days(utc(19, 10), ist(19, 18), {**schedule, "daily_limit": 2}, [0], people, [])
            expect_("email schedule Mon-Fri: step 1 Fri + wait 1 day = Sat -> moved to Mon; Sat / Sun are not a stall; Mon "
                    "with nothing sent IS; daily_limit 2 caps what a day expects",
                    [d["why"] for d in weekend_mail["days"]] == ["emails expected", "not a sending day", "not a sending day"]
                    and not any(d["stall"] for d in weekend_mail["days"])
                    and monday_mail["days"][-1]["stall"] and monday_mail["days"][-1]["expected_count"] == 3
                    and limited["days"][0]["expected_count"] == 2 and limited["days"][0]["stall"])

            broken = FakeLedger()
            with contextlib.redirect_stdout(io.StringIO()):
                code = cli.check_command(KEY, None, tools=FakeTools(broken=True), reads=broken.reads,
                                         writes=broken.writes, now=ist(20, 20))
            expect_("a tool read that fails -> that monitor step is 'failed' with the error, no alert, exit 1",
                    code == 1 and broken.monitor_rows("REF-CALL")[0]["status"] == "failed"
                    and "ConnectionError" in broken.monitor_rows("REF-CALL")[0]["error"] and broken.decisions == [])

            switched = FakeLedger()
            config.OPEN_ALERTS = True
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    cli.check_command(KEY, None, tools=FakeTools(calls=day1), reads=switched.reads,
                                      writes=switched.writes, now=ist(20, 20))
            finally:
                config.OPEN_ALERTS = False
            from state_store import ALLOWED_ANSWERS, open_decision
            from quickicp import ToolError
            try:
                open_decision(1, config.ALERT_KIND, {"text": "x"})
                refused = False
            except ToolError:
                refused = True
            expect_("the open-alert switch (config.OPEN_ALERTS, off) is the only way to OPEN one; state_store answers "
                    "campaign_stalled only with 'raised' and refuses to open it",
                    [o[1] for o in switched.opened] == ["campaign_stalled"] and switched.decisions == []
                    and ALLOWED_ANSWERS["campaign_stalled"] == {"raised"} and refused)

            with contextlib.redirect_stdout(io.StringIO()) as said:
                cli.status_command(KEY, ledger.reads, ledger.writes.get_decisions)
            shown = said.getvalue()
            expect_("status (ledger only): finished / tracked, checks, the stalled day with its decision, callbacks",
                    "T8 calls REF-CALL: finished (not tracked)" in shown and "stalled 2026-10-20" in shown
                    and "decision #900" in shown and "T9 email REF-MAIL: tracked" in shown
                    and "callback requested 2026-10-19: Ravi @ Acme (call call-19-12-Ravi)" in shown
                    and "MONITOR: 1 tracked, 1 finished" in shown)
            expect_("parse_window: '10:00-18:00 Mon-Fri' -> 10:00-18:00 on Mon..Fri; unreadable -> 09:00-19:00 Mon-Sat",
                    expect.parse_window("10:00-18:00 Mon-Fri") == (datetime.time(10), datetime.time(18), {0, 1, 2, 3, 4})
                    and expect.parse_window(None) == (datetime.time(9), datetime.time(19), {0, 1, 2, 3, 4, 5}))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    ok = all(checks)
    print(f"CAMPAIGN MONITOR SELFTEST {'OK' if ok else 'FAILED'} ({sum(checks)} of {len(checks)})")
    return ok
