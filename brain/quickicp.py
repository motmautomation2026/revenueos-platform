"""
quickicp.py
===========

Connects the Master Agent's QuickICP work orders to the QuickICP tools (n8n webhooks),
and optionally saves results to Supabase.

CHAIN
-----
    companies  : work order (accounts) -> keywords -> Prospeo exact names -> company search
                 writes data/quickicp_payload.json, data/quickicp_raw.json
    people     : company domains + job titles -> people search
                 writes data/quickicp_people_raw.json
    emails     : people -> email finder
                 writes data/quickicp_emails_raw.json
    phones     : people with emails -> phone finder
                 writes data/quickicp_phones_raw.json
    all        : all four (unvalidated), then saves to Supabase

    For the VALIDATED flow (judges between the steps), use:  python pipeline.py

RUN
---
    python quickicp.py companies | people | emails | phones | all

REQUIRED IN .env
----------------
    OPENAI_API_KEY, OPENAI_MODEL
    PROSPEO_API_KEY
    QUICKICP_WEBHOOK_URL            company search
    QUICKICP_PEOPLE_WEBHOOK_URL     people search
    QUICKICP_EMAIL_WEBHOOK_URL      email finder
    QUICKICP_PHONE_WEBHOOK_URL      phone finder
    SUPABASE_URL, SUPABASE_KEY      only for 'all'

PAGINATION (company search)
---------------------------
    We send company_count (NEW companies wanted) and coveredpages (pages of this search already
    fetched; 0 = new search). The n8n function splits the count into pages of 25 itself.

RELIABILITY
-----------
    - Every web call goes through post_json(): timeout + 3 retries (5s, 15s, 45s).
    - Every LLM client comes from llm() (OPENAI_MODEL, temperature 0, LLM_MAX_RETRIES = 2, 120 s per attempt;
      300 s for the month plan + work orders). ask_llm / ask_llm_batch turn a failure into ONE line:
      "STOPPED: <step>: the LLM did not answer within N s (2 retries). Nothing was saved for this step; run it again."
    - Every step saves its result to a file, so a failure never loses earlier work.
    - Failed batches are saved to *_failed.json and the remaining batches continue.

TEST MODE
---------
    TEST_MODE = True keeps runs cheap. Set it to False for real volumes.
"""

import json
import math
import os
import re
import sys
import time
import traceback
from typing import Literal

import httpx
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv()

# ================= SETTINGS =================

COMPANY_WEBHOOK = os.getenv("QUICKICP_WEBHOOK_URL")
PEOPLE_WEBHOOK = os.getenv("QUICKICP_PEOPLE_WEBHOOK_URL")
EMAIL_WEBHOOK = os.getenv("QUICKICP_EMAIL_WEBHOOK_URL")
PHONE_WEBHOOK = os.getenv("QUICKICP_PHONE_WEBHOOK_URL")
PROSPEO_URL = "https://api.prospeo.io/search-suggestions"
PROSPEO_HEADERS = {"X-KEY": os.getenv("PROSPEO_API_KEY") or ""}

RETRY_WAITS = [5, 15, 45]       # seconds to wait before retry 1, 2, 3
PAGE_SIZE = 25                  # companies per page in company search (fixed by n8n/Prospeo)
DOMAIN_BATCH_SIZE = 20          # companies per people-search request
PEOPLE_BATCH_SIZE = 25          # people per email/phone request
PEOPLE_LOCATIONS = ["India"]

TEST_MODE = True
TEST_MAX_COMPANIES = 40         # test runs ask for 40; n8n splits into pages itself
TEST_START = 3                  # which company to start from in the standalone people test
TEST_MAX_PEOPLE_COMPANIES = 3
TEST_PEOPLE_PER_COMPANY = 2
TEST_MAX_EMAIL_PEOPLE = 6

CompanySize = Literal[
    "1-10", "11-20", "21-50", "51-100", "101-200", "201-500",
    "501-1000", "1001-2000", "2001-5000", "5001-10000", "10000+",
]


# ================= ERRORS AND CHECKS =================

class ToolError(Exception):
    """A known, explained failure: printed as one clear line instead of a traceback."""


def check_settings(*names: str) -> None:
    """Stop early with a clear message if any required .env setting is missing."""
    missing = [name for name in names if not os.getenv(name)]
    if missing:
        raise ToolError(f"Missing in .env: {', '.join(missing)}")


# ================= RELIABLE WEB CALL =================

def post_json(name: str, url: str, payload: dict, timeout: int, headers: dict | None = None):
    """
    POST JSON and return the parsed reply.
    Retries on network errors, server errors (5xx), empty replies and invalid JSON.
    Fails immediately on rejected requests (4xx), since retrying a wrong request never helps.
    """
    last_error = ""
    for attempt in range(1, len(RETRY_WAITS) + 2):
        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
            if response.status_code >= 500:
                last_error = f"server error {response.status_code}"
            elif response.status_code >= 400:
                raise ToolError(f"{name}: request rejected ({response.status_code}): {response.text[:300]}")
            elif not response.text.strip():
                last_error = "empty reply"
            else:
                return response.json()
        except (httpx.TimeoutException, httpx.TransportError) as error:
            last_error = f"network problem: {error}"
        except ValueError:
            last_error = "reply is not valid JSON"

        if attempt <= len(RETRY_WAITS):
            wait = RETRY_WAITS[attempt - 1]
            print(f"  {name}: attempt {attempt} failed ({last_error}), retrying in {wait}s")
            time.sleep(wait)

    raise ToolError(f"{name} failed after {len(RETRY_WAITS) + 1} attempts: {last_error}")


# ================= RELIABLE LLM CALL =================

# The ONE place LLM clients are made (master_agent, runners, judges, review, check_system).
LLM_TIMEOUT_SECONDS = 120       # per attempt
LLM_TIMEOUT_LONG = 300          # per attempt: the big outputs (month plan, work orders)
LLM_MAX_RETRIES = 2             # after the first attempt; then STOPPED


def llm(timeout: float = LLM_TIMEOUT_SECONDS, **options) -> ChatOpenAI:
    """OpenAI chat model: OPENAI_MODEL, temperature 0, a timeout per attempt and LLM_MAX_RETRIES retries."""
    return ChatOpenAI(model=os.getenv("OPENAI_MODEL"), temperature=0, max_retries=LLM_MAX_RETRIES,
                      timeout=timeout, **options)


def llm_failure(name: str, error: Exception, timeout: float) -> ToolError:
    """One clear STOPPED line for a failed LLM call (after the client's own retries)."""
    import httpx
    import openai
    if isinstance(error, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return ToolError(f"STOPPED: {name}: the LLM did not answer within {timeout:g} s ({LLM_MAX_RETRIES} retries). "
                         "Nothing was saved for this step; run it again.")
    return ToolError(f"STOPPED: {name}: LLM call failed after {LLM_MAX_RETRIES} retries: {str(error)[:300]}")


def ask_llm(schema, prompt: str, name: str, timeout: float = LLM_TIMEOUT_SECONDS):
    """Ask the LLM for a structured answer; any failure (after the retries) -> one clear STOPPED ToolError."""
    try:
        return llm(timeout).with_structured_output(schema).invoke(prompt)
    except Exception as error:
        raise llm_failure(name, error, timeout) from error


def ask_llm_batch(schema, prompts: list[str], name: str, timeout: float = LLM_TIMEOUT_SECONDS,
                  max_concurrency: int = 5) -> list:
    """Several structured answers in parallel, ALL or nothing: one failure -> STOPPED (nothing is returned)."""
    try:
        return llm(timeout).with_structured_output(schema).batch(prompts, config={"max_concurrency": max_concurrency})
    except Exception as error:
        raise llm_failure(name, error, timeout) from error


# ================= FILES =================

def load_json(path: str):
    """Read a JSON file, with a clear message if it is missing or broken."""
    if not os.path.exists(path):
        raise ToolError(f"Missing file {path}. Run the step that creates it first.")
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except json.JSONDecodeError as error:
        raise ToolError(f"File {path} is not valid JSON: {error}") from error


def save_json(path: str, data) -> None:
    """Write data as readable JSON, creating the folder if needed."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


# ================= SHARED HELPERS =================

def load_quickicp_work_order(output_type: str) -> dict:
    """Find the QuickICP work order for 'accounts' or 'contacts' in the Master Agent output."""
    for work_order in load_json("data/work_orders.json"):
        if work_order.get("agent") == "QuickICP" and work_order.get("output_type") == output_type:
            return work_order
    raise ToolError(f"No QuickICP work order with output_type '{output_type}' in data/work_orders.json.")


def max_number(text: str, default: int) -> int:
    """Largest number in a text, e.g. '150-200 accounts' -> 200."""
    numbers = [int(n) for n in re.findall(r"\d+", text or "")]
    return max(numbers) if numbers else default


def clean_domain(website: str | None) -> str:
    """'https://www.Cipla.com/about' -> 'cipla.com'"""
    if not website:
        return ""
    domain = website.strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = re.sub(r"^www\.", "", domain)
    return domain.split("/")[0]


# ================= COMPANY SEARCH =================

class Keywords(BaseModel):
    industry_keywords: list[str] = Field(description="Short industry keywords, 1-3 words each, e.g. 'manufacturing'")
    location_keywords: list[str] = Field(description="Plain city or state names, e.g. 'Delhi', 'Noida', 'Pune'")
    company_size: list[CompanySize]


class ExactNames(BaseModel):
    industries: list[str]
    locations: list[str]


class CompanyPayload(BaseModel):
    industries: list[str]
    locations: list[str]
    company_size: list[CompanySize]
    company_count: int          # NEW companies wanted in this request (n8n splits into pages)
    coveredpages: int = 0       # pages of THIS search already fetched; 0 = new search


def make_keywords(work_order: dict, understanding: dict) -> Keywords:
    """Step 1: LLM turns the work order into short search keywords."""
    prompt = (
        "Turn this work order into search keywords for Prospeo.\n"
        "- industry_keywords: short keywords for the target segment.\n"
        "- location_keywords: plain city or state names from the geography. "
        "Split regions into cities, e.g. 'Delhi NCR' -> 'Delhi', 'Gurugram', 'Noida'.\n"
        "- company_size: ranges matching the ideal company size; if none given, 201-500 and above.\n\n"
        "Work order:\n" + json.dumps(work_order, ensure_ascii=False)
        + "\n\nPriority segments:\n" + json.dumps(understanding.get("priority_segments", []), ensure_ascii=False)
    )
    return ask_llm(Keywords, prompt, "Keywords")


def prospeo_suggestions(field: str, keyword: str) -> list[str]:
    """Step 2: ask Prospeo which exact names it knows for one keyword. A failure skips only this keyword."""
    try:
        data = post_json(f"Prospeo {field} '{keyword}'", PROSPEO_URL, {field: keyword}, 30, PROSPEO_HEADERS)
    except ToolError as error:
        print(f"  Warning: skipped keyword '{keyword}': {error}")
        return []
    if field == "location_search":
        return [item["name"] for item in data.get("location_suggestions", []) if item.get("name")]
    return data.get("industry_suggestions", [])


# An industry keyword with NO Prospeo suggestions is tried ONCE more with these words (lowercase keys).
INDUSTRY_SYNONYMS = {
    "e-commerce": ["retail", "online retail", "internet marketplace"],
    "ecommerce": ["retail", "online retail", "internet marketplace"],
    "e commerce": ["retail", "online retail", "internet marketplace"],
}


def industry_options(keywords: list[str], lookup=None) -> dict[str, list[str]]:
    """
    {keyword: Prospeo industry names}. A keyword with no names is retried once with its synonyms
    (INDUSTRY_SYNONYMS); what they return is listed under the original keyword. Only names Prospeo
    really returned end up here, so the exact-name check after the LLM's pick stays the same.
    lookup: prospeo_suggestions (default); a selftest passes a fake.
    """
    lookup = lookup or prospeo_suggestions
    options = {}
    for keyword in keywords:
        names = list(lookup("industry_search", keyword))
        synonyms = INDUSTRY_SYNONYMS.get(keyword.strip().lower(), [])
        if not names and synonyms:
            for synonym in synonyms:
                names += [name for name in lookup("industry_search", synonym) if name not in names]
            print(f"  Industry '{keyword}': no Prospeo names; synonyms {synonyms} -> {names or 'still none'}")
        options[keyword] = names
    return options


def pick_exact_names(work_order: dict, industry_options: dict, location_options: dict) -> ExactNames:
    """Step 3: LLM picks the matching names, only from Prospeo's suggestions."""
    prompt = (
        "Pick the exact Prospeo names that match the work order.\n"
        "- Choose ONLY from the suggestions given. Copy names exactly.\n"
        "- Industries: every suggestion that genuinely matches the target segment.\n"
        "- Locations: the single best match for each location keyword.\n\n"
        "Work order:\n" + json.dumps(work_order, ensure_ascii=False)
        + "\n\nIndustry suggestions:\n" + json.dumps(industry_options, ensure_ascii=False)
        + "\n\nLocation suggestions:\n" + json.dumps(location_options, ensure_ascii=False)
    )
    return ask_llm(ExactNames, prompt, "Pick exact names")


def build_company_payload(work_order: dict, understanding: dict) -> CompanyPayload:
    """Steps 1-4: keywords -> suggestions -> pick -> keep only names Prospeo really returned."""
    keywords = make_keywords(work_order, understanding)
    print("\n[1] Keywords:", keywords.model_dump())

    industries_found = industry_options(keywords.industry_keywords)
    location_options = {kw: prospeo_suggestions("location_search", kw) for kw in keywords.location_keywords}
    print("\n[2] Industry suggestions:", json.dumps(industries_found, indent=2, ensure_ascii=False))
    print("[2] Location suggestions:", json.dumps(location_options, indent=2, ensure_ascii=False))

    picked = pick_exact_names(work_order, industries_found, location_options)
    valid_industries = {name for names in industries_found.values() for name in names}
    valid_locations = {name for names in location_options.values() for name in names}
    industries = [name for name in picked.industries if name in valid_industries]
    locations = [name for name in picked.locations if name in valid_locations]
    print("\n[3] Exact names kept:", {"industries": industries, "locations": locations})

    if not industries or not locations:
        raise ToolError("No valid industries or locations left after the exact-name check.")

    count = TEST_MAX_COMPANIES if TEST_MODE else max_number(work_order.get("target", ""), PAGE_SIZE)
    return CompanyPayload(industries=industries, locations=locations,
                          company_size=keywords.company_size, company_count=count, coveredpages=0)


def search_companies(payload: CompanyPayload) -> list[dict]:
    """Step 5: send the payload to QuickICP company search."""
    result = post_json("Company search", COMPANY_WEBHOOK, payload.model_dump(), 300)
    if not isinstance(result, list):
        raise ToolError(f"Company search: expected a list, got {type(result).__name__}")
    return result


def run_companies() -> None:
    check_settings("OPENAI_MODEL", "PROSPEO_API_KEY", "QUICKICP_WEBHOOK_URL")
    understanding = load_json("data/understanding.json")
    work_order = load_quickicp_work_order("accounts")
    print("Work order:", work_order.get("work_order_id"), "|", work_order.get("target"), "|",
          work_order.get("geography"))

    payload = build_company_payload(work_order, understanding)
    print("\n[4] Payload:", json.dumps(payload.model_dump(), indent=2, ensure_ascii=False))
    save_json("data/quickicp_payload.json", payload.model_dump())

    companies = search_companies(payload)
    save_json("data/quickicp_raw.json", companies)
    print(f"\n[5] Companies received: {len(companies)} -> saved to data/quickicp_raw.json")


# ================= PEOPLE SEARCH =================

class JobTitles(BaseModel):
    job_titles: list[str] = Field(
        description="10-30 short job-title keywords in lowercase, correctly spelled, e.g. 'cio', 'it head', "
                    "'procurement head', 'digital transformation'"
    )


class PeoplePayload(BaseModel):
    domains: list[str]
    job_titles: list[str]
    locations: list[str]
    count_per_company: int


def load_domains(companies_path: str = "data/quickicp_raw.json") -> list[str]:
    """Cleaned, de-duplicated website domains from a companies file."""
    domains = []
    for company in load_json(companies_path):
        domain = clean_domain(company.get("WEBSITE"))
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def make_job_titles(work_order: dict) -> list[str]:
    """LLM lists the job titles to search, from the contacts work order."""
    prompt = (
        "List the job-title keywords to search for the decision-makers this work order targets.\n"
        "- Use the roles and buying committee named in the work order.\n"
        "- Short keywords, lowercase, correctly spelled, no duplicates.\n"
        "- Include common variants, e.g. 'procurement', 'purchase head', 'sourcing'.\n\n"
        "Work order:\n" + json.dumps(work_order, ensure_ascii=False)
    )
    titles = ask_llm(JobTitles, prompt, "Job titles").job_titles
    titles = list(dict.fromkeys(title.strip().lower() for title in titles if title.strip()))
    if not titles:
        raise ToolError("Job titles: the LLM returned no job titles.")
    return titles


def build_people_payloads(work_order: dict, domains: list[str]) -> list[PeoplePayload]:
    """Split domains into batches, each with the same job titles and per-company count."""
    job_titles = make_job_titles(work_order)
    print("\n[2] Job titles:", job_titles)

    contacts_wanted = max_number(work_order.get("target", ""), len(domains))
    per_company = min(10, max(1, math.ceil(contacts_wanted / len(domains))))
    if TEST_MODE:
        per_company = TEST_PEOPLE_PER_COMPANY
    print(f"[3] Contacts wanted: {contacts_wanted} for {len(domains)} companies -> {per_company} per company")

    batches = [domains[i:i + DOMAIN_BATCH_SIZE] for i in range(0, len(domains), DOMAIN_BATCH_SIZE)]
    return [PeoplePayload(domains=batch, job_titles=job_titles, locations=PEOPLE_LOCATIONS,
                          count_per_company=per_company) for batch in batches]


def search_people(payload: PeoplePayload) -> list[dict]:
    """Send one batch to QuickICP people search."""
    result = post_json("People search", PEOPLE_WEBHOOK, payload.model_dump(), 300)
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        raise ToolError(f"People search: expected a list, got {type(result).__name__}")
    return result


def run_people(companies_path: str = "data/quickicp_raw.json", test_start: int = TEST_START) -> None:
    """
    People search on the companies in companies_path.
    The pipeline passes data/companies_passed.json, so only validated companies are searched.
    """
    check_settings("OPENAI_MODEL", "QUICKICP_PEOPLE_WEBHOOK_URL")
    work_order = load_quickicp_work_order("contacts")
    print("Work order:", work_order.get("work_order_id"), "|", work_order.get("target"))

    domains = load_domains(companies_path)
    if TEST_MODE:
        domains = domains[test_start:test_start + TEST_MAX_PEOPLE_COMPANIES]
    print(f"\n[1] Domains to search (from {companies_path}): {len(domains)}")
    if not domains:
        raise ToolError(f"No domains to search in {companies_path}. Run the company step first.")

    payloads = build_people_payloads(work_order, domains)
    print(f"[4] Sending {len(payloads)} batch(es) of up to {DOMAIN_BATCH_SIZE} domains")

    people, failed = [], []
    for number, payload in enumerate(payloads, start=1):
        try:
            batch_people = search_people(payload)
            people.extend(batch_people)
            print(f"  Batch {number}: {len(batch_people)} people")
        except ToolError as error:
            failed.append({"batch": number, "domains": payload.domains, "error": str(error)})
            print(f"  Batch {number}: FAILED -> {error}")
        save_json("data/quickicp_people_raw.json", people)

    save_json("data/quickicp_people_failed.json", failed)
    print(f"\n[5] People received: {len(people)} | failed batches: {len(failed)}")
    print("Saved to data/quickicp_people_raw.json")
    if people:
        print("First record:", json.dumps(people[0], indent=2, ensure_ascii=False))


# ================= EMAIL AND PHONE FINDERS =================

def enrich_people(name: str, webhook: str, people: list[dict]) -> list[dict]:
    """Send one batch of people to an enrichment webhook (email or phone)."""
    result = post_json(name, webhook, {"people": people}, 300)
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        raise ToolError(f"{name}: expected a list, got {type(result).__name__}")
    return result


def run_enrichment(name: str, webhook: str, input_path: str, output_path: str, failed_path: str) -> None:
    """
    Read people, send them in batches, save results after every batch, record failed batches.
    Our own 'validation' block is removed before sending (the tool expects its own format)
    and attached again to each returned record, matched by LinkedIn profile URL.
    """
    people = load_json(input_path)
    if not people:
        raise ToolError(f"No people found in {input_path}. Run the previous step first.")
    if TEST_MODE:
        people = people[:TEST_MAX_EMAIL_PEOPLE]
    print(f"[1] {name}: {len(people)} people from {input_path}")

    validations = {p.get("USER SOCIAL"): p["validation"] for p in people if "validation" in p}
    to_send = [{k: v for k, v in p.items() if k != "validation"} for p in people]

    batches = [to_send[i:i + PEOPLE_BATCH_SIZE] for i in range(0, len(to_send), PEOPLE_BATCH_SIZE)]
    print(f"[2] Sending {len(batches)} batch(es) of up to {PEOPLE_BATCH_SIZE} people")

    results, failed = [], []
    for number, batch in enumerate(batches, start=1):
        try:
            batch_results = enrich_people(name, webhook, batch)
            for record in batch_results:
                if record.get("USER SOCIAL") in validations:
                    record["validation"] = validations[record["USER SOCIAL"]]
            results.extend(batch_results)
            print(f"  Batch {number}: {len(batch_results)} records")
        except ToolError as error:
            failed.append({"batch": number, "people": [p.get("FULL NAME") for p in batch], "error": str(error)})
            print(f"  Batch {number}: FAILED -> {error}")
        save_json(output_path, results)

    save_json(failed_path, failed)
    print(f"\n[3] Records received: {len(results)} | failed batches: {len(failed)}")
    print("Saved to", output_path)
    if results:
        print("First record:", json.dumps(results[0], indent=2, ensure_ascii=False))


def run_emails(input_path: str = "data/quickicp_people_raw.json") -> None:
    """Email finder. The pipeline passes data/people_passed.json, so only validated people are enriched."""
    check_settings("QUICKICP_EMAIL_WEBHOOK_URL")
    run_enrichment("Email finder", EMAIL_WEBHOOK, input_path,
                   "data/quickicp_emails_raw.json", "data/quickicp_emails_failed.json")


def run_phones() -> None:
    """Phone finder on the email results, so each record ends with person + email + phone."""
    check_settings("QUICKICP_PHONE_WEBHOOK_URL")
    run_enrichment("Phone finder", PHONE_WEBHOOK,
                   "data/quickicp_emails_raw.json", "data/quickicp_phones_raw.json",
                   "data/quickicp_phones_failed.json")


# ================= FULL CHAIN (UNVALIDATED) =================

def run_all() -> None:
    """Run companies -> people -> emails -> phones without judges, then save to Supabase.
    For the validated flow, use: python pipeline.py"""
    print("\n===== 1/5 COMPANIES =====")
    run_companies()
    print("\n===== 2/5 PEOPLE =====")
    run_people()
    print("\n===== 3/5 EMAILS =====")
    run_emails()
    print("\n===== 4/5 PHONES =====")
    run_phones()

    print("\n===== 5/5 SAVE TO SUPABASE =====")
    check_settings("SUPABASE_URL", "SUPABASE_KEY")
    try:
        # Imported here, not at the top: supabase_store imports from this file (avoids a circular import).
        from supabase_store import current_customer, save_companies, save_people, COMPANIES_FILE, PEOPLE_FILE
        customer = current_customer()   # fixed CUSTOMER_KEY from .env
        lookups = save_companies(load_json(COMPANIES_FILE), customer)
        save_people(load_json(PEOPLE_FILE), lookups, customer)
    except ToolError:
        raise
    except Exception as error:
        print(f"Supabase save failed: {error}")
        print("All results are still safe in the data/ JSON files. Fix the issue and run: python supabase_store.py")


# ================= RUN =================

MODES = {
    "companies": run_companies,
    "people": run_people,
    "emails": run_emails,
    "phones": run_phones,
    "all": run_all,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "companies"
    if mode not in MODES:
        print("Usage: python quickicp.py " + " | ".join(MODES))
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