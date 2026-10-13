"""
email_tool.py
=============

Hyper-personalized email campaigns in the RevenueOS email tool.
The tool's AI rewrites our reference emails for every recipient; we supply the objective,
reference emails and rich recipient details.

HOW IT IS USED
--------------
    Campaigns are built, launched (stored as DRAFT, samples) and approved ONLY by the orchestrator's email
    runner (runner_email.py): every launch saved in the ledger first, sending only after the email_approve
    decision. This file is its library (content rules, recipients, request, API calls). The old CLI
    commands preview / launch / approve (they skipped the ledger and the approval) are in
    archive/legacy/email_tool_cli.py.

    A human always approves. Each email is personalized about an hour before it is sent,
    only inside the schedule.

MANUAL TOOL (the campaign comes from the LEDGER of one task, never from a data/ file)
-----------
    python email_tool.py samples --run 2026-10-r5 T5    READ-ONLY: the sample emails of that task's campaign
                                                        (campaign_id + workspace_id from its 'launch' step)

WHERE EACH PART COMES FROM
--------------------------
    objective, emails, campaign   LLM, from the run's understanding + THIS task's work order, facts only
    recipients                    the contacts task's people (Supabase), with company joined. Only rows of
                                  THIS customer (CUSTOMER_KEY); a person linked to another customer's
                                  company stops the run.
    client_reference, user        .env (later: the customer's record in the platform)
    campaign_id, workspace_id     the ledger (the email task's 'launch' step)

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
    - The LLM's own list is for hype/claims only: ordinary words (ORDINARY_WORDS: free, help, easy, quick,
      simple) it adds are DROPPED by code (printed), so 'feel free' no longer forces a rewrite. Longer LLM
      phrases ('risk-free', 'free trial') and our fixed phrases stay strict.
    - Cold emails: never imply an earlier conversation, meeting, call or reply (COLD_EMAIL_RULE, prompt).
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
from datetime import datetime

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from quickicp import TEST_MODE, ToolError, ask_llm, check_settings

load_dotenv()

DAILY_LIMIT = 10 if TEST_MODE else 50
TEST_HP_COUNT = 3                      # test mode: how many prospects to mirror onto team inboxes
MAX_PHRASES = 10                       # tool limit for must_mention / never_say
MAX_RECIPIENTS = 500                   # tool limit per request
PENDING_RETRIES = 3                    # resend when the tool says 'pending'
SAMPLE_WAIT_SECONDS = 120              # how long 'samples' waits for samples to be ready
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

NEVER_SAY_DEFAULTS = ["guaranteed", "cheapest", "100%", "last chance"]
# Email 3 used to pull the LLM towards 'last chance': it is a calm follow-up (prompt guidance, not the guard).
EMAIL3_RULE = ("Email 3 is a polite final follow-up to our earlier emails: calm and helpful (e.g. a last useful "
               "idea, or an easy way to reply later). It must never use urgency or deadline wording ('last chance', "
               "'final chance', 'act now', 'hurry', 'don't miss out', 'limited time') in its subject, preheader or body.")
# Every email is cold: the prospect has never spoken with us (prompt rule for all emails).
COLD_EMAIL_RULE = ("Every email is a cold email to someone who has never spoken with us. Never imply an earlier "
                   "conversation, meeting, call or reply (e.g. 'continuing our conversation', 'as discussed', "
                   "'following up on our call', 'great speaking with you', 'as promised').")
# Ordinary words the LLM may NOT put in its own never_say list: polite writing needs them ('feel free').
# Code drops them from the LLM's list; our fixed NEVER_SAY_DEFAULTS are never dropped.
ORDINARY_WORDS = ["free", "help", "easy", "quick", "simple"]


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
    emails: list[ReferenceEmail] = Field(
        description="3 reference emails: intro, value follow-up, polite final follow-up (calm, no urgency)")


# ================= CONTENT (LLM) =================

MAX_MUST_MENTION = 3
DEFAULT_CTA = "Would a 15-minute demo next week be useful?"

# Words that show a phrase is internal strategy, never for a prospect's eyes.
INTERNAL_WORDS = ["enquir", "cluster", "users", "calling", "motm", "segment", "target", "month", "lead"]


def is_internal(phrase: str) -> bool:
    """True if a phrase contains numbers or internal-strategy words."""
    text = phrase.lower()
    return any(ch.isdigit() for ch in text) or any(word in text for word in INTERNAL_WORDS)


def write_content(ask=ask_llm, *, understanding: dict, work_order: dict) -> CampaignContent:
    """
    Objective + reference emails from the brain. Code enforces the tool's rules afterwards:
    our own never_say phrases may not appear (one rewrite, then STOPPED), and the signature is
    added by code from CUSTOMER_DISPLAY_NAME.
    understanding / work_order: the run's understanding and THIS task's work order (required; never data/).
    """
    display = display_name()
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
        "- Every phrase in YOUR never_say list must not appear in YOUR OWN emails either (whole words, any case).\n"
        "- Your own never_say list is only for hype or claims (e.g. 'revolutionary', 'risk-free'). Never add "
        f"ordinary words you may need for polite writing, such as {', '.join(repr(w) for w in ORDINARY_WORDS)}.\n"
        f"- {COLD_EMAIL_RULE}\n"
        f"- {EMAIL3_RULE}\n"
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
        feedback = rewrite_request(hits, content.objective.never_say)
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


def rewrite_request(hits: list[dict], never_say: list[str]) -> str:
    """
    The ONE rewrite request: every field that had a hit (by email and field), then the rule that ALL
    fields of ALL emails must be free of EVERY never_say phrase (moving a phrase to another field still
    fails), then the email 3 rule again.
    """
    fields: dict[tuple[int, str], list[str]] = {}
    for hit in hits:
        fields.setdefault((hit["email"], hit["field"]), []).append(f"'{hit['phrase']}' ('{hit['snippet']}')")
    return ("\n\nYOUR PREVIOUS EMAILS USED FORBIDDEN PHRASES IN THESE FIELDS (whole words, any case):\n"
            + "\n".join(f"- email {email} {field}: {'; '.join(found)}" for (email, field), found in sorted(fields.items()))
            + "\nALL fields of ALL emails (subject, preheader AND body) must be free of EVERY never_say phrase: "
            + json.dumps(never_say, ensure_ascii=False)
            + ". Moving a phrase to another field still fails ('free' also forbids 'feel free' and 'hassle-free')."
            + f"\n{EMAIL3_RULE}")


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
    # An ordinary word the LLM banned for itself (e.g. 'free' -> then 'feel free' = a rewrite every run) is
    # dropped; longer LLM phrases ('risk-free', 'free trial') and all our fixed phrases stay.
    fixed = {phrase.lower() for phrase in NEVER_SAY_DEFAULTS}
    dropped = [p.strip() for p in objective.never_say
               if p.strip().lower() in ORDINARY_WORDS and p.strip().lower() not in fixed]
    if dropped:
        print(f"  never_say: dropped ordinary word(s) the LLM banned for itself: {dropped}")
    merged = {}
    for phrase in NEVER_SAY_DEFAULTS + objective.never_say:
        if phrase.strip() and phrase.strip().lower() not in {d.lower() for d in dropped}:
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
                        "They are saved by the QuickICP contacts task.")
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
    settings = {
        "host": os.getenv("SMTP_HOST"),
        "security_mode": os.getenv("SMTP_SECURITY"),
        "port": int(os.getenv("SMTP_PORT")),
        "username": os.getenv("SMTP_USERNAME"),
        "password": os.getenv("SMTP_PASSWORD"),
        "email_address": os.getenv("SMTP_USERNAME"),
        "sender_display_name": os.getenv("SMTP_DISPLAY_NAME"),
    }
    if os.getenv("SMTP_IMAP_HOST"):
        # Lets the tool read replies. The IMAP login defaults to the SMTP one.
        settings.update({"imap_host": os.getenv("SMTP_IMAP_HOST"), "imap_port": 993, "imap_security_mode": "IMPLICIT_TLS"})
    return settings


def with_sender(body: dict) -> dict:
    """
    The request as it is SENT. A customer whose own mailbox details were handed to this process
    (run_for.py, from the platform's checklist) gets them added here, at send time only, next to
    client_reference: the tool connects the mailbox on the first campaign and updates it after
    that. The request that is saved and resent therefore never holds the mailbox password.
    """
    if "smtp" in body or not os.getenv("SMTP_HOST"):
        return body
    return {**body, "smtp": smtp_settings()}


def without_password(text: str) -> str:
    """A tool reply can echo what it was sent: never let the mailbox password into a message or a log."""
    password = os.getenv("SMTP_PASSWORD")
    return text.replace(password, "********") if password else text


# ================= STANDARD OR HYPER-PERSONALIZED (code decides) =================

HYPER, STANDARD = "HYPER_PERSONALIZED", "STANDARD"
THIN_SHARE = 0.5          # more than this share of thin recipients -> the whole campaign goes STANDARD


def is_thin(recipient: dict) -> bool:
    """Too little to write a personal email from: no name or company, or none of title / industry / website."""
    return (not (recipient.get("first_name") and recipient.get("company"))
            or not any(recipient.get(key) for key in ("title", "company_industry", "company_website")))


def choose_campaign_type(recipients: list[dict], wanted: str | None = None) -> tuple[str, str]:
    """
    (campaign type, the reason in plain words). Code decides, never the LLM.
        wanted STANDARD (the customer's email style, EMAIL_CAMPAIGN_TYPE)  -> STANDARD
        more than THIN_SHARE of the recipients are thin                    -> STANDARD (the tool would send
                                                                              them the plain email anyway)
        otherwise                                                          -> HYPER_PERSONALIZED
    """
    wanted = (wanted or os.getenv("EMAIL_CAMPAIGN_TYPE") or HYPER).strip().upper()
    thin = sum(1 for recipient in recipients if is_thin(recipient))
    if wanted == STANDARD:
        return STANDARD, "This customer's email style is: the same email for everyone."
    if recipients and thin > len(recipients) * THIN_SHARE:
        return STANDARD, (f"{thin} of {len(recipients)} recipients have too few details for a personal email, "
                          "so everyone gets the same email.")
    if thin:
        return HYPER, f"{thin} of {len(recipients)} recipients have too few details and get the plain version."
    return HYPER, ""


def campaign_kind(body: dict) -> dict:
    """{'type', 'note'} of a built request. Read from the request itself, so it is the same on every later run."""
    kind = body.get("campaign_type") or STANDARD
    chosen, note = choose_campaign_type(body.get("recipients") or [], kind)
    return {"type": kind, "note": note if chosen == kind else ""}


def fill(text: str, recipient: dict) -> str:
    """A reference text with the recipient's own values in place of {{first_name}} and {{company}}."""
    for name in ("first_name", "last_name", "company", "title"):
        text = text.replace("{{" + name + "}}", str(recipient.get(name) or ""))
    return text


def preview(body: dict) -> list[dict]:
    """A STANDARD campaign's emails as its first recipient will read them (the tool writes no samples for it)."""
    first = (body.get("recipients") or [{}])[0]
    who = f"{first.get('first_name') or 'the first recipient'} at {first.get('company') or 'their company'}"
    return [{"email": number, "for": who, "subject": fill(email.get("subject") or "", first),
             "text": plain_text(fill(email.get("body_html") or "", first))}
            for number, email in enumerate(body.get("emails") or [], start=1)]


def build_launch_request(rows: list[dict], *, understanding: dict, work_order: dict,
                         write=None) -> tuple[dict, list]:
    """
    (launch request, person row id of each recipient), for the orchestrator's email runner.
    rows: the task's people (Supabase); understanding / work_order: the run's understanding and THIS task's
    work order. All required: nothing is read from data/.
    """
    check_settings("EMAIL_TOOL_USER_EMAIL")
    customer = understanding.get("customer_name", "Customer")
    # EMAIL_WORKSPACE_NAME (run_for.py: the platform's customer name) never changes between months;
    # the understanding's name can, and the tool refuses a different user block for the same account.
    workspace = ((os.getenv("TEST_WORKSPACE_NAME") or customer) if TEST_MODE
                 else (os.getenv("EMAIL_WORKSPACE_NAME") or customer))
    reference, run_tag = campaign_reference(customer)
    content = write() if write else write_content(understanding=understanding, work_order=work_order)
    pairs = recipients_for(rows, run_tag)

    recipients = [recipient for _, recipient in pairs]
    kind, _ = choose_campaign_type(recipients)
    body = {
        "reference": reference,
        "user": {"email": os.getenv("EMAIL_TOOL_USER_EMAIL"), "workspace_name": workspace, "role": "ADMIN"},
        "recipients": recipients,
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
    if kind == HYPER:
        # The emails are then reference emails; the tool rewrites each one for every recipient.
        body["campaign_type"] = HYPER
        body["objective"] = {**content.objective.model_dump(), "email_format": "TEXT"}
    else:
        # Everyone gets these emails as written. Stored as a DRAFT: a person approves before it starts.
        body["auto_start"] = False
    if os.getenv("CLIENT_REFERENCE"):
        body["client_reference"] = os.getenv("CLIENT_REFERENCE")    # existing account: no smtp
    else:
        body["smtp"] = smtp_settings()                              # brand-new client
    return body, [person_id for person_id, _ in pairs]


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
    payload = with_sender(body)
    for attempt in range(1, PENDING_RETRIES + 1):
        status, reply = call_tool("POST", url, payload)
        if status >= 400:
            raise ToolError(f"Launch rejected ({status}): {without_password(json.dumps(reply))[:500]}")
        if reply.get("start_status") != "pending":
            break
        print(f"  Audience not ready yet (pending), sending the same request again in {wait_seconds}s "
              f"({attempt}/{PENDING_RETRIES})")
        time.sleep(wait_seconds)
    start = reply.get("start_status")
    if start in ("blocked", "failed", "pending") or not reply.get("campaign_id"):
        raise ToolError("Launch did not complete: "
                        + without_password(f"start_status={start}, error={reply.get('start_error')}"))
    if reply.get("mailbox_status") == "failed":
        # The account's earlier mailbox is used instead; the reason is kept with the reply.
        print("  WARNING: the mailbox could not be connected or updated: "
              + without_password(str(reply.get("mailbox_error"))))
    return status, reply


# ================= STEP 2: SAMPLES =================

def plain_text(body_html: str) -> str:
    """Readable text from HTML for printing."""
    text = re.sub(r"<br\s*/?>", "\n", body_html or "")
    text = re.sub(r"</p>", "\n\n", text)
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


# ================= STEP 3: APPROVE =================

APPROVE_MESSAGES = {
    "previews_incomplete": "Samples are still being written. Run 'samples' again, then approve.",
    "previews_failed": "The sample failed and cannot be approved. Check the campaign in the app.",
    "approval_stale": "Content changed after the samples were made. Run 'samples' again.",
    "preflight_failed": "Something blocks the start; see the reason above.",
}


# ================= MANUAL TOOL: samples, the campaign from the LEDGER (--run <month> <task>) =================

def ledger_email_campaign(month: str, task_id: str) -> dict:
    """
    The email campaign of ONE task of THIS customer's run, from the ledger (never a data/ file): the task's
    finished 'launch' step (key = our reference; reply = campaign_id + workspace_id).
    Returns {"run_key", "task_id", "reference", "campaign_id", "workspace_id"}.
    """
    from orchestrator import check_month, find_run
    from state_store import current_customer, get_steps, get_tasks
    run = find_run(current_customer(), check_month(month))
    task = next((t for t in get_tasks(run["id"]) if t["task_id"] == task_id), None)
    if not task:
        raise ToolError(f"STOPPED: no task {task_id} in run {run['run_key']}.")
    if (task["agent"], task["output_type"]) != ("Email Agent", "emails"):
        raise ToolError(f"STOPPED: {task_id} is a {task['agent']} / {task['output_type']} task, not an email task.")
    launched = [s for s in get_steps(task["id"]) if s["step"] == "launch" and s["status"] == "done"
                and (s["response"] or {}).get("campaign_id")]
    if not launched:
        raise ToolError(f"STOPPED: {task_id} in run {run['run_key']} has no launched email campaign in the ledger yet.")
    row = launched[-1]
    return {"run_key": run["run_key"], "task_id": task_id, "reference": row["idempotency_key"],
            "campaign_id": row["response"]["campaign_id"], "workspace_id": row["response"]["workspace_id"]}


def run_samples(campaign: dict, sleep=time.sleep) -> dict:
    """READ-ONLY: wait up to SAMPLE_WAIT_SECONDS for the sample emails, then print them. Writes nothing."""
    print(f"Campaign {campaign['reference']} ({campaign['run_key']} {campaign['task_id']})")
    deadline = time.time() + SAMPLE_WAIT_SECONDS
    while True:
        status, reply = call_tool("GET", campaign_url(campaign, "samples"))
        if status >= 400:
            raise ToolError(f"Samples request rejected ({status}): {json.dumps(reply)[:500]}")
        samples_status = reply.get("samples_status")
        if samples_status != "generating" or time.time() > deadline:
            break
        print("  Samples still generating, checking again in 5s...")
        sleep(5)
    print(f"\nSamples status: {samples_status} | approval: {reply.get('approval_status')}\n")
    for sample in reply.get("samples", []):
        print(f"--- Email {sample.get('email_number')} | state {sample.get('state')} | "
              f"for {sample.get('recipient_first_name')} at {sample.get('recipient_company')} | "
              f"fallback: {sample.get('fallback_used')} | issues: {sample.get('failure_codes')}")
        print(f"Subject: {sample.get('subject')}\n")
        print(plain_text(sample.get("body_html")) + "\n")
    print("Approving happens only through the orchestrator: python orchestrator.py decisions, then answer "
          "the email_approve decision.")
    return reply


USAGE = ("Usage: python email_tool.py samples --run <YYYY-MM[-rN]> <task id>\n"
         "  e.g. python email_tool.py samples --run 2026-10-r5 T5\n"
         "  Campaigns are launched and approved only through the orchestrator (ledger + email_approve).")
MODES = {"samples": run_samples}


if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        args = sys.argv[1:]
        try:
            if len(args) != 4 or args[0] not in MODES or args[1] != "--run":
                raise ToolError(USAGE)
            MODES[args[0]](ledger_email_campaign(args[2], args[3]))
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli)
