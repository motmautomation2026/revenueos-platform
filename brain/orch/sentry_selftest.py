"""
orch/sentry_selftest.py  -  python orch/brain_sentry.py selftest. No network: events go to a file
(BRAIN_SENTRY_TEST_FILE) instead of Sentry, and 'Sentry down' is a local closed port / a local port that never
answers. Child processes check that a command prints the SAME bytes and exits with the SAME code with and
without SENTRY_DSN (stdout + stderr together, as run_for reads them; buffered and unbuffered).
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAKE_DSN = "https://publickey@o0.ingest.sentry.io/0"
FAKE_PASSWORD = "Fake-Secret-77"

CHILD = """
import os, sys
sys.path.insert(0, {root!r})
from quickicp import report_event, run_cli

def main():
    kind = os.environ.get("CHILD_KIND", "stopped")
    print("Run selftest-sentry-2026-10 | status running")
    print("  Next:     T4 would buy data: run it without --until-wait (python orchestrator.py run 2026-10)")
    sys.stdout.write("partial ")
    print("line \\u20b9 50 lakh, \\u0939\\u093e\\u0901 \\u091c\\u0940", flush=True)
    print("to stderr", file=sys.stderr)
    sys.stdout.write("no newline yet")
    print(" - now one")
    if kind == "alert":
        report_event("campaign_stalled", "T8 calls campaign REF stalled on 2026-10-20", "warning", run="r", task="T8")
    if kind == "crash":
        raise ValueError("boom for mail rahul.sharma@acme.co.in, phone +91 98765 43210, pw {password}")
    if kind == "network":
        raise ConnectionError("connection refused")
    if kind == "stopped":
        print("STOPPED: something stopped for mail rahul.sharma@acme.co.in (pw {password})")
        print("after the stop")
    code = int(os.environ.get("CHILD_EXIT", "1"))
    if code:
        sys.exit(code)

run_cli(main)
print("sentry_sdk loaded:", "sentry_sdk" in sys.modules)
"""


def selftest() -> bool:
    checks = []

    def expect(label, condition):
        checks.append(bool(condition))
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    with tempfile.TemporaryDirectory() as folder:
        script = os.path.join(folder, "child.py")
        with open(script, "w", encoding="utf-8") as file:
            file.write(CHILD.format(root=ROOT, password=FAKE_PASSWORD))

        def child(dsn: str, kind: str = "stopped", code: int = 1, unbuffered: bool = True, test_file: bool = True,
                  argv: tuple = ("run", "2026-10", "T4")):
            events = os.path.join(folder, f"events-{time.monotonic_ns()}.jsonl")
            env = {**os.environ, "SENTRY_DSN": dsn, "CHILD_KIND": kind, "CHILD_EXIT": str(code),
                   "PYTHONIOENCODING": "utf-8", "BRAIN_LOG_DIR": folder, "CUSTOMER_KEY": "selftest-sentry",
                   "SENTRY_RELEASE": "selftest-release", "SELFTEST_USER_PASSWORD": FAKE_PASSWORD}
            env.pop("BRAIN_SENTRY_TEST_FILE", None)
            env.pop("PYTHONUNBUFFERED", None)
            if unbuffered:
                env["PYTHONUNBUFFERED"] = "1"                      # as run_for starts its steps
            if test_file:
                env["BRAIN_SENTRY_TEST_FILE"] = events
            started = time.monotonic()
            done = subprocess.run([sys.executable, script, *argv], env=env, cwd=ROOT, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=60)
            sent = []
            if os.path.exists(events):
                with open(events, encoding="utf-8") as file:
                    sent = [json.loads(line) for line in file if line.strip()]
            return done.returncode, done.stdout, sent, time.monotonic() - started

        same = []
        for kind, code in (("stopped", 1), ("plain", 0), ("plain", 2), ("crash", 1), ("network", 3), ("alert", 0)):
            for unbuffered in (True, False):
                without, with_dsn = child("", kind, code, unbuffered), child(FAKE_DSN, kind, code, unbuffered)
                strip = lambda out: b"\n".join(l for l in out.splitlines() if not l.startswith(b"sentry_sdk loaded:"))
                if kind == "crash":                         # the traceback's own lines are the same too
                    strip_tb = lambda out: strip(out)
                else:
                    strip_tb = strip
                same.append((kind, unbuffered, without[0] == with_dsn[0] and strip_tb(without[1]) == strip_tb(with_dsn[1])))
        expect("a command prints the SAME bytes (stdout + stderr, buffered and unbuffered, partial lines, flush=True, "
               "₹ / Hindi in UTF-8, the 'would buy data' and STOPPED lines) and exits with the SAME code with "
               "SENTRY_DSN as without it: STOPPED exit 1, exit 0, exit 2, a crash, a network drop (exit 3), an alert",
               all(ok for *_, ok in same))

        code, out, sent, _ = child("", "plain", 0)
        loaded_on = child(FAKE_DSN, "plain", 0)[1]
        expect("SENTRY_DSN unset -> sentry_sdk is never imported and nothing is written; set -> it is",
               b"sentry_sdk loaded: False" in out and sent == [] and b"sentry_sdk loaded: True" in loaded_on)

        code, out, sent, _ = child(FAKE_DSN, "crash", 1)
        crash = sent[0] if sent else {}
        text = json.dumps(sent)
        frames = ((crash.get("exception") or {}).get("values") or [{}])[-1].get("stacktrace", {}).get("frames", [])
        tags = crash.get("tags") or {}
        expect("a crash -> ONE error event with its traceback (frames, no local variables) and the tags customer, "
               "command (script + subcommand, never the other arguments), run, task, worker, kind; environment "
               "test / live, release",
               len(sent) == 1 and crash.get("level") == "error" and frames and not any("vars" in f for f in frames)
               and tags.get("kind") == "crash" and tags.get("customer") == "selftest-sentry"
               and tags.get("command") == "child.py run" and tags.get("run") == "selftest-sentry-2026-10"
               and tags.get("task") == "T4" and tags.get("worker") == socket.gethostname()
               and crash.get("environment") in ("test", "live") and crash.get("release") == "selftest-release")
        expect("masked like the brain log: no email, phone or password anywhere in the event; no request, "
               "breadcrumbs, user or extra",
               "rahul.sharma@acme.co.in" not in text and "98765 43210" not in text and FAKE_PASSWORD not in text
               and "rah***@***" in text and "********" in text
               and not any(k in crash for k in ("request", "breadcrumbs", "user", "extra")))

        code, out, sent, _ = child(FAKE_DSN, "stopped", 1)
        stop = sent[0] if sent else {}
        expect("a STOPPED exit -> ONE warning with the STOPPED line (masked), kind stopped, exit 1",
               len(sent) == 1 and stop.get("level") == "warning" and (stop.get("tags") or {}).get("kind") == "stopped"
               and (stop.get("tags") or {}).get("exit") == "1"
               and stop.get("message", (stop.get("logentry") or {}).get("message", "")).startswith(
                   "STOPPED: something stopped for mail rah***@***")
               and FAKE_PASSWORD not in json.dumps(stop))
        ok_exit, two_exit = child(FAKE_DSN, "plain", 0)[2], child(FAKE_DSN, "plain", 2)[2]
        network = child(FAKE_DSN, "network", 3)[2]
        expect("exit 0 -> nothing; exit 2 without a STOPPED line (a stall / dead job: their own alert) -> nothing; "
               "a network drop -> ONE warning, kind network, exit 3",
               ok_exit == [] and two_exit == [] and len(network) == 1
               and (network[0].get("tags") or {}).get("kind") == "network"
               and (network[0].get("tags") or {}).get("exit") == "3")
        alert = child(FAKE_DSN, "alert", 0)[2]
        expect("report_event (campaign_stalled / job_dead) -> ONE event with its kind, run and task",
               len(alert) == 1 and (alert[0].get("tags") or {}).get("kind") == "campaign_stalled"
               and (alert[0].get("tags") or {}).get("task") == "T8")

        closed = socket.socket()
        closed.bind(("127.0.0.1", 0))
        closed_port = closed.getsockname()[1]
        closed.close()                                              # nothing listens: connection refused
        silent = socket.socket()
        silent.bind(("127.0.0.1", 0))
        silent.listen(5)
        held = []
        def hold():
            try:
                while True:
                    held.append(silent.accept())         # accept, then never answer
            except OSError:
                pass                                     # the socket was closed at the end of the test
        threading.Thread(target=hold, daemon=True).start()
        base = child("", "stopped", 1, test_file=False)
        refused = child(f"https://k@127.0.0.1:{closed_port}/1", "stopped", 1, test_file=False)
        hanging = child(f"https://k@127.0.0.1:{silent.getsockname()[1]}/1", "stopped", 1, test_file=False)
        silent.close()
        strip = lambda out: b"\n".join(l for l in out.splitlines() if not l.startswith(b"sentry_sdk loaded:"))
        expect("Sentry down (port closed / a server that never answers): the command prints the same, exits the same "
               "and is at most ~2 s slower",
               all(r[0] == base[0] and strip(r[1]) == strip(base[1]) for r in (refused, hanging))
               and refused[3] - base[3] < 3.5 and hanging[3] - base[3] < 3.5)
        bad = child("not a dsn", "stopped", 1, test_file=False)
        expect("a broken SENTRY_DSN -> Sentry is simply off: same output, same exit code",
               bad[0] == base[0] and strip(bad[1]) == strip(base[1]))

    from orch.brain_sentry import Tee, before_send
    import io
    real = io.StringIO()
    tee = Tee(real)
    tee.write("STOPPED: first\nmiddle\n  STOPPED: second")
    tee.flush()
    expect("Tee: writes go through unchanged; the last STOPPED line is kept (also an unfinished one)",
           real.getvalue() == "STOPPED: first\nmiddle\n  STOPPED: second" and tee.last_stop() == "STOPPED: second"
           and tee.getvalue() == real.getvalue())
    expect("before_send: an event that cannot be cleaned is not sent", before_send(object(), None) is None)
    ok = all(checks)
    print(f"BRAIN SENTRY SELFTEST {'OK' if ok else 'FAILED'} ({sum(checks)} of {len(checks)})")
    return ok
