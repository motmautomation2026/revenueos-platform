"""
lead_qual/inbox.py  -  the lead inbox (until the CRM is connected): one format for every source, de-duplicated,
each lead checked ONCE. Stored only through store.py (read_inbox / save_inbox).

A lead:  lead_id (LQ-0001 ...), source (call / telecalling / email / platform / manual), source_ref, received_at,
         name, company, phone, email, state, city, text, given {industry, product, quantity, employees, company_size,
         country}, test_mode, duplicates [...], check (None until checked)
De-dup:  the same source + source_ref -> nothing changes; else the same phone (last 10 digits), then email, then
         company + name -> recorded under the existing lead's 'duplicates' (its fields and its check stay as they are).
Calls:   import_calls() reads the run's LATEST done 'poll' row in the ledger (brain_tool_calls; no Quickhowl call):
         only outcomes 'interested' / 'callback_requested'; text = summary + transcript. In a test-mode run the phone
         is ours (TEST_CALL_NUMBERS), not the prospect's: such leads are marked test_mode and get NO phone.
"""

import hashlib
import json
import re

from lead_qual import store

SOURCES = ("call", "telecalling", "email", "platform", "manual")
GIVEN = ("industry", "product", "quantity", "employees", "company_size", "country")
WANTED_OUTCOMES = ("interested", "callback_requested")


def digits(phone) -> str:
    return re.sub(r"\D", "", str(phone or ""))[-10:]


def plain(text) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split())


def same_person(a: dict, b: dict) -> str | None:
    """Why a and b are the same lead (phone, email, company + name), or None."""
    if digits(a.get("phone")) and digits(a.get("phone")) == digits(b.get("phone")):
        return "phone"
    if (a.get("email") or "").strip() and (a.get("email") or "").strip().lower() == (b.get("email") or "").strip().lower():
        return "email"
    if plain(a.get("company")) and plain(a.get("name")) and (plain(a.get("company")), plain(a.get("name"))) == (
            plain(b.get("company")), plain(b.get("name"))):
        return "company + name"
    return None


def add_lead(record: dict) -> tuple[str, dict, str]:
    """
    ('added' | 'already' | 'duplicate', the inbox lead, why). 'already' = this source_ref is in the inbox (no-op);
    'duplicate' = the same person under another ref: noted on the existing lead, nothing re-checked.
    """
    if record.get("source") not in SOURCES:
        raise store.Stop(f"STOPPED: source must be one of {', '.join(SOURCES)}.")
    if not (record.get("text") or "").strip():
        raise store.Stop("STOPPED: a lead needs its text (what the person said or asked).")
    if not (digits(record.get("phone")) or (record.get("email") or "").strip()
            or (plain(record.get("name")) and plain(record.get("company")))):
        raise store.Stop("STOPPED: a lead needs a phone, an email, or a name and a company.")
    inbox = store.read_inbox()
    for lead in inbox["leads"]:
        if lead["source"] == record["source"] and lead["source_ref"] == record["source_ref"]:
            return "already", lead, "same source_ref"
    for lead in inbox["leads"]:
        why = same_person(lead, record)
        if why:
            lead.setdefault("duplicates", []).append({k: record.get(k) for k in ("source", "source_ref", "received_at", "text")})
            store.save_inbox(inbox)
            return "duplicate", lead, why
    lead = {"lead_id": f"LQ-{len(inbox['leads']) + 1:04d}", "source": record["source"],
            "source_ref": record["source_ref"], "received_at": record.get("received_at") or store.now(),
            **{k: record.get(k) for k in ("name", "company", "phone", "email", "state", "city", "text")},
            "given": {k: record[k] for k in GIVEN if record.get(k) not in (None, "")},
            "test_mode": bool(record.get("test_mode")), "duplicates": [], "check": None}
    inbox["leads"].append(lead)
    store.save_inbox(inbox)
    return "added", lead, ""


def manual(source: str, fields: dict) -> tuple[str, dict, str]:
    """add --source telecalling|manual: the ref is the given one, else a hash of the lead (the same add twice = no-op)."""
    if source not in ("telecalling", "manual"):
        raise store.Stop("STOPPED: add takes --source telecalling or manual (calls come from import-calls).")
    ref = fields.get("ref") or hashlib.sha256(json.dumps(
        [source, plain(fields.get("name")), digits(fields.get("phone")), (fields.get("text") or "").strip()]).encode()).hexdigest()[:16]
    return add_lead({**fields, "source": source, "source_ref": ref})


def call_text(call: dict) -> str:
    lines = [f"Call summary: {call.get('summary')}"] if call.get("summary") else []
    for line in call.get("transcript") or []:
        said = line.get("text") or line.get("message") or line.get("content") or ""
        if said:
            lines.append(f"{'agent' if line.get('speaker') in ('agent', 'assistant', 'bot') else 'lead'}: {said}")
    return "\n".join(lines)


def calls_in_ledger(key: str, month: str, task_id: str, reads=None) -> tuple[list[dict], bool]:
    """(the calls of that task's LATEST done 'poll' row, the run's test_mode). Ledger reads only."""
    if reads is None:
        from types import SimpleNamespace
        from state_store import get_runs, get_steps, get_tasks     # reads only
        reads = SimpleNamespace(get_runs=get_runs, get_tasks=get_tasks, get_steps=get_steps)
    run = next((r for r in reads.get_runs(key) if r["run_key"] == f"{key}-{month}"), None)
    if not run:
        raise store.Stop(f"STOPPED: no run {key}-{month}.")
    task = next((t for t in reads.get_tasks(run["id"]) if t["task_id"] == task_id), None)
    if not task or (task.get("agent"), task.get("output_type")) != ("Quickhowl", "calls"):
        raise store.Stop(f"STOPPED: {task_id} in {run['run_key']} is not a Quickhowl calls task.")
    polls = [s for s in reads.get_steps(task["id"]) if s["step"] == "poll" and s["status"] == "done"]
    if not polls:
        raise store.Stop(f"STOPPED: {task_id} in {run['run_key']} has no call results in the ledger yet.")
    output = polls[-1]["output"]
    output = json.loads(output) if isinstance(output, str) else output or {}
    return list((output.get("calls") or {}).values()), bool(run.get("test_mode"))


def import_calls(key: str, month: str, task_id: str, reads=None) -> dict:
    """Leads from the run's calls with outcome interested / callback_requested. Returns counts per result."""
    calls, test_mode = calls_in_ledger(key, month, task_id, reads)
    counts = {"added": 0, "already": 0, "duplicate": 0, "skipped (other outcome)": 0}
    for call in calls:
        if call.get("outcome") not in WANTED_OUTCOMES:
            counts["skipped (other outcome)"] += 1
            continue
        contact = call.get("contact") or {}
        result, _, _ = add_lead({"source": "call", "source_ref": call.get("call_id"),
                                 "received_at": call.get("ended_at") or call.get("updated_at"),
                                 "name": contact.get("name"), "company": contact.get("company"),
                                 "phone": None if test_mode else contact.get("phone"),   # test run: OUR number, not theirs
                                 "text": call_text(call) or f"Call outcome: {call.get('outcome')}",
                                 "test_mode": test_mode})
        counts[result] += 1
    return counts


def check_inbox(key: str, approved: dict, ask_batch=None, recheck: bool = False) -> list[dict]:
    """
    Check every lead with no check yet (never one that has one); save each lead's check in the inbox.
    recheck: ALSO leads checked against other criteria (criteria_sha256 differs from the approved one) that have no
    human answer; their old check is kept under 'previous_checks'.
    """
    from lead_qual.check import evaluate
    inbox = store.read_inbox()
    current = approved["approval"]["sha256"]
    todo = [lead for lead in inbox["leads"] if not lead.get("check") or (
        recheck and lead["check"].get("criteria_sha256") != current and not lead["check"].get("human_verdict"))]
    if not todo:
        return []
    leads = [{"lead_id": lead["lead_id"], "source": lead["source"], "text": lead["text"],
              **{k: lead.get(k) for k in ("company", "state", "city") if lead.get(k)}, **lead.get("given", {})}
             for lead in todo]
    rows, _ = evaluate(leads, approved, ask_batch)
    for lead, row in zip(todo, rows):
        if lead.get("check"):
            lead.setdefault("previous_checks", []).append(lead["check"])
        lead["check"] = {**{k: row[k] for k in ("verdict", "score", "score_shown", "earned", "total", "verdict_reason",
                                                 "results", "questions", "facts")},
                         "criteria_sha256": approved["approval"]["sha256"], "checked_at": store.now(),
                         "human_verdict": None}
    store.save_inbox(inbox)
    return todo
