"""
quickhowl.py
============

AI voice calling (Quickhowl) for MOTM Revenue OS.
Follows the developer's guide for https://revenueoscall.b2botix.ai (tested 8 Oct 2026).

FLOW (same pattern as the email tool: build -> store -> human approves -> results)
----
    python quickhowl.py preview   Brain writes the call profile, contacts come from Supabase,
                                  the request is saved to data/call_launch_preview.json.
                                  Nothing is sent.
    python quickhowl.py launch    Sends the request with auto_start=false (guide scenario 6):
                                  account, profile, agent, campaign and contacts are STORED.
                                  Nothing is dialled. Saves data/call_campaign_state.json.
    python quickhowl.py start     Shows who will be called, you type 'yes', then sends the
                                  IDENTICAL request with auto_start=true (scenario 7) -> dialling.
    python quickhowl.py status    GET /campaigns/{reference}: where the campaign stands.
    python quickhowl.py results   GET /campaigns/{reference}/calls (with since paging):
                                  every call's status, outcome, summary, transcript.
                                  Merged into data/call_results.json (by call_id).
    python quickhowl.py watch     Runs 'results' every POLL_SECONDS until the campaign is
                                  finished (or WATCH_MINUTES pass). Ctrl+C stops watching only.
                                  A campaign still in DRAFT (not started) stops watching at once.
    python quickhowl.py pause     You type 'yes', then POST /pause: no new calls are placed.

WHO DECIDES WHAT
----------------
    LLM  : the company profile (what we offer, value, call objective) from the brain's
           understanding.json and the Quickhowl work order
    Code : contacts, phone format, test-number safety, references, which blocks to send,
           campaign goal, when the campaign is finished, every number
    Human: approves before any dialling ('start'), and pausing

REFERENCES (from the guide)
---------------------------
    client_reference  = one client account. Fixed per client forever (.env).
    reference         = one campaign. NEW for every launch: motm-<customer>-call-<YYYYMMDD-HHMMSS>.
    Same reference + same content  -> duplicate: true (safe to resend).
    Same reference + changed content -> HTTP 409. So 'start' resends the SAVED request exactly.
    There is no list endpoint, so every reference we send is kept in data/call_campaigns.json.

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
    data/understanding.json         the brain: what the customer sells
    data/work_orders.json           the Quickhowl (calls) work order
    data/quickicp_phones_raw.json   who passed the judges in the latest run (LinkedIn URLs)
    Supabase                        people (with phone) joined with their company, only rows of
                                    THIS customer (CUSTOMER_KEY)

WRITES
------
    data/call_launch_preview.json   the request (secrets removed)
    data/call_campaign_state.json   current campaign: references, last launch/start reply, next_since
    data/call_campaigns.json        history of every campaign we created (references)
    data/call_results.json          every call result, by call_id (first internal transcript line removed)

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
           Run 'launch' again (new reference). 'start' always resends the saved request exactly.
    - 422: the reply names the bad field or the refused contacts.
    - 503: server settings (launch or auto-start switched off). Nothing was created.
    - start_status 'blocked': read start_reason (e.g. phone_number failed to connect).
    - contacts_rejected: bad, duplicate or do-not-call numbers; the rest still go through.
    - Silent calls with elevenlabs: the ElevenLabs plan must be paid; or use voice_provider sarvam.
"""

import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from quickicp import TEST_MODE, ToolError, ask_llm, check_settings, clean_domain, load_json, save_json

load_dotenv()

# ---- Safety ----
REAL_CALLS_ENABLED = False      # stays False; the developer switches it on at go-live (+ QUICKHOWL_DND_HANDLED=true)
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

# ---- Files ----
PREVIEW_FILE = "data/call_launch_preview.json"
STATE_FILE = "data/call_campaign_state.json"
HISTORY_FILE = "data/call_campaigns.json"
RESULTS_FILE = "data/call_results.json"

REQUIRED_KEYS = ("QUICKHOWL_URL", "QUICKHOWL_KEY", "QUICKHOWL_CLIENT_REFERENCE", "QUICKHOWL_USER_EMAIL",
                 "QUICKHOWL_USER_PASSWORD", "QUICKHOWL_USER_FULL_NAME", "QUICKHOWL_USER_COMPANY")

# Words that mean internal strategy leaked into what the caller will say.
INTERNAL_WORDS = re.compile(r"\b(motm|cluster|clusters|segment|segments|p1|p2|p3|enquir\w*|work order|"
                            r"lead engine|month \d)\b", re.IGNORECASE)

FINISHED_STATUSES = {"completed", "failed", "paused"}


# ================= SMALL HELPERS =================

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: str, default):
    """Read a JSON file we own; return default if it does not exist yet."""
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def ask_yes(question: str) -> bool:
    try:
        return input(f"{question} Type 'yes' to continue: ").strip().lower() == "yes"
    except EOFError:
        return False


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

def calls_work_order() -> dict:
    """The Quickhowl work order written by the Master Agent."""
    work_orders = load_json("data/work_orders.json")
    for order in work_orders if isinstance(work_orders, list) else work_orders.values():
        if "quickhowl" in str(order.get("agent", "")).lower():
            return order
    raise ToolError("No Quickhowl work order in data/work_orders.json. Check AI calling is enabled "
                    "and run 'python master_agent.py'.")


def passed_linkedin_urls() -> list[tuple[str, int]]:
    """(LinkedIn URL, judge score) of people who passed the judges in the latest run, best first."""
    people = load_json("data/quickicp_phones_raw.json")
    rows = []
    for person in people:
        url = person.get("USER SOCIAL")
        validation = person.get("validation") or {}
        if url and validation.get("status", "pass") == "pass":
            rows.append((url, validation.get("score") or 0))
    rows.sort(key=lambda row: -row[1])
    if not rows:
        raise ToolError("No passed people in data/quickicp_phones_raw.json. Run 'python pipeline.py' first.")
    return rows


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


def build_contacts() -> list[dict]:
    """
    Contacts for the call campaign. Real prospect details always; the phone is the prospect's
    own in real mode, or one of our TEST_CALL_NUMBERS in test mode.
    """
    ranked = passed_linkedin_urls()
    order = {url: index for index, (url, _) in enumerate(ranked)}
    people = supabase_people([url for url, _ in ranked])
    people.sort(key=lambda p: order.get(p.get("user_social"), 10**6))
    print(f"Passed people in latest run: {len(ranked)} | found in Supabase: {len(people)}")
    return contacts_from_people(people)


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

    leaks = {name: INTERNAL_WORDS.findall(value) for name, value in profile.model_dump().items()}
    leaks = {name: words for name, words in leaks.items() if words}
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

def build_request(contacts: list[dict] | None = None) -> dict:
    """
    The full request WITHOUT secrets. Saved to disk; 'start' resends exactly this.
    contacts: given by the orchestrator's calls runner; default: the latest pipeline run's people.
    """
    check_settings("OPENAI_MODEL", *REQUIRED_KEYS)
    reason = real_calls_block_reason()
    if reason:
        raise ToolError(reason)

    understanding = load_json("data/understanding.json")
    work_order = calls_work_order()
    contacts = build_contacts() if contacts is None else contacts
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


def print_request(request: dict) -> None:
    print(f"\nclient_reference: {request['client_reference']}")
    print(f"reference:        {request['reference']}")
    print(f"campaign:         {request['campaign']['name']}")
    print(f"agent:            {request['agent']['name']} ({request['agent']['language']}, "
          f"{request['agent']['voice_provider']})")
    print("\nCompany profile the caller will use:")
    for key, value in request["company_profile"].items():
        print(f"  {key}: {value}")
    print(f"\nContacts ({len(request['contacts'])}):")
    for contact in request["contacts"]:
        role = contact.get("custom_fields", {}).get("designation", "")
        print(f"  {contact.get('name')} | {role} | {contact.get('company')} | phone {contact['phone']}")


def print_launch_reply(reply: dict) -> None:
    for block in ("account", "phone_number", "company_profile", "agent", "campaign"):
        value = reply.get(block)
        if isinstance(value, dict):
            extra = f" - {value['reason']}" if value.get("reason") else ""
            print(f"  {block}: {value.get('status')}{extra}")
        elif value is None and block in reply:
            print(f"  {block}: none")
    if reply.get("contacts_rejected"):
        print(f"  contacts_rejected: {reply['contacts_rejected']}")
    print(f"  duplicate: {reply.get('duplicate')} | start_status: {reply.get('start_status')}"
          + (f" - {reply['start_reason']}" if reply.get("start_reason") else ""))


# ================= STATE =================

def load_state() -> dict:
    state = read_json(STATE_FILE, None)
    if not state or not state.get("reference"):
        raise ToolError("No call campaign yet. Run 'python quickhowl.py launch' first.")
    return state


def save_state(state: dict) -> None:
    save_json(STATE_FILE, state)


def add_history(entry: dict) -> None:
    history = read_json(HISTORY_FILE, [])
    history.append(entry)
    save_json(HISTORY_FILE, history)


def campaign_params(state: dict) -> dict:
    return {"client_reference": state["client_reference"]}


# ================= COMMANDS =================

def cmd_preview() -> dict:
    request = build_request()
    assert_safe(request)
    save_json(PREVIEW_FILE, request)
    print_request(request)
    print(f"\nSaved {PREVIEW_FILE} (no secrets). Nothing was sent.")
    return request


def cmd_launch() -> None:
    """Store the campaign (auto_start false). Nothing is dialled."""
    request = cmd_preview()
    print("\nLaunching with auto_start=false: the campaign is STORED, nobody is called.")
    reply = call_api("POST", "/launch", body=with_secrets(request, auto_start=False))
    print_launch_reply(reply)

    state = {"client_reference": request["client_reference"], "reference": request["reference"],
             "launched_at": now_iso(), "test_mode": TEST_MODE, "launch_reply": reply,
             "started": False, "next_since": None}
    save_state(state)
    add_history({"client_reference": request["client_reference"], "reference": request["reference"],
                 "launched_at": state["launched_at"], "test_mode": TEST_MODE,
                 "contacts": len(request["contacts"])})

    if reply.get("start_status") == "blocked":
        raise ToolError(f"Launch blocked: {reply.get('start_reason')}. Fix it, then run 'launch' again.")
    print(f"\nStored. Next: python quickhowl.py start   (asks before dialling)")


def cmd_start() -> None:
    """Human approval, then the identical request with auto_start true (guide scenario 7)."""
    state = load_state()
    request = read_json(PREVIEW_FILE, None)
    if not request or request.get("reference") != state["reference"]:
        raise ToolError("The saved request does not match the stored campaign. Run 'launch' again.")
    assert_safe(request)
    print_request(request)
    if TEST_MODE:
        print("\nTEST MODE: only our own test numbers above will ring.")
    print("Calls go out inside the calling window (default 09:00-19:00 Mon-Sat), one at a time, "
          "20-40 s preparation before each. Unanswered calls are not retried.")
    if not ask_yes("\nStart dialling now?"):
        print("Not started.")
        return

    reply = call_api("POST", "/launch", body=with_secrets(request, auto_start=True))
    print_launch_reply(reply)
    state.update({"started": reply.get("start_status") in ("started", "already_started"),
                  "started_at": now_iso(), "start_reply": reply})
    save_state(state)
    if reply.get("start_status") == "blocked":
        raise ToolError(f"Start blocked: {reply.get('start_reason')}")
    print("\nNext: python quickhowl.py watch   (or 'status' / 'results' any time)")


def cmd_status() -> dict:
    state = load_state()
    reply = call_api("GET", f"/campaigns/{state['reference']}", params=campaign_params(state), timeout=60)
    print(f"\nCampaign {state['reference']}")
    print(f"  status: {reply.get('status')} | in calling window now: {reply.get('in_calling_window_now')}")
    print(f"  window: {reply.get('calling_window')}")
    print(f"  contacts: {reply.get('contacts_total')} total, {reply.get('contacts_still_to_call')} still to call")
    print(f"  contacts_by_status: {reply.get('contacts_by_status')}")
    print(f"  calls: {reply.get('calls_total')} | by outcome: {reply.get('calls_by_outcome')}")
    return reply


def clean_transcript(transcript: list | None) -> list:
    """Drop the first 'user' line: it is an internal 'greet now' instruction, not the person."""
    lines = list(transcript or [])
    for index, line in enumerate(lines):
        if line.get("speaker") == "user":
            return lines[:index] + lines[index + 1:]
    return lines


def cmd_results(quiet: bool = False) -> dict:
    """Fetch new/changed calls since last time (all pages), merge by call_id, save, summarise."""
    state = load_state()
    results = read_json(RESULTS_FILE, {})
    since, fetched, campaign_status = state.get("next_since"), 0, None
    while True:
        params = {**campaign_params(state), "limit": PAGE_LIMIT}
        if since:
            params["since"] = since
        reply = call_api("GET", f"/campaigns/{state['reference']}/calls", params=params, timeout=60)
        for item in reply.get("items", []):
            item["transcript"] = clean_transcript(item.get("transcript"))
            item["reference"] = state["reference"]
            results[item["call_id"]] = item
            fetched += 1
        campaign_status = reply.get("campaign_status")
        since = reply.get("next_since") or since
        if not reply.get("has_more"):
            break

    state["next_since"] = since
    save_state(state)
    save_json(RESULTS_FILE, results)

    mine = [r for r in results.values() if r.get("reference") == state["reference"]]
    if not quiet or fetched:
        print(f"\nCalls updated: {fetched} | campaign: {campaign_status} | calls so far: {len(mine)}")
        for call in mine:
            contact = call.get("contact") or {}
            summary = (call.get("summary") or "").replace("\n", " ")[:120]
            print(f"  {str(call.get('status')):10} {str(call.get('outcome')):18} "
                  f"{str(call.get('duration_seconds') or '-'):>4}s  {contact.get('name')} | "
                  f"{contact.get('company')}  {summary}")
        outcomes = {}
        for call in mine:
            outcomes[call.get("outcome")] = outcomes.get(call.get("outcome"), 0) + 1
        print(f"  Outcomes: {outcomes}")
        print(f"  Saved {RESULTS_FILE}")
    return {"campaign_status": campaign_status, "calls": mine}


def cmd_watch() -> None:
    """Poll every POLL_SECONDS until finished or WATCH_MINUTES pass. Ctrl+C only stops watching."""
    deadline = time.time() + WATCH_MINUTES * 60
    print(f"Watching every {POLL_SECONDS} s for up to {WATCH_MINUTES} min (Ctrl+C stops watching, "
          f"not the calls).")
    try:
        while True:
            outcome = cmd_results(quiet=True)
            status = cmd_status()
            if status.get("status") == "draft":
                # Known issue 3: a draft campaign never starts by itself; stop at once.
                print("\nThe campaign is still a DRAFT (stored, not started): nobody will be called. "
                      "Run 'python quickhowl.py start' first.")
                return
            calls = outcome["calls"]
            all_finished = all(call.get("finished") for call in calls) if calls else False
            if status.get("status") in FINISHED_STATUSES and status.get("contacts_still_to_call") == 0 \
                    and all_finished:
                print("\nCampaign finished.")
                cmd_results()
                return
            if time.time() > deadline:
                print(f"\nStopped watching after {WATCH_MINUTES} min. Calls continue; run 'watch' again.")
                return
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped watching. Calls continue on the server; run 'results' or 'watch' later.")


def cmd_pause() -> None:
    state = load_state()
    if not ask_yes(f"Pause campaign {state['reference']}? Calls in progress finish; no new calls."):
        print("Not paused.")
        return
    reply = call_api("POST", f"/campaigns/{state['reference']}/pause", params=campaign_params(state))
    print(f"  paused: {reply.get('paused')} | status: {reply.get('status')} | {reply.get('reason')}")
    if reply.get("next_action"):
        print(f"  {reply['next_action']}")


COMMANDS = {"preview": cmd_preview, "launch": cmd_launch, "start": cmd_start, "status": cmd_status,
            "results": cmd_results, "watch": cmd_watch, "pause": cmd_pause}

if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if command not in COMMANDS:
        print("Usage: python quickhowl.py " + " | ".join(COMMANDS))
        sys.exit(1)
    try:
        COMMANDS[command]()
    except ToolError as error:
        print("\nSTOPPED:", error)
        sys.exit(1)
    except Exception:
        print("\nUNEXPECTED ERROR (details below for debugging):")
        traceback.print_exc()
        sys.exit(1)