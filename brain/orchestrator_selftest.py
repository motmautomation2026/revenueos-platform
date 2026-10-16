"""
orchestrator_selftest.py
========================

Every selftest of orchestrator.py (moved here unchanged on 2026-10-13, so orchestrator.py holds only
the run controller). Same command as before:

    python orchestrator.py selftest      (or: python orchestrator_selftest.py)

Throwaway selftest-* runs only, fake toolsets, a network guard (only Supabase allowed), no LLM and no
paid call; every test row is deleted at the end. On STATE_BACKEND=sqlite it uses a throwaway temp file.
The parts: load, run, tools, contacts, email, calls, 5a, 5b, close, run data, manual tools, imports.
e2e_test.py reuses expect / raises / network_guard / sweep_selftest_runs from here.

The parts live in orch_tests/ (moved unchanged on 2026-10-16); this file keeps selftest() (the order of the
parts), the network and imports parts (they find the repo folder from this file's place) and re-exports
every name, so 'from orchestrator_selftest import expect, ...' keeps working.
"""

from contextlib import contextmanager
from datetime import datetime, timezone

from orchestrator import *          # the run controller under test (same objects, e.g. WIRED_RUNNERS)  # noqa: F401,F403
from state_store import delete_test_run
from orch_tests.common import *  # noqa: F401,F403
from orch_tests.part_load_run import *  # noqa: F401,F403
from orch_tests.part_tools import *  # noqa: F401,F403
from orch_tests.part_email import *  # noqa: F401,F403
from orch_tests.part_calls import *  # noqa: F401,F403
from orch_tests.part_flow import *  # noqa: F401,F403
from orch_tests.part_manual import *  # noqa: F401,F403


def selftest() -> bool:
    """All parts: load/status/next/answer, run (human tasks), tools (fake QuickICP). Each deletes its own rows."""
    import tempfile
    import state_store
    real_entries = dict(WIRED_RUNNERS)
    for name, (runner, _) in real_entries.items():           # real toolsets are never used in a selftest
        WIRED_RUNNERS[name] = (runner, no_real_toolset)
    temp_dir = None
    if state_store.STATE_BACKEND == "sqlite":                # never the real data/brain_state.db
        temp_dir = tempfile.mkdtemp(prefix="brain_selftest_")
        state_store._backend = state_store.SqliteBackend(os.path.join(temp_dir, "selftest.db"))
        print(f"Selftest on a throwaway SQLite file in {temp_dir}")
    try:
        with network_guard() as blocked:
            results = [selftest_load(), selftest_run(), selftest_tools(), selftest_contacts(),
                       selftest_email(), selftest_calls(), selftest_5a(), selftest_5b(), selftest_close(),
                       selftest_run_data(), selftest_percent(), selftest_network(), selftest_manual_tools(), selftest_imports()]
        print(f"\nNetwork guard: {len(blocked)} call(s) outside Supabase attempted (blocked): {blocked}")
        return all(results) and not blocked
    finally:
        WIRED_RUNNERS.update(real_entries)
        if temp_dir:
            state_store._backend.close()
            state_store._backend = None
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)


# ================= NETWORK DROP: one STOPPED line at every entry point =================

def selftest_network() -> bool:
    """
    run_cli with fake network errors; a network error inside a paid search still -> 'unknown' + paid_call_unknown;
    every entry point in a child process whose every service URL is https://selftest.invalid behind a closed local
    proxy port (nothing real can be reached). No LLM, no paid call.
    """
    import contextlib
    import io
    import subprocess
    import httpx
    import requests
    import quickicp
    import runner_kit
    import state_store
    from runner_quickicp_accounts import FakeAccountsTools
    from state_store import get_steps
    print("\nOrchestrator NETWORK selftest (fake drops; child processes with no reachable service)")
    root = os.path.dirname(os.path.abspath(__file__))
    customer = "selftest-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    saved = (dict(state_store.STATE_WRITES), set(state_store.HELD_LOCKS), list(runner_kit.PAID_IN_FLIGHT), list(sys.argv))
    real_entry = WIRED_RUNNERS["quickicp_accounts"]

    def cli(raise_error, argv=("x",), writes=None, locks=(), paid=()):
        """Run run_cli(fn) like an entry point: (exit code or the exception it raised, printed text)."""
        state_store.STATE_WRITES.update(writes or {"done": 0, "last": None, "pending": None})
        state_store.HELD_LOCKS.clear()
        state_store.HELD_LOCKS.update(locks)
        runner_kit.PAID_IN_FLIGHT[:] = list(paid)
        sys.argv[:] = list(argv)
        out = io.StringIO()

        def fn():
            raise raise_error
        try:
            with contextlib.redirect_stdout(out):
                quickicp.run_cli(fn)
            return None, out.getvalue()
        except SystemExit as stop:
            return stop.code, out.getvalue()
        except BaseException as error:
            return error, out.getvalue()

    supabase_url = (os.getenv("SUPABASE_URL") or "https://x.supabase.co").rstrip("/")
    dns = httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=httpx.Request("GET", f"{supabase_url}/rest/v1/runs"))
    ok = False
    try:
        code, text = cli(dns)
        expect("N1 DNS failure (getaddrinfo) -> ONE line 'STOPPED: no network (Supabase, ...)', exit code 3, "
               "'Nothing was saved by this command.', no traceback",
               code == 3 and text.startswith("STOPPED: no network (Supabase, [Errno 11001] getaddrinfo failed). "
                                             "Nothing lost: check the internet, then rerun the same command.")
               and "Nothing was saved by this command." in text and "Traceback" not in text)
        refused = requests.exceptions.ConnectionError(
            "Connection refused", request=requests.Request("GET", f"{os.getenv('QUICKHOWL_URL') or 'https://q.example'}/x").prepare())
        timeout = httpx.ReadTimeout("timed out", request=httpx.Request("GET", f"{os.getenv('EMAIL_TOOL_URL') or 'https://e.example'}/x"))
        try:
            raise RuntimeError("postgrest wrapper") from ConnectionResetError(10054, "forcibly closed by the remote host")
        except RuntimeError as wrapped:
            reset = wrapped
        results = [cli(refused), cli(timeout), cli(reset)]
        expect("N2 refused (requests, Quickhowl), read timeout (httpx, email tool), a reset wrapped in another error "
               "-> each one STOPPED line naming the service, exit code 3",
               [r[0] for r in results] == [3, 3, 3]
               and "(Quickhowl, " in results[0][1] and "(email tool, " in results[1][1]
               and results[2][1].startswith("STOPPED: no network (a service, ") and "forcibly closed" in results[2][1])
        tool, value = cli(ToolError("STOPPED: a known problem")), cli(ValueError("a bug"))
        expect("N3 a ToolError and a non-network error are NOT swallowed (the entry point handles them as before)",
               isinstance(tool[0], ToolError) and isinstance(value[0], ValueError) and tool[1] == value[1] == "")
        debug = cli(dns, argv=("x", "status", "--debug"))
        expect("N4 --debug: the network error is raised (full traceback) and '--debug' is removed from the arguments",
               isinstance(debug[0], httpx.ConnectError) and sys.argv == ["x", "status"])
        sent = cli(dns, writes={"done": 2, "last": "brain_decisions update", "pending": "brain_tool_calls insert"},
                   locks={7}, paid=["anvixa-ai-product-2026-10-r6:T3:search:0"])[1]
        expect("N5 a write sent but unanswered -> 'may or may not have been saved' (never 'Nothing was saved'); "
               "what was saved; the lock hint; a paid call in progress -> 'unknown' + paid_call_unknown",
               "The last change (brain_tool_calls insert) may or may not have been saved" in sent
               and "Nothing was saved" not in sent
               and "Saved before the drop: 2 state change(s) (last: brain_decisions update)" in sent
               and "python orchestrator.py unlock <YYYY-MM>" in sent
               and "A paid call was in progress (anvixa-ai-product-2026-10-r6:T3:search:0)" in sent
               and "paid_call_unknown" in sent)

        # A network error INSIDE a paid search: the row stays 'running' -> next run 'unknown' + paid_call_unknown
        def drop():
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed")
        proof = {"approved_at": "2026-10-13T08:00:00+00:00", "approved_by": "Test Person", "answer": "yes",
                 "overridden": False, "override_reason": None, "validation_errors_at_approval": [],
                 "work_orders_count": 1, "work_orders_sha256": "x" * 64, "month_plan_sha256": "y" * 64,
                 "revenue_analysis_sha256": "z" * 64}
        order = {"work_order_id": "WO-T1", "task_id": "T1", "agent": "QuickICP", "output_type": "accounts",
                 "task_type": "data_build", "week": 1, "depends_on": [], "target": "10 accounts", "instructions": ["t"]}
        run, _ = load_run(customer, "2026-10", [order], {"month": "Month 1", "revenue_analysis": {}}, {}, True, proof)
        runner_kit.PAID_IN_FLIGHT.clear()
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: FakeAccountsTools(on_search=drop))
        dropped = None
        try:
            cmd_run(customer, "2026-10")
        except httpx.ConnectError as error:
            dropped = error
        task = get_tasks(run["id"])[0]
        searches = [s for s in get_steps(task["id"]) if s["step"] == "search"]
        expect("N6 a network drop inside the PAID search: not caught there; its ledger row stays 'running'; noted "
               "as in flight; the run lock is released",
               dropped is not None and [s["status"] for s in searches] == ["running"]
               and runner_kit.PAID_IN_FLIGHT == [searches[0]["idempotency_key"]]
               and get_run(run["run_key"])["locked_by"] is None)
        WIRED_RUNNERS["quickicp_accounts"] = (real_entry[0], lambda ctx: FakeAccountsTools())
        cmd_run(customer, "2026-10")
        searches = [s for s in get_steps(task["id"]) if s["step"] == "search"]
        unknown = [d for d in get_decisions(run["id"]) if d["kind"] == "paid_call_unknown"]
        expect("N7 the next run: that search -> 'unknown' + an open paid_call_unknown decision (exactly as after a crash); "
               "never repeated automatically",
               [s["status"] for s in searches] == ["unknown"] and len(unknown) == 1 and unknown[0]["status"] == "open")

        # Every entry point in a child process: nothing real can be reached
        child = {**os.environ, "PYTHONIOENCODING": "utf-8", "HTTPS_PROXY": "http://127.0.0.1:9",
                 "HTTP_PROXY": "http://127.0.0.1:9", "ALL_PROXY": "http://127.0.0.1:9", "NO_PROXY": "",
                 # one .invalid host per service (never resolvable), so each failure names its own service
                 "SUPABASE_URL": "https://supabase.selftest.invalid", "QUICKHOWL_URL": "https://quickhowl.selftest.invalid",
                 "EMAIL_TOOL_URL": "https://email.selftest.invalid", "QUICKICP_WEBHOOK_URL": "https://n8n.selftest.invalid/a",
                 "QUICKICP_PEOPLE_WEBHOOK_URL": "https://n8n.selftest.invalid/b",
                 "QUICKICP_EMAIL_WEBHOOK_URL": "https://n8n.selftest.invalid/c",
                 "QUICKICP_PHONE_WEBHOOK_URL": "https://n8n.selftest.invalid/d",
                 "OPENAI_BASE_URL": "https://openai.selftest.invalid/v1", "OPENAI_API_BASE": "https://openai.selftest.invalid/v1",
                 "PROSPEO_URL": "https://prospeo.selftest.invalid/x"}

        def run_child(args, stdin=""):
            done = subprocess.run([sys.executable, *args], cwd=root, env=child, input=stdin, capture_output=True,
                                  text=True, timeout=240, encoding="utf-8")
            return done.returncode, done.stdout + done.stderr
        commands = [["orchestrator.py", "status"], ["orchestrator.py", "answer", "1", "done", "selftest"],
                    ["intake_adapter.py"], ["quickhowl.py", "status", "--run", "2026-10", "T1"],
                    ["email_tool.py", "samples", "--run", "2026-10", "T1"]]
        outcomes = {" ".join(c): run_child(c) for c in commands}

        def e2e_child(real_calls: bool):
            """e2e_test.py --resume, unchanged, with REAL_CALLS_ENABLED set for this child only (test-only override)."""
            wrapper = (f"import runpy, sys, quickhowl; quickhowl.REAL_CALLS_ENABLED = {real_calls}; "
                       "sys.argv = ['e2e_test.py', '--resume']; runpy.run_path('e2e_test.py', run_name='__main__')")
            return run_child(["-c", wrapper], stdin=f"{current_customer()}\n")

        outcomes["e2e_test.py --resume"] = e2e_child(False)
        bad = {cmd: out[-400:] for cmd, (code, out) in outcomes.items()
               if code != 3 or "STOPPED: no network (Supabase, " not in out or "Traceback" in out}
        if bad:
            print(f"  details: {bad}")
        expect(f"N8 every entry point, unreachable network: ONE 'STOPPED: no network (Supabase, ...)' line, exit 3, "
               f"no traceback ({', '.join(outcomes)}; e2e with REAL_CALLS_ENABLED off for that child)", bad == {})
        code, out = e2e_child(True)
        expect("N8b e2e_test.py with REAL_CALLS_ENABLED on -> its guard stops it first ('real calls are on', exit 1), "
               "before anything is read: no network line, no traceback",
               code == 1 and "STOPPED: real calls are on" in out and "no network" not in out and "Traceback" not in out)
        code, out = run_child(["orchestrator.py", "status", "--debug"])
        expect("N9 the same with --debug -> the full traceback (exit code not 3)", code not in (0, 3) and "Traceback" in out)
        code, out = run_child(["check_system.py"])
        expect("N10 check_system: each live check -> FAIL 'no network (<service>)' and the OTHER checks still run "
               "(the result line is printed); no whole-run STOPPED, no traceback",
               code == 1 and "no network (Supabase)" in out and "no network (OpenAI)" in out
               and "no network (Prospeo)" in out and "Result:" in out and "STOPPED: no network" not in out
               and "Traceback" not in out)
        source = open(os.path.join(root, "master_agent.py"), encoding="utf-8").read()
        expect("N11 master_agent.py (not run: it plans with the LLM) uses run_cli too, like the 8 others",
               all("    run_cli(cli" in open(os.path.join(root, f), encoding="utf-8").read()
                   for f in ("orchestrator.py", "e2e_test.py", "master_agent.py", "intake_adapter.py", "check_system.py",
                             "quickhowl.py", "email_tool.py", "validator.py", "supabase_store.py"))
               and source.count("run_cli(cli)") == 1)
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        WIRED_RUNNERS["quickicp_accounts"] = real_entry
        state_store.STATE_WRITES.update(saved[0])
        state_store.HELD_LOCKS.clear()
        state_store.HELD_LOCKS.update(saved[1])
        runner_kit.PAID_IN_FLIGHT[:] = saved[2]
        sys.argv[:] = saved[3]
        deleted, left = sweep_selftest_runs(customer)
        print(f"  {'FAIL' if left else 'PASS'}  cleanup: {deleted} test rows deleted, {'SOME' if left else 'none'} left")
        ok = ok and not left
    print("ORCHESTRATOR SELFTEST (network) OK" if ok else "ORCHESTRATOR SELFTEST (network) FAILED")
    return ok


# ================= IMPORTS: the old flow is gone from the run path =================

MOVED_MODULES = ("pipeline", "goal_check", "email_campaign_payload", "company_loop_cli", "people_loop_cli")
MOVED_FUNCTIONS = ("run_loop", "run_people_loop",                                   # old loop CLIs
                   "cmd_preview", "cmd_launch", "cmd_start", "build_contacts", "calls_work_order",  # quickhowl
                   "run_launch", "run_approve", "email_work_order", "validated_people_links",      # email_tool
                   "run_companies", "run_people", "run_emails", "run_phones", "run_all",            # quickicp /
                   "run_enrichment", "load_domains", "build_people_payloads", "load_quickicp_work_order",  # validator
                   "passed_company_keys")
RUN_PATH_MODULES = ("orchestrator", "orchestrator_selftest", "e2e_test", "intake_adapter", "master_agent",
                    "brain_review", "runner_kit", "runner_quickicp_accounts", "runner_quickicp_contacts",
                    "runner_email", "runner_calls", "company_loop", "people_loop", "validator", "quickicp",
                    "email_tool", "quickhowl", "state_store", "supabase_store", "plan_approval")


def moved_references(path: str) -> list[str]:
    """Every import of a moved module, and every use of a moved function, in one file (AST: also inside functions)."""
    import ast
    found = []
    for node in ast.walk(ast.parse(open(path, encoding="utf-8").read())):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in MOVED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in MOVED_MODULES:
                found.append(f"from {node.module} import ...")
            found += [f"from {node.module} import {a.name}" for a in node.names if a.name in MOVED_FUNCTIONS]
        elif isinstance(node, ast.Name) and node.id in MOVED_FUNCTIONS:
            found.append(f"uses {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in MOVED_FUNCTIONS:
            found.append(f"uses .{node.attr}")
    return [f"{os.path.basename(path)}:{ref}" for ref in found]


def selftest_imports() -> bool:
    """archive/legacy/ is never imported by the run path (static scan + a fresh Python process); the kept tools exist."""
    import glob
    import subprocess
    print("\nOrchestrator IMPORTS selftest (archive/legacy is off the run path)")
    ok = False
    try:
        root = os.path.dirname(os.path.abspath(__file__))
        files, skipped = [], []
        for path in sorted(glob.glob(os.path.join(root, "*.py"))):
            try:
                compile(open(path, encoding="utf-8").read(), path, "exec")
                files.append(path)
            except SyntaxError:
                skipped.append(os.path.basename(path))     # not valid Python: nothing can import it either
        if skipped:
            print(f"  note: skipped (not valid Python, cannot be imported): {', '.join(skipped)}")
        refs = [ref for path in files for ref in moved_references(path)]
        expect(f"static scan of {len(files)} .py files (also imports inside functions): nothing imports "
               f"{', '.join(MOVED_MODULES)} or uses run_loop / run_people_loop", refs == [])
        expect("the moved files are in archive/legacy/ and no longer in the root folder",
               all(os.path.exists(os.path.join(root, "archive", "legacy", f"{m}.py")) for m in MOVED_MODULES)
               and not any(os.path.exists(os.path.join(root, f"{m}.py")) for m in MOVED_MODULES))
        code = ("import sys\n" + "".join(f"import {m}\n" for m in RUN_PATH_MODULES)
                + f"print('LOADED', sorted(m for m in {MOVED_MODULES!r} if m in sys.modules))\n"
                + "import company_loop, people_loop\n"
                + "print('FUNCTIONS', [f for f in ('run_loop', 'save_results') if hasattr(company_loop, f)]"
                  " + [f for f in ('run_people_loop', 'company_queue', 'save_results') if hasattr(people_loop, f)])\n")
        done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=180,
                              env={**os.environ, "PYTHONIOENCODING": "utf-8"})
        lines = done.stdout.splitlines()
        expect(f"a fresh Python process imports all {len(RUN_PATH_MODULES)} run-path modules; none of the moved "
               "modules gets loaded; the loop modules no longer have the CLI functions",
               done.returncode == 0 and "LOADED []" in lines and "FUNCTIONS []" in lines)
        if done.returncode != 0:
            print(done.stderr[-1500:])
        import email_tool
        import quickhowl
        import validator
        import quickicp
        import supabase_store
        source = {name: open(os.path.join(root, f"{name}.py"), encoding="utf-8").read()
                  for name in ("quickicp", "supabase_store")}
        expect("the paid / send CLI modes are gone: quickhowl = status/results/watch/pause, email_tool = samples, "
               "validator = maps/reset-maps/selftest, quickicp has no CLI, supabase_store has no 'save'",
               set(quickhowl.COMMANDS) == {"status", "results", "watch", "pause"} and set(email_tool.MODES) == {"samples"}
               and set(validator.MODES) == {"maps", "reset-maps", "selftest"} and not hasattr(quickicp, "MODES")
               and 'if __name__ == "__main__"' not in source["quickicp"]
               and 'command == "save"' not in source["supabase_store"]
               and not any(hasattr(module, name) for module in (quickhowl, email_tool, quickicp, validator, supabase_store)
                           for name in MOVED_FUNCTIONS + ("customer_name", "STATE_FILE", "COMPANIES_FILE")))
        expect("their code is in archive/legacy/ (quickhowl_cli, email_tool_cli, quickicp_cli, validator_cli, "
               "supabase_store_cli)",
               all(os.path.exists(os.path.join(root, "archive", "legacy", f"{m}_cli.py"))
                   for m in ("quickhowl", "email_tool", "quickicp", "validator", "supabase_store")))
        expect("kept manual tools still there: quickhowl pause/status/results/watch, email_tool samples, "
               "validator maps/reset-maps, extract_html.py",
               {"pause", "status", "results", "watch"} <= set(quickhowl.COMMANDS) and "samples" in email_tool.MODES
               and {"maps", "reset-maps"} <= set(validator.MODES) and os.path.exists(os.path.join(root, "extract_html.py")))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    print("ORCHESTRATOR SELFTEST (imports) OK" if ok else "ORCHESTRATOR SELFTEST (imports) FAILED")
    return ok


if __name__ == "__main__":
    sys.exit(0 if selftest() else 1)
