"""
plan_approval.py
================

Plan approval rules and approval PROOF, shared by master_agent.py (writes it) and
orchestrator.py (reads it). Pure code: no LLM, no tools, no database.

WHY
---
Before: master_agent.py accepted a plain "yes" even while the plan still broke rules, and
nothing recorded who approved, when, or with which errors. Now:
    - CODE decides which answers are allowed (decide()).
    - A plain "yes" is REFUSED while rules are broken. Typing "override" on purpose is allowed,
      and needs a reason.
    - After the work orders are written, a proof file is saved next to them. It holds three
      fingerprints (sha256) of work_orders.json, month_plan.json and revenue_analysis.json, so a
      file edited after approval no longer matches its proof.
    - orchestrator.py 'load' refuses a plan without valid proof.
    - Old plan files are never deleted: archive_plan_files() MOVES them to
      data/archive/<YYYYMMDD-HHMMSS>/ before a new month is planned (proof first).

WHO DECIDES WHAT
----------------
    Code : allowed answers, refusal, fingerprints, proof checks
    Human: yes / override (+ reason) / changes;  PLAN_APPROVER in .env says who that human is

ANSWERS (decide)
----------------
    plan errors   you type                    result
    none          yes / y / approve(d)        approve
    none          override                    refuse ("no errors: type yes")
    some          yes / y / approve(d)        refuse ("N rules still broken ...")
    some          override                    override (a reason is asked, required)
    any           empty                       refuse ("type yes or give changes")
    any           other text                  feedback (sent to the brain, as before)

READS
-----
    .env                       PLAN_APPROVER (required, e.g. "Suyash Sharma")
    data/work_orders.json      to fingerprint / verify
    data/month_plan.json       to fingerprint / verify
    data/revenue_analysis.json to fingerprint / verify
    data/plan_approval.json    the proof (read_proof)

WRITES
------
    data/plan_approval.json    the proof (write_proof), only after the plan files are written
    data/archive/<stamp>/      old plan_approval, understanding, revenue_analysis, month_plan,
                               work_orders (archive_plan_files; a taken stamp gets -2, -3, ...)
    Example:
        {"approved_at": "2026-10-09T08:10:00+00:00", "approved_by": "Suyash Sharma",
         "answer": "yes", "overridden": false, "override_reason": null,
         "validation_errors_at_approval": [], "work_orders_count": 13,
         "work_orders_sha256": "...", "month_plan_sha256": "...", "revenue_analysis_sha256": "..."}

RUN
---
    python plan_approval.py selftest    tests decide(), the answer loop, proof write/read/tamper and
                                        the archive, in throwaway folders (no LLM, no database)
    python plan_approval.py show        read-only: print data/plan_approval.json and check it

DEBUGGING
---------
    "STOPPED: PLAN_APPROVER is missing"         -> add PLAN_APPROVER=<your name> to .env
    "STOPPED: no approval proof"                -> run master_agent.py and approve the plan
    "STOPPED: ... changed after approval"       -> work_orders / month_plan / revenue_analysis .json was
                                                   edited or replaced after approval; re-run master_agent.py
    "STOPPED: could not archive ..."            -> a plan file is open in another program (close it);
                                                   nothing was deleted, moved files are in that archive folder
    Find an older plan: data/archive/<YYYYMMDD-HHMMSS>/
"""

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from typing import Callable, NamedTuple

from dotenv import load_dotenv

from quickicp import ToolError

load_dotenv()

DATA_DIR = "data"
APPROVAL_NAME = "plan_approval.json"
WORK_ORDERS_NAME = "work_orders.json"
PLAN_NAME = "month_plan.json"
REVENUE_NAME = "revenue_analysis.json"
UNDERSTANDING_NAME = "understanding.json"
APPROVAL_FILE = os.path.join(DATA_DIR, APPROVAL_NAME)

ARCHIVE_NAME = "archive"
# Moved (never deleted) before a new month is planned. The proof goes FIRST, so a stale proof
# can never sit next to a new plan.
PLAN_FILE_NAMES = (APPROVAL_NAME, UNDERSTANDING_NAME, REVENUE_NAME, PLAN_NAME, WORK_ORDERS_NAME)
# Files the proof fingerprints (name, proof field).
FINGERPRINTS = ((WORK_ORDERS_NAME, "work_orders_sha256"), (PLAN_NAME, "month_plan_sha256"),
                (REVENUE_NAME, "revenue_analysis_sha256"))

YES_WORDS = {"yes", "y", "approve", "approved"}
OVERRIDE_WORD = "override"
APPROVER_MAX = 80
PROOF_FIELDS = ("approved_at", "approved_by", "answer", "overridden", "override_reason",
                "validation_errors_at_approval", "work_orders_count") + tuple(field for _, field in FINGERPRINTS)


class Choice(NamedTuple):
    """What code decided about one typed answer."""
    action: str      # 'approve', 'override', 'feedback' or 'refuse'
    message: str     # shown to the human (why refused, or what happens next)


# ================= WHO APPROVES =================

def plan_approver() -> str:
    """The approver's name from PLAN_APPROVER in .env (required)."""
    name = (os.getenv("PLAN_APPROVER") or "").strip()
    if not name:
        raise ToolError("STOPPED: PLAN_APPROVER is missing in .env (e.g. PLAN_APPROVER=Suyash Sharma). "
                        "It records who approved the plan. Nothing was planned.")
    if len(name) > APPROVER_MAX:
        raise ToolError(f"STOPPED: PLAN_APPROVER is longer than {APPROVER_MAX} characters.")
    return name


# ================= ANSWER RULES =================

def decide(answer: str, errors: list[str]) -> Choice:
    """Code decides what a typed answer means, given the plan's current rule errors."""
    text = (answer or "").strip()
    word = text.lower()
    if not text:
        return Choice("refuse", "Type 'yes' to approve, or describe the changes you want.")
    if word in YES_WORDS:
        if errors:
            return Choice("refuse", f"{len(errors)} plan rule(s) are still broken (listed above). "
                                    "Give changes, or type 'override' to approve anyway on purpose.")
        return Choice("approve", "Plan approved.")
    if word == OVERRIDE_WORD:
        if not errors:
            return Choice("refuse", "The plan breaks no rules: type 'yes' to approve (no override needed).")
        return Choice("override", f"Approving with {len(errors)} broken rule(s) on purpose. A reason is required.")
    return Choice("feedback", "Changes sent to the brain.")


def ask_approval(errors: list[str], read: Callable[[str], str] = input) -> dict:
    """
    Ask until the answer is valid. Returns:
        {"resume": {...for the graph...}, "answer": "yes"|"override"|None, "override_reason": str|None}
    A refused answer is asked again and never reaches the graph.
    """
    while True:
        answer = read("\nApprove? Type 'yes', or give changes. Use 'plan: ...' and/or 'analysis: ...' "
                      "(no prefix = plan)" + (", or 'override'" if errors else "") + ": ")
        choice = decide(answer, errors)
        print(f"  {choice.message}")
        if choice.action == "refuse":
            continue
        if choice.action == "feedback":
            return {"resume": {"approved": False, "feedback": answer.strip()}, "answer": None, "override_reason": None}
        if choice.action == "approve":
            return {"resume": {"approved": True}, "answer": "yes", "override_reason": None}
        reason = ""
        while not reason:
            reason = read("Reason for override (required): ").strip()
        return {"resume": {"approved": True}, "answer": "override", "override_reason": reason}


# ================= PROOF =================

def file_sha256(path: str) -> str:
    """Fingerprint of a file's exact bytes."""
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def archive_plan_files(data_dir: str = DATA_DIR, now: datetime | None = None) -> str | None:
    """
    MOVE the old plan files (PLAN_FILE_NAMES, proof first) to <data_dir>/archive/<YYYYMMDD-HHMMSS>/.
    Never deletes, never overwrites: a folder that already exists gets -2, -3, ...
    Returns the archive folder, or None if there was nothing to move (no folder is created).
    A failed move stops with STOPPED; files already moved stay in the archive.
    """
    present = [name for name in PLAN_FILE_NAMES if os.path.exists(os.path.join(data_dir, name))]
    if not present:
        return None
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(data_dir, ARCHIVE_NAME, stamp)
    number = 1
    while os.path.exists(folder):
        number += 1
        folder = os.path.join(data_dir, ARCHIVE_NAME, f"{stamp}-{number}")
    os.makedirs(folder)
    for name in present:
        try:
            os.replace(os.path.join(data_dir, name), os.path.join(folder, name))
        except OSError as error:
            raise ToolError(f"STOPPED: could not archive {name} to {folder}: {error}. "
                            "Nothing was deleted; files already moved are in that folder.") from error
    return folder


def write_proof(approval: dict, errors: list[str], approver: str, work_orders_count: int,
                data_dir: str = DATA_DIR) -> dict:
    """
    Save the approval proof. Call ONLY after work_orders.json, month_plan.json and
    revenue_analysis.json are written: the proof fingerprints those exact files.
    """
    if approval.get("answer") not in ("yes", "override"):
        raise ToolError("STOPPED: no approval to write proof for.")
    proof = {
        "approved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "approved_by": approver,
        "answer": approval["answer"],
        "overridden": approval["answer"] == "override",
        "override_reason": approval.get("override_reason"),
        "validation_errors_at_approval": list(errors),
        "work_orders_count": work_orders_count,
        **{field: file_sha256(os.path.join(data_dir, name)) for name, field in FINGERPRINTS},
    }
    with open(os.path.join(data_dir, APPROVAL_NAME), "w", encoding="utf-8") as file:
        json.dump(proof, file, indent=2, ensure_ascii=False)
    return proof


def read_proof(data_dir: str = DATA_DIR) -> dict:
    """Read the proof and check it still matches work_orders / month_plan / revenue_analysis; stop otherwise."""
    path = os.path.join(data_dir, APPROVAL_NAME)
    if not os.path.exists(path):
        raise ToolError(f"STOPPED: no approval proof ({path}). Run master_agent.py and approve the plan.")
    try:
        with open(path, encoding="utf-8") as file:
            proof = json.load(file)
    except json.JSONDecodeError as error:
        raise ToolError(f"STOPPED: approval proof {path} is not valid JSON: {error}") from error
    missing = [field for field in PROOF_FIELDS if field not in proof]
    if missing:
        raise ToolError(f"STOPPED: approval proof {path} is missing: {', '.join(missing)}")
    for name, field in FINGERPRINTS:
        file_path = os.path.join(data_dir, name)
        if not os.path.exists(file_path) or file_sha256(file_path) != proof[field]:
            raise ToolError(f"STOPPED: {file_path} changed after approval (or is missing). "
                            "Re-run master_agent.py and approve the plan again.")
    return proof


def describe(proof: dict | None) -> str:
    """One honest line about a plan approval, for status screens and decision reasons."""
    if not proof:
        return "yes, approved BEFORE approval proof existed (no proof)"
    errors = proof.get("validation_errors_at_approval") or []
    if proof.get("overridden"):
        return (f"yes with OVERRIDE by {proof['approved_by']} at {proof['approved_at']}, "
                f"{len(errors)} rule error(s): {'; '.join(errors)} | reason: {proof.get('override_reason')}")
    return f"yes by {proof['approved_by']} at {proof['approved_at']} (proof: {len(errors)} rule errors)"


# ================= SELFTEST =================

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


def scripted(*answers: str) -> Callable[[str], str]:
    """A fake input() that returns the given answers in order."""
    queue = list(answers)
    return lambda prompt: queue.pop(0)


def selftest() -> bool:
    """Test decide(), ask_approval(), proof write/read/tamper and the archive, in throwaway folders."""
    errors = ["T7 contacts prospects but no earlier QuickICP task outputs 'contacts'."]
    print("Plan approval selftest (no LLM, no database)")
    try:
        # decide(): code decides
        expect("no errors + yes -> approve", decide("yes", []).action == "approve")
        expect("no errors + 'Approved' -> approve", decide("  Approved ", []).action == "approve")
        expect("no errors + override -> refused", decide("override", []).action == "refuse")
        expect("errors + yes -> REFUSED", all(decide(w, errors).action == "refuse" for w in ("yes", "Y", "approve")))
        expect("errors + override -> override", decide("Override", errors).action == "override")
        expect("empty answer -> refused", decide("   ", errors).action == "refuse" and decide("", []).action == "refuse")
        expect("other text -> feedback", decide("plan: add Pune", errors).action == "feedback")

        # ask_approval(): refused answers are asked again and never reach the graph
        result = ask_approval(errors, scripted("yes", "", "override", "", "client accepted the gap"))
        expect("yes refused, then override with a required reason",
               result == {"resume": {"approved": True}, "answer": "override",
                          "override_reason": "client accepted the gap"})
        result = ask_approval([], scripted("override", "yes"))
        expect("override refused without errors, then yes", result["answer"] == "yes"
               and result["resume"] == {"approved": True})
        result = ask_approval(errors, scripted("yes", "plan: move T7 after T4"))
        expect("feedback passed on unchanged", result["resume"] == {"approved": False, "feedback": "plan: move T7 after T4"})

        # approver
        saved = os.environ.get("PLAN_APPROVER")
        try:
            os.environ["PLAN_APPROVER"] = ""
            expect("missing PLAN_APPROVER stops", raises(plan_approver))
            os.environ["PLAN_APPROVER"] = "  Test Person "
            expect("PLAN_APPROVER read and trimmed", plan_approver() == "Test Person")
        finally:
            if saved is None:
                os.environ.pop("PLAN_APPROVER", None)
            else:
                os.environ["PLAN_APPROVER"] = saved

        # proof: write / read / tamper / missing, in a throwaway folder
        files = {WORK_ORDERS_NAME: '[{"task_id": "T1"}]', PLAN_NAME: '{"month": "Month 1"}',
                 REVENUE_NAME: '{"revenue_goal": "not stated"}', UNDERSTANDING_NAME: '{"customer_name": "X"}'}

        def write_files(folder: str) -> None:
            for name, content in files.items():
                with open(os.path.join(folder, name), "w", encoding="utf-8") as file:
                    file.write(content)

        with tempfile.TemporaryDirectory() as folder:
            write_files(folder)
            expect("no proof -> load refused", raises(lambda: read_proof(folder)))
            expect("no approval -> no proof written",
                   raises(lambda: write_proof({"answer": None}, [], "Test Person", 1, folder)))
            proof = write_proof({"answer": "override", "override_reason": "client accepted the gap"},
                                errors, "Test Person", 1, folder)
            expect("proof written with approver, override, errors and 3 fingerprints",
                   proof["approved_by"] == "Test Person" and proof["overridden"] is True
                   and proof["validation_errors_at_approval"] == errors
                   and all(len(proof[field]) == 64 for _, field in FINGERPRINTS))
            expect("proof reads back unchanged", read_proof(folder) == proof)
            expect("override shown honestly", "OVERRIDE by Test Person" in describe(proof) and "1 rule error" in describe(proof))
            expect("no-proof run shown honestly", describe(None) == "yes, approved BEFORE approval proof existed (no proof)")
            for name in (WORK_ORDERS_NAME, REVENUE_NAME):
                original = files[name]
                with open(os.path.join(folder, name), "a", encoding="utf-8") as file:
                    file.write(" ")
                expect(f"{name} edited after approval -> refused", raises(lambda: read_proof(folder)))
                with open(os.path.join(folder, name), "w", encoding="utf-8") as file:
                    file.write(original)
            expect("files put back -> proof valid again", read_proof(folder) == proof)

        # archive: MOVE old plan files, never delete or overwrite
        with tempfile.TemporaryDirectory() as folder:
            expect("nothing to archive -> None, no folder",
                   archive_plan_files(folder) is None and not os.path.exists(os.path.join(folder, ARCHIVE_NAME)))
            write_files(folder)
            write_proof({"answer": "yes"}, [], "Test Person", 1, folder)
            fixed = datetime(2026, 10, 9, 10, 30, 0)
            target = archive_plan_files(folder, fixed)
            expect("archived into data/archive/20261009-103000",
                   target == os.path.join(folder, ARCHIVE_NAME, "20261009-103000"))
            expect("all 5 plan files moved, none left behind",
                   all(os.path.exists(os.path.join(target, n)) and not os.path.exists(os.path.join(folder, n))
                       for n in PLAN_FILE_NAMES))
            with open(os.path.join(target, REVENUE_NAME), encoding="utf-8") as file:
                expect("contents unchanged after the move", file.read() == files[REVENUE_NAME])
            write_files(folder)
            second = archive_plan_files(folder, fixed)
            expect("same second again -> new folder -2, first archive untouched",
                   second == target + "-2" and os.path.exists(os.path.join(target, APPROVAL_NAME))
                   and not os.path.exists(os.path.join(second, APPROVAL_NAME))
                   and os.path.exists(os.path.join(second, WORK_ORDERS_NAME)))
        print("PLAN APPROVAL SELFTEST OK")
        return True
    except AssertionError as error:
        print(f"  FAIL  {error}")
        print("PLAN APPROVAL SELFTEST FAILED")
        return False


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "selftest":
        sys.exit(0 if selftest() else 1)
    elif command == "show":
        try:
            proof = read_proof()
            print(json.dumps(proof, indent=2, ensure_ascii=False))
            print("Proof matches work_orders.json and month_plan.json:", describe(proof))
        except ToolError as error:
            print(error)
            sys.exit(1)
    else:
        print("Usage: python plan_approval.py [selftest | show]")
        sys.exit(1)
