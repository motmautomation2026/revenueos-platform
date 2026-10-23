"""
brain_memory/facts.py  -  CODE collects the facts of one run (Supabase, read-only). No LLM here.

Each fact: {id 'F1'.., kind, text, numbers (every number in the text), source}. Kinds:
    target        a task's target vs achieved          brain_plan_tasks (TEST run: no numbers, labelled a test cap)
    task          a task with no achieved number (human / gate task): its status + the person's note, never "unmet"
    calls         call outcome counts                   the calls task's result (outcome_counts)
    objections    objection label counts                the run's latest run_review decision (numbers.objections)
    data_quality  data-quality signals + flags          the same run_review (numbers.data_quality)
    review        a suggested action, accepted / rejected + the note    review_action_1..3 decisions
    leads         lead_qual verdict counts of the leads from THIS run's calls (call ids in its calls tasks' ledger)
                                                         data/leads_inbox.json, if it exists (read as JSON only)
"""

import json
import os
import re

NUMBER = re.compile(r"(?<![A-Za-z0-9_\-])\d[\d,]*(?:\.\d+)?")
INBOX = os.path.join("data", "leads_inbox.json")
TEST_CAP = "test run: the volume is a test cap, not a result"
HUMAN_KINDS = ("human_task", "human_gate_task")


def numbers_in(text: str) -> list[float]:
    """Every number in a text ('1,200' -> 1200; '80%' -> 80); digits inside ids like 'T3' are not numbers."""
    return [float(n.replace(",", "")) for n in NUMBER.findall(text or "")]


def collect(key: str, month: str, reads=None, inbox_path: str = INBOX) -> tuple[dict, list[dict]]:
    """(the run, its facts)."""
    if reads is None:
        from types import SimpleNamespace
        from state_store import get_decisions, get_runs, get_steps, get_tasks          # reads only
        reads = SimpleNamespace(get_runs=get_runs, get_tasks=get_tasks, get_decisions=get_decisions, get_steps=get_steps)
    run = next((r for r in reads.get_runs(key) if r["run_key"] == f"{key}-{month}"), None)
    if not run:
        from brain_memory.store import Stop
        raise Stop(f"STOPPED: no run {key}-{month}.")
    facts = []

    def add(kind, text, source):
        facts.append({"id": f"F{len(facts) + 1}", "kind": kind, "text": text, "numbers": numbers_in(text),
                      "source": source})

    test = bool(run.get("test_mode"))
    decisions = reads.get_decisions(run["id"])
    notes = {d.get("task_row_id"): d for d in decisions                    # the latest answer per task wins
             if d.get("kind") in HUMAN_KINDS and d.get("status") == "answered"}
    call_ids = set()
    for task in reads.get_tasks(run["id"]):
        name = f"{task['task_id']} {task['agent']} {task['output_type']}"
        achieved, note = task.get("achieved_number"), notes.get(task["id"])
        if achieved is None and (task.get("target_number") is not None or note):
            add("task", f"{name}: status {task['status']}" + (f"; a person answered '{note.get('answer')}'"
                + (f": {note['reason']}" if note.get("reason") else "") if note else ""), f"brain_plan_tasks {task['id']}")
        elif task.get("target_number") is not None and test:
            add("target", f"{name}: status {task['status']} ({TEST_CAP})", f"brain_plan_tasks {task['id']}")
        elif task.get("target_number") is not None:
            share = f" ({round(achieved / task['target_number'] * 100)}%)" if task["target_number"] else ""
            add("target", f"{name}: target {task['target_number']:g}, achieved {achieved:g}{share}, status {task['status']}",
                f"brain_plan_tasks {task['id']}")
        counts = (task.get("result") or {}).get("outcome_counts")
        if counts:
            add("calls", f"{task['task_id']} call outcomes: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=str)),
                f"brain_plan_tasks {task['id']}")
        if (task.get("agent"), task.get("output_type")) == ("Quickhowl", "calls"):
            polls = [s for s in reads.get_steps(task["id"]) if s["step"] == "poll" and s["status"] == "done"]
            output = polls[-1]["output"] if polls else {}
            output = json.loads(output) if isinstance(output, str) else output or {}
            call_ids |= {c.get("call_id") for c in (output.get("calls") or {}).values()} - {None}

    reviews = [d for d in decisions if d.get("kind") == "run_review"]
    if reviews:
        numbers = (reviews[-1].get("question") or {}).get("numbers") or {}
        objections = (numbers.get("objections") or {}).get("counts") or {}
        if objections:
            add("objections", "call objections: " + ", ".join(f"{k} {v}" for k, v in sorted(objections.items())),
                f"brain_decisions {reviews[-1]['id']}")
        quality = numbers.get("data_quality") or {}
        signals = {k: v for k, v in quality.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        if signals:
            flagged = [f["signal"] for f in quality.get("flagged") or []]
            add("data_quality", "data quality: " + ", ".join(f"{k} {v:g}" for k, v in sorted(signals.items()))
                + (f"; flagged: {', '.join(flagged)}" if flagged else "; nothing flagged"),
                f"brain_decisions {reviews[-1]['id']}")
    for d in decisions:
        q = d.get("question") or {}
        if d.get("kind", "").startswith("review_action") and d.get("status") == "answered":
            add("review", f"suggested action '{q.get('action')}' was {d.get('answer')}ed by a person"
                          + (f": {d.get('reason')}" if d.get("reason") else ""), f"brain_decisions {d['id']}")

    if call_ids and os.path.exists(inbox_path):
        with open(inbox_path, encoding="utf-8") as file:
            leads = json.load(file).get("leads") or []
        checked = [lead["check"] for lead in leads if lead.get("check")             # only leads from THIS run's calls
                   and lead.get("source") == "call" and lead.get("source_ref") in call_ids]
        if checked:
            verdicts, humans = {}, {}
            for c in checked:
                verdicts[c["verdict"]] = verdicts.get(c["verdict"], 0) + 1
                if c.get("human_verdict"):
                    humans[c["human_verdict"]] = humans.get(c["human_verdict"], 0) + 1
            add("leads", "lead checks: " + ", ".join(f"{k} {v}" for k, v in sorted(verdicts.items()))
                + (("; a person decided: " + ", ".join(f"{k} {v}" for k, v in sorted(humans.items()))) if humans else ""),
                inbox_path)
    return run, facts
