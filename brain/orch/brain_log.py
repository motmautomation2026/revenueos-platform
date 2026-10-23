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
    the value of every environment setting whose name has KEY / TOKEN / PASSWORD / SECRET / DSN -> '********',
    except names that are not secrets (NOT_SECRET: CUSTOMER_KEY, REVENUEOS_PROJECT_ID), so run keys stay readable

WHERE
-----
    logs/ under the folder the command runs in (run_for.py: customers/<key>/logs/). BRAIN_LOG_DIR=<folder>
    overrides it; BRAIN_LOG=off switches logging off. One file per day.
    Cleanup: once per process, at its first log write, files named EXACTLY brain-YYYY-MM-DD.log in that folder
    whose date is more than BRAIN_LOG_KEEP_DAYS days ago (default 30; 0 or an invalid value = never delete)
    are deleted. Nothing else is ever deleted; any error is ignored.

RUN
---
    python orch/brain_log.py selftest      temp folders only; reads motm-emailcheck (read-only) for the
                                           'same screen output with and without logging' check
"""

import datetime
import os
import re
import traceback

SECRET_NAME = re.compile(r"KEY|TOKEN|PASSWORD|SECRET|DSN", re.IGNORECASE)
NOT_SECRET = {"CUSTOMER_KEY", "REVENUEOS_PROJECT_ID"}     # ids, not secrets: they are in run keys and file names
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]{1,3})[A-Za-z0-9._%+-]*@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
PHONE = re.compile(r"(?<![\w-])(\+\d{2})[\d -]{8,13}\d(?![\w-])|(?<![\w-])([6-9]\d{2})\d{7}(?![\w-])")
KEY = re.compile(r"^(?P<run>[a-z0-9]+(?:-[a-z0-9]+)*-\d{4}-\d{2}(?:-r\d+)?):(?P<task>T\d+):")
_TASKS: dict = {}            # task_row_id -> (run_key, task): learned from ledger keys, no database read
LOG_FILE = re.compile(r"brain-(\d{4}-\d{2}-\d{2})\.log")
KEEP_DAYS = 30
_cleaned = False             # the cleanup runs once per process


def mask(text: str) -> str:
    """Emails, phone numbers and secret values from the environment, hidden."""
    for name, value in os.environ.items():
        # passwords from 6 characters (Quickhowl accepts 6); other secrets from 8 (shorter ones are not keys)
        if name.rsplit(":", 1)[-1].upper() in NOT_SECRET:     # (also a stray '$env:CUSTOMER_KEY' line in .env)
            continue
        if SECRET_NAME.search(name) and value and len(value) >= (6 if "PASSWORD" in name.upper() else 8):
            text = text.replace(value, "********")
    text = EMAIL.sub(lambda m: m.group(1) + "***@***", text)
    return PHONE.sub(lambda m: (m.group(1) or m.group(2)) + "***", text)


def keep_days() -> int:
    """BRAIN_LOG_KEEP_DAYS (default 30). 0, a negative or an unreadable value -> 0 = never delete."""
    value = (os.getenv("BRAIN_LOG_KEEP_DAYS") or "").strip()
    if not value:
        return KEEP_DAYS
    return int(value) if value.isdigit() else 0


def cleanup(folder: str, today: datetime.date | None = None) -> list[str]:
    """Delete brain-YYYY-MM-DD.log files in 'folder' older than keep_days(). Returns their names. Never raises."""
    deleted = []
    try:
        days = keep_days()
        if days <= 0:
            return deleted
        today = today or datetime.date.today()
        for name in os.listdir(folder):
            try:
                found = LOG_FILE.fullmatch(name)
                path = os.path.join(folder, name)
                if not found or not os.path.isfile(path) or os.path.islink(path):
                    continue
                if (today - datetime.date.fromisoformat(found[1])).days > days:
                    os.remove(path)
                    deleted.append(name)
            except Exception:
                continue         # an impossible date, a locked file: leave it
    except Exception:
        pass
    return deleted


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
        global _cleaned
        if not _cleaned:
            _cleaned = True
            cleanup(folder)
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

    saved = {name: os.environ.get(name) for name in ("BRAIN_LOG_DIR", "BRAIN_LOG", "SELFTEST_API_KEY", "BRAIN_LOG_KEEP_DAYS",
                                                     "SELFTEST_USER_PASSWORD", "SELFTEST_SHORT_KEY", "CUSTOMER_KEY",
                                                     "$env:CUSTOMER_KEY", "SENTRY_DSN", "SELFTEST_TOKEN")}
    try:
        os.environ["SELFTEST_API_KEY"] = "sk-selftest-0123456789"
        text = mask("mail rahul.sharma@acme.co.in, call +91 98765 43210 or 9876543210, key sk-selftest-0123456789, "
                    "run selftest-20261015-101059-123456, ref MOTM-ACME-HYPER-20261015-101059, 3 rows")
        expect("masking: email -> 'rah***@***', phone +91... and a 10-digit mobile -> '+91***' / '987***', a secret "
               "value -> '********'; run keys, references and counts stay readable",
               text == "mail rah***@***, call +91*** or 987***, key ********, run selftest-20261015-101059-123456, "
                       "ref MOTM-ACME-HYPER-20261015-101059, 3 rows")
        os.environ["SELFTEST_USER_PASSWORD"], os.environ["SELFTEST_SHORT_KEY"] = "Pw6chr", "abc1234"
        expect("masking: a PASSWORD setting from 6 characters (Quickhowl accepts 6); other secrets from 8",
               mask("login Pw6chr, flag abc1234") == "login ********, flag abc1234")
        os.environ.update({"CUSTOMER_KEY": "selftest-customer-key", "$env:CUSTOMER_KEY": "selftest-customer-key",
                           "SENTRY_DSN": "https://pub1234567@o9.ingest.sentry.io/42", "SELFTEST_TOKEN": "tok-0123456789"})
        expect("masking: CUSTOMER_KEY is not a secret (run keys stay readable, also for a stray '$env:CUSTOMER_KEY'); "
               "the Sentry DSN, tokens and API keys are still masked",
               mask("run=selftest-customer-key-2026-10 dsn https://pub1234567@o9.ingest.sentry.io/42 t tok-0123456789 "
                    "k sk-selftest-0123456789")
               == "run=selftest-customer-key-2026-10 dsn ******** t ******** k ********")

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
        with tempfile.TemporaryDirectory() as folder:
            today = datetime.date(2026, 10, 16)
            names = {"brain-2026-09-15.log": "31 days", "brain-2026-09-16.log": "30 days", "brain-2026-10-16.log": "today",
                     "brain-2020-01-01.log.bak": "other", "other.log": "other", "brain-2026-13-01.log": "bad date",
                     "brain-2026-9-1.log": "other", "notes.txt": "other"}
            for name in names:
                open(os.path.join(folder, name), "w").close()
            os.makedirs(os.path.join(folder, "brain-2020-01-01.log"))          # a FOLDER with a log file's name
            os.environ.pop("BRAIN_LOG_KEEP_DAYS", None)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                deleted = cleanup(folder, today)
            left = sorted(os.listdir(folder))
            expect("cleanup (default 30 days): ONLY brain-YYYY-MM-DD.log files older than 30 days are deleted (31 days "
                   "-> deleted; 30 days and today -> kept); other names, an impossible date and a folder are never "
                   "touched; nothing printed",
                   deleted == ["brain-2026-09-15.log"] and "brain-2026-09-15.log" not in left and len(left) == len(names)
                   and out.getvalue() == err.getvalue() == "")
            open(os.path.join(folder, "brain-2026-09-15.log"), "w").close()
            kept = []
            for value in ("0", "abc", "-5"):
                os.environ["BRAIN_LOG_KEEP_DAYS"] = value
                kept.append(cleanup(folder, today) == [])
            os.environ["BRAIN_LOG_KEEP_DAYS"] = "10"
            ten = cleanup(folder, today)
            expect("BRAIN_LOG_KEEP_DAYS: 0, 'abc' and -5 -> nothing deleted; 10 -> the 31- and 30-day files deleted",
                   kept == [True, True, True] and sorted(ten) == ["brain-2026-09-15.log", "brain-2026-09-16.log"])
            os.environ.pop("BRAIN_LOG_KEEP_DAYS", None)
            expect("cleanup of a folder that does not exist: no exception, nothing deleted",
                   cleanup(os.path.join(folder, "missing")) == [])

        with tempfile.TemporaryDirectory() as folder:
            global _cleaned
            open(os.path.join(folder, "brain-2000-01-01.log"), "w").close()
            os.environ["BRAIN_LOG_DIR"] = folder
            _cleaned = False
            write("INFO", "first write of this process")
            first_gone = not os.path.exists(os.path.join(folder, "brain-2000-01-01.log"))
            open(os.path.join(folder, "brain-2000-01-01.log"), "w").close()
            write("INFO", "second write")
            expect("the cleanup runs once per process, at the first log write (a later old file stays until the next process)",
                   first_gone and os.path.exists(os.path.join(folder, "brain-2000-01-01.log")))
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
