"""
quickhowl.py
============

AI voice calling (Quickhowl) for MOTM Revenue OS.
Follows the developer's guide for https://revenueoscall.b2botix.ai (tested 8 Oct 2026).

HOW IT IS USED
--------------
    Campaigns are created, stored, started and polled ONLY by the orchestrator's calls runner
    (runner_calls.py): every launch saved in the ledger (brain_tool_calls) first, dialling only after the
    call_start approval. This file is its library (profile, contacts, request, safety, API calls).
    The old CLI commands preview / launch / start (they skipped the ledger and the approval) are in
    archive/legacy/quickhowl_cli.py.

MANUAL TOOLS (the campaign comes from the LEDGER of one task, never from a data/ file)
------------
    python quickhowl.py status  --run 2026-10-r5 T6    READ-ONLY: where the campaign stands
    python quickhowl.py results --run 2026-10-r5 T6    READ-ONLY: every call (outcome, summary, transcript)
    python quickhowl.py watch   --run 2026-10-r5 T6    READ-ONLY: status + results every POLL_SECONDS until
                                                       finished, still a DRAFT, or WATCH_MINUTES pass
    python quickhowl.py pause   --run 2026-10-r5 T6    pause ONE campaign (see PAUSE SAFETY)
    [--reference <ref>]  only when the task's ledger holds more than one campaign: names one exactly.
    Reads never write anything: the runner's poll steps and its 'since' cursor stay untouched.

PAUSE SAFETY (never the wrong campaign)
    1. the reference comes only from the task's finished 'store' ledger step (this customer, this run,
       a Quickhowl calls task); more than one -> stop and list them unless --reference names one
    2. the status is read first: a reply for another campaign -> STOPPED; finished or draft -> nothing to pause
    3. you TYPE the full reference; anything else -> not paused
    4. then exactly one POST /campaigns/<that reference>/pause

WHO DECIDES WHAT
----------------
    LLM  : the company profile (what we offer, value, call objective) from the run's understanding
           and the task's Quickhowl work order (given by the runner)
    Code : contacts, phone format, test-number safety, references, which blocks to send,
           campaign goal, when the campaign is finished, every number
    Human: approves before any dialling (orchestrator call_start decision), and pausing

REFERENCES (from the guide)
---------------------------
    client_reference  = one client account. Fixed per client forever (.env).
    reference         = one campaign. NEW for every launch: motm-<customer>-call-<YYYYMMDD-HHMMSS>.
    Same reference + same content  -> duplicate: true (safe to resend).
    Same reference + changed content -> HTTP 409. So 'start' resends the SAVED request exactly.
    There is no list endpoint: every reference is kept in the ledger (the task's 'store' step).

WHAT IS SENT
------------
    user            every request (email must match the account; password only used the first time)
    phone_number    only if QUICKHOWL_VOBIZ_AUTH_ID, _AUTH_TOKEN and QUICKHOWL_DID are set
                    (needed on the client's first launch; resending the same DID is harmless)
    company_profile every request. NOTE: it REPLACES the client's saved profile, including for
                    that client's running campaigns. One client_reference = one customer.
    agent           AGENT_* settings below
    campaign        name, description, goal (CAMPAIGN_GOAL, set by code)
    contacts        name, phone, company, website, custom_fields (designation, industry, city, size)
                    No email, no LinkedIn: the caller does not need them.
    schedule        not sent: the server default is 09:00-19:00, Mon-Sat, Asia/Kolkata.

SAFETY
------
    - TEST MODE (TEST_MODE in quickicp.py): every contact keeps the REAL prospect's details
      (so the personal script is real), but the phone is one of TEST_CALL_NUMBERS.
      Before any request is sent, code checks that EVERY phone is a test number.
    - REAL MODE needs BOTH: REAL_CALLS_ENABLED = True (stays False; switched on by the developer at
      go-live) AND QUICKHOWL_DND_HANDLED=true in .env. DND (TRAI) is handled by the Quickhowl
      platform (registered, compliant, used by our internal calling team); we do not build our own
      DND check. Every call start records this confirmation (orchestrator ledger + call_start question).
    - Quickhowl itself refuses bad, duplicate or do-not-call numbers: they come back in the launch
      reply's 'contacts_rejected' (recorded by the orchestrator's calls runner).
    - Secrets (user password, Vobiz auth token) are added only at send time; they are never
      written to data/ files or printed.
    - Unanswered calls are NOT retried by the platform; one call at a time per client;
      20-40 s preparation before each dial; calls only inside the calling window.

READS
-----
    .env                            QUICKHOWL_* settings, TEST_CALL_NUMBERS, SUPABASE_*
    the runner's inputs             the run's understanding, the task's work order, the people (Supabase,
                                    only rows of THIS customer)
    the ledger (manual tools)       the task's 'store' step: reference + client_reference

WRITES
------
    Nothing in data/. The runner records every request and reply in the ledger.

.ENV KEYS
---------
    QUICKHOWL_URL                 https://revenueoscall.b2botix.ai/api/integrations/revenueos
    QUICKHOWL_KEY                 the X-RevenueOS-Key for calling
    QUICKHOWL_CLIENT_REFERENCE    the client account, e.g. motm-tech
    QUICKHOWL_USER_EMAIL          the account login email (must stay the same per client)
    QUICKHOWL_USER_PASSWORD       6+ characters, used when the account is first created
    QUICKHOWL_USER_FULL_NAME      e.g. MOTM Technologies
    QUICKHOWL_USER_COMPANY        e.g. MOTM Technologies
    TEST_CALL_NUMBERS             our own phones, comma separated: +9198XXXXXXXX,+9197XXXXXXXX
    CUSTOMER_KEY                  fixed customer key, e.g. anvixa-ai-product (which Supabase rows are ours)
    optional, first launch only:  QUICKHOWL_VOBIZ_AUTH_ID (starts MA_), QUICKHOWL_VOBIZ_AUTH_TOKEN,
                                  QUICKHOWL_DID (e.g. +918065382389)
    optional:                     CUSTOMER_DISPLAY_NAME (name the caller says), CUSTOMER_WEBSITE

DEBUGGING
---------
    - 401: wrong QUICKHOWL_KEY.   404 on status/results: wrong client_reference for that reference.
    - 409: reference reused with different content, or user email differs for that client.
           The runner makes a new reference per campaign; 'start' always resends the saved request exactly.
    - 422: the reply names the bad field or the refused contacts.
    - 503: server settings (launch or auto-start switched off). Nothing was created.
    - start_status 'blocked': read start_reason (e.g. phone_number failed to connect).
    - contacts_rejected: bad, duplicate or do-not-call numbers; the rest still go through.
    - Silent calls with elevenlabs: the ElevenLabs plan must be paid; or use voice_provider sarvam.
    - "has no stored calls campaign in the ledger yet": the calls task has not stored its campaign.
    - "has 2 campaigns": add --reference <one of the listed references>.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from quickicp import TEST_MODE, ToolError, ask_llm, check_settings, clean_domain

load_dotenv()

# ---- Safety ----
REAL_CALLS_ENABLED = True       # switched on at go-live 2026-10-12 (Rameshwar). Also needs QUICKHOWL_DND_HANDLED=true in .env
MAX_TEST_CALLS = 3              # never more than this many contacts in test mode

# ---- Agent (the voice) ----
AGENT_NAME = "Nilam"
AGENT_LANGUAGE = "hinglish"     # hinglish, hindi, english, marathi
AGENT_VOICE_PROVIDER = "elevenlabs"
AGENT_VOICE_ID = "0iMlxZ0uHXOOsLck9tMa"   # Nilam (needs a paid ElevenLabs plan); or sarvam + "ritu"

# ---- Campaign goal (decided by code, not the LLM) ----
CAMPAIGN_GOAL = "lead_generation"   # allowed: lead_generation, follow_up, survey, announcement

# ---- Polling ----
POLL_SECONDS = 60
WATCH_MINUTES = 30
PAGE_LIMIT = 100

# ---- Supabase tables (same as supabase_store.py) ----
PEOPLE_TABLE = "quickicp_revenueos_client_people_data"
COMPANY_TABLE = "quickicp_revenueos_client_company_data"


REQUIRED_KEYS = ("QUICKHOWL_URL", "QUICKHOWL_KEY", "QUICKHOWL_CLIENT_REFERENCE", "QUICKHOWL_USER_EMAIL",
                 "QUICKHOWL_USER_PASSWORD", "QUICKHOWL_USER_FULL_NAME", "QUICKHOWL_USER_COMPANY")

# Words that mean internal strategy leaked into what the caller will say.
INTERNAL_WORDS = re.compile(r"\b(motm|cluster|clusters|segment|segments|p1|p2|p3|enquir\w*|work order|"
                            r"lead engine|month \d)\b", re.IGNORECASE)

FINISHED_STATUSES = {"completed", "failed", "paused"}


# ================= SMALL HELPERS =================


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "customer").lower()).strip("-") or "customer"


def normalise_phone(phone: str | None) -> str | None:
    """'+91 96500 16204' / '09650016204' / '9650016204' -> '+919650016204'. None if not an Indian number."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return None


def test_numbers() -> list[str]:
    """TEST_CALL_NUMBERS from .env, normalised. Stops if any is invalid."""
    raw = [n.strip() for n in (os.getenv("TEST_CALL_NUMBERS") or "").split(",") if n.strip()]
    numbers = []
    for number in raw:
        clean = normalise_phone(number)
        if not clean:
            raise ToolError(f"TEST_CALL_NUMBERS has an invalid number: {number}")
        numbers.append(clean)
    if not numbers:
        raise ToolError("TEST_CALL_NUMBERS is empty in .env. Add your own phone(s): +9198XXXXXXXX,+9197XXXXXXXX")
    return numbers


# ================= HTTP (timeouts, retries, clear errors) =================

def explain_error(response: requests.Response) -> str:
    try:
        body = response.json()
        detail = body
    except ValueError:
        detail = response.text[:300]
    hints = {
        401: "X-RevenueOS-Key is missing or wrong (QUICKHOWL_KEY).",
        404: "Not found: check QUICKHOWL_CLIENT_REFERENCE matches this campaign's reference.",
        409: "Conflict: reference reused with different content, or a different user email for this client.",
        422: "Bad request: the detail names the field or refused contacts.",
        503: "Server settings: launch or auto-start is switched off. Nothing was created.",
    }
    return f"HTTP {response.status_code}. {hints.get(response.status_code, '')} Detail: {detail}"


def call_api(method: str, path: str, params: dict | None = None, body: dict | None = None,
             timeout: int = 120) -> dict:
    """
    One API call with timeouts and up to 3 retries (5 s, 15 s, 45 s) on network errors,
    5xx (except 503), empty replies and invalid JSON. 4xx and 503 stop at once with a clear reason.
    Retrying a launch is safe: the same reference with the same content answers duplicate: true.
    """
    url = os.getenv("QUICKHOWL_URL", "").rstrip("/") + path
    headers = {"X-RevenueOS-Key": os.getenv("QUICKHOWL_KEY", "")}
    last_problem = ""
    for wait in (0, 5, 15, 45):
        if wait:
            print(f"  Retrying in {wait} s ({last_problem})")
            time.sleep(wait)
        try:
            response = requests.request(method, url, headers=headers, params=params, json=body, timeout=timeout)
        except requests.RequestException as error:
            last_problem = f"network error: {error}"
            continue
        if response.status_code == 503 or 400 <= response.status_code < 500:
            raise ToolError(explain_error(response))
        if response.status_code >= 500:
            last_problem = f"HTTP {response.status_code}"
            continue
        if not response.text.strip():
            last_problem = "empty reply"
            continue
        try:
            return response.json()
        except ValueError:
            last_problem = "reply was not JSON"
    raise ToolError(f"{method} {path} failed after 4 tries: {last_problem}")


# ================= INPUTS: WORK ORDER, BRAIN, SUPABASE =================


def supabase_people(urls: list[str]) -> list[dict]:
    """People (with phone) of THIS customer (CUSTOMER_KEY) from Supabase, each joined with their company row."""
    check_settings("SUPABASE_URL", "SUPABASE_KEY")
    from supabase import create_client   # imported here so preview errors are clear if missing
    from state_store import current_customer
    customer = current_customer()
    client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
    people = (client.table(PEOPLE_TABLE).select("*").eq("customer", customer)
                    .in_("user_social", urls).execute().data or [])
    company_ids = sorted({p["company_id"] for p in people if p.get("company_id")})
    companies = {}
    if company_ids:
        rows = (client.table(COMPANY_TABLE).select("*").eq("customer", customer)
                      .in_("id", company_ids).execute().data or [])
        companies = {row["id"]: row for row in rows}
    for person in people:
        person["company"] = companies.get(person.get("company_id")) or {}
    return people


def contacts_from_people(people: list[dict]) -> list[dict]:
    """
    Supabase people (each with 'company' joined) -> call contacts. Real prospect details always;
    the phone is the prospect's own in real mode, or one of TEST_CALL_NUMBERS in test mode.
    The orchestrator's calls runner passes only people with a MOBILE number.
    """
    numbers = test_numbers() if TEST_MODE else []
    contacts, skipped = [], 0
    for person in people:
        real_phone = normalise_phone(person.get("phone_number"))
        if not real_phone:
            skipped += 1
            continue
        company = person["company"]
        website = clean_domain(company.get("website"))
        custom = {
            "designation": person.get("job_position"),
            "industry": company.get("industry") or person.get("industry"),
            "city": person.get("location") or company.get("location"),
            "company_size": company.get("headcount"),
        }
        contact = {
            "name": person.get("full_name"),
            "phone": real_phone,
            "company": company.get("company_name"),
            "website": f"https://{website}" if website else None,
            "custom_fields": {k: str(v) for k, v in custom.items() if v not in (None, "")},
        }
        contacts.append({k: v for k, v in contact.items() if v not in (None, "", {})})

    if skipped:
        print(f"Skipped {skipped} people without a valid Indian phone number.")
    if not contacts:
        raise ToolError("No contacts with a valid phone. Check phone_number in Supabase.")

    if TEST_MODE:
        count = min(len(numbers), MAX_TEST_CALLS, len(contacts))
        contacts = contacts[:count]
        for contact, number in zip(contacts, numbers):
            contact["phone"] = number   # real details, OUR phone
        print(f"TEST MODE: {count} contact(s), real prospect details, our test numbers only.")
    return contacts


# ================= BRAIN: COMPANY PROFILE (LLM) =================

class CallProfile(BaseModel):
    industry: str = Field(description="The customer's own industry, one short line")
    what_we_offer: str = Field(description="What the customer sells, 2-3 plain sentences")
    value_proposition: str = Field(description="Why a buyer should care: outcomes, 1-2 sentences")
    target_customers: str = Field(description="Who the product is for, 1 sentence")
    key_points: str = Field(description="2-4 short facts from the documents only, no invented numbers")
    call_objective: str = Field(description="What one call should achieve, 1-2 sentences")


def customer_display_name(understanding: dict) -> str:
    return os.getenv("CUSTOMER_DISPLAY_NAME") or understanding.get("customer_name") or "our company"


def internal_words_in(profile) -> dict:
    """{field: [internal words found]} for a call profile; empty when it is clean. Code decides, not the LLM."""
    found = {}
    for name, value in profile.model_dump().items():
        text = " ".join(value) if isinstance(value, list) else str(value or "")
        words = INTERNAL_WORDS.findall(text)
        if words:
            found[name] = words
    return found


def make_profile(understanding: dict, work_order: dict) -> dict:
    """LLM writes the profile from the brain; code adds name, website and the fixed tone rules."""
    company = customer_display_name(understanding)
    facts = {
        "customer": company,
        "business_summary": understanding.get("business_summary"),
        "priority_segments": understanding.get("priority_segments"),
        "qualification_questions": understanding.get("qualification_questions"),
        "calls_work_order": {k: work_order.get(k) for k in ("task", "target", "instructions") if k in work_order},
    }
    prompt = (
        "You write the company profile an AI voice caller uses to pitch on behalf of the customer.\n"
        "Rules:\n"
        "- Use ONLY facts in the documents. Never invent numbers, clients, prices or awards.\n"
        "- Write for the person being called: plain, short, spoken-language sentences.\n"
        "- No internal words: never mention MOTM, segments, P1/P2, clusters, targets, months, "
        "enquiries, work orders or lead generation.\n"
        "- call_objective: briefly find out how they handle these workflows today, check interest, "
        "and book a short demo with the customer's team.\n\n"
        "Documents:\n" + json.dumps(facts, ensure_ascii=False)
    )
    profile = ask_llm(CallProfile, prompt, "Call profile")
    leaks = internal_words_in(profile)
    if leaks:
        # One rewrite, shown exactly what leaked (the same rule as the email step's forbidden phrases).
        print(f"  Call profile contains internal words {leaks}: asking for one rewrite.")
        profile = ask_llm(CallProfile, prompt + "\n\nYour previous answer used internal words that the person "
                          f"being called must never hear: {json.dumps(leaks, ensure_ascii=False)}. Write it again "
                          "without them. Say who the customers are in plain words (for example 'companies that "
                          "build water pipelines'), never 'segments' or 'clusters'.", "Call profile (rewrite)")
        leaks = internal_words_in(profile)
    if leaks:
        raise ToolError(f"Call profile contains internal words {leaks}. Run 'preview' again.")

    tone = (f"The caller is a woman named {AGENT_NAME} and must introduce herself as {AGENT_NAME} "
            f"calling from {company}. Polite, brief and consultative. Never promise prices, discounts "
            f"or guarantees. If the person is not interested or asks not to be called again, thank "
            f"them and end the call politely.")
    result = {"company_name": company}
    if os.getenv("CUSTOMER_WEBSITE"):
        result["website"] = os.getenv("CUSTOMER_WEBSITE")
    data = profile.model_dump()
    result.update({k: data[k] for k in ("industry", "what_we_offer", "value_proposition",
                                        "target_customers", "key_points", "call_objective")})
    result["tone_notes"] = tone
    return {"profile": result}


# ================= REQUEST: BUILD, SECRETS, SAFETY =================

def build_request(contacts: list[dict], understanding: dict, work_order: dict) -> dict:
    """
    The full request WITHOUT secrets (the calls runner saves it in the ledger; 'start' resends exactly this).
    contacts, understanding, work_order: given by the runner (the run's understanding, THIS task's work
    order). Never read from data/ files.
    """
    check_settings("OPENAI_MODEL", *REQUIRED_KEYS)
    reason = real_calls_block_reason()
    if reason:
        raise ToolError(reason)

    made = make_profile(understanding, work_order)
    company = customer_display_name(understanding)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    request = {
        "client_reference": os.getenv("QUICKHOWL_CLIENT_REFERENCE"),
        "reference": f"motm-{slug(company)}-call-{stamp}"[:120],
        "company_profile": made["profile"],
        "agent": {"name": AGENT_NAME, "language": AGENT_LANGUAGE,
                  "voice_provider": AGENT_VOICE_PROVIDER, "voice_id": AGENT_VOICE_ID},
        "campaign": {
            "name": f"{company} - decision makers - {datetime.now():%B %Y}"
                    + (" (TEST)" if TEST_MODE else ""),
            "description": "First-touch AI calls to validated decision makers",
            "goal": CAMPAIGN_GOAL,
        },
        "contacts": contacts,
        "prime": True,
    }
    return request


def with_secrets(request: dict, auto_start: bool) -> dict:
    """Add the user block (and phone_number if configured) at send time only."""
    payload = dict(request)
    payload["user"] = {
        "email": os.getenv("QUICKHOWL_USER_EMAIL"),
        "password": os.getenv("QUICKHOWL_USER_PASSWORD"),
        "full_name": os.getenv("QUICKHOWL_USER_FULL_NAME"),
        "company_name": os.getenv("QUICKHOWL_USER_COMPANY"),
    }
    vobiz = [os.getenv(k) for k in ("QUICKHOWL_VOBIZ_AUTH_ID", "QUICKHOWL_VOBIZ_AUTH_TOKEN", "QUICKHOWL_DID")]
    if all(vobiz):
        payload["phone_number"] = {"auth_id": vobiz[0], "auth_token": vobiz[1], "did": vobiz[2]}
    payload["auto_start"] = auto_start
    return payload


def assert_safe(request: dict) -> None:
    """Code check before ANY request: in test mode every phone must be one of our test numbers."""
    if TEST_MODE:
        allowed = set(test_numbers())
        bad = [c.get("phone") for c in request["contacts"] if c.get("phone") not in allowed]
        if bad:
            raise ToolError(f"SAFETY STOP: test mode but these phones are not test numbers: {bad}")
        if len(request["contacts"]) > MAX_TEST_CALLS:
            raise ToolError(f"SAFETY STOP: test mode allows at most {MAX_TEST_CALLS} contacts.")
    else:
        reason = real_calls_block_reason()
        if reason:
            raise ToolError(reason)


def dnd_handled() -> bool:
    """True only when QUICKHOWL_DND_HANDLED in .env is exactly 'true' (any case)."""
    return (os.getenv("QUICKHOWL_DND_HANDLED") or "").strip().lower() == "true"


def dnd_note() -> str:
    """The DND confirmation recorded for every call start (ledger + call_start question)."""
    if dnd_handled():
        return "DND: handled by the Quickhowl platform (QUICKHOWL_DND_HANDLED=true)"
    if TEST_MODE:
        return "DND: not confirmed (QUICKHOWL_DND_HANDLED is not true); test mode: our own numbers only"
    return "DND: NOT confirmed (QUICKHOWL_DND_HANDLED is not true)"


def real_calls_block_reason() -> str | None:
    """Why REAL calls are not allowed now (None = allowed). Test mode is never blocked here."""
    if TEST_MODE:
        return None
    if not REAL_CALLS_ENABLED:
        return "STOPPED: Real calls are off (REAL_CALLS_ENABLED = False; switched on at go-live)."
    if not dnd_handled():
        return ("STOPPED: QUICKHOWL_DND_HANDLED is not true in .env: DND must be confirmed as handled by the "
                "Quickhowl platform before real calls.")
    return None


# ================= STATE =================


def campaign_params(state: dict) -> dict:
    return {"client_reference": state["client_reference"]}


# ================= COMMANDS =================


def clean_transcript(transcript: list | None) -> list:
    """Drop the first 'user' line: it is an internal 'greet now' instruction, not the person."""
    lines = list(transcript or [])
    for index, line in enumerate(lines):
        if line.get("speaker") == "user":
            return lines[:index] + lines[index + 1:]
    return lines


# ================= MANUAL TOOLS: the campaign comes from the LEDGER (--run <month> <task>) =================

def ledger_campaign(month: str, task_id: str, reference: str | None = None) -> dict:
    """
    The calls campaign of ONE task of THIS customer's run, from the ledger (never from a data/ file):
    the task's finished 'store' steps. More than one campaign in that task -> stop and list them,
    unless reference names one of them exactly.
    Returns {"run_key", "task_id", "reference", "client_reference"}.
    """
    from orchestrator import check_month, find_run
    from state_store import current_customer, get_steps, get_tasks
    run = find_run(current_customer(), check_month(month))
    task = next((t for t in get_tasks(run["id"]) if t["task_id"] == task_id), None)
    if not task:
        raise ToolError(f"STOPPED: no task {task_id} in run {run['run_key']}.")
    if (task["agent"], task["output_type"]) != ("Quickhowl", "calls"):
        raise ToolError(f"STOPPED: {task_id} is a {task['agent']} / {task['output_type']} task, "
                        "not a Quickhowl calls task.")
    stored = {}
    for step in get_steps(task["id"]):
        if step["step"] == "store" and step["status"] == "done":
            ref = (step["request"] or {}).get("reference")
            stored[ref] = (step["response"] or {}).get("client_reference") or os.getenv("QUICKHOWL_CLIENT_REFERENCE")
    if not stored:
        raise ToolError(f"STOPPED: {task_id} in run {run['run_key']} has no stored calls campaign in the ledger yet.")
    if reference is None and len(stored) > 1:
        raise ToolError(f"STOPPED: {task_id} in run {run['run_key']} has {len(stored)} campaigns: "
                        f"{', '.join(stored)}. Name one with --reference <ref>.")
    if reference is not None and reference not in stored:
        raise ToolError(f"STOPPED: {reference} is not a campaign of {task_id} in run {run['run_key']} "
                        f"(the ledger has: {', '.join(stored)}).")
    ref = reference or next(iter(stored))
    return {"run_key": run["run_key"], "task_id": task_id, "reference": ref, "client_reference": stored[ref]}


def campaign_status(campaign: dict) -> dict:
    """READ-ONLY: the campaign's status from Quickhowl. Stops if Quickhowl answers for another campaign."""
    reply = call_api("GET", f"/campaigns/{campaign['reference']}", params=campaign_params(campaign), timeout=60)
    if reply.get("reference") not in (None, campaign["reference"]):
        raise ToolError(f"STOPPED: Quickhowl answered for campaign {reply.get('reference')}, not "
                        f"{campaign['reference']}. Nothing was done.")
    return reply


def cmd_status(campaign: dict) -> dict:
    """READ-ONLY: print the campaign's status."""
    reply = campaign_status(campaign)
    print(f"\nCampaign {campaign['reference']} ({campaign['run_key']} {campaign['task_id']})")
    print(f"  status: {reply.get('status')} | in calling window now: {reply.get('in_calling_window_now')}")
    print(f"  window: {reply.get('calling_window')}")
    print(f"  contacts: {reply.get('contacts_total')} total, {reply.get('contacts_still_to_call')} still to call")
    print(f"  contacts_by_status: {reply.get('contacts_by_status')}")
    print(f"  calls: {reply.get('calls_total')} | by outcome: {reply.get('calls_by_outcome')}")
    return reply


def cmd_results(campaign: dict, quiet: bool = False) -> dict:
    """READ-ONLY: every call of the campaign (all pages), printed. Writes nothing (the runner owns the ledger)."""
    calls, since, campaign_state = {}, None, None
    while True:
        params = {**campaign_params(campaign), "limit": PAGE_LIMIT, **({"since": since} if since else {})}
        reply = call_api("GET", f"/campaigns/{campaign['reference']}/calls", params=params, timeout=60)
        for item in reply.get("items", []):
            item["transcript"] = clean_transcript(item.get("transcript"))
            calls[item["call_id"]] = item
        campaign_state = reply.get("campaign_status")
        since = reply.get("next_since") or since
        if not reply.get("has_more"):
            break
    if not quiet:
        print(f"\nCalls of {campaign['reference']}: {len(calls)} | campaign: {campaign_state}")
        for call in calls.values():
            contact = call.get("contact") or {}
            summary = (call.get("summary") or "").replace("\n", " ")[:120]
            print(f"  {contact.get('name')} | {call.get('outcome')} | {call.get('duration_seconds')} s | {summary}")
    return {"calls": list(calls.values()), "campaign_status": campaign_state}


def cmd_watch(campaign: dict, sleep=time.sleep) -> None:
    """READ-ONLY: status + results every POLL_SECONDS until finished, still a draft, or WATCH_MINUTES pass."""
    deadline = time.time() + WATCH_MINUTES * 60
    print(f"Watching {campaign['reference']} every {POLL_SECONDS} s for up to {WATCH_MINUTES} min "
          "(Ctrl+C stops watching, not the calls).")
    try:
        while True:
            outcome = cmd_results(campaign, quiet=True)
            status = cmd_status(campaign)
            if status.get("status") == "draft":
                print("\nThe campaign is still a DRAFT (stored, not started): nobody will be called. "
                      "It is started through the orchestrator (call_start decision).")
                return
            calls = outcome["calls"]
            if status.get("status") in FINISHED_STATUSES and status.get("contacts_still_to_call") == 0 \
                    and calls and all(call.get("finished") for call in calls):
                print("\nCampaign finished.")
                cmd_results(campaign)
                return
            if time.time() > deadline:
                print(f"\nStopped watching after {WATCH_MINUTES} min. Calls continue; run 'watch' again.")
                return
            sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped watching. Calls continue on the server.")


def cmd_pause(campaign: dict, confirm=input) -> dict | None:
    """
    Pause ONE campaign, never the wrong one: the reference comes only from the ledger (ledger_campaign),
    the status is read first (another campaign in the reply -> STOPPED; finished or draft -> nothing to
    pause), and you must TYPE the full reference. Then exactly one POST for that reference.
    """
    status = campaign_status(campaign)
    if status.get("status") == "draft" or status.get("status") in FINISHED_STATUSES:
        print(f"Campaign {campaign['reference']} is '{status.get('status')}': nothing to pause. Nothing was sent.")
        return None
    print(f"\nPause campaign {campaign['reference']} of {campaign['run_key']} {campaign['task_id']}?")
    print(f"  status: {status.get('status')} | contacts still to call: {status.get('contacts_still_to_call')}")
    print("  Calls in progress finish; no new calls start.")
    try:
        typed = confirm(f"Type the campaign reference to pause it ({campaign['reference']}): ").strip()
    except EOFError:
        typed = ""
    if typed != campaign["reference"]:
        print("Not paused (the reference did not match).")
        return None
    reply = call_api("POST", f"/campaigns/{campaign['reference']}/pause", params=campaign_params(campaign))
    print(f"  paused: {reply.get('paused')} | status: {reply.get('status')} | {reply.get('reason')}")
    if reply.get("next_action"):
        print(f"  {reply['next_action']}")
    return reply


COMMANDS = {"status": cmd_status, "results": cmd_results, "watch": cmd_watch, "pause": cmd_pause}
USAGE = ("Usage: python quickhowl.py <status | results | watch | pause> --run <YYYY-MM[-rN]> <task id> "
         "[--reference <ref>]\n  e.g. python quickhowl.py status --run 2026-10-r5 T6\n"
         "  Campaigns are created and started only through the orchestrator (ledger + call_start approval).")


def parse_manual_args(args: list[str]) -> tuple[str, str, str, str | None]:
    """'status --run 2026-10-r5 T6 [--reference R]' -> (command, month, task id, reference). Stops on anything else."""
    if not args or args[0] not in COMMANDS or "--run" not in args:
        raise ToolError(USAGE)
    at = args.index("--run")
    if len(args) < at + 3:
        raise ToolError(USAGE)
    used, reference = {0, at, at + 1, at + 2}, None
    if "--reference" in args:
        ref_at = args.index("--reference")
        if len(args) < ref_at + 2:
            raise ToolError(USAGE)
        reference = args[ref_at + 1]
        used |= {ref_at, ref_at + 1}
    if len(used) != len(args):
        raise ToolError(USAGE)
    return args[0], args[at + 1], args[at + 2], reference


if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        try:
            command, month, task_id, reference = parse_manual_args(sys.argv[1:])
            COMMANDS[command](ledger_campaign(month, task_id, reference))
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli)
