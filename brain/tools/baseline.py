"""
tools/baseline.py
=================

Proof that a refactor changed NOTHING a user, a selftest or run_for.py can see: the exact output and
exit code of every selftest and read-only command, captured before and after, compared line by line.

WHY
---
run_for.py (the platform's worker) parses orchestrator output: 'next would buy data (T3)', lines that
start with STOPPED, and exit codes. A refactor (split into files, logging) must leave all of it identical.

HOW
---
    capture <dir>   run every command below, save <dir>/<name>.out (stdout + stderr) and <name>.code
    compare <a> <b> normalize both sides with NOISE (times, generated ids, temp paths) and compare
                    line by line; exit 1 on the first difference per output, with both lines shown
    Commands:
      selftests     the 6 + state_store write-test + tools/email_live_check selftest
                    (they write selftest-* rows to Supabase and delete them, as always)
      cli_*         read-only orchestrator commands, ONLY with CUSTOMER_KEY=motm-emailcheck (a finished run the
                    platform's worker never touches). The answer commands stop BEFORE any write: an unknown
                    decision id, a task that does not exist, a task with no open decision.
      contract      the exact texts run_for.py parses (until_wait_stop) + the import surface of orchestrator
                    (every public name; each function's source hash; each constant's value)

RUN
---
    python tools/baseline.py capture baseline/before
    python tools/baseline.py compare baseline/before baseline/after
    baseline/ is git-ignored (the outputs hold run keys and texts of this machine).
"""

import hashlib
import inspect
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
CHECK_KEY = "motm-emailcheck"         # the only customer the CLI commands run for

COMMANDS = [
    ("selftest_orchestrator", ["orchestrator.py", "selftest"], {}),
    ("selftest_e2e", ["e2e_test.py", "selftest"], {}),
    ("selftest_intake", ["intake_adapter.py", "selftest"], {}),
    ("selftest_validator", ["validator.py", "selftest"], {}),
    ("selftest_platform_approval", ["platform_approval.py", "selftest"], {}),
    ("selftest_run_for", ["run_for.py", "selftest"], {}),
    ("selftest_state_store", ["state_store.py", "write-test"], {}),
    ("selftest_email_live_check", ["tools/email_live_check.py", "selftest"], {}),
    ("cli_usage", ["orchestrator.py"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_unknown", ["orchestrator.py", "bogus"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_bad_month", ["orchestrator.py", "run", "2026-1"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_status", ["orchestrator.py", "status"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_status_month", ["orchestrator.py", "status", "2026-10"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_next", ["orchestrator.py", "next", "2026-10"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_decisions", ["orchestrator.py", "decisions"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_answer_unknown_id", ["orchestrator.py", "answer", "999999", "yes"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_answer_no_task", ["orchestrator.py", "answer", "T9", "yes"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("cli_answer_nothing_open", ["orchestrator.py", "answer", "T2", "yes"], {"CUSTOMER_KEY": CHECK_KEY}),
    ("contract", ["tools/baseline.py", "_contract"], {"CUSTOMER_KEY": CHECK_KEY}),
]

# PYTHONHASHSEED=0 in every child: set order (printed sets, e.g. ALLOWED_ANSWERS) is then the same in every run.
# Values that differ between two runs of the SAME code. Only these are blanked; counts, texts, order
# and exit codes never are. Found by capturing twice before any change (see the commit message).
NOISE = [
    (r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)?", "<time>"),
    (r"selftest-\d{8}-\d{6}-\d{6}", "selftest-<stamp>"),
    (r"#\d+", "#<id>"),                                                    # database row ids (decisions, reviews)
    (r"(orchestrator\.py answer )\d+", r"\1<id>"),                        # the same ids in the answer hints
    (r"\[\d{2}:\d{2}:\d{2}\]", "[<clock>]"),                               # run_for worker clock
    (r"(MOTM-SELFTEST-HYPER-|motm-selftest-call-)[0-9a-f]{12}", r"\1<ref>"),  # random campaign references
    (r"(motm|MOTM)-(st|ST)\d{6}", r"\1-\2<tag>"),                         # reference tag from the selftest stamp
    (r"tmp[a-z0-9_]{8}\\", r"tmp<dir>\\"),                                 # temporary folders
    (r"\d{8}-\d{6}-(run|plan)\.log", r"<stamp>-\1.log"),                  # run_for log file names
]


def normalize(text: str) -> list[str]:
    for pattern, replacement in NOISE:
        text = re.sub(pattern, replacement, text)
    return text.splitlines()


def capture(folder: str) -> None:
    os.makedirs(folder, exist_ok=True)
    for name, args, env in COMMANDS:
        if env.get("CUSTOMER_KEY", CHECK_KEY) != CHECK_KEY:
            raise SystemExit(f"refusing {name}: CLI commands run for {CHECK_KEY} only")
        done = subprocess.run([PY, *args], cwd=ROOT, env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0", **env},
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800,
                              stdin=subprocess.DEVNULL)
        with open(os.path.join(folder, name + ".out"), "w", encoding="utf-8", newline="\n") as file:
            file.write(done.stdout + done.stderr)
        with open(os.path.join(folder, name + ".code"), "w", encoding="utf-8") as file:
            file.write(str(done.returncode))
        print(f"  {name:30} exit {done.returncode}")


def compare(a: str, b: str) -> bool:
    same = True
    for name, _, _ in COMMANDS:
        read = lambda folder, ext: open(os.path.join(folder, name + ext), encoding="utf-8").read()
        code_a, code_b = read(a, ".code"), read(b, ".code")
        lines_a, lines_b = normalize(read(a, ".out")), normalize(read(b, ".out"))
        if code_a != code_b:
            print(f"  DIFF  {name}: exit {code_a} -> {code_b}")
            same = False
            continue
        for number, (line_a, line_b) in enumerate(zip(lines_a, lines_b), start=1):
            if line_a != line_b:
                print(f"  DIFF  {name} line {number}:\n        - {line_a}\n        + {line_b}")
                same = False
                break
        else:
            if len(lines_a) != len(lines_b):
                print(f"  DIFF  {name}: {len(lines_a)} -> {len(lines_b)} lines")
                same = False
            else:
                print(f"  SAME  {name} (exit {code_a}, {len(lines_a)} lines)")
    print("BASELINE: SAME" if same else "BASELINE: DIFFERENT")
    return same


def contract() -> None:
    """The texts run_for.py parses, and the import surface of orchestrator (names, source hashes, values)."""
    sys.path.insert(0, ROOT)
    import orchestrator
    base = {"run_status": "running", "blocked_paid": None, "human_started": False, "started": None,
            "tool_kind": None, "tool": None, "applied": [], "resumed": []}
    for case in ({"run_status": "done"}, {"run_status": "stopped"}, {"blocked_paid": "T3"},
                 {"human_started": True, "started": "T2"}, {"tool_kind": "waiting", "tool": "T5 waits"},
                 {"tool_kind": "failed", "tool": "T5 failed"}, {"tool_kind": "running", "tool": "T5"},
                 {}, {"applied": ["T1"]}):
        try:
            text = orchestrator.until_wait_stop({**base, **case})
        except Exception as error:                       # the shape itself is part of the contract
            text = f"{type(error).__name__}: {error}"
        print(f"until_wait_stop {json.dumps(case, sort_keys=True)} -> {text!r}")
    for name in sorted(n for n in dir(orchestrator) if not n.startswith("_")):
        value = getattr(orchestrator, name)
        if inspect.ismodule(value):
            print(f"name {name}: module {value.__name__}")
        elif inspect.isfunction(value) or inspect.isclass(value):
            source = inspect.getsource(value)
            print(f"name {name}: {type(value).__name__} sha256={hashlib.sha256(source.encode()).hexdigest()[:16]}")
        elif name == "WIRED_RUNNERS":
            print(f"name {name}: " + ", ".join(f"{k}->{v[0].__name__}" for k, v in sorted(value.items())))
        else:
            print(f"name {name}: {type(value).__name__} {value!r}"[:400])


RUNTIME_VALUES = {"_backend", "STATE_WRITES", "HELD_LOCKS", "PAID_IN_FLIGHT", "USAGE_TOTAL"}   # changed while running
ARCHIVED = {"pipeline", "goal_check", "email_campaign_payload", "company_loop_cli", "people_loop_cli"}


def check_imports() -> bool:
    """After the orchestrator split: every name defined once, shared state shared, no copied runtime values.
    orch/brain_log.py is not part of the split (the log; imported by quickicp and state_store, not re-exported)."""
    import ast
    sys.path.insert(0, ROOT)
    import orchestrator
    import orch.run
    files = ["orchestrator.py"] + sorted(f"orch/{f}" for f in os.listdir(os.path.join(ROOT, "orch")) if f.endswith(".py") and f != "brain_log.py")
    defined, problems = {}, []
    for path in files:
        tree = ast.parse(open(os.path.join(ROOT, path), encoding="utf-8").read())
        for node in tree.body:
            names = ([node.name] if isinstance(node, (ast.FunctionDef, ast.ClassDef)) else
                     [t.id for t in getattr(node, "targets", [getattr(node, "target", None)]) if isinstance(t, ast.Name)])
            for name in names:
                if name in defined:
                    problems.append(f"{name} defined in {defined[name]} AND {path}")
                defined[name] = path
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                copied = {a.name for a in node.names} & RUNTIME_VALUES
                if copied:
                    problems.append(f"{path} imports runtime value(s) by value: {sorted(copied)}")
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = ",".join(a.name for a in node.names)
            else:
                continue
            if path.startswith("orch/") and "orchestrator" in module.split(","):
                problems.append(f"{path} imports orchestrator (it must only be imported BY orchestrator)")
            if set(module.replace(".", ",").split(",")) & ARCHIVED:
                problems.append(f"{path} imports archived code: {module}")
    missing = [name for name in defined if not hasattr(orchestrator, name)]
    if missing:
        problems.append(f"not importable from orchestrator: {missing}")
    if orchestrator.WIRED_RUNNERS is not orch.run.WIRED_RUNNERS:
        problems.append("orchestrator.WIRED_RUNNERS is not the dict orch.run uses")
    orchestrator.WIRED_RUNNERS["_probe"] = None              # what e2e_test / email_live_check do
    seen = "_probe" in orch.run.WIRED_RUNNERS
    del orchestrator.WIRED_RUNNERS["_probe"]
    if not seen:
        problems.append("a change to orchestrator.WIRED_RUNNERS does not reach orch.run")
    for problem in problems:
        print(f"  FAIL  {problem}")
    print(f"IMPORT CHECK: {'OK' if not problems else 'FAILED'} ({len(defined)} names in {len(files)} files; "
          f"WIRED_RUNNERS shared; none of {sorted(RUNTIME_VALUES)} imported by value)")
    return not problems


if __name__ == "__main__":
    command = sys.argv[1:]
    if command == ["check-imports"]:
        sys.exit(0 if check_imports() else 1)
    if command[:1] == ["capture"] and len(command) == 2:
        capture(command[1])
    elif command[:1] == ["compare"] and len(command) == 3:
        sys.exit(0 if compare(command[1], command[2]) else 1)
    elif command == ["_contract"]:
        contract()
    else:
        print("Usage: python tools/baseline.py capture <dir> | compare <dir-a> <dir-b>")
        sys.exit(1)
