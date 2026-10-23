"""
campaign_monitor/find.py  -  the campaigns to track, from the LEDGER only (Supabase reads; any run status):
    calls   a Quickhowl calls task's done 'start' step (key '<reference>:start'); client_reference from its 'store'
    email   an Email Agent emails task's done 'launch' step (key = reference; reply = campaign_id, workspace_id) that
            was approved (sending starts at the done 'approve' step); schedule / wait_days / recipients from 'request'
A campaign is tracked until its last monitor step (key 'monitor:<reference>:<n>') says finished.
"""

import json
import os


def output_of(step: dict) -> dict:
    output = step.get("output")
    return json.loads(output) if isinstance(output, str) else output or {}


def campaigns(key: str, month: str | None = None, reads=None) -> list[dict]:
    if reads is None:
        from types import SimpleNamespace
        from state_store import get_runs, get_steps, get_tasks                      # reads only
        reads = SimpleNamespace(get_runs=get_runs, get_tasks=get_tasks, get_steps=get_steps)
    found = []
    for run in sorted(reads.get_runs(key), key=lambda r: r["id"]):
        if month and run["run_key"] != f"{key}-{month}":
            continue
        for task in reads.get_tasks(run["id"]):
            kind = {("Quickhowl", "calls"): "calls", ("Email Agent", "emails"): "email"}.get(
                (task.get("agent"), task.get("output_type")))
            if not kind:
                continue
            steps = sorted(reads.get_steps(task["id"]), key=lambda s: s["id"])
            done = [s for s in steps if s["status"] == "done"]
            for campaign in (calls_of(done) if kind == "calls" else emails_of(done)):
                monitors = [s for s in steps if s["step"] == "monitor"
                            and s["idempotency_key"].startswith(f"monitor:{campaign['reference']}:")]
                last = next((output_of(s) for s in reversed(monitors) if s["status"] == "done"), {})
                found.append({**campaign, "kind": kind, "run": run, "task": task, "monitors": monitors,
                              "last_check": last, "tracked": not last.get("finished")})
    return found


def calls_of(done: list[dict]) -> list[dict]:
    stored = {(s.get("request") or {}).get("reference"): s for s in done if s["step"] == "store"}
    out = []
    for start in (s for s in done if s["step"] == "start" and s["idempotency_key"].endswith(":start")):
        reference = start["idempotency_key"][:-len(":start")]
        store = stored.get(reference) or {}
        out.append({"reference": reference, "launched_at": start.get("finished_at") or start["started_at"],
                    "client_reference": (store.get("response") or {}).get("client_reference")
                    or os.getenv("QUICKHOWL_CLIENT_REFERENCE")})
    return out


def emails_of(done: list[dict]) -> list[dict]:
    body = next((output_of(s).get("body") for s in reversed(done) if s["step"] == "request"), None) or {}
    out = []
    for launch in (s for s in done if s["step"] == "launch" and (s.get("response") or {}).get("campaign_id")):
        reference = launch["idempotency_key"]
        approved = [s for s in done if s["step"] == "approve" and s["idempotency_key"].startswith(f"{reference}:approve")]
        if not approved:
            continue                                        # not sending yet: the email runner still owns it
        out.append({"reference": reference, "launched_at": approved[0].get("finished_at") or approved[0]["started_at"],
                    "campaign_id": launch["response"]["campaign_id"], "workspace_id": launch["response"].get("workspace_id"),
                    "schedule": body.get("schedule") or {},
                    "waits": [int(e.get("wait_days_before") or 0) for e in body.get("emails") or []],
                    "recipients": [r.get("email") for r in body.get("recipients") or [] if r.get("email")]})
    return out
