"""
lead_qual/store.py  -  where the files are, reading/writing them, the approval fingerprint.

Files (all under data/, git-ignored, per customer folder under the worker):
    data/intake_document.txt                     the customer's document (written by intake_adapter.py; read only)
    data/maps/<customer_key>_lead_criteria.json  the criteria (draft -> approved)
    data/lead_verdicts.json                      the last check; an older one is MOVED to data/archive/<time>-leads/
    data/leads_inbox.json                        the lead inbox (inbox.py); a CRM / table later = only read_inbox / save_inbox
Every write goes to a temp file first and is then renamed, so a crash never leaves half a file.
"""

import datetime
import hashlib
import json
import os
import re

DOCUMENT = os.path.join("data", "intake_document.txt")
VERDICTS = os.path.join("data", "lead_verdicts.json")
INBOX = os.path.join("data", "leads_inbox.json")
ARCHIVE = os.path.join("data", "archive")
CUSTOMER_KEY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class Stop(Exception):
    """A problem the person can fix; printed as one line, exit code 1."""


def customer() -> str:
    key = (os.getenv("CUSTOMER_KEY") or "").strip()
    if not CUSTOMER_KEY.match(key):
        raise Stop(f"STOPPED: CUSTOMER_KEY is missing or not a valid key ('{key}').")
    return key


def criteria_path(key: str) -> str:
    """data/maps/anvixa_ai_product_lead_criteria.json (same naming as the validator's maps)."""
    return os.path.join("data", "maps", f"{key.replace('-', '_')}_lead_criteria.json")


def read(path: str, what: str):
    if not os.path.exists(path):
        raise Stop(f"STOPPED: {path} is missing ({what}).")
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp = path + ".tmp"
    with open(temp, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)
    os.replace(temp, path)                       # all or nothing: a crash leaves the old file, never half a file


def read_inbox() -> dict:
    """The lead inbox ({'leads': [...]}); empty when there is none yet."""
    if not os.path.exists(INBOX):
        return {"leads": []}
    return read(INBOX, "the lead inbox")


def save_inbox(inbox: dict) -> None:
    write(INBOX, inbox)


def now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def fingerprint(criteria: dict) -> str:
    """sha256 of the criteria themselves (not of status or approval), so any later edit is noticed."""
    return hashlib.sha256(json.dumps(criteria, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def archive_verdicts() -> str | None:
    """Move an existing data/lead_verdicts.json to data/archive/<time>-leads/ (never overwritten). Returns the new path."""
    if not os.path.exists(VERDICTS):
        return None
    base = os.path.join(ARCHIVE, datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "-leads")
    folder, number = base, 1
    while os.path.exists(folder):                     # two checks in the same second: never overwrite an archive
        number += 1
        folder = f"{base}-{number}"
    os.makedirs(folder)
    target = os.path.join(folder, "lead_verdicts.json")
    os.replace(VERDICTS, target)
    return target
