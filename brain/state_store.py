"""
state_store.py
==============

Read and write the 4 brain state tables (runs, tasks, tool calls, decisions). Nothing else.
No tool is ever called from here.

WHY
---
The state store is the single source of truth for where a run stands. Every task, every
external call (step) and every human/brain decision is saved BEFORE acting, so a run can
stop at any point (crash, day 1, waiting for a human) and resume exactly there, and no
paid call is repeated by accident.

WHERE THE STATE LIVES (one setting)
-----------------------------------
    STATE_BACKEND=supabase   (in .env, current) the brain_* tables created by sql/001_brain_state.sql.
    STATE_BACKEND=sqlite     (default when not set) local fallback file data/brain_state.db,
                             Python's built-in sqlite3. Tables are created automatically if missing.
    Switching changes only this file, never the orchestrator: every other file uses the
    functions below, never SQL or Supabase directly.

    The QuickICP company/people rows stay in Supabase exactly as today (supabase_store.py).

TABLES
------
    brain_customer_runs   one customer + one month plan      status: planning/waiting_human/running/done/stopped
    brain_plan_tasks      one row per work order (T1..T13)   status: pending/running/waiting_human/done/failed/stopped
    brain_tool_calls      one row per external tool call     status: running/done/failed/unknown
    brain_decisions       every human / auto / brain decision status: open/answered
    Every column is described (allowed values + example) in sql/001_brain_state.sql.

THE RULES THIS FILE SUPPORTS
----------------------------
    1. Save before acting:  start_step() writes a 'running' row BEFORE the tool is called;
       finish_step() saves the reply afterwards. A 'running' row found on the next run
       means: we crashed in the middle of that call.
    2. Idempotency:  start_step() with a key that already exists returns the SAVED row
       instead of creating a new one. The caller then decides:
           done     -> skip, use the saved output
           running  -> email tool / Quickhowl: resend the saved request (same reference = duplicate: true)
                       QuickICP (no reference): mark_unknown() + open a 'paid_call_unknown' decision
    3. Customers never mix:  QuickICP rows carry the customer column (supabase_store.py). On top of
       that, a step that saves or chooses company/people rows stores owned_rows_output(): {customer,
       run_key, company_ids, people_ids}, so we know which rows belong to which RUN.
       customer_row_ids() reads them back for one customer only.
       finish_step() refuses ids without customer + run_key.
    4. No secrets saved:  a field named exactly like a secret (password, auth_token, ...)
       with a real value stops the save. Masked values ('********') are allowed.

CUSTOMER KEY
------------
    current_customer() reads CUSTOMER_KEY from .env (e.g. anvixa-ai-product): a FIXED key, never
    derived from customer_name, so renaming the customer never splits its data. Format: lowercase
    letters, digits, single dashes. Used for brain_customer_runs.customer (create_run checks the
    format) and for the customer column of the QuickICP tables (supabase_store.py).
    The name shown to prospects stays separate (CUSTOMER_DISPLAY_NAME / understanding.json).

WHO DECIDES WHAT
----------------
    Code : statuses, which task is ready, allowed answers per decision kind, target numbers
    Human: answers open decisions (later: python orchestrator.py answer <id> <answer>)

RUN LOCK (sql/003_brain_run_lock.sql)
--------
    take_run_lock / renew_run_lock / release_run_lock / force_unlock on brain_customer_runs
    (locked_by, locked_until). A lease of 30 minutes: one atomic update takes it only if it is
    free or expired, so a crashed run never blocks forever. SQLite adds the 2 columns itself.

UPDATED_AT
----------
    Set by a database trigger on brain_customer_runs and brain_plan_tasks (SQLite and Supabase).

RUN
---
    python state_store.py check        read-only: state tables readable + row counts, QuickICP tables
                                       readable incl. their customer column
                                       (sqlite: on first use it creates data/brain_state.db with empty tables)
    python state_store.py decisions    read-only: open decisions waiting for a human
    python state_store.py write-test   writes ONLY rows of one selftest-<time> run and deletes them at the end:
                                         supabase -> in the real brain_* tables
                                         sqlite   -> in a throwaway file data/brain_state_selftest.db
                                       Leftovers after a hard crash: runs whose run_key starts with 'selftest-'
                                       (delete_test_run(<id>) removes one; it refuses any other run).

DEBUGGING
---------
    "STOPPED: STATE_BACKEND ..."            -> .env has a value other than sqlite / supabase
    "database is locked"                    -> another python process holds the file; close it
    "UNIQUE constraint failed ... open"     -> an open decision of that kind already exists;
                                               use get_open_decision() / list_open_decisions()
    "STOPPED: decision N is not open"       -> already answered; see brain_decisions row N
    "STOPPED: secret field ..."             -> strip secrets before start_step()/finish_step()
    "STOPPED: CUSTOMER_KEY is missing"      -> add CUSTOMER_KEY=<key> to .env
    "STOPPED: customer key ... not valid"   -> lowercase, digits and dashes only, e.g. anvixa-ai-product
    Look inside the file: any SQLite viewer (e.g. "DB Browser for SQLite") on data/brain_state.db.
"""

import json
import os
import re
import socket
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from orch import brain_log      # file only; never prints, never raises
from quickicp import ToolError, count_in

load_dotenv()

STATE_BACKEND = (os.getenv("STATE_BACKEND") or "sqlite").strip().lower()
STATE_DB = "data/brain_state.db"
SELFTEST_DB = "data/brain_state_selftest.db"

RUNS = "brain_customer_runs"
TASKS = "brain_plan_tasks"
STEPS = "brain_tool_calls"
DECISIONS = "brain_decisions"

RUN_STATUSES = {"planning", "waiting_human", "running", "done", "stopped"}
TASK_STATUSES = {"pending", "running", "waiting_human", "done", "failed", "stopped"}
STEP_STATUSES = {"running", "done", "failed", "unknown"}

# Code decides which answers are valid for each kind of decision.
ALLOWED_ANSWERS = {
    "plan_approval": {"yes", "no"},
    "goal_check": {"continue", "more", "stop"},
    "email_approve": {"yes", "no", "feedback"},       # feedback = a note to the email tool (needs a note)
    "call_start": {"yes", "no"},
    "paid_call_unknown": {"rerun", "skip", "stop"},   # crash during a QuickICP call
    "task_failed": {"retry", "skip", "stop"},          # tool kept failing after its retries
    "brain_review": {"done", "retry", "ask_human"},    # recorded by the brain, never left open
    "human_task": {"done", "skip", "stop"},            # a person does the task (CAM, training, ...)
    "human_gate_task": {"done", "stop"},               # same, but a gate (approval, content, training,
                                                       # readiness): it can never be skipped
    "goal_check_final": {"continue", "stop"},          # goal check after the last allowed 'more'
    "run_review": {"saved"},                           # the brain's review of a run (recorded, never open)
    "memory": {"approved"},                            # approved lessons of a month (brain_memory; recorded, never open)
    "campaign_stalled": {"raised"},                    # a campaign day with expected activity but none (campaign_monitor; recorded, never open)
    "job_dead": {"raised"},                            # a brain_jobs job failed max_attempts times (recorded, never open)
    "review_action_1": {"accept", "reject"},           # a suggested action from a review (human decides;
    "review_action_2": {"accept", "reject"},           #  accepting only records the choice)
    "review_action_3": {"accept", "reject"},
}

# Exact field names (lowercase) that must never be saved with a real value.
SECRET_FIELDS = {
    "password", "secret", "client_secret", "token", "auth_token", "access_token", "refresh_token",
    "api_key", "apikey", "authorization", "x-key", "x-revenueos-key", "service_role_key",
}
MASKED_VALUE = re.compile(r"^\*+$")   # '********' is a masked secret: allowed

# Customer key format (CUSTOMER_KEY in .env), e.g. 'anvixa-ai-product'.
CUSTOMER_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CUSTOMER_KEY_MAX = 60

# Columns stored as JSON text in SQLite (jsonb in Supabase) and as 0/1 in SQLite (boolean in Supabase).
JSON_COLUMNS = {
    RUNS: {"understanding", "plan"},
    TASKS: {"depends_on", "work_order", "result"},
    STEPS: {"request", "response", "output"},
    DECISIONS: {"question"},
}
BOOL_COLUMNS = {RUNS: {"test_mode"}, STEPS: {"paid"}}

NOW_SQL = "(strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))"   # UTC with milliseconds

# Same tables, columns, rules and names as sql/001_brain_state.sql (column descriptions are there:
# SQLite has no COMMENT ON). JSON is stored as TEXT, booleans as 0/1, times as UTC ISO text.
SQLITE_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS brain_customer_runs (
  id            INTEGER,
  run_key       TEXT NOT NULL,
  customer      TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'planning',
  understanding TEXT,
  plan          TEXT,
  test_mode     INTEGER NOT NULL DEFAULT 1,
  stop_reason   TEXT,
  locked_by     TEXT,
  locked_until  TEXT,
  created_at    TEXT NOT NULL DEFAULT {NOW_SQL},
  updated_at    TEXT NOT NULL DEFAULT {NOW_SQL},
  CONSTRAINT brain_customer_runs_pkey           PRIMARY KEY (id AUTOINCREMENT),
  CONSTRAINT brain_customer_runs_run_key_unique UNIQUE (run_key),
  CONSTRAINT brain_customer_runs_status_check
    CHECK (status IN ('planning','waiting_human','running','done','stopped'))
);
CREATE TRIGGER IF NOT EXISTS brain_customer_runs_set_updated_at AFTER UPDATE ON brain_customer_runs
BEGIN UPDATE brain_customer_runs SET updated_at = {NOW_SQL} WHERE id = NEW.id; END;

CREATE TABLE IF NOT EXISTS brain_plan_tasks (
  id              INTEGER,
  run_id          INTEGER NOT NULL,
  task_id         TEXT NOT NULL,
  agent           TEXT NOT NULL,
  output_type     TEXT NOT NULL,
  depends_on      TEXT NOT NULL DEFAULT '[]',
  week            INTEGER,
  work_order      TEXT NOT NULL,
  target_number   INTEGER,
  achieved_number INTEGER,
  status          TEXT NOT NULL DEFAULT 'pending',
  attempts        INTEGER NOT NULL DEFAULT 0,
  waiting_for     TEXT,
  result          TEXT,
  last_error      TEXT,
  started_at      TEXT,
  finished_at     TEXT,
  created_at      TEXT NOT NULL DEFAULT {NOW_SQL},
  updated_at      TEXT NOT NULL DEFAULT {NOW_SQL},
  CONSTRAINT brain_plan_tasks_pkey            PRIMARY KEY (id AUTOINCREMENT),
  CONSTRAINT brain_plan_tasks_run_id_fkey     FOREIGN KEY (run_id) REFERENCES brain_customer_runs(id),
  CONSTRAINT brain_plan_tasks_run_task_unique UNIQUE (run_id, task_id),
  CONSTRAINT brain_plan_tasks_status_check
    CHECK (status IN ('pending','running','waiting_human','done','failed','stopped'))
);
CREATE TRIGGER IF NOT EXISTS brain_plan_tasks_set_updated_at AFTER UPDATE ON brain_plan_tasks
BEGIN UPDATE brain_plan_tasks SET updated_at = {NOW_SQL} WHERE id = NEW.id; END;

CREATE TABLE IF NOT EXISTS brain_tool_calls (
  id              INTEGER,
  task_row_id     INTEGER NOT NULL,
  step            TEXT NOT NULL,
  idempotency_key TEXT NOT NULL,
  tool            TEXT NOT NULL,
  paid            INTEGER NOT NULL DEFAULT 0,
  status          TEXT NOT NULL DEFAULT 'running',
  external_ref    TEXT,
  request         TEXT,
  response        TEXT,
  output          TEXT,
  error           TEXT,
  started_at      TEXT NOT NULL DEFAULT {NOW_SQL},
  finished_at     TEXT,
  CONSTRAINT brain_tool_calls_pkey                   PRIMARY KEY (id AUTOINCREMENT),
  CONSTRAINT brain_tool_calls_task_row_id_fkey       FOREIGN KEY (task_row_id) REFERENCES brain_plan_tasks(id),
  CONSTRAINT brain_tool_calls_idempotency_key_unique UNIQUE (idempotency_key),
  CONSTRAINT brain_tool_calls_status_check
    CHECK (status IN ('running','done','failed','unknown'))
);

CREATE TABLE IF NOT EXISTS brain_decisions (
  id          INTEGER,
  run_id      INTEGER NOT NULL,
  task_row_id INTEGER,
  kind        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',
  question    TEXT,
  answer      TEXT,
  decided_by  TEXT,
  reason      TEXT,
  created_at  TEXT NOT NULL DEFAULT {NOW_SQL},
  answered_at TEXT,
  CONSTRAINT brain_decisions_pkey             PRIMARY KEY (id AUTOINCREMENT),
  CONSTRAINT brain_decisions_run_id_fkey      FOREIGN KEY (run_id) REFERENCES brain_customer_runs(id),
  CONSTRAINT brain_decisions_task_row_id_fkey FOREIGN KEY (task_row_id) REFERENCES brain_plan_tasks(id),
  CONSTRAINT brain_decisions_status_check     CHECK (status IN ('open','answered'))
);
CREATE UNIQUE INDEX IF NOT EXISTS brain_decisions_one_open_question
  ON brain_decisions (run_id, coalesce(task_row_id, 0), kind) WHERE status = 'open';
"""


# ================= SMALL HELPERS =================

def now_iso() -> str:
    """Current UTC time with milliseconds, e.g. '2026-10-09T10:30:00.123+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def check_customer_key(key: str) -> str:
    """Return key if it is a valid customer key (lowercase letters, digits, single dashes); stop otherwise."""
    if not key or len(key) > CUSTOMER_KEY_MAX or not CUSTOMER_KEY_PATTERN.match(key):
        raise ToolError(f"STOPPED: customer key '{key}' is not valid. Use lowercase letters, digits and "
                        f"single dashes (max {CUSTOMER_KEY_MAX}), e.g. anvixa-ai-product.")
    return key


def current_customer() -> str:
    """
    The fixed customer key from CUSTOMER_KEY in .env, e.g. 'anvixa-ai-product'.
    Never derived from a display name, so renaming the customer never splits its data.
    Used for brain_customer_runs.customer and the customer column of the QuickICP tables.
    """
    key = (os.getenv("CUSTOMER_KEY") or "").strip()
    if not key:
        raise ToolError("STOPPED: CUSTOMER_KEY is missing in .env (e.g. CUSTOMER_KEY=anvixa-ai-product). "
                        "It keeps each customer's data apart; never change it for an existing customer.")
    return check_customer_key(key)


def target_number(target_text: str) -> int | None:
    """Largest COUNT in a work order target ('150-200 accounts' -> 200); None if it has none. A percent is a
    share, not a count ('100% of contacts' -> None): runner_kit.percent_target resolves it at run time."""
    return count_in(target_text)


def assert_no_secrets(data, path: str = "") -> None:
    """Raise if a field named exactly like a secret (see SECRET_FIELDS) has a real, unmasked value."""
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in SECRET_FIELDS and value not in (None, "") \
                    and not (isinstance(value, str) and MASKED_VALUE.match(value)):
                raise ToolError(f"STOPPED: secret field '{path}{key}' must not be saved in the state store.")
            assert_no_secrets(value, f"{path}{key}.")
    elif isinstance(data, list):
        for item in data:
            assert_no_secrets(item, path)


def check_value(value: str, allowed: set[str], what: str) -> None:
    """Raise a clear error if value is not one of the allowed values."""
    if value not in allowed:
        raise ToolError(f"STOPPED: '{value}' is not a valid {what}. Use one of: {', '.join(sorted(allowed))}")


# ================= BACKENDS (the only code that knows SQLite or Supabase) =================
# Both offer the same 5 methods. 'where' is {column: value}; value None means IS NULL.

class SqliteBackend:
    """State in a local SQLite file. Tables are created if missing."""

    def __init__(self, path: str):
        """Open (or create) the file and make sure all tables exist."""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.path = path
        # isolation_level=None: every statement commits at once, except inside BEGIN ... COMMIT below.
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SQLITE_SCHEMA)
        # Older files (before sql/003): add the run lock columns if they are missing.
        columns = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({RUNS})")}
        for column in ("locked_by", "locked_until"):
            if column not in columns:
                self.conn.execute(f"ALTER TABLE {RUNS} ADD COLUMN {column} TEXT")

    @staticmethod
    def _encode(table: str, row: dict) -> dict:
        """Python values -> SQLite values (dict/list -> JSON text, bool -> 0/1)."""
        out = {}
        for column, value in row.items():
            if column in JSON_COLUMNS.get(table, set()) and value is not None:
                value = json.dumps(value, ensure_ascii=False)
            elif column in BOOL_COLUMNS.get(table, set()) and value is not None:
                value = int(bool(value))
            out[column] = value
        return out

    @staticmethod
    def _decode(table: str, row: sqlite3.Row) -> dict:
        """SQLite row -> Python dict (JSON text -> dict/list, 0/1 -> bool)."""
        out = dict(row)
        for column in JSON_COLUMNS.get(table, set()):
            if out.get(column) is not None:
                out[column] = json.loads(out[column])
        for column in BOOL_COLUMNS.get(table, set()):
            if out.get(column) is not None:
                out[column] = bool(out[column])
        return out

    @staticmethod
    def _where(where: dict | None) -> tuple[str, list]:
        """{'run_id': 1, 'task_row_id': None} -> (' WHERE run_id = ? AND task_row_id IS NULL', [1])."""
        if not where:
            return "", []
        parts, params = [], []
        for column, value in where.items():
            if value is None:
                parts.append(f"{column} IS NULL")
            else:
                parts.append(f"{column} = ?")
                params.append(value)
        return " WHERE " + " AND ".join(parts), params

    def select(self, table: str, where: dict | None = None, limit: int | None = None) -> list[dict]:
        """Rows matching where, in id order."""
        clause, params = self._where(where)
        sql = f"SELECT * FROM {table}{clause} ORDER BY id" + (f" LIMIT {int(limit)}" if limit else "")
        return [self._decode(table, row) for row in self.conn.execute(sql, params)]

    def insert(self, table: str, row: dict) -> dict:
        """Insert one row and return it as saved (with id and defaults)."""
        row = self._encode(table, row)
        columns = ", ".join(row)
        marks = ", ".join("?" for _ in row)
        cursor = self.conn.execute(f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values()))
        return self.select(table, {"id": cursor.lastrowid})[0]

    def insert_ignore(self, table: str, rows: list[dict], conflict: str) -> None:
        """Insert rows; rows that clash on the 'conflict' columns are skipped (never overwritten)."""
        rows = [self._encode(table, row) for row in rows]
        columns = ", ".join(rows[0])
        marks = ", ".join("?" for _ in rows[0])
        sql = f"INSERT INTO {table} ({columns}) VALUES ({marks}) ON CONFLICT({conflict}) DO NOTHING"
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            self.conn.executemany(sql, [list(row.values()) for row in rows])
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def update(self, table: str, fields: dict, where: dict) -> list[dict]:
        """Update rows matching where; returns the updated rows ([] if none matched)."""
        fields = self._encode(table, fields)
        clause, params = self._where(where)
        sets = ", ".join(f"{column} = ?" for column in fields)
        self.conn.execute("BEGIN IMMEDIATE")   # lock, so the rows we read are the rows we update
        try:
            ids = [row["id"] for row in self.conn.execute(f"SELECT id FROM {table}{clause}", params)]
            if ids:
                self.conn.execute(f"UPDATE {table} SET {sets}{clause}", list(fields.values()) + params)
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return [self.select(table, {"id": row_id})[0] for row_id in ids]

    def delete(self, table: str, where: dict) -> int:
        """Delete rows matching where (used only by the write test cleanup); returns how many."""
        if not where:
            raise ToolError("STOPPED: delete without a filter is not allowed.")
        clause, params = self._where(where)
        return self.conn.execute(f"DELETE FROM {table}{clause}", params).rowcount

    def take_lock(self, run_id: int, holder: str, until: str, now: str) -> bool:
        """One atomic update: take the run lock only if it is free or expired. True if we got it."""
        cursor = self.conn.execute(
            f"UPDATE {RUNS} SET locked_by = ?, locked_until = ? "
            "WHERE id = ? AND (locked_until IS NULL OR locked_until < ?)", (holder, until, run_id, now))
        return cursor.rowcount == 1

    def close(self) -> None:
        """Close the file (needed on Windows before the file can be deleted)."""
        self.conn.close()


# What this process wrote, for the network-drop line (quickicp.network_stop_line). Read-only bookkeeping:
# 'pending' = a write was sent but its reply never came (it may or may not have been saved).
STATE_WRITES = {"done": 0, "last": None, "pending": None}
HELD_LOCKS: set[int] = set()          # run ids whose lock this process took and has not released yet


def _write(label: str, send):
    """Send one write, keeping STATE_WRITES up to date (no retry, no change to what is written)."""
    STATE_WRITES["pending"] = label
    result = send()
    STATE_WRITES.update(done=STATE_WRITES["done"] + 1, last=label, pending=None)
    return result


class SupabaseBackend:
    """State in Supabase. Only usable after sql/001_brain_state.sql has been run by the database developer."""

    def __init__(self):
        """Reuse the existing Supabase connection from supabase_store.py."""
        from supabase_store import db
        self.db = db

    @staticmethod
    def _filter(query, where: dict | None):
        """Add .eq() / .is_(null) filters for every where item."""
        for column, value in (where or {}).items():
            query = query.is_(column, "null") if value is None else query.eq(column, value)
        return query

    def select(self, table: str, where: dict | None = None, limit: int | None = None) -> list[dict]:
        """Rows matching where, in id order."""
        query = self._filter(self.db.table(table).select("*"), where).order("id")
        if limit:
            query = query.limit(limit)
        return query.execute().data

    def insert(self, table: str, row: dict) -> dict:
        """Insert one row and return it as saved."""
        return _write(f"{table} insert", lambda: self.db.table(table).insert(row).execute().data[0])

    def insert_ignore(self, table: str, rows: list[dict], conflict: str) -> None:
        """Insert rows; rows that clash on the 'conflict' columns are skipped (never overwritten)."""
        _write(f"{table} insert", lambda: self.db.table(table).upsert(rows, on_conflict=conflict,
                                                                       ignore_duplicates=True).execute())

    def update(self, table: str, fields: dict, where: dict) -> list[dict]:
        """Update rows matching where; returns the updated rows ([] if none matched)."""
        return _write(f"{table} update", lambda: self._filter(self.db.table(table).update(fields), where).execute().data)

    def delete(self, table: str, where: dict) -> int:
        """Delete rows matching where (used only by the write test cleanup); returns how many."""
        if not where:
            raise ToolError("STOPPED: delete without a filter is not allowed.")
        return _write(f"{table} delete", lambda: len(self._filter(self.db.table(table).delete(), where).execute().data))

    def take_lock(self, run_id: int, holder: str, until: str, now: str) -> bool:
        """One atomic update: take the run lock only if it is free or expired. True if we got it."""
        rows = _write(f"{RUNS} lock", lambda: self.db.table(RUNS).update({"locked_by": holder, "locked_until": until})
                      .eq("id", run_id).or_(f"locked_until.is.null,locked_until.lt.{now}").execute().data)
        return len(rows) == 1

    def close(self) -> None:
        """Nothing to close."""


_backend = None


def backend():
    """The backend chosen by STATE_BACKEND, opened once and reused."""
    global _backend
    if _backend is None:
        if STATE_BACKEND == "sqlite":
            _backend = SqliteBackend(STATE_DB)
        elif STATE_BACKEND == "supabase":
            _backend = SupabaseBackend()
        else:
            raise ToolError(f"STOPPED: STATE_BACKEND '{STATE_BACKEND}' is unknown. Use sqlite or supabase.")
    return _backend


def first(rows: list[dict]) -> dict | None:
    """First row of a result, or None."""
    return rows[0] if rows else None


# ================= RUNS =================

def get_run(run_key: str) -> dict | None:
    """The run with this key, or None."""
    return first(backend().select(RUNS, {"run_key": run_key}))


def create_run(run_key: str, customer: str, test_mode: bool, understanding: dict | None = None) -> dict:
    """
    Create the run, or return it unchanged if it already exists (safe to call on every start).
    customer must be a valid customer key: pass current_customer().
    """
    check_customer_key(customer)
    existing = get_run(run_key)
    if existing:
        return existing
    return backend().insert(RUNS, {"run_key": run_key, "customer": customer,
                                   "test_mode": test_mode, "understanding": understanding})


def get_runs(customer: str) -> list[dict]:
    """All runs of one customer, oldest first."""
    return backend().select(RUNS, {"customer": check_customer_key(customer)})


def update_run(run_id: int, **fields) -> dict:
    """Update run fields, e.g. update_run(1, status='running', plan={...})."""
    if "status" in fields:
        check_value(fields["status"], RUN_STATUSES, "run status")
    return backend().update(RUNS, fields, {"id": run_id})[0]


# ================= RUN LOCK (lease, sql/003_brain_run_lock.sql) =================

LOCK_MINUTES = 30


def lock_time(minutes: float = 0) -> str:
    """UTC time as text the database compares correctly, e.g. '2026-10-10T11:30:00.000Z'."""
    moment = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def lock_holder() -> str:
    """A name for this process: <computer>:<process id>:<random>."""
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def take_run_lock(run_id: int, holder: str, minutes: float = LOCK_MINUTES) -> None:
    """Take the run lock (free or expired only), or stop with who holds it and until when."""
    HELD_LOCKS.add(run_id)                     # noted BEFORE sending: a lost reply may still have taken it
    if not backend().take_lock(run_id, holder, lock_time(minutes), lock_time()):
        HELD_LOCKS.discard(run_id)
        run = first(backend().select(RUNS, {"id": run_id})) or {}
        raise ToolError(f"STOPPED: run is busy: locked by {run.get('locked_by')} until {run.get('locked_until')}. "
                        "Wait for it to finish, or after a crash: python orchestrator.py unlock <YYYY-MM>")


def renew_run_lock(run_id: int, holder: str, minutes: float = LOCK_MINUTES) -> None:
    """Extend our lock (before a long or paid call). Stop if we no longer hold it."""
    if not backend().update(RUNS, {"locked_until": lock_time(minutes)}, {"id": run_id, "locked_by": holder}):
        raise ToolError("STOPPED: lost the run lock (it expired and another run took it). Nothing more was done.")


def release_run_lock(run_id: int, holder: str) -> bool:
    """Free the lock if we hold it. True if released."""
    released = bool(backend().update(RUNS, {"locked_by": None, "locked_until": None}, {"id": run_id, "locked_by": holder}))
    HELD_LOCKS.discard(run_id)
    return released


def force_unlock(run_id: int) -> None:
    """Free the lock whoever holds it (human command after a crash)."""
    backend().update(RUNS, {"locked_by": None, "locked_until": None}, {"id": run_id})
    HELD_LOCKS.discard(run_id)


# ================= TASKS =================

def add_tasks(run_id: int, work_orders: list[dict]) -> int:
    """
    Save the approved work orders as tasks (status 'pending').
    Tasks that already exist for this run are left untouched, so their status is never reset.
    Returns how many tasks the run has now.
    """
    rows = [{
        "run_id": run_id,
        "task_id": wo["task_id"],
        "agent": wo["agent"],
        "output_type": wo["output_type"],
        "depends_on": wo.get("depends_on", []),
        "week": wo.get("week"),
        "work_order": wo,
        "target_number": target_number(wo.get("target", "")),
    } for wo in work_orders]
    if rows:
        backend().insert_ignore(TASKS, rows, "run_id,task_id")
    return len(get_tasks(run_id))


def get_tasks(run_id: int) -> list[dict]:
    """All tasks of a run, in id order."""
    return backend().select(TASKS, {"run_id": run_id})


def get_task(run_id: int, task_id: str) -> dict | None:
    """One task by its plan id (e.g. 'T3'), or None."""
    return first(backend().select(TASKS, {"run_id": run_id, "task_id": task_id}))


def update_task(task_row_id: int, **fields) -> dict:
    """Update task fields, e.g. update_task(5, status='waiting_human', waiting_for='email_approve')."""
    if "status" in fields:
        check_value(fields["status"], TASK_STATUSES, "task status")
    return backend().update(TASKS, fields, {"id": task_row_id})[0]


def ready_tasks(run_id: int) -> list[dict]:
    """
    Every 'pending' task whose depends_on tasks are all 'done' (code decides, not the LLM),
    earliest plan week first, then plan order.
    """
    tasks = get_tasks(run_id)
    done = {t["task_id"] for t in tasks if t["status"] == "done"}
    ready = [t for t in tasks if t["status"] == "pending" and all(dep in done for dep in t["depends_on"])]
    return sorted(ready, key=lambda t: (t["week"] if t["week"] is not None else 99, t["id"]))


def next_ready_task(run_id: int) -> dict | None:
    """The first of ready_tasks(), or None if no task is ready."""
    return first(ready_tasks(run_id))


# ================= STEPS (external calls) =================

def get_step(idempotency_key: str) -> dict | None:
    """The step with this key, or None."""
    return first(backend().select(STEPS, {"idempotency_key": idempotency_key}))


def start_step(task_row_id: int, step: str, idempotency_key: str, tool: str,
               paid: bool, request: dict | None = None) -> tuple[dict, bool]:
    """
    Save a step as 'running' BEFORE the tool is called.
    Returns (row, is_new). is_new False = this key was used before: read row['status']
    and do NOT call the tool blindly (see the module header).
    """
    existing = get_step(idempotency_key)
    if existing:
        if existing["task_row_id"] != task_row_id:
            # Never hand one task another task's call (its reply would be reused silently).
            raise ToolError(f"STOPPED: key {idempotency_key} is already used by another task "
                            f"(task row {existing['task_row_id']}). References must be unique.")
        brain_log.ledger(existing, "seen before")
        return existing, False
    assert_no_secrets(request)
    row = {"task_row_id": task_row_id, "step": step, "idempotency_key": idempotency_key,
           "tool": tool, "paid": paid, "status": "running", "request": request}
    saved = backend().insert(STEPS, row)
    brain_log.ledger(saved, "started")
    return saved, True


def owned_rows_output(run: dict, company_ids: list[int] | None = None,
                      people_ids: list[int] | None = None, **extra) -> dict:
    """
    The step output for a step that saved or chose QuickICP rows: records which company/people
    row ids belong to this run and customer (the QuickICP tables have no customer column yet).
    """
    return {"customer": run["customer"], "run_key": run["run_key"],
            "company_ids": sorted(set(company_ids or [])),
            "people_ids": sorted(set(people_ids or [])), **extra}


def finish_step(step_id: int, status: str, response: dict | None = None, output=None,
                external_ref: str | None = None, error: str | None = None) -> dict:
    """Save the result of a step: status 'done' or 'failed', with the reply and what it produced."""
    check_value(status, STEP_STATUSES - {"running"}, "finished step status")
    assert_no_secrets(response)
    assert_no_secrets(output)
    if isinstance(output, dict) and {"company_ids", "people_ids"} & output.keys() \
            and not (output.get("customer") and output.get("run_key")):
        raise ToolError("STOPPED: step output lists company/people ids without customer and run_key. "
                        "Build it with owned_rows_output().")
    fields = {"status": status, "response": response, "output": output,
              "error": error, "finished_at": now_iso()}
    if external_ref is not None:
        fields["external_ref"] = external_ref
    saved = backend().update(STEPS, fields, {"id": step_id})[0]
    brain_log.ledger(saved, "finished", "INFO" if status == "done" else "WARNING")
    return saved


def mark_unknown(step_id: int, reason: str) -> dict:
    """A 'running' step found after a crash, for a tool with no reference: we cannot know if it ran."""
    saved = backend().update(STEPS, {"status": "unknown", "error": reason, "finished_at": now_iso()},
                             {"id": step_id})[0]
    brain_log.ledger(saved, "marked unknown (crash during a paid call)", "WARNING")
    return saved


def get_steps(task_row_id: int) -> list[dict]:
    """All steps of a task, in the order they were started."""
    return backend().select(STEPS, {"task_row_id": task_row_id})


def customer_row_ids(customer: str, kind: str) -> set[int]:
    """Every company_ids or people_ids recorded by DONE steps for this customer only."""
    check_value(kind, {"company_ids", "people_ids"}, "row id kind")
    ids = set()
    for run in backend().select(RUNS, {"customer": customer}):
        for task in get_tasks(run["id"]):
            for step in backend().select(STEPS, {"task_row_id": task["id"], "status": "done"}):
                output = step["output"]
                if isinstance(output, dict) and output.get("customer") == customer:
                    ids.update(output.get(kind, []))
    return ids


# ================= DECISIONS =================

def get_open_decision(run_id: int, kind: str, task_row_id: int | None = None) -> dict | None:
    """The open decision of this kind for this run (and task), or None."""
    return first(backend().select(DECISIONS, {"run_id": run_id, "kind": kind,
                                              "status": "open", "task_row_id": task_row_id}))


def open_decision(run_id: int, kind: str, question: dict, task_row_id: int | None = None) -> dict:
    """
    Ask a human: save an open decision. If the same question is already open, return that one.
    question['allowed_answers'] may narrow the answers for THIS question (e.g. no 'feedback' when no rounds
    are left); it is always kept inside the kind's ALLOWED_ANSWERS, and answer_decision enforces it.
    """
    check_value(kind, set(ALLOWED_ANSWERS) - {"brain_review", "run_review", "memory", "campaign_stalled", "job_dead"}, "decision kind to ask")
    existing = get_open_decision(run_id, kind, task_row_id)
    if existing:
        return existing
    offered = set(question.get("allowed_answers") or ALLOWED_ANSWERS[kind]) & ALLOWED_ANSWERS[kind]
    question = {**question, "allowed_answers": sorted(offered or ALLOWED_ANSWERS[kind])}
    return backend().insert(DECISIONS, {"run_id": run_id, "task_row_id": task_row_id,
                                        "kind": kind, "question": question})


def answer_decision(decision_id: int, answer: str, decided_by: str = "user", reason: str | None = None) -> dict:
    """
    Answer an open decision. Only an OPEN decision can be answered, and only with an allowed answer.
    The next run reads the answer and continues the waiting task.
    """
    decision = first(backend().select(DECISIONS, {"id": decision_id}))
    if not decision:
        raise ToolError(f"STOPPED: decision {decision_id} does not exist.")
    if decision["status"] != "open":
        raise ToolError(f"STOPPED: decision {decision_id} is not open (answered '{decision['answer']}' "
                        f"by {decision['decided_by']} at {decision['answered_at']}).")
    check_value(answer, ALLOWED_ANSWERS[decision["kind"]], f"answer for {decision['kind']}")
    offered = (decision.get("question") or {}).get("allowed_answers")
    if offered and answer not in offered:
        raise ToolError(f"STOPPED: '{answer}' is not offered for decision {decision_id}; "
                        f"answer one of: {', '.join(offered)}.")
    fields = {"status": "answered", "answer": answer, "decided_by": decided_by,
              "reason": reason, "answered_at": now_iso()}
    updated = backend().update(DECISIONS, fields, {"id": decision_id, "status": "open"})
    if not updated:
        raise ToolError(f"STOPPED: decision {decision_id} was answered by someone else just now.")
    return updated[0]


def record_decision(run_id: int, kind: str, answer: str, decided_by: str,
                    reason: str | None = None, question: dict | None = None,
                    task_row_id: int | None = None) -> dict:
    """Save a decision that is already made (brain review, or 'auto' when no human is asked)."""
    check_value(kind, set(ALLOWED_ANSWERS), "decision kind")
    check_value(answer, ALLOWED_ANSWERS[kind], f"answer for {kind}")
    return backend().insert(DECISIONS, {
        "run_id": run_id, "task_row_id": task_row_id, "kind": kind, "status": "answered",
        "question": question, "answer": answer, "decided_by": decided_by,
        "reason": reason, "answered_at": now_iso()})


def get_decisions(run_id: int) -> list[dict]:
    """Every decision of a run (open and answered), oldest first."""
    return backend().select(DECISIONS, {"run_id": run_id})


def list_open_decisions(run_id: int | None = None) -> list[dict]:
    """Every open decision (optionally for one run), oldest first."""
    where = {"status": "open"} if run_id is None else {"status": "open", "run_id": run_id}
    return backend().select(DECISIONS, where)


# ================= READ-ONLY CHECK =================

def check() -> bool:
    """Read every state table and 1 row of each QuickICP table. Writes no rows."""
    where = os.path.abspath(STATE_DB) if STATE_BACKEND == "sqlite" else "Supabase"
    print(f"State backend: {STATE_BACKEND} ({where})")
    ok = True
    for table in (RUNS, TASKS, STEPS, DECISIONS):
        try:
            print(f"  PASS  {table}: {len(backend().select(table))} rows")
        except Exception as error:
            ok = False
            print(f"  FAIL  {table}: {error}")

    print("QuickICP tables in Supabase (read-only, incl. the new customer column):")
    try:
        from supabase_store import COMPANY_TABLE, PEOPLE_TABLE, db
        for table, columns in ((COMPANY_TABLE, "id, customer, website, linkedin, company_name"),
                               (PEOPLE_TABLE, "id, customer, user_social, company_id")):
            db.table(table).select(columns).limit(1).execute()
            print(f"  PASS  {table}")
    except Exception as error:
        ok = False
        print(f"  FAIL  Supabase: {getattr(error, 'message', None) or error}")

    if ok:
        print(f"Open decisions: {len(list_open_decisions())}")
    print("STATE STORE OK" if ok else "STOPPED: see the FAIL lines above.")
    return ok


def print_open_decisions() -> None:
    """Print every open decision and its allowed answers."""
    decisions = list_open_decisions()
    if not decisions:
        print("No open decisions.")
    for d in decisions:
        question = d["question"] or {}
        print(f"#{d['id']}  run {d['run_id']}  task_row {d['task_row_id']}  {d['kind']}  "
              f"answers: {' / '.join(question.get('allowed_answers', []))}")
        print(f"      {question.get('text', '')}")


# ================= WRITE TEST (own test rows only, always deleted) =================

SELFTEST_PREFIX = "selftest-"


def expect(label: str, condition: bool) -> None:
    """Print PASS, or stop the test with the label."""
    if not condition:
        raise AssertionError(label)
    print(f"  PASS  {label}")


def raises(function) -> bool:
    """True if calling function() raises ToolError."""
    try:
        function()
    except ToolError:
        return True
    return False


def later(a: str, b: str) -> bool:
    """True if timestamp a is later than timestamp b (works for SQLite and Supabase formats)."""
    return datetime.fromisoformat(a) > datetime.fromisoformat(b)


def delete_test_run(run_id: int) -> int:
    """Delete one write-test run and every row under it (children first). Refuses runs not named selftest-*."""
    run = first(backend().select(RUNS, {"id": run_id}))
    if not run:
        return 0
    if not run["run_key"].startswith(SELFTEST_PREFIX):
        raise ToolError(f"STOPPED: refusing to delete run '{run['run_key']}': only {SELFTEST_PREFIX}* runs.")
    deleted = backend().delete(DECISIONS, {"run_id": run_id})
    for task in get_tasks(run_id):
        deleted += backend().delete(STEPS, {"task_row_id": task["id"]})
    deleted += backend().delete(TASKS, {"run_id": run_id})
    deleted += backend().delete(RUNS, {"id": run_id})
    return deleted


def write_test() -> bool:
    """
    Exercise every function on the CURRENT backend with a unique selftest-<time> run, then delete
    every row it wrote (also when a check fails).
        sqlite   : on a throwaway file (SELFTEST_DB), deleted afterwards; real state untouched.
        supabase : on the real brain_* tables; only this test run's rows are written, then deleted.
    """
    global _backend
    real_backend = _backend
    if STATE_BACKEND == "sqlite":
        if os.path.exists(SELFTEST_DB):
            os.remove(SELFTEST_DB)
        _backend = SqliteBackend(SELFTEST_DB)
        print(f"Write test on throwaway file {SELFTEST_DB}")
    else:
        backend()
        print("Write test on Supabase brain_* tables (only this test's rows; deleted at the end)")

    key = SELFTEST_PREFIX + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    customer = f"{key}-customer"
    launch_key = f"{key}-launch"
    print(f"Test run_key: {key}")
    run, ok = None, False
    try:
        # Customer key (fixed setting, never derived from a display name)
        saved_env = os.environ.get("CUSTOMER_KEY")
        try:
            os.environ["CUSTOMER_KEY"] = ""
            expect("missing CUSTOMER_KEY stops", raises(current_customer))
            os.environ["CUSTOMER_KEY"] = "Anvixa AI Product"
            expect("display-name style key refused", raises(current_customer))
            os.environ["CUSTOMER_KEY"] = "anvixa-ai-product"
            expect("valid key accepted", current_customer() == "anvixa-ai-product")
        finally:
            if saved_env is None:
                os.environ.pop("CUSTOMER_KEY", None)
            else:
                os.environ["CUSTOMER_KEY"] = saved_env
        expect("create_run refuses an invalid customer key",
               raises(lambda: create_run(key, "Anvixa AI Product", True)) and get_run(key) is None)

        # Runs
        run = create_run(key, customer, True, {"customer_name": "Selftest"})
        expect("create_run twice returns the same run", create_run(key, "x", True)["id"] == run["id"])
        expect("JSON and bool fields round-trip", run["understanding"]["customer_name"] == "Selftest"
               and run["test_mode"] is True)
        time.sleep(0.05)
        updated = update_run(run["id"], status="running")
        expect("updated_at trigger moves forward", later(updated["updated_at"], run["updated_at"]))
        expect("bad run status refused", raises(lambda: update_run(run["id"], status="finished")))

        # Tasks
        work_orders = [
            {"task_id": "T1", "agent": "QuickICP", "output_type": "accounts", "depends_on": [], "week": 1,
             "target": "150-200 accounts"},
            {"task_id": "T2", "agent": "Email Agent", "output_type": "emails", "depends_on": ["T1"], "week": 2,
             "target": "no numeric target"},
        ]
        expect("add_tasks saves 2 tasks", add_tasks(run["id"], work_orders) == 2)
        t1, t2 = get_task(run["id"], "T1"), get_task(run["id"], "T2")
        expect("target_number decided by code (200 / None)", t1["target_number"] == 200 and t2["target_number"] is None)
        expect("depends_on round-trips as a list", t2["depends_on"] == ["T1"])
        expect("next ready task is T1 (T2 waits for T1)", next_ready_task(run["id"])["task_id"] == "T1")
        update_task(t1["id"], status="done")
        expect("add_tasks again does not reset status", add_tasks(run["id"], work_orders) == 2
               and get_task(run["id"], "T1")["status"] == "done")
        expect("next ready task is T2 after T1 done", next_ready_task(run["id"])["task_id"] == "T2")
        expect("bad task status refused", raises(lambda: update_task(t2["id"], status="finished")))

        # Tool calls
        step, is_new = start_step(t2["id"], "launch", launch_key, "email_tool", False, {"reference": launch_key})
        expect("start_step saves 'running' before acting", is_new and step["status"] == "running")
        again, is_new = start_step(t2["id"], "launch", launch_key, "email_tool", False, {"other": 1})
        expect("same idempotency key returns the saved call", not is_new and again["id"] == step["id"]
               and again["request"] == {"reference": launch_key})
        done = finish_step(step["id"], "done", {"campaign_id": "c-1"},
                           owned_rows_output(run, company_ids=[3, 1, 3], people_ids=[7]), external_ref="c-1")
        expect("finish_step saves reply, output, external_ref", done["status"] == "done"
               and done["output"]["company_ids"] == [1, 3] and done["external_ref"] == "c-1")
        expect("ids without customer/run_key refused",
               raises(lambda: finish_step(step["id"], "done", output={"company_ids": [1]})))
        expect("customer_row_ids: own customer sees its ids", customer_row_ids(customer, "company_ids") == {1, 3}
               and customer_row_ids(customer, "people_ids") == {7})
        expect("customer_row_ids: another customer sees nothing",
               customer_row_ids(f"{key}-other", "company_ids") == set())
        paid, _ = start_step(t1["id"], "companies", f"{key}:T1:companies:0", "quickicp", True, {"company_count": 40})
        expect("crashed paid call marked unknown", mark_unknown(paid["id"], "crash during call")["status"] == "unknown")

        # Secrets: exact names only
        expect("secret field refused before saving", raises(lambda: start_step(
            t1["id"], "x", f"{key}-secret", "quickhowl", False, {"user": {"email": "a@b.c", "password": "pw"}}))
            and get_step(f"{key}-secret") is None)
        expect("auth_token / Authorization refused",
               raises(lambda: assert_no_secrets({"phone_number": {"auth_token": "t"}}))
               and raises(lambda: assert_no_secrets({"Authorization": "Bearer t"})))
        safe = {"token_count": 5, "auth_id": "MA_1", "keywords": ["x"], "client_reference": "motm-tech",
                "passwords_reset": 0, "smtp": {"password": "********"}}
        expect("non-secret names and masked values allowed", not raises(lambda: assert_no_secrets(safe)))

        # Decisions
        plan_q = open_decision(run["id"], "plan_approval", {"text": "Approve the plan?"})
        expect("same open question (no task) returns the same row",
               open_decision(run["id"], "plan_approval", {"text": "again"})["id"] == plan_q["id"])
        email_q = open_decision(run["id"], "email_approve", {"text": "Approve samples?"}, t2["id"])
        expect("open_decision stores allowed answers", email_q["question"]["allowed_answers"] == ["feedback", "no", "yes"])
        expect("2 open decisions listed", len(list_open_decisions(run["id"])) == 2)
        expect("wrong answer refused", raises(lambda: answer_decision(email_q["id"], "maybe")))
        answered = answer_decision(email_q["id"], "yes", reason="samples look right")
        expect("answer saved", answered["status"] == "answered" and answered["answer"] == "yes")
        expect("second answer refused", raises(lambda: answer_decision(email_q["id"], "no")))
        expect("missing decision refused", raises(lambda: answer_decision(-1, "yes")))
        reopened = open_decision(run["id"], "email_approve", {"text": "New samples?"}, t2["id"])
        expect("new question allowed after the old one was answered", reopened["id"] != email_q["id"])
        answer_decision(reopened["id"], "no", reason="selftest")
        narrow = open_decision(run["id"], "email_approve", {"text": "No rounds left", "allowed_answers": ["yes", "no", "x"]},
                               t2["id"])
        expect("a question may narrow its answers (never beyond the kind's), and only those are accepted",
               narrow["question"]["allowed_answers"] == ["no", "yes"]
               and raises(lambda: answer_decision(narrow["id"], "feedback", reason="more")))
        answer_decision(narrow["id"], "no", reason="selftest")
        brain = record_decision(run["id"], "brain_review", "done", "brain", "200 of 200", task_row_id=t1["id"])
        expect("brain decision recorded as answered", brain["status"] == "answered")
        expect("brain_review cannot be left open", raises(lambda: open_decision(run["id"], "brain_review", {})))
        ok = True
    except AssertionError as error:
        print(f"  FAIL  {error}")
    finally:
        clean = True
        if run:
            deleted = delete_test_run(run["id"])
            clean = get_run(key) is None and get_step(launch_key) is None
            print(f"  {'PASS' if clean else 'FAIL'}  cleanup: {deleted} test rows deleted, "
                  f"{'none' if clean else 'SOME'} left")
        if STATE_BACKEND == "sqlite":
            _backend.close()
            os.remove(SELFTEST_DB)
            _backend = real_backend

    print("WRITE TEST OK (all test rows deleted)" if ok and clean else "WRITE TEST FAILED")
    return ok and clean


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "check":
        sys.exit(0 if check() else 1)
    elif command == "decisions":
        print_open_decisions()
    elif command == "write-test":
        sys.exit(0 if write_test() else 1)
    else:
        print("Usage: python state_store.py [check | decisions | write-test]")
        sys.exit(1)
