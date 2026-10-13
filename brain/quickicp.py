"""
quickicp.py
===========

The QuickICP / Prospeo / OpenAI library the orchestrator's runners use: company search payloads
(keywords -> Prospeo exact names), company and people search, email and phone finders, and the ONE LLM
factory (llm / ask_llm / ask_llm_batch) for the whole brain. Every paid call goes through a runner, which
saves it in the ledger (brain_tool_calls) first. No CLI any more: the old 'python quickicp.py companies |
people | emails | phones | all' commands (paid calls outside the ledger) are in archive/legacy/quickicp_cli.py.

REQUIRED IN .env
----------------
    OPENAI_API_KEY, OPENAI_MODEL
    PROSPEO_API_KEY
    QUICKICP_WEBHOOK_URL            company search
    QUICKICP_PEOPLE_WEBHOOK_URL     people search
    QUICKICP_EMAIL_WEBHOOK_URL      email finder
    QUICKICP_PHONE_WEBHOOK_URL      phone finder

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
    - A failed enrichment batch is skipped by the runner; the remaining batches continue.

TEST MODE
---------
    TEST_MODE = True keeps runs cheap. Set it to False for real volumes.
"""

import json
import os
import re
import time
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


def max_number(text: str, default: int) -> int:
    """Largest number in a text, e.g. '150-200 accounts' -> 200."""
    numbers = [int(n) for n in re.findall(r"\d+", text or "")]
    return max(numbers) if numbers else default


# Tool tasks the orchestrator runs by a COUNT (their target needs a number, or a % of another counted task).
COUNTED_TOOL_TASKS = {("QuickICP", "accounts"), ("QuickICP", "contacts"), ("Email Agent", "emails"),
                      ("Quickhowl", "calls")}
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def count_in(text: str | None) -> int | None:
    """The largest COUNT in a target; a percent is a share and a task id is a name, neither is a count:
    '100% of 250 contacts' -> 250, '50% of T4 contacts' -> None, '150-200 accounts' -> 200."""
    number = max_number(re.sub(r"\bT\d+\b", " ", PERCENT.sub(" ", text or "")), -1)
    return None if number < 0 else number


def percent_of(text: str | None) -> float | None:
    """The share in a target, '100% of T4 contacts' -> 100.0; None without a '%'."""
    found = PERCENT.search(text or "")
    return float(found.group(1)) if found else None


def percent_base(text: str | None, depends_on: list[str], counted: set[str]) -> str | None:
    """
    The counted task a % target is a share OF (code): the counted task id named in the text that the task
    depends on ('100% of T4 contacts' -> 'T4'); else its ONLY directly-dependent counted task; else None.
    """
    named = {task_id for task_id in re.findall(r"\bT\d+\b", text or "") if task_id in counted}
    if named:
        return named.pop() if len(named) == 1 and next(iter(named)) in depends_on else None
    deps = [d for d in depends_on if d in counted]
    return deps[0] if len(deps) == 1 else None


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


def search_people(payload: PeoplePayload) -> list[dict]:
    """Send one batch to QuickICP people search."""
    result = post_json("People search", PEOPLE_WEBHOOK, payload.model_dump(), 300)
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        raise ToolError(f"People search: expected a list, got {type(result).__name__}")
    return result


# ================= EMAIL AND PHONE FINDERS =================

def enrich_people(name: str, webhook: str, people: list[dict]) -> list[dict]:
    """Send one batch of people to an enrichment webhook (email or phone)."""
    result = post_json(name, webhook, {"people": people}, 300)
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        raise ToolError(f"{name}: expected a list, got {type(result).__name__}")
    return result
