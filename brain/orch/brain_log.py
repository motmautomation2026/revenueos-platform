"""
orch/brain_log.py
=================

The brain's log: one line per event in logs/brain-YYYY-MM-DD.log, for finding out afterwards what a run did.
FILE ONLY: nothing is ever written to stdout or stderr (run_for.py parses those), and a failure to log is
swallowed, so logging can never stop or change a run.

WHAT IS LOGGED
--------------
    quickicp.run_cli        every command's end (exit code), and every exception with its traceback; the
                            exception is then RE-RAISED (or turned into the same STOPPED line + exit 3 as before)
    state_store ledger      start_step / finish_step / mark_unknown: tool, step, key, paid, status, error
                            (never the request or reply bodies)
    Line:  <time> <LEVEL> run=<run_key> task=<T#> step=<step> <message>
    run / task come from the ledger key ('<run_key>:<T#>:<step>...'); a task seen once is remembered for its
    calls whose key is a tool reference.

MASKED (in messages and tracebacks)
-----------------------------------
    emails -> 'abc***@***', phone numbers (+CC... or a 10-digit mobile) -> '+91***' / '987***',
    the value of every environment setting whose name has KEY / TOKEN / PASSWORD / SECRET -> '********'

WHERE
-----
    logs/ under the folder the command runs in (run_for.py: customers/<key>/logs/). BRAIN_LOG_DIR=<folder>
    overrides it; BRAIN_LOG=off switches logging off. One file per day; old files are never deleted here.

RUN
---
    python orch/brain_log.py selftest      temp folders only; reads motm-emailcheck (read-only) for the
                                           'same screen output with and without logging' check
"""

import datetime
import os
import re
import traceback

SECRET_NAME = re.compile(r"KEY|TOKEN|PASSWORD|SECRET", re.IGNORECASE)
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]{1,3})[A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\w-])(\+\d{2})[\d -]{8,13}\d(?![\w-])|(?<![\w-])([6-9]\d{2})\d{7}(?![\w-])")
KEY = re.compile(r"^(?P<run>[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}(?:-r\d+)?):(?P<task>T\d+):")
_TASKS: dict = {}            # task_row_id -> (run_key, task): learned from ledger keys, no database read


def mask(text: str) -> str:
    """Emails, phone numbers and secret values from the environment, hidden."""
    for name, value in os.environ.items():
        if SECRET_NAME.search(name) and value and len(value) >= 8:
            text = text.replace(value, "********")
    text = EMAIL.sub(lambda m: m.group(1) + "***@***", text)
    return PHONE.sub(lambda m: (m.group(1) or m.group(2)) + "***", text)


def write(level: str, message: str, run: str | None = None, task: str | None = None, step: str | None = None,
          error: BaseException | None = None) -> None:
    """One line (+ the masked traceback of 'error'). Never raises, never prints."""
    try:
        if (os.getenv("BRAIN_LOG") or "").strip().lower() == "off":
            return
        text = message + ("\n" + "".join(traceback.format_exception(error)).rstrip() if error is not None else "")
        now = datetime.datetime.now().astimezone()
        line = (f"{now.isoformat(timespec='seconds')} {level:<7} run={run or '-'} task={task or '-'} "
                f"step={step or '-'} " + mask(text).replace("\n", "\n    ") + "\n")
        folder = os.getenv("BRAIN_LOG_DIR") or os.path.join(os.getcwd(), "logs")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"brain-{now:%Y-%m-%d}.log"), "a", encoding="utf-8") as file:
            file.write(line)
    except Exception:
        pass                 # logging must never stop or change a run


def ledger(row: dict, event: str, level: str = "INFO") -> None:
    """A ledger row (brain_tool_calls) as one line: tool, step, key, paid, status, error. Never raises."""
    try:
        key = row.get("idempotency_key") or ""
        found = KEY.match(key)
        if found:
            _TASKS[row.get("task_row_id")] = (found["run"], found["task"])
        run, task = _TASKS.get(row.get("task_row_id"), (None, None))
        error = f" error: {row['error']}" if row.get("error") else ""
        write(level, f"{row.get('tool')} {event}: key {key} | {'PAID' if row.get('paid') else 'free'} | "
                     f"status {row.get('status')}{error}", run, task, row.get("step"))
    except Exception:
        pass


# ================= SELFTEST (temp folders; one read-only child process on motm-emailcheck) =================

def selftest() -> bool:
    import contextlib
    import io
    import subprocess
    import sys
    import tempfile
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, root)
    checks = []

    def expect(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    saved = {name: os.environ.get(name) for name in ("BRAIN_LOG_DIR", "BRAIN_LOG", "SELFTEST_API_KEY")}
    try:
        os.environ["SELFTEST_API_KEY"] = "sk-selftest-0123456789"
        text = mask("mail rahul.sharma@acme.co.in, call +91 98765 43210 or 9876543210, key sk-selftest-0123456789, "
                    "run selftest-20261015-101059-123456, ref MOTM-ACME-HYPER-20261015-101059, 3 rows")
        expect("masking: email -> 'rah***@***', phone +91... and a 10-digit mobile -> '+91***' / '987***', a secret "
               "value -> '********'; run keys, references and counts stay readable",
               text == "mail rah***@***, call +91*** or 987***, key ********, run selftest-20261015-101059-123456, "
                       "ref MOTM-ACME-HYPER-20261015-101059, 3 rows")

        with tempfile.TemporaryDirectory() as folder:
            os.environ["BRAIN_LOG_DIR"] = folder
            ledger({"task_row_id": 7, "idempotency_key": "acme-2026-10:T3:search:1", "tool": "quickicp", "paid": True,
                    "status": "running", "step": "search"}, "started")
            ledger({"task_row_id": 7, "idempotency_key": "MOTM-ACME-HYPER-1", "tool": "email_tool", "paid": True,
                    "status": "failed", "step": "launch", "error": "Launch rejected for bob@acme.com"}, "finished", "WARNING")
            files = os.listdir(folder)
            lines = open(os.path.join(folder, files[0]), encoding="utf-8").read().splitlines() if files else []
            expect("a line per ledger event, one file per day (brain-YYYY-MM-DD.log); run and task from the key, then "
                   "remembered for the task's reference keys; the error masked",
                   len(files) == 1 and re.fullmatch(r"brain-\d{4}-\d{2}-\d{2}\.log", files[0]) and len(lines) == 2
                   and "INFO    run=acme-2026-10 task=T3 step=search quickicp started: key acme-2026-10:T3:search:1 "
                       "| PAID | status running" in lines[0]
                   and "WARNING run=acme-2026-10 task=T3 step=launch" in lines[1] and "bob***@***" in lines[1]
                   and "bob@acme.com" not in lines[1])

            import quickicp
            def entry(error):
                def main():
                    raise error
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        quickicp.run_cli(main)
                    except BaseException as raised:      # noqa: B036 - the selftest looks at the type
                        return type(raised), out.getvalue()
                return None, out.getvalue()

            class Crash(BaseException):
                pass
            kinds = [entry(ValueError("bad value for carol@acme.com"))[0], entry(Crash("simulated"))[0],
                     entry(KeyboardInterrupt())[0], entry(SystemExit(1))[0]]
            network = entry(ConnectionError("[Errno 11001] getaddrinfo failed"))
            log_text = open(os.path.join(folder, os.listdir(folder)[0]), encoding="utf-8").read()
            expect("run_cli: each exception is logged with its traceback and RE-RAISED with its own type (ValueError, a "
                   "BaseException crash, KeyboardInterrupt, SystemExit); a network drop still -> the STOPPED line + "
                   "exit 3; the log has the tracebacks, masked",
                   kinds == [ValueError, Crash, KeyboardInterrupt, SystemExit] and network[0] is SystemExit
                   and network[1].startswith("STOPPED: no network") and "Traceback (most recent call last)" in log_text
                   and "ValueError: bad value for car***@***" in log_text and "carol@acme.com" not in log_text
                   and "exit 1" in log_text)

        with tempfile.TemporaryDirectory() as folder:
            blocker = os.path.join(folder, "not-a-folder")
            open(blocker, "w").close()
            os.environ["BRAIN_LOG_DIR"] = blocker                    # a FILE: no log folder can be made there
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                write("ERROR", "cannot be written")
                ledger({"idempotency_key": "x"}, "started")
            expect("an unwritable log folder: no exception, nothing on stdout or stderr", out.getvalue() == err.getvalue() == "")

        with tempfile.TemporaryDirectory() as folder:
            outputs = []
            for setting in ({"BRAIN_LOG": "off"}, {"BRAIN_LOG_DIR": folder}):
                env = {**{k: v for k, v in os.environ.items() if k not in ("BRAIN_LOG", "BRAIN_LOG_DIR")},
                       "CUSTOMER_KEY": "motm-emailcheck", "PYTHONIOENCODING": "utf-8", **setting}
                for args in (["status", "2026-10"], ["answer", "999999", "yes"]):
                    done = subprocess.run([sys.executable, os.path.join(root, "orchestrator.py"), *args], cwd=root,
                                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=120)
                    outputs.append((done.returncode, done.stdout, done.stderr))
            logged = os.listdir(folder)
            expect("orchestrator.py status / answer (read-only, motm-emailcheck): stdout, stderr and exit code are "
                   "IDENTICAL with logging off and on; with it on, the log file got the lines",
                   outputs[:2] == outputs[2:] and outputs[1][0] == 1 and logged
                   and "exit 1" in open(os.path.join(folder, logged[0]), encoding="utf-8").read())
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    ok = all(checks)
    print(f"BRAIN LOG SELFTEST {'OK' if ok else 'FAILED'} ({sum(checks)} of {len(checks)})")
    return ok


if __name__ == "__main__":
    import sys
    sys.exit(0 if sys.argv[1:] == ["selftest"] and selftest() else 1)
