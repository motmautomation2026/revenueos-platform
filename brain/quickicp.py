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
    - Every LLM call logs one usage line (call, model, input / cached input / output tokens, seconds):
      printed and appended to data/llm_usage.jsonl (git-ignored). No prompt text, no secrets. Missing
      usage -> 'n/a'; a logging problem never stops a run. usage_summary() = the total of this process.
    - A failed enrichment batch is skipped by the runner; the remaining batches continue.

TEST MODE
---------
    TEST_MODE = True keeps runs cheap. Set it to False for real volumes.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Literal

import httpx
from dotenv import load_dotenv
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv()

# ================= SETTINGS =================

COMPANY_WEBHOOK = os.getenv("QUICKICP_WEBHOOK_URL")
PEOPLE_WEBHOOK = os.getenv("QUICKICP_PEOPLE_WEBHOOK_URL")
EMAIL_WEBHOOK = os.getenv("QUICKICP_EMAIL_WEBHOOK_URL")
PHONE_WEBHOOK = os.getenv("QUICKICP_PHONE_WEBHOOK_URL")
PROSPEO_URL = os.getenv("PROSPEO_URL") or "https://api.prospeo.io/search-suggestions"   # env: selftests only
PROSPEO_HEADERS = {"X-KEY": os.getenv("PROSPEO_API_KEY") or ""}

RETRY_WAITS = [5, 15, 45]       # seconds to wait before retry 1, 2, 3
PAGE_SIZE = 25                  # companies per page in company search (fixed by n8n/Prospeo)
DOMAIN_BATCH_SIZE = 20          # companies per people-search request
PEOPLE_BATCH_SIZE = 25          # people per email/phone request
PEOPLE_LOCATIONS = ["India"]

def staging_flag(default: bool) -> bool:
    """
    Test mode for THIS process. run_for.py sets BRAIN_TEST_MODE=true|false per customer, from the
    platform's Staging / Production switch (and, once a run exists, from that run's own mode), so
    one customer can be tried in staging while another runs for real. Unset = the default below.
    """
    value = (os.getenv("BRAIN_TEST_MODE") or "").strip().lower()
    return value == "true" if value in ("true", "false") else default


def staging_number(name: str, default: int) -> int:
    """A staging limit the person chose in the platform (STAGING_COMPANIES, STAGING_PEOPLE), or the default."""
    value = (os.getenv(name) or "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else default


TEST_MODE_DEFAULT = True        # used when BRAIN_TEST_MODE is not set (a script run by hand): test caps.
TEST_MODE = staging_flag(TEST_MODE_DEFAULT)
TEST_MAX_COMPANIES = staging_number("STAGING_COMPANIES", 40)   # test runs ask for 40; n8n splits into pages itself
TEST_MAX_PEOPLE_COMPANIES = 3
TEST_PEOPLE_PER_COMPANY = 2
TEST_MAX_EMAIL_PEOPLE = staging_number("STAGING_PEOPLE", 6)    # people sent to the email / phone finders in test mode

CompanySize = Literal[
    "1-10", "11-20", "21-50", "51-100", "101-200", "201-500",
    "501-1000", "1001-2000", "2001-5000", "5001-10000", "10000+",
]


# ================= ERRORS AND CHECKS =================

class ToolError(Exception):
    """A known, explained failure: printed as one clear line instead of a traceback."""


# ================= NETWORK DROP: one clear STOPPED line at the entry points (run_cli) =================

NETWORK_WINERRORS = {10054, 10060, 10061, 11001, 11004}   # reset, timed out, refused, host not found, no data


def network_error(error: BaseException) -> BaseException | None:
    """The network error in error's cause chain (DNS, connect, timeout, reset, proxy), or None."""
    import socket
    kinds = [socket.gaierror, ConnectionError, TimeoutError, httpx.TransportError]
    for module, names in (("httpcore", ("NetworkError", "TimeoutException", "ProxyError")),
                          ("requests.exceptions", ("ConnectionError", "Timeout")),
                          ("openai", ("APIConnectionError",))):
        try:
            imported = __import__(module, fromlist=list(names))
            kinds += [getattr(imported, name) for name in names if hasattr(imported, name)]
        except ImportError:
            pass
    seen, current = 0, error
    while current is not None and seen < 12:
        if isinstance(current, tuple(kinds)) or getattr(current, "winerror", None) in NETWORK_WINERRORS:
            return current
        current, seen = current.__cause__ or current.__context__, seen + 1
    return None


def network_service(error: BaseException) -> str:
    """Which service could not be reached, from the failing request's host (env URLs first)."""
    from urllib.parse import urlparse
    host = None
    for item in (error, error.__cause__, error.__context__):
        try:
            request = getattr(item, "request", None)
            url = getattr(request, "url", None)
            host = getattr(url, "host", None) or (urlparse(str(url)).hostname if url else None)
        except RuntimeError:                       # httpx: '.request' not set on this error
            host = None
        if host:
            break
    names = {"SUPABASE_URL": "Supabase", "QUICKHOWL_URL": "Quickhowl", "EMAIL_TOOL_URL": "email tool",
             "QUICKICP_WEBHOOK_URL": "QuickICP (n8n)", "QUICKICP_PEOPLE_WEBHOOK_URL": "QuickICP (n8n)",
             "QUICKICP_EMAIL_WEBHOOK_URL": "QuickICP (n8n)", "QUICKICP_PHONE_WEBHOOK_URL": "QuickICP (n8n)",
             "PROSPEO_URL": "Prospeo", "OPENAI_BASE_URL": "OpenAI"}
    for key, name in names.items():
        if host and host == urlparse(os.getenv(key) or "").hostname:
            return name
    if host and "openai" in host:
        return "OpenAI"
    if host and "prospeo" in host:
        return "Prospeo"
    return host or "a service"


def network_stop_line(error: BaseException, rerun: str) -> str:
    """The one STOPPED line (+ what was saved, a paid call in flight, a lock left held), from what is known."""
    short = str(error).strip().splitlines()[0][:120] if str(error).strip() else type(error).__name__
    lines = [f"STOPPED: no network ({network_service(error)}, {short}). Nothing lost: check the internet, then {rerun}."]
    store = sys.modules.get("state_store")
    if store is not None:
        writes = store.STATE_WRITES
        if writes["pending"]:
            lines.append(f"  The last change ({writes['pending']}) may or may not have been saved; the rerun "
                         "checks what is there.")
        if writes["done"]:
            lines.append(f"  Saved before the drop: {writes['done']} state change(s) (last: {writes['last']}).")
        elif not writes["pending"]:
            lines.append("  Nothing was saved by this command.")
        if store.HELD_LOCKS:
            lines.append("  The run lock may still be held by this stopped command: it expires within 30 min, "
                         "or free it with: python orchestrator.py unlock <YYYY-MM>.")
    kit = sys.modules.get("runner_kit")
    if kit is not None and kit.PAID_IN_FLIGHT:
        lines.append(f"  A paid call was in progress ({kit.PAID_IN_FLIGHT[-1]}): the next 'run' marks it 'unknown' "
                     "and asks you (paid_call_unknown). It is never repeated automatically.")
    return "\n".join(lines)


def run_cli(main, rerun: str = "rerun the same command") -> None:
    """
    Run an entry point's command. A network drop (DNS, connect, timeout, reset) anywhere in it -> one STOPPED
    line (network_stop_line) and exit code 3, no traceback. '--debug' (removed from sys.argv first) shows the
    full traceback instead. Every other error and exit behaves exactly as before.
    """
    from orch import brain_log            # file only; never prints, never raises
    debug = "--debug" in sys.argv
    sys.argv[:] = [arg for arg in sys.argv if arg != "--debug"]
    command = " ".join([os.path.basename(sys.argv[0])] + sys.argv[1:])
    try:
        main()
    except SystemExit as stop:
        brain_log.write("INFO" if stop.code in (0, None) else "WARNING", f"{command}: exit {stop.code}")
        raise
    except Exception as error:
        found = network_error(error)
        if found is None or debug:
            brain_log.write("ERROR", f"{command}: crashed", error=error)
            raise
        brain_log.write("ERROR", f"{command}: network drop -> STOPPED, exit 3", error=error)
        print(network_stop_line(found, rerun))
        sys.exit(3)
    except BaseException as error:        # Ctrl+C, a simulated crash: logged, then exactly as before
        brain_log.write("ERROR", f"{command}: interrupted", error=error)
        raise
    brain_log.write("INFO", f"{command}: exit 0")


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


USAGE_FILE = "data/llm_usage.jsonl"   # one JSON line per LLM call (git-ignored with data/); never prompt text
USAGE_TOTAL = {"calls": 0, "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0, "seconds": 0.0}


class UsageLog(BaseCallbackHandler):
    """Logs every LLM call made with this model: call name, model, tokens (input / cached / output), seconds."""

    def __init__(self, name: str):
        self.name, self.started = name, {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.started[run_id] = time.monotonic()

    def on_llm_end(self, response, *, run_id, **kwargs):
        log_usage(self.name, response, time.monotonic() - self.started.pop(run_id, time.monotonic()))


def log_usage(name: str, response, seconds: float) -> None:
    """Print + append one usage line. Missing usage -> 'n/a'. Never raises: logging must never stop a run."""
    try:
        message = response.generations[0][0].message
        usage = getattr(message, "usage_metadata", None) or {}
        model = (response.llm_output or {}).get("model_name") or message.response_metadata.get("model_name") or "n/a"
        line = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "call": name, "model": model,
                "input_tokens": usage.get("input_tokens", "n/a"),
                "cached_input_tokens": (usage.get("input_token_details") or {}).get("cache_read", "n/a"),
                "output_tokens": usage.get("output_tokens", "n/a"), "seconds": round(seconds, 1)}
        USAGE_TOTAL["calls"] += 1
        USAGE_TOTAL["seconds"] += seconds
        for key in ("input_tokens", "cached_input_tokens", "output_tokens"):
            if isinstance(line[key], int):
                USAGE_TOTAL[key] += line[key]
        print(f"  [LLM] {name} | {model} | in {line['input_tokens']} (cached {line['cached_input_tokens']}) | "
              f"out {line['output_tokens']} | {line['seconds']} s")
        os.makedirs(os.path.dirname(USAGE_FILE) or ".", exist_ok=True)
        with open(USAGE_FILE, "a", encoding="utf-8") as file:
            file.write(json.dumps(line) + "\n")
    except Exception as error:                    # never stop a run because of the usage log
        print(f"  [LLM] {name}: usage not logged ({type(error).__name__})")


def usage_summary() -> str:
    """The total of every LLM call logged in this process, one line."""
    t = USAGE_TOTAL
    return (f"LLM usage total: {t['calls']} call(s) | in {t['input_tokens']} (cached {t['cached_input_tokens']}) | "
            f"out {t['output_tokens']} | {t['seconds']:.0f} s")


def llm(timeout: float = LLM_TIMEOUT_SECONDS, name: str = "LLM", **options) -> ChatOpenAI:
    """OpenAI chat model: OPENAI_MODEL, temperature 0, a timeout per attempt and LLM_MAX_RETRIES retries.
    Every call is usage-logged under 'name' (UsageLog)."""
    return ChatOpenAI(model=os.getenv("OPENAI_MODEL"), temperature=0, max_retries=LLM_MAX_RETRIES,
                      timeout=timeout, callbacks=[UsageLog(name)], **options)


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
        return llm(timeout, name=name).with_structured_output(schema).invoke(prompt)
    except Exception as error:
        raise llm_failure(name, error, timeout) from error


def ask_llm_batch(schema, prompts: list[str], name: str, timeout: float = LLM_TIMEOUT_SECONDS,
                  max_concurrency: int = 5) -> list:
    """Several structured answers in parallel, ALL or nothing: one failure -> STOPPED (nothing is returned)."""
    try:
        return llm(timeout, name=name).with_structured_output(schema).batch(prompts, config={"max_concurrency": max_concurrency})
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
        "- industry_keywords: short keywords for the target segment. Prospeo matches single industry words, "
        "so prefer ONE word each (e.g. 'water', 'chemical', 'construction', 'utilities'), not phrases.\n"
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
    (INDUSTRY_SYNONYMS), then, if it is a phrase, once with each of its words; what they return is
    listed under the original keyword. Only names Prospeo
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
        words = [word for word in re.split(r"[\s/&,]+", keyword.strip()) if len(word) >= 4]
        if not names and len(words) > 1:
            # Prospeo knows industries by single words: 'water projects' finds nothing, 'water' finds
            # 'Water Supply and Irrigation Systems'. Try each word of the phrase once; the LLM's pick
            # and the exact-name check still decide which of these really match.
            for word in words:
                names += [name for name in lookup("industry_search", word) if name not in names]
            print(f"  Industry '{keyword}': no Prospeo names; its words {words} -> {names or 'still none'}")
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
