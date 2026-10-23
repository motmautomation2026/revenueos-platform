"""
brain_memory/cli.py  -  the commands (run from the brain folder, or the customer's own folder under the worker):

    python -m brain_memory build --run <month>       facts (code, read-only) + lessons (1 LLM call) -> a DRAFT entry
    python -m brain_memory approve --run <month>     a person approves the latest draft of that month (yes + name)
    python -m brain_memory show                      the drafts, and the lessons master_agent would get now
    python -m brain_memory selftest                  fake LLM, fake reads, temp folder: no network, no LLM
"""

import os
import sys

from brain_memory import store

USAGE = __doc__.split("\n\n", 1)[1].rstrip()


def build(key: str, month: str, reads=None, ask=None) -> dict:
    from brain_memory import facts as facts_module, lessons
    run, facts = facts_module.collect(key, month, reads)
    if not facts:
        raise store.Stop(f"STOPPED: run {run['run_key']} has no facts yet (no targets, outcomes, reviews or notes).")
    test = bool(run.get("test_mode"))
    kept, dropped = lessons.write(facts, ask, test)
    entry = {"month": month, "run_key": run["run_key"], "run_id": run["id"], "built_at": store.now(), "test": test,
             "status": "draft", "facts": facts, "lessons": kept, "dropped": dropped}
    data = store.read_drafts(key)
    data["entries"].append(entry)                         # every build is kept (history)
    store.save_drafts(key, data)
    return entry


def approve(key: str, month: str, ask=input, record=None) -> dict:
    data = store.read_drafts(key)
    drafts = [e for e in data["entries"] if e["month"] == month and e["status"] == "draft"]
    if not drafts:
        raise store.Stop(f"STOPPED: no draft memory for {month}. Run: python -m brain_memory build --run {month}")
    entry = drafts[-1]
    if not entry["lessons"]:
        raise store.Stop("STOPPED: this draft has no lessons left after the checks; build it again.")
    print(f"Memory for {entry['run_key']} (built {entry['built_at']})"
          + (" - a TEST run: master_agent uses it only in test mode with --allow-test" if entry.get("test") else "") + ":")
    for fact in entry["facts"]:
        print(f"  {fact['id']}: {fact['text']}")
    for lesson in entry["lessons"]:
        print(f"  LESSON [{', '.join(lesson['fact_ids'])}] {lesson['text']}")
    for lesson in entry["dropped"]:
        print(f"  dropped: {lesson['text']} ({lesson['reason']})")
    if ask("Approve these lessons for later plans? Type yes: ").strip().lower() != "yes":
        raise store.Stop("STOPPED: not approved. Nothing changed.")
    name = (os.getenv("PLAN_APPROVER") or "").strip() or ask("Your name: ").strip()
    if not name:
        raise store.Stop("STOPPED: a name is needed for the approval. Nothing changed.")
    core = {k: entry.get(k) for k in store.CORE}
    row = store.save_approved(entry["run_id"], core, name[:80], record)
    entry.update(status="approved", approved_by=name[:80], decision_id=row.get("id"), sha256=store.fingerprint(core))
    store.save_drafts(key, data)
    return entry


def no_memory_line(key: str, reads=None) -> str:
    """show's last line when master_agent gets no lessons: say so if approved TEST-run entries exist."""
    try:
        tests = sum(1 for entry in store.approved_rows(key, reads) if entry.get("test"))
    except Exception:                                            # the read failed: lessons_text already said so
        tests = 0
    if tests:
        return f"No approved real-run memory ({tests} test {'entry' if tests == 1 else 'entries'}, used only with --allow-test)"
    return "No approved memory: master_agent's prompts are unchanged."


def main() -> None:
    args = sys.argv[1:]
    command = args[0] if args else ""
    try:
        if command == "selftest" and len(args) == 1:
            from brain_memory.selftest import selftest
            sys.exit(0 if selftest() else 1)
        elif command == "build" and len(args) == 3 and args[1] == "--run":
            entry = build(store.customer(), args[2])
            print(f"Draft memory for {entry['run_key']}{' (TEST run)' if entry['test'] else ''}: {len(entry['facts'])} facts, {len(entry['lessons'])} lesson(s), "
                  f"{len(entry['dropped'])} dropped. Saved: {store.draft_path(store.customer())}")
            for lesson in entry["lessons"]:
                print(f"  LESSON [{', '.join(lesson['fact_ids'])}] {lesson['text']}")
            for lesson in entry["dropped"]:
                print(f"  dropped: {lesson['text']} ({lesson['reason']})")
            print(f"Next: python -m brain_memory approve --run {args[2]}")
        elif command == "approve" and len(args) == 3 and args[1] == "--run":
            entry = approve(store.customer(), args[2])
            print(f"Approved by {entry['approved_by']} (sha256 {entry['sha256'][:12]}...), saved as decision "
                  f"#{entry['decision_id']}.")
        elif command == "show" and len(args) == 1:
            from brain_memory.read import lessons_text
            key = store.customer()
            for entry in store.read_drafts(key)["entries"]:
                print(f"  {entry['month']} built {entry['built_at']}: {entry['status']}{' (TEST run)' if entry.get('test') else ''}, "
                      f"{len(entry['lessons'])} lesson(s)")
            print(lessons_text(key) or no_memory_line(key))
        else:
            print("Usage:\n" + USAGE)
            sys.exit(1)
    except Exception as error:
        from quickicp import ToolError
        if not isinstance(error, (store.Stop, ToolError)):
            raise
        print(error)
        sys.exit(1)
