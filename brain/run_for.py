"""
run_for.py
==========

Pick the customer project FROM THE DATABASE, then run any brain command for it.
No project id or customer key is typed into .env any more.

WHY
---
Before: REVENUEOS_PROJECT_ID, CUSTOMER_KEY and CUSTOMER_DISPLAY_NAME were typed into .env for
one customer, and edited by hand to switch customers. A typo there reads the wrong plan or files
data under the wrong key. Now the platform stores each project's brain key on the project itself
(revenueos_projects.brain_customer_key, set automatically when a project is created; see
supabase/migrations/0003_project_brain_key.sql), and this file reads it from there.

HOW
---
    1. select    choose a project (by id, or 'new' = the one ready project the brain has not run
                 yet). The choice is saved in data/selected_project.json.
    2. run       every later command goes through this file. It puts the chosen project's
                 values into the process environment and then runs the normal script, unchanged.

    The scripts call load_dotenv(), which never overwrites a value that is already set, so the
    database values win over whatever .env still says.

READS AND WRITES
----------------
    Only SELECTs on revenueos_projects and the brain_* tables. The ONE table written is
    revenueos_brain_requests (the platform's "Start execution" requests, by the worker only).
    The only file written by hand-run commands is data/selected_project.json.

NOBODY TYPES A COMMAND: REQUESTS FROM THE PLATFORM
--------------------------------------------------
    With the worker running (python run_for.py work), the platform does the rest:
        1. A person clicks "Start execution" on the project's Dashboard.
           -> a row in revenueos_brain_requests, status 'requested'
        2. The worker claims it and, in the background, in that customer's folder:
           intake -> plan. The month plan is PUBLISHED TO THE PLATFORM and waits there.
        3. The person approves it on the Dashboard (yes / changes / override + reason).
           The same rules as the typed prompt (plan_approval.decide); the proof names that person.
        4. check -> load. The request ends 'loaded' and the worker starts the run as usual.
    A failed step is shown on the Dashboard with its STOPPED line and a "Try again" button; a
    failure after the approval (check, load) resumes there, so the plan is not written twice.
    Run ONE worker per machine. Install it as a service with install_worker.ps1.

    A STEP THAT BUYS DATA is still started by a person, never by the worker on its own. When the
    safe step stops with "next would buy data (T3)", the worker offers that task in the platform
    (revenueos_brain_paid_steps). The person clicks Start on the Dashboard; the worker then runs
    exactly one  orchestrator.py run <month>  for that customer and goes back to the safe step.

WHAT IS SET FOR THE CHOSEN PROJECT
----------------------------------
    REVENUEOS_PROJECT_ID     the project's id
    CLIENT_REFERENCE, EMAIL_TOOL_USER_EMAIL, QUICKHOWL_CLIENT_REFERENCE, QUICKHOWL_USER_*
                             the project's OWN email and calling accounts, from revenueos_brain_accounts
                             (filled in on the platform: Tools > Tool accounts). A project without a
                             row never gets another customer's accounts from .env.
    STAGING_COMPANIES, STAGING_PEOPLE
                             the project's Staging limits from the platform: the tools never fetch
                             more than this for THIS customer. Calls and emails still go to the phone
                             and email on each profile (edited in the platform for a trial).
    BRAIN_TEST_MODE          true only for a run the brain itself loaded as a test.
    SMTP_*, EMAIL_WORKSPACE_NAME
                             a NEW customer's own mailbox, from its checklist's "Email sender setup"
                             answers and its customer email (platform: Tools > Email). Its email account
                             is then created by its first campaign, under its customer key.
    CUSTOMER_KEY             its brain_customer_key (never changes for a project)
    CUSTOMER_DISPLAY_NAME    its customer name, unless .env was written for this same customer
    CUSTOMER_LONG_NAMES      emptied, unless .env was written for this same customer
    CUSTOMER_WEBSITE         emptied, unless .env was written for this same customer
    (.env "was written for this customer" when its own CUSTOMER_KEY equals the project's key.)

EVERY CUSTOMER WORKS INDEPENDENTLY
----------------------------------
    The scripts keep their working files in ./data (about 240 places), so two customers in one
    folder overwrite each other. That was the only reason the brain handled one customer at a
    time. Now every step runs inside the customer's OWN folder:

        customers/<customer key>/data/         that customer's plan files, results, archives
        customers/<customer key>/knowledge/    a copy of ./knowledge (the scripts read it relatively)
        customers/<customer key>/logs/         output of background steps

    The scripts are not changed: they still read and write ./data, it is just a different
    folder per customer. Their imports and .env are found from the script's own location.
    The customer .env was written for (its CUSTOMER_KEY) keeps using this folder itself, so its
    existing ./data is not moved.

ASYNCHRONOUS: THE WORKER
------------------------
    python run_for.py work
        Keeps running. Every 30 seconds it looks at every customer's active run and, when
        something changed for that customer (a decision was answered in the platform, a task
        moved), starts  orchestrator.py run <month> --until-wait  for it, in the background, in
        that customer's folder. Customers run at the same time and never wait for each other.
        So an approval given in the platform is acted on within about half a minute, with
        nobody typing a command.

        It only ever starts the brain's own safe step: --until-wait stops when a person is
        needed and BEFORE a new task that would buy data. Those still need a person.
        Ctrl+C stops the worker after the steps already running have finished.

    python run_for.py work --once     look once, start what is due, wait for it, then exit

THE WHOLE FLOW IN ONE COMMAND (the default)
-------------------------------------------
    python run_for.py                 = python run_for.py start
        1. select   the ready project the brain has not run yet (stops if there are two: never guesses)
        2. confirm  shows the customer, the month and whether TEST_MODE is on; you type the customer key
        3. intake   intake_adapter.py             reads the plan from the database (no LLM)
        4. plan     master_agent.py --intake      LLM plans the month; YOU approve it at its prompt
        5. check    check_system.py               (after planning: it needs the plan files to exist)
        6. load     orchestrator.py load <month>
        7. run      orchestrator.py run <month> --until-wait   stops when a person is needed, and BEFORE
                                                               a new task that would buy data
    Each step is its own process; the first one that fails stops the flow and prints how to resume.

    python run_for.py start <project-id>           a specific project instead of the new one
    python run_for.py start --month 2026-11        a month other than the current one
    python run_for.py start --from load            resume at a step (intake | plan | check | load | run)
    Two customers can be started at the same time in two windows.

    Nothing here skips an approval: the month plan, every email campaign and every call campaign
    still wait for a person, exactly as when the scripts are run one by one.

RUN (one step at a time)
------------------------
    python run_for.py list                         ready projects, their key, and whether the brain ran them
    python run_for.py select new                   choose the ready project with no run yet
    python run_for.py select <project-id>          choose a specific project
    python run_for.py show                         which project is chosen
    python run_for.py for <project-id> orchestrator.py status      one command for a project, without changing the chosen one
    python run_for.py check_system.py              run any brain script for the chosen project
    python run_for.py intake_adapter.py
    python run_for.py master_agent.py --intake     (costs LLM calls, exactly as without this file)
    python run_for.py orchestrator.py status
    python run_for.py selftest                     fake data: no network, no LLM, throwaway folder

DEBUGGING
---------
    - "column ... brain_customer_key does not exist": run supabase/migrations/0003_project_brain_key.sql once.
    - 'select new' refuses when there are several candidates: it never guesses. Use the id from 'list'.
    - To see what a script will be given, run:  python run_for.py show
"""

import datetime
import json
import os
import re
import runpy
import subprocess
import shutil
import socket
import sys
import tempfile
import time

SELECTION_FILE = os.path.join("data", "selected_project.json")
PROJECTS = "revenueos_projects"
RUNS = "brain_customer_runs"
READY_STATUS = "plan_ready"

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])(-r\d+)?$")     # 2026-10 or a replacement plan 2026-10-r2
STEP_NAMES = ["intake", "plan", "check", "load", "run"]
BRAIN_DIR = os.path.dirname(os.path.abspath(__file__))
CUSTOMERS_DIR = "customers"
ACTIVE_RUN_STATUSES = {"planning", "running", "waiting_human"}
WORK_EVERY_SECONDS = 30
REQUEST_STEPS = ["intake", "plan", "check", "load"]      # a platform request ends when the run is loaded
REQUEST_RESUME_STEPS = ("check", "load")                 # steps after the approval: safe to resume at
REQUEST_ERROR_MAX = 600

# Each customer's own tool accounts (revenueos_brain_accounts): column -> the setting the tools read.
# The platform's lib/brain/accounts.ts has the same pairs.
ACCOUNTS = "revenueos_brain_accounts"
ACCOUNT_SETTINGS = {
    "email_client_reference": "CLIENT_REFERENCE",
    "email_user_email": "EMAIL_TOOL_USER_EMAIL",
    "calls_client_reference": "QUICKHOWL_CLIENT_REFERENCE",
    "calls_user_email": "QUICKHOWL_USER_EMAIL",
    "calls_user_password": "QUICKHOWL_USER_PASSWORD",
    "calls_user_full_name": "QUICKHOWL_USER_FULL_NAME",
    "calls_user_company": "QUICKHOWL_USER_COMPANY",
}

UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
CUSTOMER_KEY = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class SelectError(Exception):
    """A problem the person can fix; printed without a traceback."""


# ================= READ THE DATABASE (select only) =================

def connect():
    """Supabase client from SUPABASE_URL + SUPABASE_KEY in .env. Imported here so 'selftest' needs no packages."""
    from dotenv import load_dotenv
    from supabase import create_client
    load_dotenv()
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise SelectError("STOPPED: SUPABASE_URL or SUPABASE_KEY is missing in .env.")
    return create_client(url, key)


def read_projects(client) -> list[dict]:
    """Every platform project with its brain key, newest first. Select only."""
    try:
        rows = (client.table(PROJECTS)
                .select("id, customer_name, status, brain_customer_key, updated_at")
                .execute().data) or []
    except Exception as error:
        if "brain_customer_key" in str(error):
            raise SelectError("STOPPED: revenueos_projects has no brain_customer_key column yet. "
                              "Run supabase/migrations/0003_project_brain_key.sql once.") from error
        raise
    return sorted(rows, key=lambda row: row.get("updated_at") or "", reverse=True)


def customers_with_runs(client) -> set[str]:
    """Customer keys the brain already has at least one run for. Select only."""
    rows = client.table(RUNS).select("customer").execute().data or []
    return {row["customer"] for row in rows if row.get("customer")}


# The customer's sender details: checklist answers by code (the platform's lib/checklist-sender.ts
# has the same codes and the same two security choices) -> what the email step is handed.
SENDER_CODES = {
    "SENDER-NAME": "name",
    "SENDER-EMAIL": "email",
    "SENDER-SMTP-HOST": "host",
    "SENDER-SMTP-SECURITY": "security",
    "SENDER-SMTP-PASSWORD": "password",
    "SENDER-IMAP-HOST": "imap_host",
    "SENDER-STYLE": "style",
}
SENDER_STYLE = {                          # the customer's email style -> the campaign type the email step wants
    "Personalised for each recipient": "HYPER_PERSONALIZED",
    "Same email for everyone": "STANDARD",
}
SENDER_SECURITY = {                       # the email tool accepts only these, on these ports
    "STARTTLS (port 587)": ("STARTTLS", "587"),
    "SSL/TLS (port 465)": ("IMPLICIT_TLS", "465"),
}
SENDER_SETTINGS = ("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_PASSWORD",
                   "SMTP_DISPLAY_NAME", "SMTP_IMAP_HOST", "EMAIL_WORKSPACE_NAME", "EMAIL_CAMPAIGN_TYPE")


def sender_of(customer_email: str | None, items: list[dict]) -> dict | None:
    """The sender details if they are COMPLETE and usable, else None (a half-filled sender is never used)."""
    found = {SENDER_CODES[item["code"]]: (item.get("answer") or "").strip()
             for item in items if item.get("code") in SENDER_CODES}
    security = SENDER_SECURITY.get(found.get("security") or "")
    login = (customer_email or "").strip().lower()
    if not (login and security and all(found.get(key) for key in ("name", "email", "host", "password"))):
        return None
    return {"login_email": login, "name": found["name"], "email": found["email"], "host": found["host"],
            "password": found["password"], "mode": security[0], "port": security[1],
            "imap_host": found.get("imap_host") or "",
            "campaign_type": SENDER_STYLE.get(found.get("style") or "", "HYPER_PERSONALIZED")}


def read_sender(client, project_id: str) -> dict | None:
    """
    This project's sender details from its active checklist plus its customer email, or None when
    they are not complete (or the database has no customer email yet:
    supabase/migrations/0005_project_customer_email.sql). Select only; never printed or saved.
    """
    try:
        project = client.table(PROJECTS).select("customer_email").eq("id", project_id).execute().data or []
        checklist = (client.table("revenueos_checklists").select("id")
                     .eq("project_id", project_id).eq("is_active", True).execute().data) or []
        if not project or not checklist:
            return None
        items = (client.table("revenueos_checklist_items").select("code, answer")
                 .eq("checklist_id", checklist[0]["id"]).execute().data) or []
    except Exception:
        return None
    return sender_of(project[0].get("customer_email"), items)


def read_customer_email(client, project_id: str) -> str | None:
    """The customer's email as typed on the platform's New project form, or None. Select only."""
    try:
        rows = client.table(PROJECTS).select("customer_email").eq("id", project_id).execute().data or []
    except Exception:
        return None
    email = ((rows[0].get("customer_email") if rows else "") or "").strip().lower()
    return email or None


STAGING_SETTINGS = ("STAGING_COMPANIES", "STAGING_PEOPLE")

# A customer's own caller line (the number calls are made FROM): column -> the setting quickhowl.py
# reads. The platform's lib/brain/caller.ts has the same pairs.
CALLER_LINE_SETTINGS = {
    "calls_vobiz_auth_id": "QUICKHOWL_VOBIZ_AUTH_ID",
    "calls_vobiz_auth_token": "QUICKHOWL_VOBIZ_AUTH_TOKEN",
    "calls_did": "QUICKHOWL_DID",
}


def read_caller_line(client, project_id: str) -> dict | None:
    """
    The project's own caller line from the platform (Tools > Calls), or None when it has none or the
    database has no such columns yet (supabase/migrations/0009_caller_line.sql). Select only; the
    token is only ever handed to the calling step's process, never printed or saved.
    """
    try:
        rows = (client.table(ACCOUNTS).select(", ".join(CALLER_LINE_SETTINGS))
                .eq("project_id", project_id).execute().data) or []
    except Exception:
        return None
    line = {column: (rows[0].get(column) or "").strip() for column in CALLER_LINE_SETTINGS} if rows else {}
    return line if line and all(line.values()) else None       # a half-filled line is never sent


def read_staging(client, project_id: str) -> dict | None:
    """
    The project's Staging / Production choice and its staging limits, from the platform, or None
    before supabase/migrations/0008_project_run_mode.sql is applied. Select only.
        {"staging": bool, "companies": int | None, "people": int | None}
    """
    try:
        rows = (client.table(PROJECTS).select("run_mode, staging_companies, staging_people")
                .eq("id", project_id).execute().data) or []
    except Exception:
        return None
    if not rows or "run_mode" not in rows[0]:
        return None
    row = rows[0]
    return {"staging": row.get("run_mode") == "staging", "companies": row.get("staging_companies"),
            "people": row.get("staging_people")}


def read_setup(client, project_id: str) -> dict:
    """Everything the tools need about ONE customer's accounts, as keyword arguments for environment_for."""
    return {"accounts": read_accounts(client, project_id), "sender": read_sender(client, project_id),
            "customer_email": read_customer_email(client, project_id), "staging": read_staging(client, project_id),
            "caller_line": read_caller_line(client, project_id)}


def read_accounts(client, project_id: str) -> dict | None:
    """
    This project's own tool accounts, or None when it has no row (or the table is not there yet:
    supabase/migrations/0004_brain_accounts.sql). Select only. The values are only ever handed to
    the step's process; they are never printed or written to a file.
    """
    try:
        rows = (client.table(ACCOUNTS).select(", ".join(ACCOUNT_SETTINGS))
                .eq("project_id", project_id).execute().data) or []
    except Exception:
        return None
    return rows[0] if rows else None


# ================= CHOOSE (code decides; never guesses) =================

def usable(project: dict) -> bool:
    """A project the brain can work on: its plan is ready and it has a valid key."""
    return project.get("status") == READY_STATUS and bool(CUSTOMER_KEY.match(project.get("brain_customer_key") or ""))


def choose(projects: list[dict], ran: set[str], wanted: str) -> dict:
    """
    The project for 'wanted': a project id, or 'new' = the ONE ready project with no brain run yet.
    Stops with a clear reason instead of guessing.
    """
    wanted = wanted.strip().lower()
    if wanted == "new":
        fresh = [p for p in projects if usable(p) and p["brain_customer_key"] not in ran]
        if not fresh:
            raise SelectError("STOPPED: no ready project is waiting. Every ready project already has a brain run "
                              "(see: python run_for.py list).")
        if len(fresh) > 1:
            names = ", ".join(f"{p['customer_name']} ({p['id']})" for p in fresh)
            raise SelectError(f"STOPPED: {len(fresh)} ready projects have no run yet: {names}. "
                              "Choose one by id: python run_for.py select <project-id>")
        return fresh[0]

    if not UUID.match(wanted):
        raise SelectError(f"STOPPED: '{wanted}' is not a project id. Use 'new' or an id from: python run_for.py list")
    project = next((p for p in projects if str(p["id"]).lower() == wanted), None)
    if not project:
        raise SelectError(f"STOPPED: no project with id {wanted}.")
    if project.get("status") != READY_STATUS:
        raise SelectError(f"STOPPED: {project['customer_name']} is not ready (status '{project.get('status')}'). "
                          "Its plan must be finished in the platform first.")
    if not CUSTOMER_KEY.match(project.get("brain_customer_key") or ""):
        raise SelectError(f"STOPPED: {project['customer_name']} has no valid brain key "
                          f"('{project.get('brain_customer_key')}').")
    return project


def selection_of(project: dict) -> dict:
    """What is saved: only what the scripts need. No secret is ever stored here."""
    return {"project_id": str(project["id"]).lower(), "customer_key": project["brain_customer_key"],
            "customer_name": project["customer_name"]}


def save_selection(selection: dict, path: str = SELECTION_FILE) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(selection, file, indent=2, ensure_ascii=False)


def load_selection(path: str = SELECTION_FILE) -> dict:
    """The saved choice, checked again before use (a hand-edited file must not slip through)."""
    if not os.path.exists(path):
        raise SelectError("STOPPED: no project is chosen yet. Run: python run_for.py select new")
    with open(path, encoding="utf-8") as file:
        selection = json.load(file)
    if not UUID.match(str(selection.get("project_id", ""))) or not CUSTOMER_KEY.match(str(selection.get("customer_key", ""))):
        raise SelectError(f"STOPPED: {path} is damaged. Choose again: python run_for.py select <project-id>")
    return selection


# ================= ONE FOLDER PER CUSTOMER =================

def workspace_for(customer_key: str, dotenv_key: str | None, brain_dir: str = BRAIN_DIR) -> str:
    """
    The folder a customer's steps run in. The customer .env was written for keeps the brain
    folder itself (its ./data already lives there); every other customer gets its own folder.
    """
    if not CUSTOMER_KEY.match(customer_key or ""):
        raise SelectError(f"STOPPED: '{customer_key}' is not a valid customer key.")
    if (dotenv_key or "").strip() == customer_key:
        return brain_dir
    return os.path.join(brain_dir, CUSTOMERS_DIR, customer_key)


def prepare_workspace(folder: str, brain_dir: str = BRAIN_DIR) -> str:
    """Create the customer's folder with data/, logs/ and an up-to-date copy of knowledge/. Safe to repeat."""
    if os.path.abspath(folder) == os.path.abspath(brain_dir):
        return folder
    for name in ("data", "logs"):
        os.makedirs(os.path.join(folder, name), exist_ok=True)
    source = os.path.join(brain_dir, "knowledge")
    if os.path.isdir(source):
        # The scripts read knowledge/... relative to where they run, so each customer folder needs it.
        shutil.copytree(source, os.path.join(folder, "knowledge"), dirs_exist_ok=True)
    return folder


# ================= APPLY TO THE PROCESS =================

def environment_for(selection: dict, dotenv_customer_key: str | None, accounts: dict | None = None,
                    sender: dict | None = None, customer_email: str | None = None,
                    staging: dict | None = None, test_mode: bool | None = None,
                    caller_line: dict | None = None) -> dict:
    """
    The values the scripts read, for the chosen project. The customer's naming settings in .env
    are kept only when .env was written for this same customer; for any other customer they are
    replaced or emptied, so one customer's name can never appear in another customer's emails.

    The email and calling accounts follow the same rule, one step stricter: a project with its
    own accounts (accounts = its revenueos_brain_accounts row) always gets exactly those, and a
    project without any never inherits the accounts .env holds for someone else.

    A customer with no existing email account but complete sender details (sender = read_sender)
    gets its own NEW account: the reference is its customer key, the user is its customer email,
    and the mailbox details are handed to the email step, which adds them only when it sends.

    The same customer email opens the customer's calling account: with no existing Quickhowl
    account, the reference is its customer key and the user is its customer email (Quickhowl
    creates the account on the first launch; the first password is that email, as in the email tool).

    STAGING is per customer too, and it is simple: the person's two limits (how many companies,
    how many people) are handed to the tools, which then never fetch more (runner_kit.target_for).
    Nothing else changes: calls and emails go to the phone and email on each profile, which the
    person edits in the platform before approving a campaign. Staging is NOT the brain's test mode.
    test_mode = the mode the RUN being worked on was loaded in; a run the brain itself loaded as a
    test stays a test (BRAIN_TEST_MODE), whatever the platform says.
    """
    values = {"REVENUEOS_PROJECT_ID": selection["project_id"], "CUSTOMER_KEY": selection["customer_key"]}
    if (dotenv_customer_key or "").strip() != selection["customer_key"]:
        values["CUSTOMER_DISPLAY_NAME"] = selection["customer_name"]
        values["CUSTOMER_LONG_NAMES"] = ""
        values["CUSTOMER_WEBSITE"] = ""
    if accounts is not None:
        for column, setting in ACCOUNT_SETTINGS.items():
            values[setting] = (accounts.get(column) or "").strip()
    elif (dotenv_customer_key or "").strip() != selection["customer_key"]:
        for setting in ACCOUNT_SETTINGS.values():
            values[setting] = ""

    if customer_email and values.get("QUICKHOWL_CLIENT_REFERENCE") == "":   # no existing calling account
        values.update({
            "QUICKHOWL_CLIENT_REFERENCE": selection["customer_key"],
            "QUICKHOWL_USER_EMAIL": customer_email,
            "QUICKHOWL_USER_PASSWORD": customer_email,
            "QUICKHOWL_USER_FULL_NAME": selection["customer_name"],
            "QUICKHOWL_USER_COMPANY": selection["customer_name"],
        })

    if staging is not None or test_mode is not None:
        values["BRAIN_TEST_MODE"] = "true" if test_mode else "false"
        values.update({setting: "" for setting in STAGING_SETTINGS})
        if staging and staging["staging"]:
            values.update({"STAGING_COMPANIES": str(staging.get("companies") or ""),
                           "STAGING_PEOPLE": str(staging.get("people") or "")})

    # The caller line is per customer too: this customer's own, or none. Never another customer's.
    # (The customer .env was written for keeps .env's line until it has its own, like its accounts.)
    if caller_line or (dotenv_customer_key or "").strip() != selection["customer_key"]:
        for column, setting in CALLER_LINE_SETTINGS.items():
            values[setting] = (caller_line or {}).get(column, "")

    # Mailbox details are per customer too: always set, so none is ever inherited.
    values.update({setting: "" for setting in SENDER_SETTINGS})
    if sender and values.get("CLIENT_REFERENCE") == "":       # no existing email account for this customer
        values.update({
            "CLIENT_REFERENCE": selection["customer_key"],
            "EMAIL_TOOL_USER_EMAIL": sender["login_email"],
            "EMAIL_WORKSPACE_NAME": selection["customer_name"],
            "SMTP_HOST": sender["host"], "SMTP_PORT": sender["port"], "SMTP_SECURITY": sender["mode"],
            "SMTP_USERNAME": sender["email"], "SMTP_PASSWORD": sender["password"],
            "SMTP_DISPLAY_NAME": sender["name"], "SMTP_IMAP_HOST": sender["imap_host"],
            "EMAIL_CAMPAIGN_TYPE": sender["campaign_type"],
        })
    return values


def dotenv_customer_key() -> str | None:
    """CUSTOMER_KEY as written in the .env file itself (not the process environment)."""
    try:
        from dotenv import dotenv_values
    except ImportError:
        return None
    return dotenv_values().get("CUSTOMER_KEY")


def script_path(script: str, brain_dir: str = BRAIN_DIR) -> str:
    """The full path of a brain script, or stop: only scripts in the brain folder itself are run."""
    path = os.path.join(brain_dir, script)
    if not script.endswith(".py") or os.path.basename(script) != script or not os.path.exists(path):
        raise SelectError(f"STOPPED: '{script}' is not a brain script in this folder.")
    return path


def run_script(script: str, arguments: list[str], selection: dict, call=subprocess.call, client=None) -> int:
    """Run one brain script for the chosen project, in that customer's own folder. Returns its exit code."""
    path = script_path(script)
    dotenv_key = dotenv_customer_key()
    folder = prepare_workspace(workspace_for(selection["customer_key"], dotenv_key))
    client = client or connect()
    environment = {**os.environ, **environment_for(selection, dotenv_key, **read_setup(client, selection["project_id"]))}
    print(f"[run_for] {selection['customer_name']}  key={selection['customer_key']}  folder={folder}")
    return call([sys.executable, path, *arguments], env=environment, cwd=folder)



# ================= THE WHOLE FLOW (start) =================

def steps_for(month: str) -> list[tuple[str, str, list[str]]]:
    """(name, script, arguments) for every step of the flow, in order."""
    # check_system needs the plan files that 'plan' writes, so in a customer's own (new) folder
    # it can only pass after planning.
    return [
        ("intake", "intake_adapter.py", []),
        ("plan", "master_agent.py", ["--intake"]),
        ("check", "check_system.py", []),
        ("load", "orchestrator.py", ["load", month]),
        ("run", "orchestrator.py", ["run", month, "--until-wait"]),
    ]


def steps_from(month: str, first: str) -> list[tuple[str, str, list[str]]]:
    """The steps from 'first' to the end. Stops on an unknown step name."""
    if first not in STEP_NAMES:
        raise SelectError(f"STOPPED: unknown step '{first}'. Steps: {' | '.join(STEP_NAMES)}")
    return steps_for(month)[STEP_NAMES.index(first):]


def check_month(month: str) -> str:
    if not MONTH.match(month):
        raise SelectError(f"STOPPED: '{month}' is not a month. Use e.g. 2026-10 (or 2026-10-r2 for a replacement plan).")
    return month


def this_month(today: datetime.date | None = None) -> str:
    """The current month as the run key uses it, e.g. '2026-10'."""
    today = today or datetime.date.today()
    return f"{today.year:04d}-{today.month:02d}"


def test_mode_in(source: str) -> bool | None:
    """The DEFAULT test mode as written in quickicp.py (read as text, so no package is needed). None if not found."""
    match = re.search(r"^TEST_MODE(?:_DEFAULT)?\s*=\s*(True|False)\b", source, re.M)
    return None if not match else match.group(1) == "True"


def read_test_mode() -> bool | None:
    try:
        with open(os.path.join(BRAIN_DIR, "quickicp.py"), encoding="utf-8") as file:
            return test_mode_in(file.read())
    except OSError:
        return None


def parse_start(arguments: list[str]) -> dict:
    """start [new | <project-id>] [--month YYYY-MM] [--from STEP]"""
    options = {"wanted": "new", "month": None, "first": "intake"}
    rest = list(arguments)
    while rest:
        word = rest.pop(0)
        if word in ("--month", "--from"):
            if not rest:
                raise SelectError(f"STOPPED: {word} needs a value.")
            options["month" if word == "--month" else "first"] = rest.pop(0)
        elif word.startswith("--"):
            raise SelectError(f"STOPPED: unknown option '{word}'.")
        else:
            options["wanted"] = word
    return options


def confirm_start(selection: dict, month: str, steps: list, test_mode: bool | None, ask=input) -> None:
    """Show exactly what is about to run and make the person type the customer key. Stops on anything else."""
    mode = ("TEST MODE: small volumes, team inboxes and test numbers only" if test_mode
            else "LIVE: real volumes; real prospects are emailed and called after approval" if test_mode is False
            else "UNKNOWN: TEST_MODE could not be read from quickicp.py")
    print(f"\nCustomer : {selection['customer_name']}")
    print(f"Key      : {selection['customer_key']}")
    print(f"Project  : {selection['project_id']}")
    print(f"Month    : {month}")
    print(f"Mode     : {mode}")
    print(f"Steps    : {' -> '.join(name for name, _, _ in steps)}")
    print("The 'plan' step costs LLM calls. Every approval still waits for a person.")
    typed = ask(f"\nType the customer key ({selection['customer_key']}) to start, anything else to stop: ").strip()
    if typed != selection["customer_key"]:
        raise SelectError("STOPPED: not confirmed. Nothing was run.")


def run_steps(steps: list, selection: dict, month: str, call=subprocess.call, setup: dict | None = None) -> None:
    """Run each step as its own process, in the customer's own folder. Stop at the first failure."""
    dotenv_key = dotenv_customer_key()
    folder = prepare_workspace(workspace_for(selection["customer_key"], dotenv_key))
    environment = {**os.environ, **environment_for(selection, dotenv_key, **(setup or {}))}
    print(f"\nWorking folder: {folder}")
    for number, (name, script, arguments) in enumerate(steps, start=1):
        print(f"\n{'=' * 20} {number}/{len(steps)}: {name.upper()}  ({script} {' '.join(arguments)}) {'=' * 20}")
        code = call([sys.executable, script_path(script), *arguments], env=environment, cwd=folder)
        if code != 0:
            raise SelectError(f"\nSTOPPED at step '{name}' (exit code {code}). Fix the issue, then resume with:\n"
                              f"  python run_for.py start {selection['project_id']} --month {month} --from {name}")
    print("\nFLOW COMPLETE up to the next point a person is needed. See it in the platform's Dashboard.")
    print("To have answers given in the platform acted on automatically, keep this running:")
    print("  python run_for.py work")


def cmd_start_flow(arguments: list[str]) -> None:
    options = parse_start(arguments)
    month = check_month(options["month"] or this_month())
    steps = steps_from(month, options["first"])

    client = connect()
    project = choose(read_projects(client), customers_with_runs(client), options["wanted"])
    selection = selection_of(project)
    confirm_start(selection, month, steps, read_test_mode())
    save_selection(selection)
    run_steps(steps, selection, month, setup=read_setup(client, selection["project_id"]))


# ================= ASYNCHRONOUS WORKER (work) =================

def month_of(run_key: str, customer: str) -> str:
    """'new-co-222222-2026-10-r2' + 'new-co-222222' -> '2026-10-r2'."""
    return run_key[len(customer) + 1:] if run_key.startswith(customer + "-") else run_key


def read_work(client) -> list[dict]:
    """
    Every customer's latest ACTIVE run with a fingerprint of its state. Select only.
    The fingerprint changes when anything a new step could act on changes: the run's status,
    a task's status, or a decision being asked or answered.
    """
    runs = client.table(RUNS).select("id, run_key, customer, status, test_mode, updated_at").execute().data or []
    tasks = client.table("brain_plan_tasks").select("run_id, status, updated_at").execute().data or []
    decisions = client.table("brain_decisions").select("run_id, status, answered_at").execute().data or []

    latest = {}
    for run in sorted(runs, key=lambda row: row["id"]):
        latest[run["customer"]] = run          # the newest run of each customer
    work = []
    for customer, run in latest.items():
        if run.get("status") not in ACTIVE_RUN_STATUSES or not CUSTOMER_KEY.match(customer or ""):
            continue
        own_tasks = sorted((t.get("status") or "", t.get("updated_at") or "") for t in tasks if t["run_id"] == run["id"])
        own = [d for d in decisions if d["run_id"] == run["id"]]
        open_count = sum(1 for d in own if d.get("status") == "open")
        last_answer = max((d.get("answered_at") or "" for d in own), default="")
        work.append({
            "customer": customer, "run_key": run["run_key"], "month": month_of(run["run_key"], customer),
            "test_mode": run.get("test_mode"),
            "open_decisions": open_count,
            "fingerprint": json.dumps([run.get("status"), own_tasks, open_count, last_answer]),
        })
    return sorted(work, key=lambda item: item["customer"])


def due(work: list[dict], seen: dict, busy: set) -> list[dict]:
    """Customers to start a step for now: something changed since their last step, and none is running."""
    return [item for item in work
            if item["customer"] not in busy and seen.get(item["customer"]) != item["fingerprint"]]


BUYS_DATA = re.compile(r"next would buy data \((T\d+)\)")     # orchestrator.py --until-wait, when it stops for this


def blocked_paid_task(log_text: str) -> str | None:
    """The task the safe step refused to start because it buys data ('T3'), or None."""
    found = BUYS_DATA.findall(log_text or "")
    return found[-1] if found else None


def last_stopped_line(log_text: str) -> str:
    lines = [line.strip() for line in (log_text or "").splitlines() if line.startswith("STOPPED")]
    return lines[-1] if lines else ""


def read_log(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as file:
            return file.read()
    except OSError:
        return ""


def start_background_step(item: dict, projects: list[dict], dotenv_key: str | None, popen=subprocess.Popen,
                          client=None, until_wait: bool = True):
    """
    Start one step for one customer, in its folder, output to a log file:
        until_wait=True    orchestrator.py run <month> --until-wait   the safe step (never buys data)
        until_wait=False   orchestrator.py run <month>                ONE step, started by a person in the
                                                                      platform: it may start a task that buys data
    """
    project = next((p for p in projects if p.get("brain_customer_key") == item["customer"]), None)
    if not project:
        return None, f"no platform project has the key {item['customer']}"
    selection = selection_of(project)
    setup = read_setup(client, selection["project_id"]) if client else {}
    folder = prepare_workspace(workspace_for(item["customer"], dotenv_key))
    log_folder = os.path.join(folder, "logs")
    os.makedirs(log_folder, exist_ok=True)
    log_path = os.path.join(log_folder, time.strftime("%Y%m%d-%H%M%S") + "-run.log")
    log = open(log_path, "w", encoding="utf-8")
    process = popen([sys.executable, script_path("orchestrator.py"), "run", item["month"]]
                    + (["--until-wait"] if until_wait else []),
                    env=child_environment(environment_for(selection, dotenv_key, **setup, test_mode=item.get("test_mode"))),
                    cwd=folder,
                    stdout=log, stderr=subprocess.STDOUT)
    return {"process": process, "log": log, "log_path": log_path, "month": item["month"],
            "project_id": selection["project_id"]}, None


def work_once(client, seen: dict, running: dict, dotenv_key: str | None, popen=subprocess.Popen,
              paid_steps=None) -> None:
    """
    One look: note finished steps, start the steps a person started in the platform, then start
    the safe step for every customer that is due. paid_steps (platform_approval.PaidSteps) is how
    a step that buys data is offered to, and started by, a person; without it such a step waits
    for a typed command, as before.
    """
    for customer, job in list(running.items()):
        code = job["process"].poll()
        if code is None:
            continue
        job["log"].close()
        del running[customer]
        print(f"[{time.strftime('%H:%M:%S')}] {customer}: step finished (exit {code}); log: {job['log_path']}")
        if not paid_steps:
            continue
        text = read_log(job["log_path"])
        if job.get("paid_step"):
            # The one step a person started: say how it ended. The run itself carries on below.
            ended = ({"status": "done"} if code == 0 else
                     {"status": "failed", "error": (last_stopped_line(text) or f"The step stopped (exit code {code}).")[:REQUEST_ERROR_MAX]})
            paid_steps.update(job["paid_step"], ended, only_from=("running",))
            seen.pop(customer, None)       # it ran ONE step: the safe step must look at the run again
        else:
            task_id = blocked_paid_task(text)
            if task_id:
                if paid_steps.offer(job["project_id"], customer, job["month"], task_id):
                    print(f"[{time.strftime('%H:%M:%S')}] {customer}: {task_id} buys data; waiting for a person to start it in the platform")
            else:
                paid_steps.withdraw(customer)

    if paid_steps:
        projects = None
        for step in paid_steps.approved():
            customer = step["customer_key"]
            if customer in running:
                continue
            if not paid_steps.update(step["id"], {"status": "running"}, only_from=("approved",)):
                continue                                  # another worker took it
            projects = projects or read_projects(client)
            modes = {item["customer"]: item.get("test_mode") for item in read_work(client)}
            job, problem = start_background_step({"customer": customer, "month": step["month"],
                                                  "test_mode": modes.get(customer)}, projects, dotenv_key,
                                                 popen, client, until_wait=False)
            if problem:
                paid_steps.update(step["id"], {"status": "failed", "error": problem}, only_from=("running",))
                continue
            job["paid_step"] = step["id"]
            running[customer] = job
            seen[customer] = "running"
            print(f"[{time.strftime('%H:%M:%S')}] {customer}: started run {step['month']} for {step['task_id']} "
                  "(started by a person in the platform; buys data)")

    work = read_work(client)
    # Remember the state as it is once a step has finished, so the same state is not run again.
    for item in work:
        if item["customer"] not in running and item["customer"] in seen and seen[item["customer"]] == "running":
            seen[item["customer"]] = item["fingerprint"]

    to_start = due(work, seen, set(running))
    if not to_start:
        return
    projects = read_projects(client)
    for item in to_start:
        job, problem = start_background_step(item, projects, dotenv_key, popen, client)
        if problem:
            print(f"[{time.strftime('%H:%M:%S')}] {item['customer']}: skipped ({problem})")
            seen[item["customer"]] = item["fingerprint"]
            continue
        running[item["customer"]] = job
        seen[item["customer"]] = "running"
        waiting = f", {item['open_decisions']} decision(s) open" if item["open_decisions"] else ""
        print(f"[{time.strftime('%H:%M:%S')}] {item['customer']}: started run {item['month']} --until-wait{waiting}")


def cmd_work(arguments: list[str]) -> None:
    once = arguments == ["--once"]
    if arguments and not once:
        raise SelectError("Usage: python run_for.py work [--once]")
    import platform_approval
    client, dotenv_key = connect(), dotenv_customer_key()
    requests, worker = platform_approval.Requests(client), socket.gethostname()
    paid_steps = platform_approval.PaidSteps(client)
    if not paid_steps.available():
        print("Starting steps that buy data from the platform is OFF "
              "(run supabase/migrations/0007_brain_paid_steps.sql once, then restart the worker).")
        paid_steps = None
    seen, running, planning = {}, {}, {}
    print(f"Worker '{worker}' started. Looking every {WORK_EVERY_SECONDS} seconds. Ctrl+C to stop.")
    try:
        lost = fail_orphans(requests, worker)
        if lost:
            print(f"{lost} request(s) were in progress when the worker last stopped; marked failed (Try again in the platform).")
    except Exception as error:
        print(f"Requests from the platform are OFF: {error}\n"
              "  (run supabase/migrations/0003_project_brain_key.sql once, then restart the worker)")
        requests = None
    try:
        while True:
            try:
                if requests:
                    pick_up_requests(requests, planning, dotenv_key, worker)
                work_once(client, seen, running, dotenv_key, paid_steps=paid_steps)
            except SelectError:
                raise
            except Exception as error:      # e.g. the network dropped: keep the worker alive, look again
                print(f"[{time.strftime('%H:%M:%S')}] look failed, trying again: {type(error).__name__}: {error}")
            if once and not running and not planning:
                break
            time.sleep(2 if once else WORK_EVERY_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping: no new steps will be started.")
    # A step that is running may be in the middle of a tool call: let it finish, never kill it.
    for customer, job in running.items():
        print(f"Waiting for {customer} to finish its step...")
        job["process"].wait()
        job["log"].close()
    for job in planning.values():
        print(f"Waiting for {job['customer']} to finish planning (it may be waiting for approval in the platform)...")
        job["process"].wait()
        job["log"].close()
    print("Worker stopped.")

# ================= REQUESTS FROM THE PLATFORM (nobody types a command) =================

def free_month(run_keys: set[str], customer: str, month: str) -> str:
    """The month to load: 'month' itself, or its first revision that has no run yet (-r2, -r3, ...)."""
    if f"{customer}-{month}" not in run_keys:
        return month
    number = 2
    while f"{customer}-{month}-r{number}" in run_keys:
        number += 1
    return f"{month}-r{number}"


def child_environment(extra: dict) -> dict:
    """The environment of a background step. Output is UTF-8 and unbuffered, so logs are complete."""
    return {**os.environ, **extra, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}


def run_logged(command: list[str], env: dict, cwd: str) -> tuple[int, str]:
    """Run one step, pass its output on (to the log), and return (exit code, why it stopped: its last STOPPED line, or its FAIL lines)."""
    process = subprocess.Popen(command, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    stopped, failed = "", []
    for line in process.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
        if line.startswith("STOPPED"):
            stopped = line.strip()
        elif line.startswith("FAIL "):                     # check_system.py reports this way
            failed.append(" ".join(line.split()))
    return process.wait(), stopped or "; ".join(failed[:4])


def plan_for_request(request_id: str, requests, client, dotenv_key: str | None, run=run_logged,
                     test_mode=None, today: datetime.date | None = None) -> str:
    """
    Everything between "Start execution" in the platform and a loaded run, for ONE request:
        intake -> plan (approved in the platform) -> check -> load
    Returns 'loaded', 'failed' or 'cancelled'; the same is written on the request for the platform.
    A request that failed at 'check' or 'load' resumes there: its approved plan is still in the folder.
    """
    request = requests.get(request_id)
    if not request or request.get("status") != "planning":
        raise SelectError(f"STOPPED: request {request_id} is not claimed for planning.")

    def fail(step: str, message: str) -> str:
        requests.update(request_id, {"status": "failed", "failed_step": step, "error": message[:REQUEST_ERROR_MAX]})
        print(f"[request] failed at '{step}': {message}")
        return "failed"

    project = next((p for p in read_projects(client) if str(p["id"]).lower() == str(request["project_id"]).lower()), None)
    if not project or not usable(project) or project["brain_customer_key"] != request.get("customer_key"):
        return fail("start", "This project is not ready for the Brain: its GTM plan must be finished first.")
    selection = selection_of(project)
    customer = selection["customer_key"]

    runs = [row for row in (client.table(RUNS).select("run_key, customer, status").execute().data or [])
            if row.get("customer") == customer]
    resume = request.get("failed_step") in REQUEST_RESUME_STEPS and MONTH.match(request.get("month") or "")
    first = request["failed_step"] if resume else "intake"
    month = request["month"] if resume else free_month({row["run_key"] for row in runs}, customer, this_month(today))
    in_progress = [row["run_key"] for row in runs
                   if row.get("status") in ACTIVE_RUN_STATUSES and row["run_key"] != f"{customer}-{month}"]
    if in_progress:
        return fail("start", f"This customer already has a run in progress ({in_progress[0]}). "
                             "It must finish or be closed before a new month is planned.")

    # Resuming after the approval: the request was already approved (approved_by is kept on it).
    resumed = {"status": "finishing"} if resume and request.get("approved_by") else {}
    setup = read_setup(client, selection["project_id"])
    planned_in = setup["staging"]["staging"] if setup.get("staging") else (test_mode or read_test_mode)()
    requests.update(request_id, {"month": month, "test_mode": planned_in, "failed_step": None,
                                 "heartbeat_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **resumed})
    folder = prepare_workspace(workspace_for(customer, dotenv_key))
    environment = child_environment({**environment_for(selection, dotenv_key, **setup),
                                     "BRAIN_REQUEST_ID": request_id})
    print(f"[request] {selection['customer_name']}  key={customer}  month={month}  from={first}  folder={folder}")

    steps = [step for step in steps_from(month, first) if step[0] in REQUEST_STEPS]
    for name, script, arguments in steps:
        current = requests.get(request_id)
        if not current or current.get("status") == "cancelled":
            print("[request] cancelled in the platform; stopping.")
            return "cancelled"
        if name in REQUEST_RESUME_STEPS and current.get("status") != "finishing":
            # Only an approval recorded in the platform moves a request to 'finishing'.
            return fail("plan", "The month plan has no recorded approval, so nothing was loaded.")
        print(f"\n{'=' * 20} {name.upper()}  ({script} {' '.join(arguments)}) {'=' * 20}")
        code, stopped = run([sys.executable, script_path(script), *arguments], environment, folder)
        if code != 0:
            current = requests.get(request_id) or {}
            if current.get("status") in ("cancelled", "failed"):      # already recorded by the step itself
                return current["status"]
            return fail(name, stopped or f"The '{name}' step stopped (exit code {code}). See the worker's log.")

    if not requests.update(request_id, {"status": "loaded", "brain_message": None}, only_from=("finishing",)):
        return fail("load", "The run was loaded but the month plan has no recorded approval.")
    print("[request] loaded. The worker starts the run on its next look.")
    return "loaded"


def start_planning(request: dict, dotenv_key: str | None, popen=subprocess.Popen) -> dict:
    """Start plan_for_request for one request as its own background process, output to a log file."""
    folder = prepare_workspace(workspace_for(request.get("customer_key") or "", dotenv_key))
    log_folder = os.path.join(folder, "logs")
    os.makedirs(log_folder, exist_ok=True)
    log_path = os.path.join(log_folder, time.strftime("%Y%m%d-%H%M%S") + "-plan.log")
    log = open(log_path, "w", encoding="utf-8")
    process = popen([sys.executable, os.path.join(BRAIN_DIR, "run_for.py"), "_plan", str(request["id"])],
                    env=child_environment({}), cwd=BRAIN_DIR, stdout=log, stderr=subprocess.STDOUT)
    return {"process": process, "log": log, "log_path": log_path, "customer": request.get("customer_key")}


def pick_up_requests(requests, planning: dict, dotenv_key: str | None, worker: str, popen=subprocess.Popen) -> None:
    """One look at the platform's requests: note finished planning, then claim and start every new request."""
    for request_id, job in list(planning.items()):
        code = job["process"].poll()
        if code is None:
            continue
        job["log"].close()
        del planning[request_id]
        if code != 0:        # it crashed before it could report; a request it already ended is left as it is
            requests.update(request_id, {"status": "failed", "failed_step": "start",
                                         "error": "Planning stopped unexpectedly. See the worker's log."})
        print(f"[{time.strftime('%H:%M:%S')}] {job['customer']}: planning ended (exit {code}); log: {job['log_path']}")

    for request in requests.requested():
        if not requests.claim(request["id"], worker):
            continue                                    # another worker took it
        try:
            planning[request["id"]] = start_planning(request, dotenv_key, popen)
        except SelectError as error:
            requests.update(request["id"], {"status": "failed", "failed_step": "start", "error": str(error)[:REQUEST_ERROR_MAX]})
            continue
        print(f"[{time.strftime('%H:%M:%S')}] {request.get('customer_key')}: request picked up; planning started")


def fail_orphans(requests, worker: str) -> int:
    """After a restart: requests this worker was planning are lost with it. Say so; the platform offers 'Try again'."""
    orphans = requests.in_progress(worker)
    for request in orphans:
        requests.update(request["id"], {"status": "failed", "failed_step": "start",
                                        "error": "The Brain worker restarted while this was in progress."})
    return len(orphans)


def cmd_plan_request(request_id: str) -> None:
    """Internal (started by the worker): python run_for.py _plan <request-id>"""
    if not UUID.match(request_id):
        raise SelectError("Usage: python run_for.py _plan <request-id>   (started by the worker, not by hand)")
    import platform_approval
    client = connect()
    plan_for_request(request_id, platform_approval.Requests(client), client, dotenv_customer_key())


# ================= COMMANDS =================

def cmd_list() -> None:
    client = connect()
    projects, ran = read_projects(client), customers_with_runs(client)
    if not projects:
        print("No projects in the platform yet.")
        return
    print(f"{'customer':<32} {'status':<22} {'brain key':<34} {'brain':<10} id")
    for project in projects:
        key = project.get("brain_customer_key") or "(none)"
        state = "has runs" if key in ran else ("ready" if usable(project) else "-")
        print(f"{(project.get('customer_name') or '')[:31]:<32} {(project.get('status') or '')[:21]:<22} "
              f"{key[:33]:<34} {state:<10} {project['id']}")
    print("\n'ready' = plan finished, no brain run yet:  python run_for.py select new")


def cmd_select(wanted: str) -> None:
    client = connect()
    project = choose(read_projects(client), customers_with_runs(client), wanted)
    selection = selection_of(project)
    save_selection(selection)
    print(f"Chosen: {selection['customer_name']}  (key {selection['customer_key']}, project {selection['project_id']})")
    print("Next:   python run_for.py check_system.py")


def cmd_show() -> None:
    selection = load_selection()
    print(f"Chosen: {selection['customer_name']}  (key {selection['customer_key']}, project {selection['project_id']})")
    for name, value in environment_for(selection, dotenv_customer_key()).items():
        if (name not in ACCOUNT_SETTINGS.values() and name not in SENDER_SETTINGS and name not in STAGING_SETTINGS
                and name not in CALLER_LINE_SETTINGS.values()):
            print(f"  {name} = {value or '(empty)'}")
    print("  Tool accounts: this project's own, from the platform (Tools > Tool accounts); not shown here.")


# ================= SELFTEST (no network, no LLM, throwaway folder) =================

class FakeQuery:
    def __init__(self, rows, calls, table):
        self._rows, self._calls, self._table = rows, calls, table

    def select(self, columns):
        self._calls.append((self._table, "select", columns))
        return self

    def eq(self, column, value):
        self._rows = [row for row in self._rows if row.get(column) == value]
        return self

    def execute(self):
        return type("Reply", (), {"data": list(self._rows)})()

    def __getattr__(self, name):
        raise AssertionError(f"selftest: run_for must only select, but called {name}()")


class FakeClient:
    """Mimics supabase: table().select().execute().data. Records every call; anything but select is an error."""

    def __init__(self, tables):
        self.tables, self.calls = tables, []

    def table(self, name):
        return FakeQuery(self.tables[name], self.calls, name)


def expect(label: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(f"FAIL: {label}")
    print(f"PASS  {label}")


def stops(function) -> str | None:
    try:
        function()
    except SelectError as error:
        return str(error)
    return None


def selftest() -> None:
    old = {"id": "11111111-1111-4111-8111-111111111111", "customer_name": "Old Co", "status": "plan_ready",
           "brain_customer_key": "old-co", "updated_at": "2026-10-09T00:00:00Z"}
    new = {"id": "22222222-2222-4222-8222-222222222222", "customer_name": "New Co", "status": "plan_ready",
           "brain_customer_key": "new-co-222222", "updated_at": "2026-10-12T00:00:00Z"}
    draft = {"id": "33333333-3333-4333-8333-333333333333", "customer_name": "Draft Co", "status": "checklist_ready",
             "brain_customer_key": "draft-co-333333", "updated_at": "2026-10-13T00:00:00Z"}
    client = FakeClient({PROJECTS: [old, new, draft], RUNS: [{"customer": "old-co"}, {"customer": "old-co"}]})
    projects, ran = read_projects(client), customers_with_runs(client)

    expect("projects are read newest first", [p["customer_name"] for p in projects] == ["Draft Co", "New Co", "Old Co"])
    expect("only selects were made", all(call[1] == "select" for call in client.calls) and len(client.calls) == 2)
    expect("'new' picks the one ready project with no run", choose(projects, ran, "new")["customer_name"] == "New Co")
    expect("a project can be chosen by id, in any letter case", choose(projects, ran, old["id"].upper())["brain_customer_key"] == "old-co")
    expect("a project whose plan is not ready is refused", "not ready" in (stops(lambda: choose(projects, ran, draft["id"])) or ""))
    expect("an unknown id is refused", "no project with id" in (stops(lambda: choose(projects, ran, "44444444-4444-4444-8444-444444444444")) or ""))
    expect("text that is not an id is refused", "not a project id" in (stops(lambda: choose(projects, ran, "new co")) or ""))
    expect("'new' with nothing waiting stops", "no ready project is waiting" in (stops(lambda: choose([old, draft], ran, "new")) or ""))
    second = {**new, "id": "55555555-5555-4555-8555-555555555555", "customer_name": "Other Co", "brain_customer_key": "other-co-555555"}
    expect("'new' with two candidates never guesses", "Choose one by id" in (stops(lambda: choose([new, second], ran, "new")) or ""))
    expect("a bad key is refused", "no valid brain key" in (stops(lambda: choose([{**new, "brain_customer_key": "Bad Key"}], ran, new["id"])) or ""))

    selection = selection_of(new)
    other = environment_for(selection, "old-co")
    expect("another customer's naming settings are replaced or emptied",
           {name: other[name] for name in ("REVENUEOS_PROJECT_ID", "CUSTOMER_KEY", "CUSTOMER_DISPLAY_NAME",
                                           "CUSTOMER_LONG_NAMES", "CUSTOMER_WEBSITE")}
           == {"REVENUEOS_PROJECT_ID": new["id"], "CUSTOMER_KEY": "new-co-222222",
               "CUSTOMER_DISPLAY_NAME": "New Co", "CUSTOMER_LONG_NAMES": "", "CUSTOMER_WEBSITE": ""})
    same = environment_for(selection_of(old), "old-co")
    expect("the .env customer keeps its own naming settings",
           {name: value for name, value in same.items() if name not in SENDER_SETTINGS}
           == {"REVENUEOS_PROJECT_ID": old["id"], "CUSTOMER_KEY": "old-co"})

    # ---- each customer's own tool accounts ----
    row = {"project_id": new["id"], "email_client_reference": " client-new ", "email_user_email": "ops@new.co",
           "calls_client_reference": "qh-new", "calls_user_email": "calls@new.co", "calls_user_password": "secret-pass",
           "calls_user_full_name": "New Caller", "calls_user_company": None}
    accounts_client = FakeClient({ACCOUNTS: [row, {**row, "project_id": old["id"], "email_client_reference": "client-old"}]})
    own_accounts = read_accounts(accounts_client, new["id"])
    expect("a project's accounts are read for that project only, with a select",
           own_accounts["email_client_reference"] == " client-new " and accounts_client.calls[0][1] == "select")
    expect("a project with no accounts row, or no accounts table yet, has none",
           read_accounts(accounts_client, draft["id"]) is None and read_accounts(FakeClient({}), new["id"]) is None)
    given = environment_for(selection, "old-co", own_accounts)
    expect("the tools are handed this customer's own accounts",
           given["CLIENT_REFERENCE"] == "client-new" and given["EMAIL_TOOL_USER_EMAIL"] == "ops@new.co"
           and given["QUICKHOWL_CLIENT_REFERENCE"] == "qh-new" and given["QUICKHOWL_USER_PASSWORD"] == "secret-pass")
    expect("a field left empty stays empty: it is never filled from .env", given["QUICKHOWL_USER_COMPANY"] == "")
    expect("every account setting is covered", set(ACCOUNT_SETTINGS.values()) <= set(given))
    expect("its own accounts win even for the customer .env was written for",
           environment_for(selection_of(old), "old-co", {"email_client_reference": "client-old"})["CLIENT_REFERENCE"] == "client-old")
    expect("a customer without accounts never inherits another customer's from .env",
           all(other[setting] == "" for setting in ACCOUNT_SETTINGS.values()))
    expect("the customer .env was written for keeps .env's accounts until it has its own",
           not any(setting in same for setting in ACCOUNT_SETTINGS.values()))

    # ---- a new customer's own sender (from the checklist) ----
    answers = [{"code": "SENDER-NAME", "answer": "New Co Sales"}, {"code": "SENDER-EMAIL", "answer": "sales@new.co"},
               {"code": "SENDER-SMTP-HOST", "answer": "smtp.new.co"}, {"code": "SENDER-SMTP-SECURITY", "answer": "SSL/TLS (port 465)"},
               {"code": "SENDER-SMTP-PASSWORD", "answer": "mail-secret"}, {"code": "SENDER-IMAP-HOST", "answer": "imap.new.co"},
               {"code": "Q-1", "answer": "something else"}]
    sender = sender_of(" Owner@New.Co ", answers)
    expect("complete sender details are read, with the security choice turned into the tool's own value and port",
           sender == {"login_email": "owner@new.co", "name": "New Co Sales", "email": "sales@new.co", "host": "smtp.new.co",
                      "password": "mail-secret", "mode": "IMPLICIT_TLS", "port": "465", "imap_host": "imap.new.co",
                      "campaign_type": "HYPER_PERSONALIZED"})
    expect("the customer's email style becomes the campaign type the email step is asked for",
           sender_of("o@new.co", answers + [{"code": "SENDER-STYLE", "answer": "Same email for everyone"}])["campaign_type"] == "STANDARD"
           and sender_of("o@new.co", answers + [{"code": "SENDER-STYLE", "answer": "anything else"}])["campaign_type"] == "HYPER_PERSONALIZED"
           and environment_for(selection, "old-co", {}, sender)["EMAIL_CAMPAIGN_TYPE"] == "HYPER_PERSONALIZED")
    expect("a half-filled sender is never used",
           sender_of("owner@new.co", answers[:4]) is None and sender_of("", answers) is None
           and sender_of("owner@new.co", [{**a, "answer": "TLS"} if a["code"] == "SENDER-SMTP-SECURITY" else a for a in answers]) is None)
    sender_client = FakeClient({PROJECTS: [{**new, "customer_email": "owner@new.co"}],
                                "revenueos_checklists": [{"id": "c-old", "project_id": new["id"], "is_active": False},
                                                         {"id": "c-1", "project_id": new["id"], "is_active": True}],
                                "revenueos_checklist_items": [{**a, "checklist_id": "c-1"} for a in answers]
                                                             + [{"code": "SENDER-EMAIL", "answer": "old@new.co", "checklist_id": "c-old"}]})
    expect("the sender is read from the project's ACTIVE checklist, with selects only",
           read_sender(sender_client, new["id"]) == sender and all(call[1] == "select" for call in sender_client.calls))
    expect("no checklist, or a database without the customer email yet, means no sender",
           read_sender(FakeClient({PROJECTS: [new], "revenueos_checklists": []}), new["id"]) is None
           and read_sender(FakeClient({}), new["id"]) is None)
    fresh = environment_for(selection, "old-co", {"calls_client_reference": "qh-new"}, sender)
    expect("a new customer gets its OWN email account: its key as the reference, its email as the user",
           fresh["CLIENT_REFERENCE"] == "new-co-222222" and fresh["EMAIL_TOOL_USER_EMAIL"] == "owner@new.co"
           and fresh["EMAIL_WORKSPACE_NAME"] == "New Co" and fresh["QUICKHOWL_CLIENT_REFERENCE"] == "qh-new")
    expect("and its mailbox details are handed to the email step",
           (fresh["SMTP_HOST"], fresh["SMTP_PORT"], fresh["SMTP_SECURITY"], fresh["SMTP_USERNAME"], fresh["SMTP_PASSWORD"],
            fresh["SMTP_DISPLAY_NAME"], fresh["SMTP_IMAP_HOST"])
           == ("smtp.new.co", "465", "IMPLICIT_TLS", "sales@new.co", "mail-secret", "New Co Sales", "imap.new.co"))
    calling = environment_for(selection, "old-co", {}, sender, "owner@new.co")
    expect("the same customer email opens the customer's own calling account, under its key",
           (calling["QUICKHOWL_CLIENT_REFERENCE"], calling["QUICKHOWL_USER_EMAIL"], calling["QUICKHOWL_USER_PASSWORD"],
            calling["QUICKHOWL_USER_FULL_NAME"], calling["QUICKHOWL_USER_COMPANY"])
           == ("new-co-222222", "owner@new.co", "owner@new.co", "New Co", "New Co")
           and calling["EMAIL_TOOL_USER_EMAIL"] == "owner@new.co")
    expect("an existing calling account is kept as it is",
           environment_for(selection, "old-co", own_accounts, None, "owner@new.co")["QUICKHOWL_USER_EMAIL"] == "calls@new.co")
    expect("without a customer email no calling account is made up",
           environment_for(selection, "old-co", {}, sender)["QUICKHOWL_CLIENT_REFERENCE"] == "")
    setup = read_setup(sender_client, new["id"])
    expect("one read gives a customer's whole setup",
           setup == {"accounts": None, "sender": sender, "customer_email": "owner@new.co", "staging": None,
                     "caller_line": None})

    # ---- a customer's own caller line ----
    line_row = {"project_id": new["id"], "calls_vobiz_auth_id": "MA_123", "calls_vobiz_auth_token": " tok-secret ", "calls_did": "+918065382389"}
    line_client = FakeClient({ACCOUNTS: [line_row, {**line_row, "project_id": old["id"], "calls_did": ""}]})
    line = read_caller_line(line_client, new["id"])
    expect("a complete caller line is read for that project only",
           line == {"calls_vobiz_auth_id": "MA_123", "calls_vobiz_auth_token": "tok-secret", "calls_did": "+918065382389"})
    expect("a half-filled line, no row, or a database without the columns means no line",
           read_caller_line(line_client, old["id"]) is None and read_caller_line(line_client, draft["id"]) is None
           and read_caller_line(FakeClient({}), new["id"]) is None)
    with_line = environment_for(selection, "old-co", {}, sender, "owner@new.co", None, None, line)
    expect("the calling step is handed this customer's own caller line",
           (with_line["QUICKHOWL_VOBIZ_AUTH_ID"], with_line["QUICKHOWL_VOBIZ_AUTH_TOKEN"], with_line["QUICKHOWL_DID"])
           == ("MA_123", "tok-secret", "+918065382389"))
    expect("without one, no caller line is handed over, so none is ever inherited",
           all(given[setting] == "" for setting in CALLER_LINE_SETTINGS.values()))
    # ---- staging or production, per customer ----
    expect("the default mode is read from quickicp.py's own line",
           test_mode_in("TEST_MODE_DEFAULT = False       # note\nTEST_MODE = staging_flag(TEST_MODE_DEFAULT)\n") is False
           and test_mode_in("TEST_MODE_DEFAULT = True\n") is True)
    staged = {"staging": True, "companies": 5, "people": 10}
    in_staging = environment_for(selection, "old-co", {}, sender, "owner@new.co", staged)
    expect("staging hands over the person's two limits, and nothing else changes: it is not test mode",
           (in_staging["BRAIN_TEST_MODE"], in_staging["STAGING_COMPANIES"], in_staging["STAGING_PEOPLE"]) == ("false", "5", "10"))
    live = environment_for(selection, "old-co", {}, sender, "owner@new.co", {**staged, "staging": False})
    expect("a production customer has no limits, even if staging numbers are still saved",
           live["BRAIN_TEST_MODE"] == "false" and all(live[setting] == "" for setting in STAGING_SETTINGS))
    expect("a run the brain loaded as a test stays a test, whatever the platform says",
           environment_for(selection, "old-co", {}, sender, "owner@new.co", {**staged, "staging": False}, test_mode=True)["BRAIN_TEST_MODE"] == "true"
           and environment_for(selection, "old-co", {}, sender, "owner@new.co", staged, test_mode=False)["BRAIN_TEST_MODE"] == "false")
    expect("before the platform has the switch, the mode is left to the brain's own default",
           "BRAIN_TEST_MODE" not in environment_for(selection, "old-co", {}, sender))
    staging_client = FakeClient({PROJECTS: [{**new, "run_mode": "staging", "staging_companies": 5, "staging_people": 10}]})
    expect("the choice is read from the project, with a select",
           read_staging(staging_client, new["id"]) == {"staging": True, "companies": 5, "people": 10}
           and read_staging(FakeClient({PROJECTS: [new]}), new["id"]) is None)
    kept = environment_for(selection, "old-co", own_accounts, sender)
    expect("a customer with an existing email account keeps it: no mailbox details are sent",
           kept["CLIENT_REFERENCE"] == "client-new" and kept["SMTP_HOST"] == "" and kept["SMTP_PASSWORD"] == ""
           and kept["EMAIL_WORKSPACE_NAME"] == "")
    expect("without sender details nothing is inherited from another customer",
           all(given[setting] == "" for setting in SENDER_SETTINGS) and all(other[setting] == "" for setting in SENDER_SETTINGS))

    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "data", "selected_project.json")
        expect("no choice yet stops with the command to run", "select new" in (stops(lambda: load_selection(path)) or ""))
        save_selection(selection, path)
        expect("the choice is saved and read back", load_selection(path) == selection)
        expect("no secret is stored in the choice", set(selection) == {"project_id", "customer_key", "customer_name"})
        with open(path, "w", encoding="utf-8") as file:
            json.dump({"project_id": "x", "customer_key": "y"}, file)
        expect("a damaged choice file is refused", "damaged" in (stops(lambda: load_selection(path)) or ""))
        expect("a script outside this folder is refused",
               "not a brain script" in (stops(lambda: script_path(os.path.join("..", "x.py"))) or "")
               and "not a brain script" in (stops(lambda: script_path("missing_script.py")) or ""))

        # ---- one folder per customer ----
        brain = os.path.join(folder, "brain")
        os.makedirs(os.path.join(brain, "knowledge"))
        with open(os.path.join(brain, "knowledge", "motm_context.md"), "w", encoding="utf-8") as file:
            file.write("context v1")
        own = workspace_for("new-co-222222", "old-co", brain)
        expect("another customer gets its own folder", own == os.path.join(brain, CUSTOMERS_DIR, "new-co-222222"))
        expect("the .env customer keeps the brain folder itself", workspace_for("old-co", "old-co", brain) == brain)
        expect("two customers never share a folder", own != workspace_for("other-co-555555", "old-co", brain))
        expect("a bad key cannot become a folder name", "not a valid customer key" in (stops(lambda: workspace_for("../etc", None, brain)) or ""))
        prepare_workspace(own, brain)
        expect("the folder gets data/, logs/ and a copy of knowledge/",
               all(os.path.isdir(os.path.join(own, name)) for name in ("data", "logs", "knowledge"))
               and open(os.path.join(own, "knowledge", "motm_context.md"), encoding="utf-8").read() == "context v1")
        with open(os.path.join(own, "data", "work_orders.json"), "w", encoding="utf-8") as file:
            file.write("{}")
        with open(os.path.join(brain, "knowledge", "motm_context.md"), "w", encoding="utf-8") as file:
            file.write("context v2")
        prepare_workspace(own, brain)
        expect("preparing again refreshes knowledge/ and keeps the customer's data",
               open(os.path.join(own, "knowledge", "motm_context.md"), encoding="utf-8").read() == "context v2"
               and os.path.exists(os.path.join(own, "data", "work_orders.json")))
        expect("preparing the brain folder itself changes nothing", prepare_workspace(brain, brain) == brain
               and not os.path.exists(os.path.join(brain, "data")))

    expect("the flow runs intake, plan, check, load, run in order",
           [(n, s, a) for n, s, a in steps_for("2026-10")] == [
               ("intake", "intake_adapter.py", []), ("plan", "master_agent.py", ["--intake"]),
               ("check", "check_system.py", []), ("load", "orchestrator.py", ["load", "2026-10"]),
               ("run", "orchestrator.py", ["run", "2026-10", "--until-wait"])])
    expect("--from resumes at that step", [n for n, _, _ in steps_from("2026-10", "load")] == ["load", "run"])
    expect("an unknown step is refused", "unknown step" in (stops(lambda: steps_from("2026-10", "deploy")) or ""))
    expect("months are checked", check_month("2026-10-r2") == "2026-10-r2" and "not a month" in (stops(lambda: check_month("Oct 2026")) or ""))
    expect("the default month is the current one", this_month(datetime.date(2026, 3, 9)) == "2026-03")
    expect("start options are read", parse_start([new["id"], "--month", "2026-11", "--from", "plan"])
           == {"wanted": new["id"], "month": "2026-11", "first": "plan"} and parse_start([])["wanted"] == "new")
    expect("an unknown option is refused", "unknown option" in (stops(lambda: parse_start(["--force"])) or ""))
    expect("TEST_MODE is read from the file text",
           test_mode_in("X = 1\nTEST_MODE = True   # note\n") is True and test_mode_in("TEST_MODE = False\n") is False
           and test_mode_in("nothing here") is None)

    steps = steps_for("2026-10")
    expect("nothing runs unless the customer key is typed exactly",
           "not confirmed" in (stops(lambda: confirm_start(selection, "2026-10", steps, False, ask=lambda _: "yes")) or ""))
    confirm_start(selection, "2026-10", steps, True, ask=lambda _: " new-co-222222 ")
    expect("typing the customer key confirms", True)

    ran_steps, seen_env, folders = [], {}, set()
    def fake_call(command, env, cwd):
        ran_steps.append([os.path.basename(command[1]), *command[2:]])
        seen_env.update({k: env[k] for k in ("CUSTOMER_KEY", "REVENUEOS_PROJECT_ID")})
        folders.add(cwd)
        return 1 if command[1].endswith("master_agent.py") else 0
    real_prepare = globals()["prepare_workspace"]
    globals()["prepare_workspace"] = lambda folder, brain_dir=BRAIN_DIR: folder      # no folder is created in the test
    try:
        stopped = stops(lambda: run_steps(steps, selection, "2026-10", call=fake_call)) or ""
    finally:
        globals()["prepare_workspace"] = real_prepare
    expect("the flow stops at the first failing step and later steps do not run",
           ran_steps == [["intake_adapter.py"], ["master_agent.py", "--intake"]])
    expect("every step runs in that customer's own folder",
           folders == {os.path.join(BRAIN_DIR, CUSTOMERS_DIR, "new-co-222222")})
    expect("it says how to resume from that step", f"start {new['id']} --month 2026-10 --from plan" in stopped)
    expect("each step is given the chosen project's values",
           seen_env == {"CUSTOMER_KEY": "new-co-222222", "REVENUEOS_PROJECT_ID": new["id"]})

    # ---- the asynchronous worker ----
    expect("the month is read from the run key", month_of("new-co-222222-2026-10-r2", "new-co-222222") == "2026-10-r2")
    def work_client(answered_at=None, old_status="done", task_status="waiting_human"):
        return FakeClient({
            RUNS: [{"id": 1, "run_key": "old-co-2026-09", "customer": "old-co", "status": "done", "updated_at": "a"},
                   {"id": 2, "run_key": "old-co-2026-10", "customer": "old-co", "status": old_status, "updated_at": "b"},
                   {"id": 3, "run_key": "new-co-222222-2026-10", "customer": "new-co-222222", "status": "waiting_human", "updated_at": "c"}],
            "brain_plan_tasks": [{"run_id": 3, "status": task_status, "updated_at": "t1"}, {"run_id": 2, "status": "done", "updated_at": "t0"}],
            "brain_decisions": [{"run_id": 3, "status": "answered" if answered_at else "open", "answered_at": answered_at}],
            PROJECTS: [old, new, draft],
        })
    first_look = read_work(work_client())
    expect("only customers whose LATEST run is active have work", [w["customer"] for w in first_look] == ["new-co-222222"]
           and first_look[0]["month"] == "2026-10" and first_look[0]["open_decisions"] == 1)
    expect("two customers with active runs are both listed, independently",
           [w["customer"] for w in read_work(work_client(old_status="running"))] == ["new-co-222222", "old-co"])
    expect("answering a decision changes the fingerprint",
           read_work(work_client("2026-10-12T09:50:00Z"))[0]["fingerprint"] != first_look[0]["fingerprint"])
    expect("a task moving changes the fingerprint",
           read_work(work_client(task_status="running"))[0]["fingerprint"] != first_look[0]["fingerprint"])
    expect("nothing changed -> same fingerprint", read_work(work_client())[0]["fingerprint"] == first_look[0]["fingerprint"])
    expect("a customer is due the first time it is seen", [w["customer"] for w in due(first_look, {}, set())] == ["new-co-222222"])
    expect("not due again while nothing changed", due(first_look, {"new-co-222222": first_look[0]["fingerprint"]}, set()) == [])
    expect("not due while its step is still running", due(first_look, {}, {"new-co-222222"}) == [])

    class FakeProcess:
        def __init__(self):
            self.code = None
        def poll(self):
            return self.code
        def wait(self):
            return self.code
    started = []
    def fake_popen(command, env, cwd, stdout, stderr):
        started.append({"args": [os.path.basename(command[1]), *command[2:]], "key": env["CUSTOMER_KEY"], "cwd": cwd})
        return FakeProcess()
    with tempfile.TemporaryDirectory() as folder:
        real_workspace = globals()["workspace_for"]
        globals()["workspace_for"] = lambda key, dotenv_key, brain_dir=BRAIN_DIR: os.path.join(folder, key)
        try:
            seen, running = {}, {}
            work_once(work_client(old_status="running"), seen, running, "someone-else", popen=fake_popen)
            expect("the worker starts the safe step for each due customer, in parallel",
                   [(j["args"], j["key"]) for j in started] == [
                       (["orchestrator.py", "run", "2026-10", "--until-wait"], "new-co-222222"),
                       (["orchestrator.py", "run", "2026-10", "--until-wait"], "old-co")])
            expect("each in its own folder, with a log file",
                   len({j["cwd"] for j in started}) == 2 and all(os.path.exists(job["log_path"]) for job in running.values()))
            work_once(work_client(old_status="running"), seen, running, "someone-else", popen=fake_popen)
            expect("a customer whose step is still running is not started twice", len(started) == 2)
            for job in running.values():
                job["process"].code = 0
            work_once(work_client(old_status="running"), seen, running, "someone-else", popen=fake_popen)
            expect("after a step finishes with nothing changed, it is not started again", len(started) == 2 and not running)
            work_once(work_client("2026-10-12T09:50:00Z", old_status="running"), seen, running, "someone-else", popen=fake_popen)
            expect("answering a decision starts ONLY that customer's next step",
                   len(started) == 3 and started[2]["key"] == "new-co-222222" and list(running) == ["new-co-222222"])
            for job in running.values():
                job["log"].close()
        finally:
            globals()["workspace_for"] = real_workspace

    # ---- requests from the platform ----
    import platform_approval
    expect("the first plan of a month uses the month itself", free_month(set(), "new-co-222222", "2026-10") == "2026-10")
    expect("a month that already has runs gets the next free revision",
           free_month({"new-co-222222-2026-10", "new-co-222222-2026-10-r2"}, "new-co-222222", "2026-10") == "2026-10-r3")
    expect("background steps write UTF-8, unbuffered",
           child_environment({"A": "1"})["PYTHONIOENCODING"] == "utf-8" and child_environment({})["PYTHONUNBUFFERED"] == "1")

    class Store(platform_approval.MemoryRequests):
        def requested(self):
            return [dict(self.row)] if self.row.get("status") == "requested" else []
        def claim(self, request_id, worker):
            return self.update(request_id, {"status": "planning", "worker": worker}, only_from=("requested",))
        def in_progress(self, worker):
            return [dict(self.row)] if self.row.get("worker") == worker and self.row.get("status") in ("planning", "awaiting_approval", "finishing") else []

    rid = "99999999-9999-4999-8999-999999999999"
    def request_store(**fields):
        return Store({"id": rid, "project_id": new["id"], "customer_key": "new-co-222222", "status": "planning", **fields})
    def plan_client(runs=()):
        return FakeClient({PROJECTS: [old, new, draft], RUNS: list(runs)})
    real_prepare = globals()["prepare_workspace"]
    globals()["prepare_workspace"] = lambda folder, brain_dir=BRAIN_DIR: folder
    try:
        def planner(store, fail_at=None, approve=True, cancel_at=None):
            ran = []
            def run(command, env, cwd):
                name = os.path.basename(command[1]) + (" " + command[2] if len(command) > 2 else "")
                ran.append((name, env.get("BRAIN_REQUEST_ID"), env["CUSTOMER_KEY"], cwd))
                if cancel_at and cancel_at in name:
                    store.row["status"] = "cancelled"
                    return 1, "STOPPED: this request was cancelled in the platform. Nothing was approved."
                if fail_at and fail_at in name:
                    return 1, "STOPPED: OPENAI_API_KEY is missing in .env."
                if "master_agent" in name and approve:
                    store.row.update({"status": "finishing", "approved_by": "Asha"})   # what the approval reader does
                return 0, ""
            return ran, run

        store = request_store()
        ran, run = planner(store)
        outcome = plan_for_request(rid, store, plan_client(), "old-co", run=run, test_mode=lambda: False, today=datetime.date(2026, 10, 12))
        expect("a request runs intake, plan, check, load and ends 'loaded'",
               outcome == "loaded" and store.row["status"] == "loaded"
               and [name for name, *_ in ran] == ["intake_adapter.py", "master_agent.py --intake", "check_system.py", "orchestrator.py load"])
        expect("the month and the mode are recorded on the request", store.row["month"] == "2026-10" and store.row["test_mode"] is False)
        expect("every step gets the request id, the customer's key and the customer's own folder",
               {(r, k, c) for _, r, k, c in ran} == {(rid, "new-co-222222", os.path.join(BRAIN_DIR, CUSTOMERS_DIR, "new-co-222222"))})
        expect("the run step is NOT part of a request (the worker starts it)", not any("run" == name.split()[-1] for name, *_ in ran))

        store = request_store()
        ran, run = planner(store, fail_at="check_system")
        outcome = plan_for_request(rid, store, plan_client(), "old-co", run=run, test_mode=lambda: True, today=datetime.date(2026, 10, 12))
        expect("a failing step ends the request as failed, with the step and its STOPPED line",
               outcome == "failed" and store.row["failed_step"] == "check" and "OPENAI_API_KEY is missing" in store.row["error"]
               and len(ran) == 3)

        store.row.update({"status": "planning"})          # 'Try again' in the platform, claimed by the worker
        ran, run = planner(store)
        outcome = plan_for_request(rid, store, plan_client(), "old-co", run=run, test_mode=lambda: True)
        expect("trying again resumes at the failed step: the approved plan is not planned (or paid for) twice",
               outcome == "loaded" and [name for name, *_ in ran] == ["check_system.py", "orchestrator.py load"]
               and store.row["month"] == "2026-10" and store.row["failed_step"] is None)

        store = request_store()
        ran, run = planner(store, cancel_at="master_agent")
        expect("cancelling in the platform stops the request and it stays cancelled",
               plan_for_request(rid, store, plan_client(), "old-co", run=run, test_mode=lambda: True) == "cancelled"
               and store.row["status"] == "cancelled" and len(ran) == 2)

        store = request_store()
        ran, run = planner(store, approve=False)
        expect("a plan step that ends without an approval is never loaded",
               plan_for_request(rid, store, plan_client(), "old-co", run=run, test_mode=lambda: True) == "failed"
               and store.row["failed_step"] == "plan" and len(ran) == 2)

        store = request_store()
        ran, run = planner(store)
        active = [{"run_key": "new-co-222222-2026-10", "customer": "new-co-222222", "status": "running"}]
        expect("a customer with a run in progress is not planned again",
               plan_for_request(rid, store, plan_client(active), "old-co", run=run, test_mode=lambda: True, today=datetime.date(2026, 10, 12)) == "failed"
               and "already has a run in progress" in store.row["error"] and ran == [])
        closed = [{"run_key": "new-co-222222-2026-10", "customer": "new-co-222222", "status": "stopped"}]
        store = request_store()
        ran, run = planner(store)
        plan_for_request(rid, store, plan_client(closed), "old-co", run=run, test_mode=lambda: True, today=datetime.date(2026, 10, 12))
        expect("after a closed run, the new plan is loaded as the next revision", store.row["month"] == "2026-10-r2")

        store = request_store(project_id=draft["id"], customer_key="draft-co-333333")
        ran, run = planner(store)
        expect("a project whose plan is not ready is refused",
               plan_for_request(rid, store, plan_client(), "old-co", run=run, test_mode=lambda: True) == "failed" and ran == [])
        store = request_store(customer_key="old-co")
        expect("a request naming another customer's key is refused",
               plan_for_request(rid, store, plan_client(), "old-co", run=planner(store)[1], test_mode=lambda: True) == "failed")
        expect("only a claimed request is planned",
               "not claimed" in (stops(lambda: plan_for_request(rid, request_store(status="requested"), plan_client(), "old-co")) or ""))
    finally:
        globals()["prepare_workspace"] = real_prepare

    with tempfile.TemporaryDirectory() as folder:
        real_workspace = globals()["workspace_for"]
        globals()["workspace_for"] = lambda key, dotenv_key, brain_dir=BRAIN_DIR: real_workspace(key, dotenv_key, folder)
        try:
            store, planning, launched = request_store(status="requested"), {}, []
            def plan_popen(command, env, cwd, stdout, stderr):
                launched.append([os.path.basename(command[1]), *command[2:]])
                return FakeProcess()
            pick_up_requests(store, planning, "old-co", "pc-1", popen=plan_popen)
            expect("the worker claims a new request and starts its planning in the background",
                   store.row["status"] == "planning" and store.row["worker"] == "pc-1"
                   and launched == [["run_for.py", "_plan", rid]] and list(planning) == [rid])
            pick_up_requests(store, planning, "old-co", "pc-1", popen=plan_popen)
            expect("a request is never started twice", len(launched) == 1)
            planning[rid]["process"].code = 3
            pick_up_requests(store, planning, "old-co", "pc-1", popen=plan_popen)
            expect("planning that crashes is reported on the request", store.row["status"] == "failed" and not planning)

            store = request_store(status="requested", customer_key="../etc")
            pick_up_requests(store, {}, "old-co", "pc-1", popen=plan_popen)
            expect("a request with a bad key is failed, not started", store.row["status"] == "failed" and len(launched) == 1)

            store = request_store(status="awaiting_approval", worker="pc-1")
            expect("after a restart, the worker's unfinished requests are marked failed",
                   fail_orphans(store, "pc-1") == 1 and store.row["status"] == "failed" and "restarted" in store.row["error"])
            store = request_store(status="awaiting_approval", worker="pc-2")
            expect("another worker's requests are left alone", fail_orphans(store, "pc-1") == 0 and store.row["status"] == "awaiting_approval")
        finally:
            globals()["workspace_for"] = real_workspace

    # ---- a step that buys data is offered to a person, and started by one ----
    expect("the safe step's own words say which task buys data",
           blocked_paid_task("x\nSTOPPED after 1 step(s): next would buy data (T3): run it without --until-wait\n") == "T3"
           and blocked_paid_task("STOPPED after 2 step(s): a person is needed") is None)

    class FakePaidSteps:
        def __init__(self):
            self.rows = []
        def offer(self, project_id, customer, month, task_id):
            if any(r["customer_key"] == customer and r["task_id"] == task_id and r["status"] in ("waiting", "approved", "running") for r in self.rows):
                return False
            self.rows.append({"id": f"p{len(self.rows) + 1}", "project_id": project_id, "customer_key": customer, "month": month,
                              "task_id": task_id, "status": "waiting"})
            return True
        def approved(self):
            return [dict(r) for r in self.rows if r["status"] == "approved"]
        def update(self, step_id, fields, only_from):
            row = next((r for r in self.rows if r["id"] == step_id and r["status"] in only_from), None)
            if row:
                row.update(fields)
            return bool(row)
        def withdraw(self, customer):
            for r in self.rows:
                if r["customer_key"] == customer and r["status"] == "waiting":
                    r["status"] = "withdrawn"

    with tempfile.TemporaryDirectory() as folder:
        real_workspace = globals()["workspace_for"]
        globals()["workspace_for"] = lambda key, dotenv_key, brain_dir=BRAIN_DIR: os.path.join(folder, key)
        try:
            paid, seen, running, begun = FakePaidSteps(), {}, {}, []
            def paid_popen(command, env, cwd, stdout, stderr):
                begun.append([os.path.basename(command[1]), *command[2:]])
                return FakeProcess()
            def finish(text, code=0):
                for job in running.values():
                    job["log"].write(text)
                    job["log"].flush()
                    job["process"].code = code

            work_once(work_client(), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("the worker itself only ever starts the safe step", begun == [["orchestrator.py", "run", "2026-10", "--until-wait"]])
            finish("STOPPED after 1 step(s): next would buy data (T3): run it without --until-wait\n")
            work_once(work_client(), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("when the safe step stops before a task that buys data, that task is offered in the platform",
                   [(r["task_id"], r["status"], r["project_id"], r["month"]) for r in paid.rows] == [("T3", "waiting", new["id"], "2026-10")]
                   and len(begun) == 1 and not running)
            work_once(work_client(), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("nothing is started while nobody has answered", len(begun) == 1 and len(paid.rows) == 1)

            paid.rows[0]["status"] = "approved"            # a person clicks Start in the platform
            work_once(work_client(), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("once a person starts it, the worker runs exactly ONE step, without --until-wait",
                   begun[-1] == ["orchestrator.py", "run", "2026-10"] and paid.rows[0]["status"] == "running" and len(begun) == 2)
            work_once(work_client(), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("the same approval is never run twice", len(begun) == 2)
            finish("Tool: T3 -> done\n")
            work_once(work_client(task_status="done"), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("when it ends the offer is closed and the safe step takes over again",
                   paid.rows[0]["status"] == "done" and begun[-1] == ["orchestrator.py", "run", "2026-10", "--until-wait"])

            finish("STOPPED after 1 step(s): a person is needed\n")
            paid.rows.append({"id": "p9", "customer_key": "new-co-222222", "task_id": "T4", "month": "2026-10", "status": "waiting"})
            work_once(work_client(task_status="done"), seen, running, "someone-else", popen=paid_popen, paid_steps=paid)
            expect("an unanswered offer is withdrawn when the run moved on another way", paid.rows[-1]["status"] == "withdrawn")

            paid2, seen2, running2 = FakePaidSteps(), {}, {}
            paid2.rows.append({"id": "p1", "customer_key": "new-co-222222", "task_id": "T3", "month": "2026-10", "status": "approved"})
            work_once(work_client(), seen2, running2, "someone-else", popen=paid_popen, paid_steps=paid2)
            for job in running2.values():
                job["log"].write("STOPPED: QuickICP did not answer.\n")
                job["log"].flush()
                job["process"].code = 1
            work_once(work_client(), seen2, running2, "someone-else", popen=paid_popen, paid_steps=paid2)
            expect("a started step that fails says why", paid2.rows[0]["status"] == "failed" and "QuickICP did not answer" in paid2.rows[0]["error"])
            for job in list(running.values()) + list(running2.values()):
                job["log"].close()
        finally:
            globals()["workspace_for"] = real_workspace

    class MissingColumn:
        def table(self, name):
            raise RuntimeError('column revenueos_projects.brain_customer_key does not exist')
    expect("a missing column names the SQL file to run", "0003_project_brain_key.sql" in (stops(lambda: read_projects(MissingColumn())) or ""))
    print("\nSELFTEST PASSED: no network, no LLM, no real file touched.")


# ================= MAIN =================

def main(arguments: list[str]) -> None:
    if arguments and arguments[0] in ("-h", "--help", "help"):
        print(__doc__)
        return
    if not arguments:
        cmd_start_flow([])          # the default: the whole flow for the new project
        return
    command = arguments[0]
    if command == "start":
        cmd_start_flow(arguments[1:])
    elif command == "work":
        cmd_work(arguments[1:])
    elif command == "_plan":
        cmd_plan_request(arguments[1] if len(arguments) == 2 else "")
    elif command == "for":
        if len(arguments) < 3:
            raise SelectError("Usage: python run_for.py for <project-id> <script.py> [arguments]")
        client = connect()
        project = choose(read_projects(client), customers_with_runs(client), arguments[1])
        sys.exit(run_script(arguments[2], arguments[3:], selection_of(project), client=client))
    elif command == "selftest":
        selftest()
    elif command == "list":
        cmd_list()
    elif command == "select":
        if len(arguments) != 2:
            raise SelectError("Usage: python run_for.py select new | <project-id>")
        cmd_select(arguments[1])
    elif command == "show":
        cmd_show()
    else:
        sys.exit(run_script(command, arguments[1:], load_selection()))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SelectError as error:
        print(error)
        sys.exit(1)
