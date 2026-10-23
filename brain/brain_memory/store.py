"""
brain_memory/store.py  -  ALL storage of the brain's memory (so a table or CRM later changes only this file).

    draft     data/memory/<customer_key>_memory.json (local file, git-ignored): every build of every month is kept
              ('entries', newest last); written temp file + rename, never half a file
    approved  a brain_decisions row: kind 'memory', status answered, decided_by 'user', answer 'approved',
              run_id = that month's run, reason 'approved by <name>', question = the entry (facts, lessons,
              approved_by, approved_at, sha256). Never an open decision. Read back by lessons_text() (read-only)
"""

import datetime
import hashlib
import json
import os
import re

MEMORY_DIR = os.path.join("data", "memory")
KIND = "memory"
CORE = ("month", "run_key", "test", "facts", "lessons")       # what the sha256 covers (and what approval stores)
CUSTOMER_KEY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class Stop(Exception):
    """A problem the person can fix; printed as one line, exit code 1."""


def customer() -> str:
    key = (os.getenv("CUSTOMER_KEY") or "").strip()
    if not CUSTOMER_KEY.match(key):
        raise Stop(f"STOPPED: CUSTOMER_KEY is missing or not a valid key ('{key}').")
    return key


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def draft_path(key: str) -> str:
    return os.path.join(MEMORY_DIR, f"{key.replace('-', '_')}_memory.json")


def read_drafts(key: str) -> dict:
    path = draft_path(key)
    if not os.path.exists(path):
        return {"customer": key, "entries": []}
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def save_drafts(key: str, data: dict) -> None:
    path = draft_path(key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def fingerprint(entry: dict) -> str:
    """sha256 of what was approved: the month, the run, the facts and the lessons."""
    core = {k: entry.get(k) for k in CORE}
    return hashlib.sha256(json.dumps(core, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def save_approved(run_id: int, entry: dict, name: str, record=None) -> dict:
    """The approved entry as ONE answered brain_decisions row (kind 'memory'). Returns the row."""
    if record is None:
        from state_store import record_decision as record
    approved = {**entry, "status": "approved", "approved_by": name, "approved_at": now(), "sha256": fingerprint(entry)}
    return record(run_id, KIND, "approved", "user", f"approved by {name}", question=approved)


def approved_rows(key: str, reads=None) -> list[dict]:
    """Every approved memory row of this customer whose sha256 still matches (read-only), oldest first."""
    if reads is None:
        from types import SimpleNamespace
        from state_store import get_decisions, get_runs           # reads only
        reads = SimpleNamespace(get_runs=get_runs, get_decisions=get_decisions)
    rows = []
    for run in reads.get_runs(key):
        for decision in reads.get_decisions(run["id"]):
            entry = decision.get("question") or {}
            if (decision.get("kind") == KIND and decision.get("status") == "answered"
                    and decision.get("answer") == "approved" and entry.get("sha256") == fingerprint(entry)):
                rows.append(entry)
    return rows
