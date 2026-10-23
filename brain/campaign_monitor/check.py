"""
campaign_monitor/check.py  -  one check of every tracked campaign of a customer.

Per campaign: free reads (tools.py) -> expected vs actual per day (expect.py) -> ONE free ledger step on the
campaign's own task (step 'monitor', key 'monitor:<reference>:<n>') with the result -> one log line -> for each
stalled day not raised before, ONE campaign_stalled decision (answered by the brain, answer 'raised'; see config).
Task states, runs and the orchestrator are never touched.
"""

import datetime

from campaign_monitor import config, expect
from campaign_monitor.find import campaigns


def default_writes():
    from types import SimpleNamespace
    from state_store import finish_step, get_decisions, open_decision, record_decision, start_step
    return SimpleNamespace(start_step=start_step, finish_step=finish_step, get_decisions=get_decisions,
                           record_decision=record_decision, open_decision=open_decision)


def log_line(level: str, message: str, run: str, task: str) -> None:
    from orch.brain_log import write                     # file only; never prints, never raises
    write(level, message, run, task, "monitor")


def judge(campaign: dict, tools, now) -> dict:
    """Reads + code: {'days', 'finished', 'callbacks', 'summary'(, 'steps', 'note')}."""
    if campaign["kind"] == "calls":
        status = tools.calls_status(campaign)
        return expect.calls_days(campaign["launched_at"], now, status, tools.calls(campaign))
    status, results = tools.email_status(campaign), tools.email_results(campaign)
    if results is None:
        return {"days": [], "finished": False, "callbacks": [], "summary": f"{status or 'status n/a'}",
                "note": "email results not available yet (part 7): no stall can be judged"}
    return expect.email_days(campaign["launched_at"], now, campaign["schedule"], campaign["waits"],
                             campaign["recipients"], results, status)


def raised_days(writes, run_id: int, reference: str) -> set[str]:
    return {(d.get("question") or {}).get("day") for d in writes.get_decisions(run_id)
            if d.get("kind") == config.ALERT_KIND and (d.get("question") or {}).get("campaign") == reference}


def raise_alert(writes, campaign: dict, day: dict) -> dict:
    task = campaign["task"]
    question = {"campaign": campaign["reference"], "kind": campaign["kind"], "task_id": task["task_id"],
                "day": day["day"], "expected": day["expected_count"], "actual": day["actual"], "text": day["why"]}
    reason = f"{task['task_id']} {campaign['kind']} campaign {campaign['reference']} stalled on {day['day']}: {day['why']}"
    from quickicp import report_event                  # Sentry, only when SENTRY_DSN is set
    report_event(config.ALERT_KIND, reason, "warning", run=campaign["run"]["run_key"], task=task["task_id"])
    if config.OPEN_ALERTS:
        return writes.open_decision(campaign["run"]["id"], config.ALERT_KIND, question, task["id"])
    return writes.record_decision(campaign["run"]["id"], config.ALERT_KIND, "raised", "brain", reason,
                                  question=question, task_row_id=task["id"])


def check_campaign(campaign: dict, tools, writes, now) -> dict:
    task, run = campaign["task"], campaign["run"]
    key = f"monitor:{campaign['reference']}:{len(campaign['monitors']) + 1}"
    tool = "quickhowl" if campaign["kind"] == "calls" else "email_tool"
    row, new = writes.start_step(task["id"], "monitor", key, tool, False,
                                 {"reference": campaign["reference"], "checked_at": now.isoformat()})
    if not new:
        return {"reference": campaign["reference"], "skipped": f"key {key} already used (another check?)"}
    try:
        verdict = judge(campaign, tools, now)
    except Exception as error:
        writes.finish_step(row["id"], "failed", error=f"{type(error).__name__}: {error}"[:500])
        log_line("WARNING", f"monitor {campaign['reference']}: read failed ({type(error).__name__})", run["run_key"], task["task_id"])
        return {"reference": campaign["reference"], "failed": f"{type(error).__name__}: {error}"}
    stalls = [d for d in verdict["days"] if d["stall"]]
    known = raised_days(writes, run["id"], campaign["reference"]) if stalls else set()
    alerts = [raise_alert(writes, campaign, d)["id"] for d in stalls if d["day"] not in known]
    output = {"reference": campaign["reference"], "kind": campaign["kind"], "checked_at": now.isoformat(), **verdict,
              "stalled_days": [d["day"] for d in stalls], "alerts": alerts}
    writes.finish_step(row["id"], "done", output=output)
    line = (f"monitor {campaign['reference']} ({campaign['kind']}): {verdict['summary']}; "
            + ("FINISHED: tracking stops" if verdict["finished"] else
               f"stalled on {', '.join(output['stalled_days'])}" if stalls else verdict.get("note") or "on track")
            + (f"; {len(alerts)} alert(s) raised" if alerts else "")
            + (f"; {len(verdict['callbacks'])} callback(s) for the lead inbox" if verdict["callbacks"] else ""))
    log_line("WARNING" if stalls else "INFO", line, run["run_key"], task["task_id"])
    return {**output, "line": line, "key": key}


def check_all(key: str, month: str | None = None, tools=None, reads=None, writes=None, now=None,
              reference: str | None = None) -> list[dict]:
    """Every tracked campaign of the customer (or of one run), or only the one named by reference (brain_jobs)."""
    if tools is None:
        from campaign_monitor.tools import RealTools
        tools = RealTools()
    writes = writes or default_writes()
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return [check_campaign(c, tools, writes, now) for c in campaigns(key, month, reads)
            if c["tracked"] and reference in (None, c["reference"])]
