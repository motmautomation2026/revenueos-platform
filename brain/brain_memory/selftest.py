"""
brain_memory/selftest.py  -  python -m brain_memory selftest. Fake reads (shaped like run anvixa-ai-product-2026-10-r7),
a fake LLM, a fake decision writer and a temp folder: no network, no LLM, nothing written outside the temp folder.
"""

import contextlib
import io
import json
import os
import tempfile
from types import SimpleNamespace

from brain_memory import facts as facts_module, lessons, read, store
from brain_memory.cli import approve, build, no_memory_line

KEY = "selftest-memory"


def r7_reads(test_mode=True, calls_task=True):
    """r7's shape: human / gate tasks with a target but no achieved number, tool tasks with test caps, 1 test call."""
    def task(row, task_id, agent, output, target, achieved, result=None):
        return {"id": row, "task_id": task_id, "agent": agent, "output_type": output, "target_number": target,
                "achieved_number": achieved, "status": "done", "result": result or {}}
    tasks = [task(2849, "T2", "Content & Training", "client_training", 5, None),
             task(2851, "T4", "QuickICP", "accounts", 200, 181),
             task(2852, "T5", "QuickICP", "contacts", 200, 15),
             task(2855, "T8", "Quickhowl", "calls", 12, 1, {"outcome_counts": {"not_interested": 1}}),
             task(2857, "T10", "Human Tele Calling", "qualified_leads", 12, None),
             task(2860, "T13", "CAM", "review", 4, None),
             task(2861, "T1", "CAM", "coordination", None, None)]
    if not calls_task:
        tasks = [t for t in tasks if t["task_id"] != "T8"]
    decisions = [{"id": 3546, "kind": "human_gate_task", "status": "answered", "task_row_id": 2849, "answer": "done",
                  "reason": "E2E TEST", "question": {}},
                 {"id": 3557, "kind": "human_task", "status": "answered", "task_row_id": 2857, "answer": "done",
                  "reason": None, "question": {}},
                 {"id": 3564, "kind": "run_review", "status": "answered", "question": {"numbers": {
                     "objections": {"counts": {"not_interested": 1}},
                     "data_quality": {"landline_pct": 20.0, "people_failed_judge_pct": 32.0,
                                      "flagged": [{"signal": "landline_pct"}, {"signal": "people_failed_judge_pct"}]}}}},
                 {"id": 3562, "kind": "review_action_2", "status": "answered", "answer": "reject",
                  "reason": "Only 1 test call", "question": {"action": "adjust the call script"}},
                 {"id": 3570, "kind": "human_task", "status": "open", "task_row_id": 2860, "answer": None, "question": {}}]
    poll = {"step": "poll", "status": "done", "output": json.dumps({"calls": {"a": {"call_id": "call-r7"}}})}
    return SimpleNamespace(get_runs=lambda key: [{"id": 1, "run_key": f"{key}-2026-10", "test_mode": test_mode}],
                           get_tasks=lambda run_id: tasks, get_decisions=lambda run_id: decisions,
                           get_steps=lambda row: [{"step": "launch", "status": "done", "output": {}}, poll] if row == 2855 else [])


def fake_lessons(*items, sent=None):
    def ask(schema, prompt, name):
        if sent is not None:
            sent.append(prompt)
        return lessons.Lessons(lessons=[lessons.Lesson(text=t, fact_ids=ids) for t, ids in items])
    return ask


def selftest() -> bool:
    checks = []

    def expect(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    def stops(function) -> str:
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                function()
        except store.Stop as error:
            return str(error)
        return ""

    saved_cwd, saved_approver = os.getcwd(), os.environ.get("PLAN_APPROVER")
    with tempfile.TemporaryDirectory() as folder:
        try:
            os.chdir(folder)
            os.environ.pop("PLAN_APPROVER", None)
            os.makedirs("data")
            with open(os.path.join("data", "leads_inbox.json"), "w", encoding="utf-8") as file:
                json.dump({"leads": [{"source": "call", "source_ref": "call-r7", "check": {"verdict": "needs_human"}},
                                     {"source": "call", "source_ref": "call-other-run", "check": {"verdict": "verified"}},
                                     {"source": "telecalling", "source_ref": "de6b", "check": {"verdict": "verified"}},
                                     {"source": "call", "source_ref": "call-r7-2", "check": None}]}, file)

            run, facts = facts_module.collect(KEY, "2026-10", r7_reads())
            text = {f["source"]: f["text"] for f in facts if f["kind"] in ("target", "task")}
            expect("TEST run (r7): volume facts carry NO numbers and say 'test cap, not a result'",
                   text["brain_plan_tasks 2851"] == f"T4 QuickICP accounts: status done ({facts_module.TEST_CAP})"
                   and text["brain_plan_tasks 2855"] == f"T8 Quickhowl calls: status done ({facts_module.TEST_CAP})"
                   and all(f["numbers"] == [] for f in facts if f["kind"] == "target"))
            expect("tasks with no achieved number (human / gate) -> status + the person's note only; never 'achieved "
                   "none' or a target; an open decision adds nothing; a task with no target and no note is left out",
                   text["brain_plan_tasks 2849"] == "T2 Content & Training client_training: status done; a person answered 'done': E2E TEST"
                   and text["brain_plan_tasks 2857"] == "T10 Human Tele Calling qualified_leads: status done; a person answered 'done'"
                   and text["brain_plan_tasks 2860"] == "T13 CAM review: status done"
                   and "brain_plan_tasks 2861" not in text
                   and not any("none" in f["text"] or "target" in f["text"] for f in facts if f["kind"] == "task"))
            leads = [f["text"] for f in facts if f["kind"] == "leads"]
            expect("lead_qual facts: only leads from THIS run's calls (call ids in its calls task's ledger); another "
                   "run's call and a tele-calling lead are left out",
                   leads == ["lead checks: needs_human 1"])
            _, no_calls = facts_module.collect(KEY, "2026-10", r7_reads(calls_task=False))
            expect("a run with no calls task -> no lead facts at all (the inbox is not this run's)",
                   not any(f["kind"] == "leads" for f in no_calls))
            _, real = facts_module.collect(KEY, "2026-10", r7_reads(test_mode=False))
            real_text = {f["source"]: f["text"] for f in real}
            expect("REAL run: volume facts keep target vs achieved; human tasks still status + note only",
                   real_text["brain_plan_tasks 2851"] == "T4 QuickICP accounts: target 200, achieved 181 (90%), status done"
                   and real_text["brain_plan_tasks 2849"] == text["brain_plan_tasks 2849"])
            expect("numbers in a fact: '1,200' -> 1200, '80%' -> 80; the 3 of 'T3' is not a number",
                   facts_module.numbers_in("T3 target 1,200, achieved 80%") == [1200.0, 80.0])

            ids = {f["source"]: f["id"] for f in facts if f["kind"] in ("target", "task")}
            calls_id = next(f["id"] for f in facts if f["kind"] == "calls")
            sent = []
            ask = fake_lessons(("The one test call was not interested (1); the script waits for the developer.", [calls_id]),
                               ("Contacts reached only 15 of 200.", [ids["brain_plan_tasks 2852"]]),
                               ("No source for this lesson.", []),
                               ("Unknown fact.", ["F99"]),
                               *[(f"Lesson {chr(65 + i)} from the review.", [calls_id]) for i in range(8)], sent=sent)
            entry = build(KEY, "2026-10", r7_reads(), ask)
            reasons = [d["reason"] for d in entry["dropped"]]
            expect("build of a TEST run: entry test: true; the LLM is told the volumes are test caps; a lesson using a "
                   "test-cap volume (15 of 200) is dropped (no numbers in that fact); no id / unknown id / a 9th dropped",
                   entry["test"] is True and sent and sent[0].startswith(lessons.TEST_NOTE)
                   and len(entry["lessons"]) == 8
                   and reasons[:3] == ["number(s) not in the cited facts: ['15', '200']", "no fact id",
                                       "unknown fact id(s) ['F99']"]
                   and reasons[3] == "more than 8 lessons" and entry["status"] == "draft"
                   and os.path.exists(store.draft_path(KEY)))
            real_sent = []
            real_entry = build(KEY, "2026-10", r7_reads(test_mode=False), fake_lessons(("x", [calls_id]), sent=real_sent))
            expect("build of a REAL run: test: false and no test note; a second build keeps the first (history)",
                   real_entry["test"] is False and not real_sent[0].startswith(lessons.TEST_NOTE)
                   and len(store.read_drafts(KEY)["entries"]) == 2)

            rows = []
            record = lambda run_id, kind, answer, by, reason, question: rows.append(
                {"id": 99, "run_id": run_id, "kind": kind, "answer": answer, "decided_by": by, "reason": reason,
                 "question": question}) or rows[-1]
            refused = stops(lambda: approve(KEY, "2026-10", ask=lambda _: "no", record=record))
            answers = iter(["yes", "Asha Rao"])
            with contextlib.redirect_stdout(io.StringIO()):
                approved = approve(KEY, "2026-10", ask=lambda _: next(answers), record=record)
            row = rows[0] if rows else {}
            flipped = {**row.get("question", {}), "test": not row.get("question", {}).get("test")}
            expect("approve: anything but 'yes' -> nothing written; 'yes' + name -> ONE answered brain_decisions row "
                   "(kind memory, answer approved, decided_by user, reason 'approved by <name>'); the entry keeps "
                   "'test' and the sha256 covers it",
                   "not approved" in refused and len(rows) == 1 and row["run_id"] == 1
                   and (row["kind"], row["answer"], row["decided_by"], row["reason"]) == ("memory", "approved", "user",
                                                                                      "approved by Asha Rao")
                   and row["question"]["test"] is False
                   and row["question"]["sha256"] == store.fingerprint(row["question"]) != store.fingerprint(flipped)
                   and row["question"]["approved_by"] == "Asha Rao" and approved["status"] == "approved"
                   and store.read_drafts(KEY)["entries"][-1]["decision_id"] == 99)

            def memory_row(month, lesson, tamper=False, kind="memory", status="answered", test=False):
                entry = {"month": month, "run_key": f"{KEY}-{month}", "test": test, "facts": [],
                         "lessons": [{"text": lesson, "fact_ids": []}]}
                entry["sha256"] = store.fingerprint(entry)
                if tamper:
                    entry["lessons"][0]["text"] = lesson + " (edited later)"
                return {"kind": kind, "status": status, "answer": "approved", "question": entry}
            rows_by_run = [memory_row("2026-08", "August lesson"), memory_row("2026-09", "September lesson"),
                           memory_row("2026-10", "October lesson, first"), memory_row("2026-10", "October lesson, approved again"),
                           memory_row("2026-11", "November lesson"), memory_row("2026-12", "December lesson", tamper=True),
                           memory_row("2027-01", "an open row", status="open"), memory_row("2027-02", "other kind", kind="run_review")]
            reads = SimpleNamespace(get_runs=lambda key: [{"id": 1}], get_decisions=lambda run_id: rows_by_run)
            given = read.lessons_text(KEY, reads)
            expect("lessons_text: approved rows only, sha256 checked (a changed one is ignored), the latest approval of a "
                   "month wins, only the 3 most recent months, newest first, with the 'document wins' rule",
                   given.splitlines()[1:] == ["- [2026-11] November lesson", "- [2026-10] October lesson, approved again",
                                              "- [2026-09] September lesson", read.RULE]
                   and given.startswith("Lessons from earlier months"))
            mixed = [memory_row("2026-09", "real September"), memory_row("2026-10", "r7 test lesson", test=True)]
            mixed_reads = SimpleNamespace(get_runs=lambda key: [{"id": 1}], get_decisions=lambda run_id: mixed)
            only_test = SimpleNamespace(get_runs=lambda key: [{"id": 1}], get_decisions=lambda run_id: mixed[1:])
            expect("TEST-run memory is left out by default (a real plan never gets it); with allow_test it is included",
                   "r7 test lesson" not in read.lessons_text(KEY, mixed_reads) and "real September" in read.lessons_text(KEY, mixed_reads)
                   and read.lessons_text(KEY, only_test) == ""
                   and "- [2026-10] r7 test lesson" in read.lessons_text(KEY, mixed_reads, allow_test=True))
            two_tests = [mixed[1], memory_row("2026-11", "another test lesson", test=True)]
            expect("show, no lessons for a real plan: only test entries approved -> 'No approved real-run memory (N test "
                   "entry/entries, used only with --allow-test)'; nothing approved at all -> the old line",
                   no_memory_line(KEY, only_test) == "No approved real-run memory (1 test entry, used only with --allow-test)"
                   and no_memory_line(KEY, SimpleNamespace(get_runs=lambda key: [{"id": 1}], get_decisions=lambda r: two_tests))
                   == "No approved real-run memory (2 test entries, used only with --allow-test)"
                   and no_memory_line(KEY, SimpleNamespace(get_runs=lambda key: [], get_decisions=lambda r: []))
                   == "No approved memory: master_agent's prompts are unchanged.")
            long = [memory_row("2026-10", "x" * 1500), memory_row("2026-11", "y" * 1500), memory_row("2026-12", "z" * 1500)]
            capped = read.lessons_text(KEY, SimpleNamespace(get_runs=lambda key: [{"id": 1}], get_decisions=lambda r: long))
            broken = SimpleNamespace(get_runs=lambda key: (_ for _ in ()).throw(ConnectionError("down")))
            with contextlib.redirect_stdout(io.StringIO()) as said:
                none_read = read.lessons_text(KEY, broken)
            expect("about 800 tokens at most (the oldest lessons go first); no approved memory -> ''; a read that fails "
                   "-> '' with a note (planning never stops for it)",
                   "z" * 1500 in capped and "y" * 1500 in capped and "x" * 1500 not in capped
                   and read.lessons_text(KEY, SimpleNamespace(get_runs=lambda key: [], get_decisions=lambda r: [])) == ""
                   and none_read == "" and "lessons not loaded (ConnectionError)" in said.getvalue())
            from state_store import ALLOWED_ANSWERS, open_decision
            from quickicp import ToolError
            try:
                open_decision(1, "memory", {"text": "x"})
                never_open = False
            except ToolError:
                never_open = True
            expect("state_store: kind 'memory' answers only 'approved' and can never be an OPEN decision",
                   ALLOWED_ANSWERS["memory"] == {"approved"} and never_open)
        finally:
            os.chdir(saved_cwd)
            if saved_approver is not None:
                os.environ["PLAN_APPROVER"] = saved_approver
    ok = all(checks)
    print(f"BRAIN MEMORY SELFTEST {'OK' if ok else 'FAILED'} ({sum(checks)} of {len(checks)})")
    return ok
