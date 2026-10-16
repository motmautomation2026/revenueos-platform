"""
tools/email_live_check.py
=========================

ONE real test-mode check of the email task against the LIVE email tool: launch (as a DRAFT), sample
batches, feedback rounds, approve or no. Only the email runner runs; the ledger and the approvals work
exactly as in a normal run, answered at the keyboard. No QuickICP, no Prospeo, no Quickhowl: nothing is
bought and nobody is called.

WHY
---
e2e_test.py stops at REAL_CALLS_ENABLED (the platform's setting, not ours to change), and a full run
would buy QuickICP data again. The email feedback loop only needs 3 people and the email tool.

HOW
---
    1. guards       TEST_MODE on (quickicp + email_tool), BRAIN_TEST_MODE not 'false', no STAGING_*,
                    exactly 3 TEST_RECIPIENTS, EMAIL_TOOL_* + CLIENT_REFERENCE set
    2. a separate key   motm-emailcheck (or --key motm-emailcheck-*): no platform project has it (checked,
                    read-only), so the platform's worker never touches this run
    3. 3 people     READ-ONLY from anvixa-ai-product (the latest run's done contacts task, 3 different
                    companies; or --people id,id,id). Nothing is written to the people/company tables.
                    In test mode they are only profile details: the emails go to new '+' addresses of
                    our 3 TEST_RECIPIENTS inboxes (email_tool.recipients_for).
    4. the run      <key>-2026-10 (next free revision if it exists), test mode: T1 = a done contacts task
                    holding the 3 ids, T2 = the email task with anvixa-ai-product's latest email work
                    order and understanding (read-only copies, so the content is realistic)
    5. the loop     orchestrator.cmd_run step by step, with the REAL email tools except:
                      people()  -> the 3 rows read in step 3
                      launch()  -> checked in code BEFORE the paid call: every recipient is a '+' address of
                                   a TEST_RECIPIENTS inbox, at most 3, sample_recipients among them
                    email_approve (and task_failed) are asked here; answers go through orchestrator.cmd_answer.
    Suggested: one feedback round, then 'no' (the campaign stays a DRAFT, nothing is sent). 'yes' starts
    sending to our 3 inboxes only (up to 3 emails each, over the schedule).

COST
----
    OpenAI: ~1-2 calls for the email content (~$0.01, logged in data/llm_usage.jsonl).
    Email tool: 1 draft campaign (3 recipients); 1 sample batch + 1 per feedback round (their daily sample
    limit); emails only after 'yes', to our 3 inboxes. Supabase: ~1 run, 2 tasks, ~10 ledger rows, a few
    decisions under the separate key (kept as the record; --cleanup deletes them).

RUN
---
    python tools/email_live_check.py                     start (type the key to confirm), then the loop
    python tools/email_live_check.py --resume            continue the latest run of the key (after quit / crash)
    python tools/email_live_check.py --people 11,12,13   choose the 3 anvixa-ai-product people yourself
    python tools/email_live_check.py --key motm-emailcheck-2
    python tools/email_live_check.py --cleanup           delete every run of the key (type the key to confirm);
                                                         the campaign itself stays in the email tool
    python tools/email_live_check.py selftest            fakes only: no network, no LLM, nothing written

DEBUGGING
---------
    "STOPPED: recipient ... is not a '+' address of TEST_RECIPIENTS"  -> nothing was sent; check TEST_MODE / .env
    "STOPPED: a platform project has the key"   -> use --key motm-emailcheck-<something>
    "STOPPED: ... has an active run"            -> --resume it, or --cleanup first
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))    # the repo root

from quickicp import ToolError, run_cli  # noqa: E402

KEY = "motm-emailcheck"
SOURCE = "anvixa-ai-product"           # read-only: its latest run's people, understanding and email work order
MONTH = "2026-10"
PEOPLE = 3
ACTIVE = {"planning", "running", "waiting_human"}


# ================= GUARDS (code, before anything is written or sent) =================

def setting_problems(env: dict, test_mode: bool, inboxes: list[str]) -> list[str]:
    """Why this process may NOT run the live check (empty = it may)."""
    problems = []
    if not test_mode:
        problems.append("TEST_MODE is off (quickicp / email_tool)")
    if (env.get("BRAIN_TEST_MODE") or "").strip().lower() == "false":
        problems.append("BRAIN_TEST_MODE=false is set in this shell")
    problems += [f"{name} is set in this shell" for name in ("STAGING_COMPANIES", "STAGING_PEOPLE") if env.get(name)]
    if len(inboxes) != 3:
        problems.append(f"TEST_RECIPIENTS must list exactly our 3 inboxes (it has {len(inboxes)})")
    problems += [f"{name} is missing" for name in ("EMAIL_TOOL_URL", "EMAIL_TOOL_KEY", "EMAIL_TOOL_USER_EMAIL",
                                                   "CLIENT_REFERENCE") if not env.get(name)]
    return problems


def key_problem(key: str) -> str | None:
    """Only motm-emailcheck keys: never a real customer's."""
    if key != KEY and not key.startswith(KEY + "-"):
        return f"STOPPED: the key must be '{KEY}' or start with '{KEY}-' (got '{key}')."
    return None


def recipients_problem(body: dict, inboxes: list[str]) -> str | None:
    """The launch body may only email '+' addresses of our TEST_RECIPIENTS inboxes, at most 3. None = ok."""
    bases = {inbox.strip().lower() for inbox in inboxes}
    emails = [(r.get("email") or "").strip().lower() for r in body.get("recipients") or []]
    if not emails or len(emails) > PEOPLE:
        return f"STOPPED: {len(emails)} recipients; the live check sends to 1-{PEOPLE} test inboxes only."
    for email in emails:
        local, _, domain = email.partition("@")
        if "+" not in local or f"{local.split('+')[0]}@{domain}" not in bases:
            return f"STOPPED: recipient '{email[:3]}***' is not a '+' address of TEST_RECIPIENTS. Nothing was sent."
    if "smtp" in body:
        return "STOPPED: the request holds mailbox settings; the live check uses the existing account only."
    if not set(body.get("sample_recipients") or []) <= set(emails):
        return "STOPPED: a sample recipient is not one of the recipients. Nothing was sent."
    return None


def pick_people(rows: list[dict], n: int = PEOPLE) -> list[dict]:
    """n people with an email, from different companies first (input order otherwise)."""
    with_email = [r for r in rows if r.get("email") and (r.get("company") or {}).get("company_name")]
    picked, seen = [], set()
    for row in with_email:
        company = row["company"]["company_name"].strip().lower()
        if company not in seen:
            picked.append(row)
            seen.add(company)
    picked += [r for r in with_email if r not in picked]
    return picked[:n]


def mask(email: str | None) -> str:
    return (email or "")[:3] + "***"


# ================= THE TOOLS: real email tool, 3 read-only people, guarded launch =================

def live_tools(base, rows: list[dict], inboxes: list[str]):
    """A toolset class: the real email tools (base), with people() = the 3 rows and a guarded launch()."""
    class LiveCheckTools(base):
        def people(self, customer, ids):
            return [dict(r) for r in rows if r["id"] in ids]

        def launch(self, body):
            problem = recipients_problem(body, inboxes)
            if problem:
                raise ToolError(problem)
            return super().launch(body)
    return LiveCheckTools


# ================= READ (select only) =================

def source_run():
    """The latest anvixa-ai-product run that has a done contacts task and an email task."""
    from state_store import get_runs, get_tasks
    for run in reversed(get_runs(SOURCE)):
        tasks = get_tasks(run["id"])
        contacts = [t for t in tasks if (t["agent"], t["output_type"]) == ("QuickICP", "contacts")
                    and t["status"] == "done" and (t["result"] or {}).get("people_ids")]
        email = [t for t in tasks if (t["agent"], t["output_type"]) == ("Email Agent", "emails")]
        if contacts and email and run.get("understanding"):
            return run, contacts[-1]["result"]["people_ids"], email[-1]["work_order"]
    raise ToolError(f"STOPPED: no {SOURCE} run with a done contacts task and an email task.")


def platform_has_key(key: str) -> bool:
    """True if a platform project uses this key (then its worker would pick the run up). Select only."""
    from supabase_store import db
    try:
        rows = db.table("revenueos_projects").select("id").eq("brain_customer_key", key).execute().data or []
    except Exception as error:
        raise ToolError(f"STOPPED: could not check the platform projects ({type(error).__name__}); "
                        "the live check only runs when it is sure the worker ignores its key.") from error
    return bool(rows)


# ================= START / LOOP / CLEANUP =================

def start(key: str, people_ids: list[int] | None, ask=input) -> str:
    """Guards, the 3 people, confirmation, then the test run. Returns its month."""
    import email_tool
    import quickicp
    from orchestrator import load_run, next_revision, run_key_for
    from runner_email import RealEmailTools
    from state_store import get_run, get_runs, get_tasks, update_task

    problem = key_problem(key)
    if problem:
        raise ToolError(problem)
    inboxes = email_tool.test_inboxes()
    problems = setting_problems(dict(os.environ), quickicp.TEST_MODE and email_tool.TEST_MODE, inboxes)
    if problems:
        raise ToolError("STOPPED: " + "; ".join(problems) + ".")
    if platform_has_key(key):
        raise ToolError(f"STOPPED: a platform project has the key '{key}'; use --key {KEY}-<something>.")
    active = [r["run_key"] for r in get_runs(key) if r["status"] in ACTIVE]
    if active:
        raise ToolError(f"STOPPED: {key} has an active run ({active[-1]}): --resume it, or --cleanup first.")

    run, source_ids, work_order = source_run()
    rows = RealEmailTools({}, {}).people(SOURCE, people_ids or source_ids)
    chosen = pick_people(rows)
    if len(chosen) < PEOPLE:
        raise ToolError(f"STOPPED: only {len(chosen)} {SOURCE} people with an email and a company were found.")
    month = MONTH if not get_run(run_key_for(key, MONTH)) else next_revision(key, MONTH)

    print(f"\nEMAIL LIVE CHECK (test mode) -> run {key}-{month}")
    print(f"  content from:  {run['run_key']} (understanding + email work order, read-only)")
    for row in chosen:
        print(f"  person {row['id']}: {row.get('full_name', '')[:1]}*** at {row['company']['company_name']} "
              f"(profile only; their own email {mask(row.get('email'))} is NOT used)")
    print(f"  emails go to:  '+' addresses of {', '.join(mask(i) for i in inboxes)} only")
    print("  cost: ~1-2 LLM calls (~$0.01); 1 draft campaign + 1 sample batch per round; nothing bought, nobody called")
    if ask(f"Type the key ({key}) to start: ").strip() != key:
        raise ToolError("STOPPED: not confirmed. Nothing was written.")

    os.environ["CUSTOMER_KEY"] = key           # anything that reads the current customer sees this key, never anvixa
    orders = [{"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "contacts",
               "task_type": "outreach", "week": 1, "depends_on": [], "target": f"{PEOPLE} contacts",
               "instructions": [f"live check: {PEOPLE} saved {SOURCE} people (read-only)"]},
              {**work_order, "work_order_id": "WO-T2", "task_id": "T2", "depends_on": ["T1"],
               "target": f"{PEOPLE} emails"}]
    proof = {"approved_at": utc_now(), "approved_by": "email live check", "answer": "yes", "overridden": False,
             "override_reason": None, "validation_errors_at_approval": [], "work_orders_count": 2,
             "work_orders_sha256": "live-check", "month_plan_sha256": "live-check", "revenue_analysis_sha256": "live-check"}
    new_run, _ = load_run(key, month, orders, {"month": "Email live check", "revenue_analysis": {}},
                          run["understanding"], True, proof)
    t1 = [t for t in get_tasks(new_run["id"]) if t["task_id"] == "T1"][0]
    update_task(t1["id"], status="done", achieved_number=len(chosen),
                result={"people_ids": [r["id"] for r in chosen], "customer": key, "run_key": new_run["run_key"],
                        "source": f"{SOURCE} (read-only, live check)"})
    return month


def wire(key: str, month: str, people_ids: list[int]) -> None:
    """The email runner with the live-check tools (in THIS process only)."""
    import email_tool
    import orchestrator
    from runner_email import RealEmailTools
    from runner_kit import run_understanding
    rows = RealEmailTools({}, {}).people(SOURCE, people_ids)
    tools = live_tools(RealEmailTools, rows, email_tool.test_inboxes())
    runner = orchestrator.WIRED_RUNNERS["email"][0]
    orchestrator.WIRED_RUNNERS["email"] = (runner, lambda ctx: tools(run_understanding(ctx.run), ctx.task["work_order"]))
    os.environ["CUSTOMER_KEY"] = key


def loop(key: str, month: str, ask=input) -> str:
    """cmd_run step by step; open decisions answered here. Returns how it ended."""
    from orchestrator import cmd_answer, cmd_run, find_run, print_question
    from state_store import get_tasks, list_open_decisions
    while True:
        run = find_run(key, month)
        task = [t for t in get_tasks(run["id"]) if t["task_id"] == "T2"][0]
        if task["status"] in ("done", "stopped") or run["status"] not in ACTIVE:
            print(f"\nT2 is '{task['status']}' (run '{run['status']}'). {task.get('last_error') or ''}")
            return task["status"]
        open_ones = list_open_decisions(run["id"])
        if not open_ones:
            if task["status"] == "running" and ask("\nSamples are being written. Enter = check again, quit = stop: ").strip() == "quit":
                return "quit"
            cmd_run(key, month)
            continue
        decision = open_ones[0]
        allowed = decision["question"].get("allowed_answers") or []
        print(f"\n#{decision['id']} {decision['kind']}")
        print_question(decision)
        answer = ask(f"Answer {' / '.join(allowed)} (or quit): ").strip().lower()
        if answer == "quit":
            return "quit"
        if answer not in allowed:
            print(f"  Not offered here. Type one of: {', '.join(allowed)}")
            continue
        note = ask("Note (required for feedback; what to change): ").strip()
        try:
            cmd_answer(key, decision["id"], answer, note or None)
        except ToolError as error:
            print(f"  {error}")


def latest_month(key: str) -> tuple[str, list[int]]:
    """The latest active run of the key: its month and the people ids of its T1."""
    from orchestrator import month_of
    from state_store import get_runs, get_tasks
    runs = [r for r in get_runs(key) if r["status"] in ACTIVE]
    if not runs:
        raise ToolError(f"STOPPED: {key} has no active run to resume. Start one: python tools/email_live_check.py")
    t1 = [t for t in get_tasks(runs[-1]["id"]) if t["task_id"] == "T1"][0]
    return month_of(runs[-1]), t1["result"]["people_ids"]


def cleanup(key: str, ask=input) -> int:
    """Delete every run of the key and every row under it (children first). Only motm-emailcheck keys."""
    from state_store import DECISIONS, RUNS, STEPS, TASKS, backend, get_runs, get_tasks
    problem = key_problem(key)
    if problem:
        raise ToolError(problem)
    runs = get_runs(key)
    if not runs:
        print(f"Nothing to delete: {key} has no runs.")
        return 0
    print(f"Delete {len(runs)} run(s) of {key}: {', '.join(r['run_key'] for r in runs)}")
    print("(The campaign itself stays in the email tool.)")
    if ask(f"Type the key ({key}) to delete: ").strip() != key:
        raise ToolError("STOPPED: not confirmed. Nothing was deleted.")
    deleted = 0
    for run in runs:
        assert run["customer"] == key and key_problem(run["customer"]) is None
        deleted += backend().delete(DECISIONS, {"run_id": run["id"]})
        for task in get_tasks(run["id"]):
            deleted += backend().delete(STEPS, {"task_row_id": task["id"]})
        deleted += backend().delete(TASKS, {"run_id": run["id"]})
        deleted += backend().delete(RUNS, {"id": run["id"]})
    print(f"Deleted {deleted} row(s). {key} has {len(get_runs(key))} run(s) left.")
    return deleted


def utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ================= SELFTEST (fakes only: no network, no LLM, nothing written) =================

def selftest() -> bool:
    checks = []

    def expect(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    inboxes = ["team@motm.test", "ops@motm.test", "qa@motm.test"]
    env = {"EMAIL_TOOL_URL": "u", "EMAIL_TOOL_KEY": "k", "EMAIL_TOOL_USER_EMAIL": "e", "CLIENT_REFERENCE": "c"}
    expect("guards: test mode + 3 inboxes + settings -> allowed", setting_problems(env, True, inboxes) == [])
    expect("guards: test mode off, BRAIN_TEST_MODE=false, a staging limit, 2 inboxes, no CLIENT_REFERENCE -> each refused",
           len(setting_problems({**env, "BRAIN_TEST_MODE": "false", "STAGING_PEOPLE": "5", "CLIENT_REFERENCE": ""},
                                False, inboxes[:2])) == 5)
    expect("key: motm-emailcheck and motm-emailcheck-2 allowed; anvixa-ai-product and motm-emailcheckx refused",
           key_problem(KEY) is None and key_problem(KEY + "-2") is None
           and key_problem(SOURCE) and key_problem(KEY + "x"))

    good = {"recipients": [{"email": "team+hp1@motm.test"}, {"email": "ops+hp2@motm.test"}, {"email": "QA+hp3@motm.test"}],
            "sample_recipients": ["team+hp1@motm.test"]}
    expect("launch guard: 3 '+' addresses of our inboxes -> allowed", recipients_problem(good, inboxes) is None)
    bad = {
        "a real prospect address": {**good, "recipients": good["recipients"][:2] + [{"email": "rahul@acme.com"}]},
        "our inbox without '+'": {**good, "recipients": [{"email": "team@motm.test"}]},
        "a '+' address of another inbox": {**good, "recipients": [{"email": "boss+hp1@motm.test"}]},
        "4 recipients": {**good, "recipients": good["recipients"] + [{"email": "team+hp4@motm.test"}]},
        "mailbox settings": {**good, "smtp": {"host": "x"}},
        "a sample recipient outside the list": {**good, "sample_recipients": ["rahul@acme.com"]},
    }
    expect("launch guard: refused for " + ", ".join(bad),
           all(recipients_problem(body, inboxes) for body in bad.values())
           and "rah***" in recipients_problem(bad["a real prospect address"], inboxes))

    class FakeReal:
        launched = []

        def __init__(self, understanding, work_order):
            pass

        def people(self, customer, ids):
            raise AssertionError("the real people() must never be used")

        def launch(self, body):
            FakeReal.launched.append(body)
            return {"campaign_id": "c"}
    rows = [{"id": 1, "email": "a@x.test", "company": {"company_name": "Acme"}},
            {"id": 2, "email": "b@x.test", "company": {"company_name": "Acme"}},
            {"id": 3, "email": "", "company": {"company_name": "Bolt"}},
            {"id": 4, "email": "d@x.test", "company": {"company_name": "Core"}},
            {"id": 5, "email": "e@x.test", "company": {"company_name": "Dune"}}]
    expect("pick_people: 3 with an email, different companies first", [r["id"] for r in pick_people(rows)] == [1, 4, 5]
           and [r["id"] for r in pick_people(rows[:2] + rows[3:4])] == [1, 4, 2])
    tools = live_tools(FakeReal, pick_people(rows), inboxes)({}, {})
    blocked = False
    try:
        tools.launch(bad["a real prospect address"])
    except ToolError:
        blocked = True
    tools.launch(good)
    expect("live tools: people() = the 3 read-only rows (by id, never the real lookup); a bad launch is stopped BEFORE "
           "the call; a good one goes through",
           [r["id"] for r in tools.people(KEY, [1, 4])] == [1, 4] and blocked and FakeReal.launched == [good])

    ok = all(checks)
    print("EMAIL LIVE CHECK SELFTEST " + ("OK" if ok else "FAILED") + f" ({sum(checks)} of {len(checks)})")
    return ok


def main() -> None:
    args = sys.argv[1:]
    if args == ["selftest"]:
        sys.exit(0 if selftest() else 1)
    key = args[args.index("--key") + 1] if "--key" in args else KEY
    if "--cleanup" in args:
        cleanup(key)
        return
    try:
        people = [int(x) for x in args[args.index("--people") + 1].split(",")] if "--people" in args else None
    except (IndexError, ValueError):
        raise ToolError("STOPPED: --people needs ids like 11,12,13.")
    if "--resume" in args:
        month, people_ids = latest_month(key)
    else:
        month = start(key, people)
        from orchestrator import find_run
        from state_store import get_tasks
        people_ids = [t for t in get_tasks(find_run(key, month)["id"]) if t["task_id"] == "T1"][0]["result"]["people_ids"]
    wire(key, month, people_ids)
    ended = loop(key, month)
    print(f"Live check {key}-{month}: {ended}."
          + (" Continue with: python tools/email_live_check.py --resume" if ended == "quit" else ""))


if __name__ == "__main__":
    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        try:
            main()
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli)
