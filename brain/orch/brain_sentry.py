"""
orch/brain_sentry.py  -  optional error tracking (Sentry). ONLY when SENTRY_DSN is set; unset = this file is never
imported and nothing changes. Started by quickicp.run_cli (every command); nothing else imports it.

WHAT IS SENT
    a crash (with its traceback), a STOPPED exit (its STOPPED line), a network drop (exit 3), and the
    campaign_stalled / job_dead alerts (quickicp.report_event). Tags: customer, run, task, command, worker, kind;
    environment test / live (TEST_MODE); release = SENTRY_RELEASE, else the git sha if known.

WHAT IS NEVER SENT
    every text goes through brain_log.mask() (emails, phones, secret values from the environment); no request
    bodies, no breadcrumbs, no local variables, no user / PII, no command arguments (they can hold notes).

NEVER STOPS OR SLOWS A COMMAND
    every call here is wrapped; the events go out on Sentry's background thread; at the end at most
    SHUTDOWN_SECONDS are spent sending what is left. An unreachable Sentry only loses the event.

STDOUT
    while it runs, sys.stdout is a pass-through: every write and flush goes to the real stream unchanged (same
    text, same encoding, same buffering), only the last line starting with STOPPED is remembered.

    python orch/brain_sentry.py selftest      fake transport + local sockets only: no network
"""

import os
import re
import socket
import subprocess
import sys

SHUTDOWN_SECONDS = 2
STOP_LINE = re.compile(r"^\s*(STOPPED|SAFETY STOP)\b")
MONTH = re.compile(r"^\d{4}-\d{2}(-r\d+)?$")
TASK = re.compile(r"^T\d+$")
WORD = re.compile(r"^[a-z][a-z_-]{0,30}$")
TEST_FILE = "BRAIN_SENTRY_TEST_FILE"          # selftest only: write events to this file instead of sending them


class Tee:
    """sys.stdout as it was, plus: remember the last STOPPED line. Every other attribute is the real stream's."""

    def __init__(self, stream):
        self._stream, self._partial, self.stopped = stream, "", None

    def write(self, text):
        written = self._stream.write(text)
        try:
            lines = (self._partial + text).split("\n")
            self._partial = lines.pop()[-2000:]
            for line in lines:
                if STOP_LINE.match(line):
                    self.stopped = line.strip()[:1000]
        except Exception:
            pass
        return written

    def flush(self):
        return self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def last_stop(self):
        return self._partial.strip()[:1000] if STOP_LINE.match(self._partial) else self.stopped


def scrub(value):
    """Every string masked like the brain log (emails, phones, secret values)."""
    from orch.brain_log import mask
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return value


def before_send(event, hint):
    try:
        for key in ("request", "breadcrumbs", "user", "extra"):
            event.pop(key, None)
        for exception in (event.get("exception") or {}).get("values") or []:
            for frame in (exception.get("stacktrace") or {}).get("frames") or []:
                frame.pop("vars", None)
        tags = event.pop("tags", None)       # our own values (customer, command, run, task, worker, kind, exit):
        event = scrub(event)                 # not masked, or CUSTOMER_KEY (a *KEY* setting) would hide the customer
        if tags is not None:
            event["tags"] = tags
        return event
    except Exception:
        return None                                # if it cannot be cleaned, it is not sent


def release() -> str | None:
    if os.getenv("SENTRY_RELEASE"):
        return os.getenv("SENTRY_RELEASE").strip()
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sha = subprocess.run(["git", "-C", here, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=2).stdout.strip()
        return sha or None
    except Exception:
        return None


def tags_for(argv: list[str]) -> dict:
    """customer, command (script + its subcommand word, never the other arguments), run, task, worker."""
    customer = (os.getenv("CUSTOMER_KEY") or "").strip()
    args = argv[1:]
    script = os.path.basename(argv[0]) if argv else "?"
    if script == "__main__.py" and argv:
        script = os.path.basename(os.path.dirname(argv[0]))           # python -m <package>
    command = script + (f" {args[0]}" if args and WORD.match(args[0]) else "")
    month = next((a for a in args if MONTH.match(a)), None)
    task = next((a for a in args if TASK.match(a)), None)
    tags = {"customer": customer or "-", "command": command, "worker": socket.gethostname()}
    if customer and month:
        tags["run"] = f"{customer}-{month}"
    if task:
        tags["task"] = task
    return tags


class FileTransport:
    """Selftest only (BRAIN_SENTRY_TEST_FILE): one JSON line per event, nothing sent."""

    def __new__(cls, path):
        import json
        from sentry_sdk.transport import Transport

        class _File(Transport):
            def capture_envelope(self, envelope):
                for item in envelope.items:
                    event = item.get_event() or item.get_transaction_event()
                    if event:
                        with open(path, "a", encoding="utf-8") as file:
                            file.write(json.dumps(event, default=str) + "\n")
        return _File()


class Session:
    def __init__(self, argv: list[str], test_mode: bool, transport=None):
        import sentry_sdk
        if transport is None and os.getenv(TEST_FILE):
            transport = FileTransport(os.getenv(TEST_FILE))
        self.sdk = sentry_sdk
        sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"), transport=transport, send_default_pii=False,
                        include_local_variables=False, default_integrations=False, auto_enabling_integrations=False,
                        max_breadcrumbs=0, traces_sample_rate=0, attach_stacktrace=False,
                        shutdown_timeout=SHUTDOWN_SECONDS, before_send=before_send,
                        environment="test" if test_mode else "live", release=release())
        self.tags = tags_for(argv)
        for key, value in self.tags.items():
            sentry_sdk.set_tag(key, value)
        self.tee = Tee(sys.stdout)
        sys.stdout = self.tee

    def crash(self, error: BaseException) -> None:
        with self.sdk.new_scope() as scope:
            scope.set_tag("kind", "crash")
            self.sdk.capture_exception(error)

    def exit(self, code) -> None:
        line = self.tee.last_stop()
        if code in (0, None) or not (line or code == 1):
            return
        self.message("stopped", line or f"exit {code}", "warning", {"exit": str(code)})

    def network(self, line: str) -> None:
        self.message("network", line, "warning", {"exit": "3"})

    def message(self, kind: str, text: str, level: str, tags: dict | None = None) -> None:
        with self.sdk.new_scope() as scope:
            scope.set_tag("kind", kind)
            for key, value in (tags or {}).items():
                scope.set_tag(key, str(value))
            scope.fingerprint = [kind, self.tags["command"], re.sub(r"\d+", "N", text)[:120]]
            self.sdk.capture_message(text, level=level)

    def close(self) -> None:
        if sys.stdout is self.tee:
            sys.stdout = self.tee._stream
        self.sdk.flush(timeout=SHUTDOWN_SECONDS)


_SESSION = None


def start(argv: list[str], test_mode: bool, transport=None):
    """The session, or None if Sentry could not be started (then everything stays as without it)."""
    global _SESSION
    try:
        _SESSION = Session(argv, test_mode, transport)
    except Exception:
        _SESSION = None
    return _SESSION


def event(kind: str, text: str, level: str = "warning", tags: dict | None = None) -> None:
    """An alert (campaign_stalled, job_dead) from inside a command; only if run_cli started Sentry."""
    try:
        if _SESSION is not None:
            _SESSION.message(kind, text, level, tags)
    except Exception:
        pass


def call(method: str, *args) -> None:
    """run_cli's calls: never raise."""
    try:
        if _SESSION is not None:
            getattr(_SESSION, method)(*args)
    except Exception:
        pass


def stop() -> None:
    global _SESSION
    try:
        if _SESSION is not None:
            _SESSION.close()
    except Exception:
        pass
    finally:
        _SESSION = None


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if sys.argv[1:] == ["selftest"]:
        from orch.sentry_selftest import selftest
        sys.exit(0 if selftest() else 1)
    sys.exit(1)
