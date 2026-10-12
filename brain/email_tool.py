"""
email_tool.py
=============

Hyper-personalized email campaigns in the RevenueOS email tool.
The tool's AI rewrites our reference emails for every recipient; we supply the objective,
reference emails and rich recipient details.

FLOW
----
    python email_tool.py preview    build the request and save it; nothing is sent
    python email_tool.py launch     step 1: campaign stored as DRAFT, sample emails requested
    python email_tool.py samples    step 2: wait for the sample emails and print them
    python email_tool.py approve    step 3: you confirm, the campaign starts (RUNNING)

    A human always approves. Each email is personalized about an hour before it is sent,
    only inside the schedule.

WHERE EACH PART COMES FROM
--------------------------
    objective, emails, campaign   LLM, from the brain (understanding + email work order), facts only
    recipients                    Supabase: people who PASSED the judge in the latest pipeline run
                                  (their LinkedIn URLs in data/quickicp_phones_raw.json), with company joined.
                                  Only rows of THIS customer (CUSTOMER_KEY); a person linked to another
                                  customer's company stops the run.
    client_reference, user        .env (later: the customer's record in the platform)
    campaign_id, workspace_id     saved after launch in data/email_campaign_state.json

TEST MODE
---------
    The tool keeps old saved details for addresses it already knows, so known test addresses would
    get the plain reference email. Test mode therefore gives each test recipient a REAL prospect's
    details but a NEW '+' address of a team inbox (e.g. ramvish023+hp1a@gmail.com).
    Personalization is real; no prospect is emailed.

REFERENCE EMAIL RULES (code, known issue 4)
-------------------------------------------
    - never_say = our fixed phrases ('guaranteed', 'cheapest', '100%', 'last chance') FIRST, then the
      LLM's own (max 10 in total, so ours are never cut). The first prompt names the fixed list and says
      the LLM's own phrases may not appear in its own emails. None may appear in any subject, preheader
      or body, matched as WHOLE phrases in any case (strict: 'free' also hits 'feel free' and
      'hassle-free', not 'freedom'). A hit is printed with email, field and the text around it:
      one LLM rewrite (shown those pieces), then STOPPED with the same lines.
    - The LLM writes no sign-off. Code adds '<p>Regards,<br>Team <CUSTOMER_DISPLAY_NAME></p>' and
      replaces every long customer name with the display name (CUSTOMER_DISPLAY_NAME is required):
      CUSTOMER_LONG_NAMES (.env, comma-separated) + the understanding's customer_name, longest first,
      so 'Anvixa AI Product' and 'Anvixa AI' both become 'Anvixa' (never 'Anvixa Product').

TOOL RULES (from the developer)
-------------------------------
    - Always send client_reference for an existing account; a new 'reference' per campaign.
    - 'user' must stay exactly the same for a client_reference (409 otherwise).
    - Max 500 recipients, 10 emails, 10 must_mention and 10 never_say phrases.
    - start_status 'pending' = audience not ready: the SAME request is sent again.
    - No unsubscribe footer is added by the tool yet: fix before emailing real prospects.

REQUIRED IN .env
----------------
    EMAIL_TOOL_URL, EMAIL_TOOL_KEY, EMAIL_TOOL_USER_EMAIL, CLIENT_REFERENCE
    TEST_WORKSPACE_NAME, TEST_RECIPIENTS        test mode
    SUPABASE_URL, SUPABASE_KEY
    CUSTOMER_KEY                  fixed customer key, e.g. anvixa-ai-product (which Supabase rows are ours)
    CUSTOMER_DISPLAY_NAME         the name prospects see, e.g. Anvixa
OPTIONAL IN .env
----------------
    CUSTOMER_LONG_NAMES           long names replaced by the display name, e.g. Anvixa AI Product, Anvixa AI
"""

import html
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from quickicp import TEST_MODE, ToolError, ask_llm, check_settings, load_json, save_json

load_dotenv()

DAILY_LIMIT = 10 if TEST_MODE else 50
TEST_HP_COUNT = 3                      # test mode: how many prospects to mirror onto team inboxes
MAX_PHRASES = 10                       # tool limit for must_mention / never_say
MAX_RECIPIENTS = 500                   # tool limit per request
PENDING_RETRIES = 3                    # resend when the tool says 'pending'
SAMPLE_WAIT_SECONDS = 120              # how long 'samples' waits for samples to be ready
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

ENRICHED_FILE = "data/quickicp_phones_raw.json"
STATE_FILE = "data/email_campaign_state.json"
SAMPLES_FILE = "data/email_samples.json"
NEVER_SAY_DEFAULTS = ["guaranteed", "cheapest", "100%", "last chance"]


# ================= WHAT THE LLM WRITES =================

# ================= WHAT THE LLM WRITES =================

class Objective(BaseModel):
    objective: str = Field(description="The goal of each email for the reader, e.g. 'Book a 15-minute demo'")
    offer: str = Field(description="What the customer offers, one plain sentence a prospect would understand")
    cta: str = Field(description="ONE short question, e.g. 'Would a 15-minute demo next week be useful?'")
    target: str = Field(description="Who the emails are for, by role, e.g. 'Procurement and operations leaders'")
    problem_solved: str = Field(description="The prospect's pain point in plain words")
    tone: str = Field(description="Writing style, e.g. 'professional and direct'")
    must_mention: list[str] = Field(description="At most 3 short phrases (max 5 words each) every email must contain")
    never_say: list[str] = Field(description="Up to 10 phrases that must never appear")


class ReferenceEmail(BaseModel):
    subject: str = Field(description="Short subject; may use {{company}}")
    body_html: str = Field(description="Max 90 words. <p> paragraphs only. First paragraph 'Hi {{first_name}},'. "
                                       "Ends with the exact CTA question.")
    preheader: str = Field(description="One short inbox preview line")
    wait_days_before: int = Field(description="0 for the first email, then days after the previous one")


class CampaignContent(BaseModel):
    campaign_name: str = Field(description="'<Customer> - <Target role> - <Month Year>'")
    campaign_description: str
    objective: Objective
    emails: list[ReferenceEmail] = Field(description="3 reference emails: intro, value follow-up, short final note")


# ================= CONTENT (LLM) =================

MAX_MUST_MENTION = 3
DEFAULT_CTA = "Would a 15-minute demo next week be useful?"

# Words that show a phrase is internal strategy, never for a prospect's eyes.
INTERNAL_WORDS = ["enquir", "cluster", "users", "calling", "motm", "segment", "target", "month", "lead"]


def is_internal(phrase: str) -> bool:
    """True if a phrase contains numbers or internal-strategy words."""
    text = phrase.lower()
    return any(ch.isdigit() for ch in text) or any(word in text for word in INTERNAL_WORDS)


def email_work_order() -> dict:
    """The Email Agent work order from data/work_orders.json ({} if there is none)."""
    for work_order in load_json("data/work_orders.json"):
        if work_order.get("agent") == "Email Agent":
            return work_order
    return {}


def write_content(ask=ask_llm, understanding: dict | None = None, work_order: dict | None = None) -> CampaignContent:
    """
    Objective + reference emails from the brain. Code enforces the tool's rules afterwards:
    our own never_say phrases may not appear (one rewrite, then STOPPED), and the signature is
    added by code from CUSTOMER_DISPLAY_NAME.
    understanding / work_order: given by a selftest (then no file in data/ is read); None = read the files.
    """
    display = display_name()
    if understanding is None:
        understanding = load_json("data/understanding.json")
    if work_order is None:
        work_order = email_work_order()
    prompt = (
        "You are the Master Agent of MOTM Revenue OS. Prepare a HYPER-PERSONALIZED email campaign.\n"
        "The email tool's AI rewrites your reference emails for every recipient.\n"
        "Rules:\n"
        "- Write for the PROSPECT. Never mention targeting criteria, regions, user counts, enquiry goals, "
        "calling, MOTM, or any internal strategy.\n"
        "- Use only facts about the customer's product from the understanding. Never invent claims or numbers.\n"
        "- objective: what each email should achieve for the reader, e.g. 'Book a 15-minute demo'.\n"
        "- cta: ONE short question, e.g. 'Would a 15-minute demo next week be useful?'. It ends every email "
        "word for word.\n"
        "- must_mention: at most 3 short product phrases (max 5 words each), e.g. 'no-code automation'.\n"
        "- never_say: hype words the emails must avoid. These are ALWAYS forbidden (they are added "
        f"automatically): {json.dumps(NEVER_SAY_DEFAULTS)}. Do not use any of them in any subject, "
        "preheader or body. You may add a few more.\n"
        "- Every phrase in YOUR never_say list must not appear in YOUR OWN emails either (whole words, any case): "
        "only ban a word you do not use, e.g. if you ban 'free', also avoid 'feel free' and 'free demo'.\n"
        "- Each email: max 90 words, <p> paragraphs only, first paragraph 'Hi {{first_name}},'. "
        "Subjects may use {{company}}. No other variables.\n"
        "- Do NOT add any sign-off, name or signature: it is added automatically.\n"
        f"- Today is {datetime.now():%B %Y}; use it in the campaign name.\n\n"
        "Email work order:\n" + json.dumps(work_order, ensure_ascii=False)
        + "\n\nCustomer understanding:\n" + json.dumps(understanding, ensure_ascii=False)
    )
    feedback, hits = "", []
    for _ in range(2):                          # the first answer + at most ONE rewrite
        content = ask(CampaignContent, prompt + feedback, "Campaign content")
        if not content.emails:
            raise ToolError("Campaign content: the LLM returned no emails.")
        content = enforce_rules(content)
        hits = never_say_hits(content)
        if not hits:
            return add_signature(content, display, long_names(understanding, display))
        print(f"  never_say list (ours first, then the LLM's): {content.objective.never_say}")
        print(f"  Reference emails use never_say phrases {hit_phrases(hits)}: asking for one rewrite.")
        for line in hit_lines(hits):
            print(f"    {line}")
        feedback = ("\n\nYOUR PREVIOUS EMAILS USED THESE FORBIDDEN PHRASES (whole words, any case):\n"
                    + "\n".join(f"- {line}" for line in hit_lines(hits))
                    + "\nRewrite every subject, preheader and body without them (whole words, any case: "
                    "'free' also forbids 'feel free' and 'hassle-free').")
    raise ToolError(f"STOPPED: the reference emails still contain never_say phrases {hit_phrases(hits)} after one "
                    "rewrite:\n  " + "\n  ".join(hit_lines(hits)))


def display_name() -> str:
    """The name prospects see (CUSTOMER_DISPLAY_NAME, e.g. 'Anvixa'). Required."""
    name = (os.getenv("CUSTOMER_DISPLAY_NAME") or "").strip()
    if not name:
        raise ToolError("STOPPED: CUSTOMER_DISPLAY_NAME is missing in .env (e.g. CUSTOMER_DISPLAY_NAME=Anvixa).")
    return name


def phrase_pattern(phrase: str) -> re.Pattern:
    """
    A never_say phrase as a WHOLE phrase, any case: no letter or digit directly before or after it.
    'free' matches 'free demo', 'Free trial', 'hassle-free' and 'feel free' (strict); not 'freedom' or 'carefree'.
    """
    return re.compile(r"(?<![a-z0-9])" + re.escape(phrase.strip()) + r"(?![a-z0-9])", re.IGNORECASE)


def never_say_hits(content: CampaignContent) -> list[dict]:
    """
    Every never_say phrase (the fixed ones + the LLM's) found in a subject, preheader or body, with
    where: [{"phrase", "email" (1-based), "field", "snippet" (~40 characters around it)}]. Code decides.
    """
    phrases = list(dict.fromkeys(p.strip().lower() for p in NEVER_SAY_DEFAULTS + content.objective.never_say
                                 if p.strip()))
    hits = []
    for number, email in enumerate(content.emails, start=1):
        fields = {"subject": email.subject, "preheader": email.preheader, "body": plain_text(email.body_html)}
        for field, text in fields.items():
            for phrase in phrases:
                for match in phrase_pattern(phrase).finditer(text):
                    start, end = max(0, match.start() - 40), min(len(text), match.end() + 40)
                    around = " ".join(text[start:end].split())   # one line, also for multi-paragraph bodies
                    snippet = ("…" if start else "") + around + ("…" if end < len(text) else "")
                    hits.append({"phrase": phrase, "email": number, "field": field, "snippet": snippet})
    return hits


def hit_phrases(hits: list[dict]) -> list[str]:
    """The distinct phrases found, sorted: ['free', 'guaranteed']."""
    return sorted({hit["phrase"] for hit in hits})


def hit_lines(hits: list[dict]) -> list[str]:
    """One readable line per hit: "'free' in email 2 body: '…Feel free to reply…'"."""
    return [f"'{hit['phrase']}' in email {hit['email']} {hit['field']}: '{hit['snippet']}'" for hit in hits]


def long_names(understanding: dict, display: str) -> list[str]:
    """
    Every long customer name to replace by the display name, LONGEST FIRST:
    CUSTOMER_LONG_NAMES in .env (comma-separated, e.g. 'Anvixa AI Product, Anvixa AI') + the
    understanding's customer_name (the LLM may write it differently from plan to plan).
    Longest first, so 'Anvixa AI Product' becomes 'Anvixa', never 'Anvixa Product'.
    """
    names = [name.strip() for name in (os.getenv("CUSTOMER_LONG_NAMES") or "").split(",")]
    names.append((understanding.get("customer_name") or "").strip())
    unique = {name.lower(): name for name in names if name and name.lower() != display.lower()}
    return sorted(unique.values(), key=len, reverse=True)


def add_signature(content: CampaignContent, display: str, names: list[str]) -> CampaignContent:
    """Long customer names -> display name everywhere (one pass, longest first); every body ends with 'Regards, Team <display>'."""
    signature = f"<p>Regards,<br>Team {display}</p>"
    ordered = sorted(names, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(name) for name in ordered), re.IGNORECASE) if ordered else None
    for email in content.emails:
        if pattern:
            email.subject = pattern.sub(display, email.subject)
            email.preheader = pattern.sub(display, email.preheader)
            email.body_html = pattern.sub(display, email.body_html)
        if not email.body_html.rstrip().endswith(signature):
            email.body_html = email.body_html.rstrip() + signature
    return content


def enforce_rules(content: CampaignContent) -> CampaignContent:
    """Guarantee the tool's rules in code, whatever the LLM wrote."""
    objective = content.objective

    # CTA must be one question.
    cta = objective.cta.strip()
    if not cta.endswith("?") or len(cta.split()) > 20:
        print(f"  CTA '{cta}' is not a short question; using default.")
        cta = DEFAULT_CTA
    objective.cta = cta



    # never_say: OUR fixed phrases first, then the LLM's (same phrase once, any case), so the cut to the
    # tool's limit only ever drops the LLM's extras, never ours.
    merged = {}
    for phrase in NEVER_SAY_DEFAULTS + objective.never_say:
        if phrase.strip():
            merged.setdefault(phrase.strip().lower(), phrase.strip())
    objective.never_say = list(merged.values())[:MAX_PHRASES]

    # Emails: at most 10, sensible waits, greeting in <p>, exact CTA present.
    content.emails = content.emails[:10]
    for index, email in enumerate(content.emails):
        email.wait_days_before = 0 if index == 0 else max(1, email.wait_days_before)
        body = email.body_html.strip()
        if body.startswith("Hi {{first_name}},") and not body.startswith("<p>"):
            body = "<p>Hi {{first_name}},</p>" + body[len("Hi {{first_name}},"):]
        if cta not in body:
            body += f"<p>{cta}</p>"
        email.body_html = body

        # must_mention: short, customer-facing, and already present in EVERY reference email body.
    # The tool rejects a rewrite that misses any phrase, so a phrase our own emails lack is dropped.
    bodies = [email.body_html.lower() for email in content.emails]
    kept = []
    for phrase in objective.must_mention:
        if len(phrase.split()) > 5 or is_internal(phrase):
            continue
        if all(phrase.lower() in body for body in bodies):
            kept.append(phrase)
        else:
            print(f"  Dropped must_mention '{phrase}': not in every reference email.")
    objective.must_mention = kept[:MAX_MUST_MENTION]
    

    print(f"  CTA: {objective.cta}")
    print(f"  must_mention: {objective.must_mention}")
    return content


# ================= RECIPIENTS (SUPABASE) =================

def split_name(full_name: str | None) -> tuple[str, str]:
    """'Rajat Kumar Sharma' -> ('Rajat', 'Kumar Sharma')"""
    parts = (full_name or "").strip().split()
    return (parts[0].title() if parts else ""), " ".join(parts[1:]).title()


def split_location(location: str | None) -> tuple[str, str, str]:
    """'Kurla, Maharashtra, India' -> ('Kurla', 'Maharashtra', 'India'); 'Delhi, India' -> ('Delhi', '', 'India')"""
    parts = [p.strip() for p in (location or "").split(",") if p.strip()]
    if not parts:
        return "", "", ""
    country = parts[-1] if len(parts) >= 2 else ""
    state = parts[-2] if len(parts) >= 3 else ""
    return parts[0], state, country


def validated_people_links() -> list[str]:
    """LinkedIn URLs of people who passed the judge and have an email, from the latest pipeline run."""
    links = []
    for person in load_json(ENRICHED_FILE):
        if person.get("validation", {}).get("status") == "pass" and person.get("email") and person.get("USER SOCIAL"):
            links.append(person["USER SOCIAL"])
    if not links:
        raise ToolError("No validated people with an email. Run: python pipeline.py")
    return links


def fetch_people(links: list[str]) -> list[dict]:
    """Read those people of THIS customer (CUSTOMER_KEY) from Supabase, each with its company joined."""
    check_settings("SUPABASE_URL", "SUPABASE_KEY")
    from supabase_store import COMPANY_TABLE, PEOPLE_TABLE, db
    from state_store import current_customer
    customer = current_customer()
    rows = (db.table(PEOPLE_TABLE)
              .select(f"*, {COMPANY_TABLE}(customer, company_name, website, location, industry)")
              .eq("customer", customer)
              .in_("user_social", links)
              .execute().data)
    if not rows:
        raise ToolError(f"Validated people of customer '{customer}' are not in Supabase yet. "
                        "Run: python pipeline.py save")
    for row in rows:
        row["company"] = row.pop(COMPANY_TABLE, None) or {}
        # Code check: a person must never be linked to another customer's company.
        if row["company"] and row["company"].get("customer") != customer:
            raise ToolError(f"STOPPED: person {row.get('full_name')} is linked to a company of another "
                            f"customer ('{row['company'].get('customer')}'). Check company_id {row.get('company_id')}.")
    return rows


def to_recipient(row: dict, email: str) -> dict:
    """Supabase person + company -> the tool's recipient fields. Empty fields are left out."""
    first, last = split_name(row.get("full_name"))
    city, state, country = split_location(row.get("location"))
    company = row["company"]
    website = company.get("website") or ""
    if website and not website.startswith("http"):
        website = "https://" + website
    recipient = {
        "email": email,
        "first_name": first,
        "last_name": last,
        "company": company.get("company_name"),
        "title": row.get("job_position"),
        "company_industry": company.get("industry") or row.get("industry"),
        "city": city,
        "state": state,
        "country": country or row.get("country"),
        "company_website": website,
    }
    return {key: value for key, value in recipient.items() if value}


def plus_address(base: str, tag: str) -> str:
    """'ramvish023@gmail.com' + 'hp1a' -> 'ramvish023+hp1a@gmail.com' (same inbox, new address)."""
    local, domain = base.split("@", 1)
    return f"{local}+{tag}@{domain}"


def test_inboxes() -> list[str]:
    inboxes = [item.split(":", 1)[0].strip() for item in (os.getenv("TEST_RECIPIENTS") or "").split(",")
               if "@" in item]
    if not inboxes:
        raise ToolError("Test mode needs TEST_RECIPIENTS in .env, e.g. you@motm.tech:YourName")
    return inboxes


def recipients_for(rows: list[dict], run_tag: str) -> list[tuple]:
    """(person row id, recipient) pairs. TEST_MODE: first TEST_HP_COUNT people on '+' team addresses."""
    if TEST_MODE:
        inboxes = test_inboxes()
        return [(row.get("id"), to_recipient(row, plus_address(inboxes[i % len(inboxes)], f"hp{run_tag}{i}")))
                for i, row in enumerate(rows[:TEST_HP_COUNT])]
    pairs, seen = [], set()
    for row in rows:
        email = (row.get("email") or "").strip().lower()
        if EMAIL_PATTERN.match(email) and email not in seen:
            seen.add(email)
            pairs.append((row.get("id"), to_recipient(row, email)))
    return pairs[:MAX_RECIPIENTS]


# ================= LAUNCH REQUEST =================

def campaign_reference(customer: str) -> tuple[str, str]:
    """A NEW reference per campaign, plus a short tag used for test '+' addresses."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Z0-9]+", "-", customer.upper()).strip("-")
    return f"MOTM-{slug}-HYPER-{stamp}", stamp[-6:]


def smtp_settings() -> dict:
    """Mailbox details, only for a brand-new client (no CLIENT_REFERENCE yet)."""
    check_settings("SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_DISPLAY_NAME")
    return {
        "host": os.getenv("SMTP_HOST"),
        "security_mode": os.getenv("SMTP_SECURITY"),
        "port": int(os.getenv("SMTP_PORT")),
        "username": os.getenv("SMTP_USERNAME"),
        "password": os.getenv("SMTP_PASSWORD"),
        "email_address": os.getenv("SMTP_USERNAME"),
        "sender_display_name": os.getenv("SMTP_DISPLAY_NAME"),
    }


def build_launch_body(rows: list[dict] | None = None, write=None) -> dict:
    """The full launch request. rows: people to email (default: the latest pipeline run's file)."""
    return build_launch_request(rows, write)[0]


def build_launch_request(rows: list[dict] | None = None, write=None) -> tuple[dict, list]:
    """(launch request, person row id of each recipient). Used by the CLI and the orchestrator's runner."""
    check_settings("EMAIL_TOOL_USER_EMAIL")
    customer = load_json("data/understanding.json").get("customer_name", "Customer")
    workspace = (os.getenv("TEST_WORKSPACE_NAME") or customer) if TEST_MODE else customer
    reference, run_tag = campaign_reference(customer)
    content = (write or write_content)()
    pairs = recipients_for(rows if rows is not None else fetch_people(validated_people_links()), run_tag)

    body = {
        "reference": reference,
        "campaign_type": "HYPER_PERSONALIZED",
        "user": {"email": os.getenv("EMAIL_TOOL_USER_EMAIL"), "workspace_name": workspace, "role": "ADMIN"},
        "objective": {**content.objective.model_dump(), "email_format": "TEXT"},
        "recipients": [recipient for _, recipient in pairs],
        "campaign": {"name": content.campaign_name, "description": content.campaign_description},
        "emails": [email.model_dump() for email in content.emails],
        "schedule": {
            "timezone": "Asia/Kolkata",
            "weekdays": [1, 2, 3, 4, 5],
            "window_start_local": "09:30:00",
            "window_end_local": "17:30:00",
            "daily_limit": DAILY_LIMIT,
        },
    }
    if os.getenv("CLIENT_REFERENCE"):
        body["client_reference"] = os.getenv("CLIENT_REFERENCE")    # existing account: no smtp
    else:
        body["smtp"] = smtp_settings()                              # brand-new client
    return body, [person_id for person_id, _ in pairs]


def masked(body: dict) -> dict:
    copy = json.loads(json.dumps(body))
    if "smtp" in copy:
        copy["smtp"]["password"] = "********"
    return copy


# ================= TOOL CALLS =================

def tool_base() -> str:
    """'https://host/api/v1/integrations/revenueos/launch' -> 'https://host'"""
    check_settings("EMAIL_TOOL_URL", "EMAIL_TOOL_KEY")
    return os.getenv("EMAIL_TOOL_URL").split("/api/")[0]


def call_tool(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    """One call to the email tool. Network failures become a clear ToolError."""
    headers = {"X-RevenueOS-Key": os.getenv("EMAIL_TOOL_KEY")}
    try:
        response = httpx.request(method, url, json=body, headers=headers, timeout=120)
    except (httpx.TimeoutException, httpx.TransportError) as error:
        raise ToolError(f"Email tool did not answer ({error}).") from error
    try:
        reply = response.json()
    except ValueError:
        reply = {"raw": response.text[:1000]}
    return response.status_code, reply


def campaign_url(state: dict, action: str) -> str:
    return (f"{tool_base()}/api/v1/workspaces/{state['workspace_id']}/integrations/revenueos/"
            f"campaigns/{state['campaign_id']}/{action}")


# ================= STEP 1: LAUNCH =================

def send_launch(body: dict, wait_seconds: int = 20) -> tuple[int, dict]:
    """
    POST the launch request. 'pending' (audience not ready) -> the SAME request again, up to
    PENDING_RETRIES times. Same reference + same content is safe to resend (duplicate: true).
    """
    url = os.getenv("EMAIL_TOOL_URL")
    for attempt in range(1, PENDING_RETRIES + 1):
        status, reply = call_tool("POST", url, body)
        if status >= 400:
            raise ToolError(f"Launch rejected ({status}): {json.dumps(reply)[:500]}")
        if reply.get("start_status") != "pending":
            break
        print(f"  Audience not ready yet (pending), sending the same request again in {wait_seconds}s "
              f"({attempt}/{PENDING_RETRIES})")
        time.sleep(wait_seconds)
    start = reply.get("start_status")
    if start in ("blocked", "failed", "pending") or not reply.get("campaign_id"):
        raise ToolError(f"Launch did not complete: start_status={start}, error={reply.get('start_error')}")
    return status, reply


def run_launch(send: bool) -> None:
    body = build_launch_body()
    save_json("data/email_launch_preview.json", masked(body))
    print(json.dumps(masked(body), indent=2, ensure_ascii=False))
    print(f"\nRecipients: {len(body['recipients'])} | test mode: {TEST_MODE} | type: HYPER_PERSONALIZED")
    print("Saved data/email_launch_preview.json")
    if not send:
        return

    status, reply = send_launch(body)
    save_json("data/email_launch_result.json", {"status": status, "reply": reply})
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    start = reply.get("start_status")

    save_json(STATE_FILE, {
        "reference": body["reference"],
        "workspace_id": reply["workspace_id"],
        "campaign_id": reply["campaign_id"],
        "campaign_name": body["campaign"]["name"],
        "launched_at": datetime.now().isoformat(timespec="seconds"),
    })
    print(f"\nLAUNCHED as {reply.get('campaign_status')} ({start}). Saved {STATE_FILE}")
    print("Next: python email_tool.py samples")


# ================= STEP 2: SAMPLES =================

def plain_text(body_html: str) -> str:
    """Readable text from HTML for printing."""
    text = re.sub(r"<br\s*/?>", "\n", body_html or "")
    text = re.sub(r"</p>", "\n\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def run_samples() -> None:
    state = load_json(STATE_FILE)
    print(f"Campaign: {state['campaign_name']} ({state['campaign_id']})")

    deadline = time.time() + SAMPLE_WAIT_SECONDS
    while True:
        status, reply = call_tool("GET", campaign_url(state, "samples"))
        if status >= 400:
            raise ToolError(f"Samples request rejected ({status}): {json.dumps(reply)[:500]}")
        samples_status = reply.get("samples_status")
        if samples_status != "generating" or time.time() > deadline:
            break
        print("  Samples still generating, checking again in 5s...")
        time.sleep(5)

    save_json(SAMPLES_FILE, reply)
    print(f"\nSamples status: {samples_status} | approval: {reply.get('approval_status')}\n")
    for sample in reply.get("samples", []):
        print(f"--- Email {sample.get('email_number')} | state {sample.get('state')} | "
              f"for {sample.get('recipient_first_name')} at {sample.get('recipient_company')} | "
              f"fallback: {sample.get('fallback_used')} | issues: {sample.get('failure_codes')}")
        print(f"Subject: {sample.get('subject')}\n")
        print(plain_text(sample.get("body_html")) + "\n")

    if samples_status == "ready":
        print("Read them carefully. If they are good: python email_tool.py approve")
    else:
        print("Samples are not ready to approve. Run 'samples' again shortly, or check the campaign in the app.")


# ================= STEP 3: APPROVE =================

APPROVE_MESSAGES = {
    "previews_incomplete": "Samples are still being written. Run 'samples' again, then approve.",
    "previews_failed": "The sample failed and cannot be approved. Check the campaign in the app.",
    "approval_stale": "Content changed after the samples were made. Run 'samples' again.",
    "preflight_failed": "Something blocks the start; see the reason above.",
}


def run_approve() -> None:
    state = load_json(STATE_FILE)
    print(f"Campaign: {state['campaign_name']} ({state['campaign_id']})")
    answer = input("Approve and start sending? Type 'yes' to confirm: ").strip().lower()
    if answer != "yes":
        print("Not approved. Nothing was started.")
        return

    status, reply = call_tool("POST", campaign_url(state, "approve"))
    print(json.dumps(reply, indent=2, ensure_ascii=False))
    if status >= 400:
        code = reply.get("error", {}).get("code") if isinstance(reply.get("error"), dict) else None
        raise ToolError(f"Approve rejected ({status}). " + APPROVE_MESSAGES.get(code, json.dumps(reply)[:300]))

    state["approved_at"] = datetime.now().isoformat(timespec="seconds")
    state["campaign_status"] = reply.get("campaign_status")
    save_json(STATE_FILE, state)
    print(f"\n{reply.get('status', '').upper()}: campaign is {reply.get('campaign_status')}. "
          "Emails are personalized about an hour before each send, inside the schedule.")


# ================= RUN =================

MODES = {
    "preview": lambda: run_launch(send=False),
    "launch": lambda: run_launch(send=True),
    "samples": run_samples,
    "approve": run_approve,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    if mode not in MODES:
        print("Usage: python email_tool.py " + " | ".join(MODES))
        sys.exit(1)
    try:
        MODES[mode]()
    except ToolError as error:
        print("\nSTOPPED:", error)
        sys.exit(1)
    except Exception:
        print("\nUNEXPECTED ERROR (details below for debugging):")
        traceback.print_exc()
        sys.exit(1)