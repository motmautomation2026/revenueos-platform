"""
check_system.py
===============

Health check for the whole MOTM Revenue OS system. Run it before a real run, or after any change.

WHAT IT CHECKS
--------------
    1. .env        every required setting is present; test-only secrets that should be gone are flagged
    2. Git safety  .env is ignored by Git, and no secret value from .env appears in any project file
    3. Files       the Master Agent outputs and the MOTM context exist
    4. OpenAI      a tiny test call works (costs a fraction of a cent)
    5. Prospeo     the suggestions lookup answers
    6. Supabase    both tables can be read
    7. Email tool  configured for CAMPAIGN mode (no live call: the tool has no health endpoint)
    8. Quickhowl   every QUICKHOWL_* setting, user password length, Vobiz keys all-or-none,
                   CUSTOMER_DISPLAY_NAME, CUSTOMER_LONG_NAMES (WARN if missing), TEST_CALL_NUMBERS valid, real calls off until go-live,
                   QUICKHOWL_DND_HANDLED=true (DND handled by the Quickhowl platform) (no live call)
    Also: duplicate keys in .env (names only, never values)

    The QuickICP webhooks are not called here (every call uses credits).
    They are tested by running the pipeline.

RUN
---
    python check_system.py

RESULT
------
    PASS  works
    WARN  works, but something should be cleaned up
    FAIL  must be fixed before a real run
"""

import os
import subprocess
import sys

import httpx
from dotenv import dotenv_values, load_dotenv

load_dotenv()

REQUIRED = [
    "OPENAI_API_KEY", "OPENAI_MODEL", "PROSPEO_API_KEY",
    "QUICKICP_WEBHOOK_URL", "QUICKICP_PEOPLE_WEBHOOK_URL",
    "QUICKICP_EMAIL_WEBHOOK_URL", "QUICKICP_PHONE_WEBHOOK_URL",
    "SUPABASE_URL", "SUPABASE_KEY",
    "EMAIL_TOOL_URL", "EMAIL_TOOL_KEY", "EMAIL_TOOL_USER_EMAIL", "CLIENT_REFERENCE",
]
SHOULD_BE_REMOVED = [
    "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_DISPLAY_NAME",
    "IMAP_HOST", "IMAP_PORT", "IMAP_SECURITY",
]
SECRET_KEYS = ["OPENAI_API_KEY", "PROSPEO_API_KEY", "SUPABASE_KEY", "EMAIL_TOOL_KEY", "SMTP_PASSWORD",
               "QUICKHOWL_KEY", "QUICKHOWL_USER_PASSWORD", "QUICKHOWL_VOBIZ_AUTH_TOKEN"]
QUICKHOWL_REQUIRED = ["QUICKHOWL_URL", "QUICKHOWL_KEY", "QUICKHOWL_CLIENT_REFERENCE", "QUICKHOWL_USER_EMAIL",
                      "QUICKHOWL_USER_PASSWORD", "QUICKHOWL_USER_FULL_NAME", "QUICKHOWL_USER_COMPANY"]
VOBIZ_KEYS = ["QUICKHOWL_VOBIZ_AUTH_ID", "QUICKHOWL_VOBIZ_AUTH_TOKEN", "QUICKHOWL_DID"]
REQUIRED_FILES = ["knowledge/motm_context.md", "data/understanding.json", "data/work_orders.json"]
SKIP_FOLDERS = {".venv", ".git", "__pycache__", "node_modules"}

results = []


def report(status: str, name: str, detail: str = "") -> None:
    results.append(status)
    print(f"{status:5} {name}" + (f"  -  {detail}" if detail else ""))


# ================= 1. .env =================

def check_env() -> None:
    missing = [key for key in REQUIRED if not os.getenv(key)]
    if missing:
        report("FAIL", ".env required settings", "missing: " + ", ".join(missing))
    else:
        report("PASS", ".env required settings", f"all {len(REQUIRED)} present")

    # The same key twice: only the LAST value is used, which is easy to get wrong. Names only, never values.
    keys = [line.split("=", 1)[0].strip() for line in open(".env", encoding="utf-8")
            if "=" in line and not line.lstrip().startswith("#")]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    if duplicates:
        report("WARN", ".env duplicate keys", "only the last value counts: " + ", ".join(duplicates))
    else:
        report("PASS", ".env duplicate keys", "none")

    leftover = [key for key in SHOULD_BE_REMOVED if key in dotenv_values(".env")]
    if leftover:
        report("WARN", ".env test-only mailbox settings", "remove: " + ", ".join(leftover))
    else:
        report("PASS", ".env test-only mailbox settings", "removed")


# ================= 2. GIT SAFETY =================

def check_git() -> None:
    try:
        ignored = subprocess.run(["git", "check-ignore", ".env"], capture_output=True, text=True).returncode == 0
        tracked = subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        report("WARN", "Git safety", "git not found; check manually that .env is not committed")
        return
    if tracked:
        report("FAIL", "Git safety", ".env is committed to Git! Remove it: git rm --cached .env")
    elif not ignored:
        report("FAIL", "Git safety", ".env is not in .gitignore")
    else:
        report("PASS", "Git safety", ".env is ignored and not committed")


def check_secret_leaks() -> None:
    """Look for the exact secret values from .env inside any project file."""
    secrets = {key: os.getenv(key) for key in SECRET_KEYS if os.getenv(key) and len(os.getenv(key)) >= 12}
    leaks = []
    for root, folders, files in os.walk("."):
        folders[:] = [f for f in folders if f not in SKIP_FOLDERS]
        for file_name in files:
            if file_name == ".env":
                continue
            path = os.path.join(root, file_name)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as file:
                    text = file.read()
            except OSError:
                continue
            for key, value in secrets.items():
                checked = text
                if key == "QUICKHOWL_USER_PASSWORD" and is_known_login_email_case(path):
                    # Known case: the (weak, test) password is part of the login email, which Quickhowl
                    # returns as 'login_email'. Only the email text is ignored; the password anywhere
                    # else in this file still FAILs. The weak password itself is a WARN (check_quickhowl).
                    checked = text.replace(os.getenv("QUICKHOWL_USER_EMAIL"), "")
                if value in checked:
                    leaks.append(f"{key} in {path}")
    if leaks:
        report("FAIL", "Secrets in project files", "; ".join(leaks))
    else:
        report("PASS", "Secrets in project files", "no secret values found outside .env")


def is_known_login_email_case(path: str) -> bool:
    """The one known case: data/call_campaign_state.json, and the password is part of the login email."""
    email = os.getenv("QUICKHOWL_USER_EMAIL") or ""
    password = os.getenv("QUICKHOWL_USER_PASSWORD") or ""
    return (os.path.normpath(path) == os.path.normpath("./data/call_campaign_state.json")
            and bool(password) and password in email)


# ================= 3. FILES =================

def check_files() -> None:
    missing = [path for path in REQUIRED_FILES if not os.path.exists(path)]
    if missing:
        report("FAIL", "Required files", "missing: " + ", ".join(missing) + " (run master_agent.py)")
    else:
        report("PASS", "Required files", "Master Agent outputs and MOTM context present")


# ================= 4-6. LIVE CHECKS =================

def check_openai() -> None:
    try:
        from quickicp import llm      # the one LLM factory (short timeout + tiny answer for a health check)
        reply = llm(timeout=30, max_tokens=5).invoke("Reply OK")
        report("PASS", "OpenAI", f"model {os.getenv('OPENAI_MODEL')} replied: {reply.content.strip()[:20]}")
    except Exception as error:
        report("FAIL", "OpenAI", str(error)[:200])


def check_prospeo() -> None:
    try:
        response = httpx.post("https://api.prospeo.io/search-suggestions", json={"location_search": "Pune"},
                              headers={"X-KEY": os.getenv("PROSPEO_API_KEY") or ""}, timeout=30)
        if response.status_code == 200:
            report("PASS", "Prospeo suggestions", "lookup answered")
        else:
            report("FAIL", "Prospeo suggestions", f"status {response.status_code}: {response.text[:150]}")
    except Exception as error:
        report("FAIL", "Prospeo suggestions", str(error)[:200])


def check_supabase() -> None:
    try:
        from supabase_store import COMPANY_TABLE, PEOPLE_TABLE, db
        companies = db.table(COMPANY_TABLE).select("id", count="exact").limit(1).execute().count
        people = db.table(PEOPLE_TABLE).select("id", count="exact").limit(1).execute().count
        report("PASS", "Supabase", f"{companies} companies, {people} people saved")
    except Exception as error:
        report("FAIL", "Supabase", str(error)[:200])


# ================= 7. EMAIL TOOL =================

def check_email_tool() -> None:
    if os.getenv("CLIENT_REFERENCE") and os.getenv("EMAIL_TOOL_KEY") and os.getenv("EMAIL_TOOL_URL"):
        report("PASS", "Email tool config", f"CAMPAIGN mode, client {os.getenv('CLIENT_REFERENCE')} "
                                           "(not called: no health endpoint)")
    else:
        report("FAIL", "Email tool config", "needs EMAIL_TOOL_URL, EMAIL_TOOL_KEY and CLIENT_REFERENCE")


# ================= 8. QUICKHOWL (AI CALLING) =================

def check_quickhowl() -> None:
    """Settings only (known issue 5). No live call: Quickhowl has no health endpoint."""
    missing = [key for key in QUICKHOWL_REQUIRED if not os.getenv(key)]
    if missing:
        report("FAIL", "Quickhowl settings", "missing: " + ", ".join(missing))
    else:
        report("PASS", "Quickhowl settings", f"all {len(QUICKHOWL_REQUIRED)} present, client "
                                             f"{os.getenv('QUICKHOWL_CLIENT_REFERENCE')}")
    password = os.getenv("QUICKHOWL_USER_PASSWORD") or ""
    if len(password) < 6:
        report("FAIL", "Quickhowl user password", "must be at least 6 characters")
    # Weak: short, or part of the (public) login email / name / company. Key names only, never values.
    inside = [key for key in ("QUICKHOWL_USER_EMAIL", "QUICKHOWL_USER_FULL_NAME", "QUICKHOWL_USER_COMPANY")
              if password and password.lower() in (os.getenv(key) or "").lower()]
    if password and (len(password) < 16 or inside):
        reasons = (["shorter than 16 characters"] if len(password) < 16 else []) + [f"part of {key}" for key in inside]
        report("WARN", "Quickhowl user password",
               f"weak Quickhowl password ({'; '.join(reasons)}), change before real prospects "
               "(the developer must reset it on the server too)")
    elif password:
        report("PASS", "Quickhowl user password", "16+ characters, not part of the login email / name")

    vobiz = [key for key in VOBIZ_KEYS if os.getenv(key)]
    if vobiz and len(vobiz) != len(VOBIZ_KEYS):
        report("WARN", "Quickhowl phone number (Vobiz)", "set all three or none: "
                                                         + ", ".join(k for k in VOBIZ_KEYS if k not in vobiz) + " missing")
    if not os.getenv("CUSTOMER_DISPLAY_NAME"):
        report("FAIL", "CUSTOMER_DISPLAY_NAME", "missing: the name the caller says and the email signature use")
    else:
        report("PASS", "CUSTOMER_DISPLAY_NAME", os.getenv("CUSTOMER_DISPLAY_NAME"))
    if not (os.getenv("CUSTOMER_LONG_NAMES") or "").strip():
        report("WARN", "CUSTOMER_LONG_NAMES", "missing: only the understanding's customer_name is replaced by the "
                                              "display name in emails (e.g. set: Anvixa AI Product, Anvixa AI)")
    else:
        report("PASS", "CUSTOMER_LONG_NAMES", os.getenv("CUSTOMER_LONG_NAMES"))

    try:
        import quickhowl
        if quickhowl.TEST_MODE:
            numbers = quickhowl.test_numbers()
            used = min(len(numbers), quickhowl.MAX_TEST_CALLS)
            report("PASS", "TEST_CALL_NUMBERS", f"{len(numbers)} valid Indian number(s); test runs call at most {used}")
        if quickhowl.REAL_CALLS_ENABLED:
            report("WARN", "Real calls", "REAL_CALLS_ENABLED = True: real prospects can be called")
        else:
            report("PASS", "Real calls", "off until go-live (you switch REAL_CALLS_ENABLED on)")
        if quickhowl.dnd_handled():
            report("PASS", "Quickhowl DND", "QUICKHOWL_DND_HANDLED=true: DND handled by the Quickhowl platform")
        else:
            report("WARN", "Quickhowl DND", "QUICKHOWL_DND_HANDLED is not true: real calls stay blocked even at go-live")
    except Exception as error:
        report("FAIL", "Quickhowl test numbers", str(error)[:200])


# ================= RUN =================

if __name__ == "__main__":
    print("MOTM Revenue OS - system check\n")
    check_env()
    check_git()
    check_secret_leaks()
    check_files()
    check_openai()
    check_prospeo()
    check_supabase()
    check_email_tool()
    check_quickhowl()

    fails, warns = results.count("FAIL"), results.count("WARN")
    print(f"\nResult: {results.count('PASS')} pass, {warns} warn, {fails} fail")
    if fails:
        print("Fix every FAIL before a real run.")
        sys.exit(1)
    print("System is ready." + (" Clean up the WARN items when you can." if warns else ""))