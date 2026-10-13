"""
validator.py
============

LLM-as-judge validation layer for QuickICP results, now with SAVED MAPS so the
same industry or the same job title always gets the same verdict.

WHY MAPS
    Before: every company and person was judged fresh, so the same title could
    PASS in one run and FAIL in the next (e.g. "Operations Manager").
    Now: each unique INDUSTRY and each unique JOB TITLE is judged by the LLM
    only ONCE, saved in a map file, and reused in every later run.
    Same industry -> same score. Same title -> same score. Fewer LLM calls.

MAP FILES (one pair per customer, inside data/maps/)
    data/maps/<customer>_industry_map.json
    data/maps/<customer>_title_map.json
    <customer> comes from the FIXED CUSTOMER_KEY in .env, never from a name
    (e.g. CUSTOMER_KEY=anvixa-ai-product -> anvixa_ai_product, the same file names as before).

    Map file shape:
        {
          "customer": "Anvixa AI Product",
          "kind": "industry",                      or "title"
          "items": {
            "chemical manufacturing": {            <- cleaned key (do not change)
              "label": "Chemical Manufacturing",   <- name as first seen
              "score": 85,                         <- industry map: LLM score
              "reason": "...",
              "source": "llm",                     <- "llm" or "cam" (hand-edited)
              "judged_at": "2026-10-08T06:30:00+00:00"
            }
          },
        Title map entries store the FUNCTION only (the LLM's part):
            "global sourcing manager": {
              "label": "Global Sourcing Manager",
              "function": "procurement",
              "function_relevant": true,
              "reason": "...", "source": "llm", "judged_at": "..."
            }
        Seniority, buying_role and score are computed by CODE from the title
        words every run (see title_seniority), so rule changes apply at once.
          "company_overrides": {                   <- industry map only, optional
            "genpact.com": {"score": 0, "reason": "IT/BPO, not a manufacturer", "source": "cam"}
          }
        }

    HOW THE CAM CORRECTS A VERDICT (no code change needed)
        - Industry: open the map file, change "score", set "source": "cam".
        - Title: set "source": "cam" and add "buying_role" and "score"
          (e.g. "influencer", 70). A "cam" title entry is used exactly as written.
          Or just flip "function_relevant" to true/false and keep "source": "llm".
          Every later run uses the new value.
        - A company in a good industry that is clearly wrong (e.g. a BPO filed
          under manufacturing): add its website domain under "company_overrides".
        - To rebuild a map from scratch: python validator.py reset-maps

STEP 1: company judge (judge_companies, called by the accounts runner)
    Gets:   the companies of one search round, that round's payload (cities, sizes; from the task's
            ledger) and company_context(the run's understanding, the task's work order). No data/ file.
    Uses:   data/maps/<customer>_industry_map.json   (created if missing; new industries added)

    HOW A COMPANY IS JUDGED
        1. Code: location must contain a target city; headcount >= smallest size searched.
           Failing either -> "fail" (checked_by "code").
        2. Code: a company_overrides entry for its domain wins, if present.
        3. Industry map: the company gets its INDUSTRY's score.
           Industries not yet in the map are judged by the LLM once (industry name only),
           then saved.
        4. Code decides: score >= PASS_SCORE -> "pass", otherwise "fail".

STEP 2: people judge (judge_people, called by the contacts runner)
    Gets:   the people of one search round, the run's passed company domains / LinkedIn pages and
            people_context(the run's understanding, the task's work order). No data/ file.
    Uses:   data/maps/<customer>_title_map.json      (created if missing; new titles added)

    HOW A PERSON IS JUDGED
        1. Code: the person's company must have passed step 1 -> otherwise "fail".
           No job title -> "review".
        2. Title map: the title is cleaned ("Manager (Supply Chain Procurement)"
           -> "manager supply chain procurement") and looked up. New titles are judged by the LLM
           once for FUNCTION ONLY (is this a relevant function? yes/no), then saved.
        3. Code decides SENIORITY from the title words:
             Head / VP / Director / GM / CXO / President  -> decision_maker (90)
             Manager / Lead / AGM / DGM / Chief Manager    -> influencer (70)
             Assistant/Associate/Deputy Manager, Executive,
             Specialist, Engineer, Coordinator, Analyst    -> user (50)
             function not relevant                         -> not_relevant (20)
             no level word found                           -> "review"
        4. Code decides: score >= PEOPLE_PASS_SCORE and buying_role != "not_relevant" -> "pass".
        CODE RULES that win over the map (a 'cam' entry still wins over them):
             "EA / PA / Secretary / Assistant TO <anyone>", "Executive Assistant"   -> user (fails)
             safety, EHS, HSE, medical, health, training, L&D, vigilance, fire,
             physical / plant security, security guard                           -> not_relevant
             IT / information / cyber / data / network security, CISO           -> relevant function,
                                                                                    level from the title

RELIABILITY
    - Nothing is ever silently passed: failed LLM batches and missing verdicts -> "review".
      Those industries/titles are NOT saved, so the next run tries them again.
    - One failed LLM batch does not stop the others.
    - A broken hand-edit in a map file stops with a clear message (file name + fix).
    - A map entry with an invalid score or buying_role -> "review", never "pass".

RUN
    python validator.py maps          print both maps for this customer
    python validator.py reset-maps    delete both maps for this customer (asks 'yes')
    python validator.py selftest      pure-code checks of the title rules (no LLM, no files)
    The judges run only inside the orchestrator's runners. The old file-based commands
    companies / people / all are in archive/legacy/validator_cli.py.

TUNING
    PASS_SCORE, PEOPLE_PASS_SCORE   higher = stricter
    CITY_ALIASES                    add spellings if a valid city is being rejected
    BATCH_SIZE                      items per LLM call
    SENIORITY_SCORES, DECISION_WORDS, JUNIOR_MODIFIERS, USER_WORDS
                                    the code rules for seniority (edit words here)

DEBUGGING
    - "STOPPED: ... is not valid JSON": a hand-edit broke the map file; fix the
      comma/quote it names, or run reset-maps.
    - A verdict looks wrong every run: that is the map. Edit the entry (see above).
    - Many "review" results: a judge batch failed (network / OpenAI). Run again;
      only the missing industries/titles are re-sent.
    - Customer renamed (the run's understanding or the platform): the maps do NOT change; they follow
      CUSTOMER_KEY. A NEW CUSTOMER_KEY starts new, empty maps (the old files stay in data/maps/).
"""

import json
import os
import re
import sys
from datetime import datetime, timezone
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from quickicp import ToolError, clean_domain

load_dotenv()

PASS_SCORE = 65
PEOPLE_PASS_SCORE = 65
BATCH_SIZE = 20
MAP_DIR = "data/maps"

# Other spellings of target cities that appear in addresses.
CITY_ALIASES = {
    "gurugram": ["gurgaon"],
    "delhi": ["new delhi"],
    "mumbai": ["navi mumbai", "thane"],
    "noida": ["greater noida"],
    "pune": ["pimpri", "chinchwad", "hinjawadi"],
}

BUYING_ROLES = ("decision_maker", "influencer", "user", "not_relevant")
BuyingRole = Literal["decision_maker", "influencer", "user", "not_relevant"]

# ---- Seniority rules: decided by CODE, never by the LLM ----
SENIORITY_SCORES = {"decision_maker": 90, "influencer": 70, "user": 50}
NOT_RELEVANT_SCORE = 20
# Any of these words -> decision_maker.
DECISION_WORDS = {"ceo", "cxo", "cio", "cto", "cfo", "coo", "cpo", "cdo", "chro", "md",
                  "president", "vp", "director", "head", "founder", "owner"}
# Top executives approve every kind of purchase: always decision_maker, any function.
TOP_EXEC_WORDS = {"ceo", "md", "coo", "cxo", "president", "founder", "owner"}
TOP_EXEC_PHRASES = ("managing director", "chief executive", "chief operating officer")
# Assistant / deputy general managers -> influencer (checked before "general manager").
AGM_DGM_PHRASES = ("assistant general manager", "asst general manager", "deputy general manager")
# "Manager" with one of these in front -> user ("Assistant IT Manager", "Deputy Manager").
JUNIOR_MODIFIERS = {"assistant", "asst", "associate", "deputy", "junior", "jr"}
# No manager/head word, but one of these -> user.
USER_WORDS = {"executive", "specialist", "engineer", "coordinator", "analyst", "officer",
              "associate", "assistant", "trainee", "intern"}

# ---- Code rules that win over the title map (known issue 2) ----
# Support staff OF an executive ("EA to CEO", "Secretary to CFO", "Assistant to MD") -> user, never
# a decision maker, even though the title names an executive.
SUPPORT_TO_EXEC = re.compile(r"\b(ea|pa|executive assistant|personal assistant|executive secretary|"
                             r"secretary|assistant)\s+to\b")
SUPPORT_STARTS = ("executive assistant", "personal assistant", "ea ", "pa ")
# Functions that never buy or run the product, whatever the LLM said. 'trainee' is a seniority word,
# not the training function, so it is not listed.
EXCLUDED_FUNCTION_WORDS = {"safety", "ehs", "hse", "medical", "doctor", "physician", "nurse", "paramedic",
                           "health", "training", "trainer", "vigilance", "fire", "guard"}
EXCLUDED_FUNCTION_PHRASES = ("learning and development",)
# 'security' alone is physical/plant security (not relevant); with one of these it is IT security (relevant).
IT_SECURITY_WORDS = {"it", "information", "cyber", "data", "network", "infosec", "application", "cloud"}


def is_support_to_exec(key: str) -> bool:
    """'ea to ceo', 'secretary to cfo', 'assistant to managing director', 'executive assistant' -> True."""
    return bool(SUPPORT_TO_EXEC.search(key)) or key.startswith(SUPPORT_STARTS) or key in ("ea", "pa")


def is_excluded_function(key: str) -> bool:
    """Safety / EHS / medical / training / physical security titles -> True. IT security -> False."""
    words = set(key.split())
    if words & EXCLUDED_FUNCTION_WORDS or any(phrase in key for phrase in EXCLUDED_FUNCTION_PHRASES):
        return True
    return "security" in words and not words & IT_SECURITY_WORDS


def is_it_security(key: str) -> bool:
    """'head it security', 'information security manager', 'ciso' -> True (a relevant function)."""
    words = set(key.split())
    return ("security" in words and bool(words & IT_SECURITY_WORDS)) or bool(words & {"cybersecurity", "ciso", "infosec"})


def code_rule_reason(key: str) -> str | None:
    """The code rule that decides this title, if any (shown as the verdict reason)."""
    if is_support_to_exec(key):
        return "assistant / secretary to an executive: user level (code rule)"
    if is_excluded_function(key) and not is_top_executive(key):
        return "safety / EHS / medical / training / physical security: not a target function (code rule)"
    if is_it_security(key):
        return "IT / information / cyber security: relevant function (code rule), level from the title"
    return None


def title_seniority(key: str) -> str | None:
    """
    Seniority from a CLEANED title (see title_key). Same title -> same answer, always.
    Returns decision_maker / influencer / user, or None if no level word is found.
      'vice president procurement'          -> decision_maker
      'unit head dgm'                       -> decision_maker  (head wins)
      'asst general manager procurement'    -> influencer
      'chief manager procurement'           -> influencer
      'assistant it manager'                -> user
      'procurement'                         -> None  (review)
    """
    words = set(key.split())
    if words & DECISION_WORDS or "vice president" in key:
        return "decision_maker"
    if any(phrase in key for phrase in AGM_DGM_PHRASES) or words & {"agm", "dgm"}:
        return "influencer"
    if "general manager" in key or "gm" in words:
        return "decision_maker"
    if "manager" in words or "lead" in words:
        return "user" if words & JUNIOR_MODIFIERS else "influencer"
    if words & USER_WORDS:
        return "user"
    return None


def is_top_executive(key: str) -> bool:
    """CEO / MD / COO / President / Founder... ('vice president' does NOT count)."""
    words = set(key.split())
    if "vice president" in key:
        words.discard("president")
    return bool(words & TOP_EXEC_WORDS) or any(phrase in key for phrase in TOP_EXEC_PHRASES)


def title_role_and_score(key: str, entry: dict) -> tuple[str, int] | None:
    """
    buying_role + score for a title. A 'cam' entry is used exactly as written.
    Otherwise: function not relevant -> not_relevant; else code seniority.
    None means: cannot decide (bad hand-edit, or no level word) -> review.
    """
    if entry.get("source") == "cam":
        score, role = entry_score(entry), entry.get("buying_role")
        return (role, score) if score is not None and role in BUYING_ROLES else None
    # Code rules first (known issue 2): they win over the LLM's function verdict and the exec words.
    if is_support_to_exec(key):
        return "user", SENIORITY_SCORES["user"]
    if is_excluded_function(key) and not is_top_executive(key):
        return "not_relevant", NOT_RELEVANT_SCORE
    if is_it_security(key) and not is_top_executive(key):
        level = title_seniority(key) or ("decision_maker" if "ciso" in key.split() else None)
        return (level, SENIORITY_SCORES[level]) if level else None
    if is_top_executive(key):
        return "decision_maker", SENIORITY_SCORES["decision_maker"]
    if entry.get("function_relevant") is not True:
        return ("not_relevant", NOT_RELEVANT_SCORE) if entry.get("function_relevant") is False else None
    level = title_seniority(key)
    return (level, SENIORITY_SCORES[level]) if level else None


# ================= SHARED: RELIABLE BATCH JUDGING =================

def judge_in_batches(schema, prompts: list[str]) -> list:
    """
    Send all prompts in parallel. Returns one item per prompt:
    the parsed answer, or the Exception if that batch failed (so other batches still count).
    """
    from quickicp import llm      # the one LLM factory: OPENAI_MODEL, temperature 0, timeout + 2 retries
    return llm(name=f"Judge ({schema.__name__})").with_structured_output(schema).batch(
        prompts, config={"max_concurrency": 5}, return_exceptions=True
    )


def review(reason: str, checked_by: str, **extra) -> dict:
    """A 'review' verdict: never passed automatically, flagged for a human."""
    return {"status": "review", "score": None, "reason": reason, "checked_by": checked_by, **extra}


def print_counts(judged: list[dict]) -> None:
    counts = {s: sum(1 for item in judged if item["validation"]["status"] == s) for s in ("pass", "fail", "review")}
    print(f"\nPassed: {counts['pass']} | Failed: {counts['fail']} | Review: {counts['review']}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def entry_score(entry: dict) -> int | None:
    """Score from a map entry (LLM or hand-edited). None if missing or not a number."""
    score = entry.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return None
    return max(0, min(100, int(score)))


# ================= MAPS: LOAD / SAVE / KEYS =================


def customer_slug() -> str:
    """
    CUSTOMER_KEY 'anvixa-ai-product' -> 'anvixa_ai_product' (used in the map file names).
    From the FIXED customer key, never from a name: a renamed customer (e.g. 'Anvika Ai' in the
    platform) keeps the same maps. Gives exactly the old file names for 'anvixa-ai-product'.
    """
    from state_store import current_customer
    return current_customer().replace("-", "_")


def map_path(kind: str) -> str:
    return os.path.join(MAP_DIR, f"{customer_slug()}_{kind}_map.json")


def load_map(kind: str) -> dict:
    """Load this customer's map, or start an empty one. Stops clearly on a broken hand-edit."""
    path = map_path(kind)
    if not os.path.exists(path):
        return {"customer": None, "kind": kind, "items": {}, "company_overrides": {}}   # name set by fill_map
    try:
        with open(path, encoding="utf-8") as file:
            data = json.load(file)
    except json.JSONDecodeError as error:
        raise ToolError(f"{path} is not valid JSON ({error}). Fix the edit, or run "
                        f"'python validator.py reset-maps' to rebuild it.")
    if not isinstance(data, dict) or not isinstance(data.get("items", {}), dict):
        raise ToolError(f"{path} has the wrong shape (needs an 'items' object). "
                        f"Fix it, or run 'python validator.py reset-maps'.")
    data.setdefault("items", {})
    data.setdefault("company_overrides", {})
    return data


def save_map(kind: str, data: dict) -> None:
    os.makedirs(MAP_DIR, exist_ok=True)
    with open(map_path(kind), "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def industry_key(industry: str | None) -> str:
    """'  Chemical  Manufacturing ' -> 'chemical manufacturing'"""
    return re.sub(r"\s+", " ", (industry or "").lower()).strip()


def title_key(title: str | None) -> str:
    """
    Clean a job title so small differences (case, punctuation, spaces) match the
    same map entry. Bracket text is KEPT, because it often holds the function:
    'Manager (Supply Chain Procurement)'      -> 'manager supply chain procurement'
    'Deputy Manager- Sourcing & Development'  -> 'deputy manager sourcing and development'
    """
    text = (title or "").lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)    # punctuation -> spaces
    return re.sub(r"\s+", " ", text).strip()


# ================= MAPS: JUDGE NEW ITEMS ONCE =================

class ItemVerdict(BaseModel):
    index: int = Field(description="The item's index number exactly as given in the list")
    score: int = Field(description="0-100 score, using the scale in the instructions")
    reason: str = Field(description="One short sentence explaining the score")


class ItemVerdicts(BaseModel):
    verdicts: list[ItemVerdict]


class TitleVerdict(BaseModel):
    index: int = Field(description="The title's index number exactly as given in the list")
    function: str = Field(description="The title's business function in 1-3 words, e.g. 'procurement', 'finance', 'HR'")
    function_relevant: bool = Field(description="True if this function buys, runs or is a target role for the product")
    reason: str = Field(description="One short sentence about the function only, not seniority")


class TitleVerdicts(BaseModel):
    verdicts: list[TitleVerdict]


def industry_prompt(context: dict, items: list[tuple[int, str]]) -> str:
    """Judge INDUSTRIES (not companies): how likely companies in each are to buy."""
    industries = [{"index": i, "industry": label} for i, label in items]
    return (
        "You are a strict B2B lead judge for MOTM Revenue OS.\n"
        "Judge each INDUSTRY (not a company): how likely large companies in this industry are "
        "to need and buy the customer's product.\n"
        "Scoring scale:\n"
        "  85-100  P1 segment AND an obvious need for the product\n"
        "  65-84   good fit: P1 or P2 segment with a likely need\n"
        "  40-64   weak fit: possible need, but not a priority segment\n"
        "  0-39    no fit: product is not relevant to this industry\n"
        "Return one verdict for EVERY industry, using its exact index.\n\n"
        "Customer context:\n" + json.dumps(context, ensure_ascii=False)
        + "\n\nIndustries:\n" + json.dumps(industries, ensure_ascii=False)
    )


def title_prompt(context: dict, items: list[tuple[int, str]]) -> str:
    """Judge the FUNCTION of each job title only. Seniority is decided by code."""
    titles = [{"index": i, "job_title": label} for i, label in items]
    return (
        "You are a strict B2B lead judge for MOTM Revenue OS.\n"
        "For each JOB TITLE, judge ONLY its business FUNCTION. Ignore seniority completely "
        "(junior or senior does not matter here; code handles that).\n"
        "function_relevant = true if the function is one of the target roles in the work order, "
        "or a function that clearly buys or runs this kind of product (for example operations, "
        "supply chain, warehouse or plant operations count as operations).\n"
        "function_relevant = false for unrelated functions such as HR, R&D, sales, marketing, "
        "legal, quality, safety / EHS, medical / health, training / learning and development, and "
        "physical or plant security. IT, information, cyber, data or network security IS relevant "
        "(security teams approve software tools).\n"
        "Return one verdict for EVERY title, using its exact index.\n\n"
        "Customer and target roles:\n" + json.dumps(context, ensure_ascii=False)
        + "\n\nJob titles:\n" + json.dumps(titles, ensure_ascii=False)
    )


def judge_new_items(kind: str, labels: dict[str, str], context: dict) -> dict[str, dict]:
    """
    Judge map keys that are not in the map yet. labels = {key: label as first seen}.
    Returns {key: entry} for every item judged successfully. Failed or missing
    items are left out, so they become 'review' now and are retried next run.
    """
    keys = list(labels)
    batches = [keys[i:i + BATCH_SIZE] for i in range(0, len(keys), BATCH_SIZE)]
    if kind == "industry":
        schema, make_prompt = ItemVerdicts, industry_prompt
    else:
        schema, make_prompt = TitleVerdicts, title_prompt
    prompts = [make_prompt(context, [(i, labels[key]) for i, key in enumerate(batch)]) for batch in batches]
    results = judge_in_batches(schema, prompts)

    new_entries = {}
    for batch, result in zip(batches, results):
        if isinstance(result, Exception):
            print(f"  Warning: one {kind} judge batch failed ({result}); those items are marked review.")
            continue
        verdicts = {v.index: v for v in result.verdicts}
        for i, key in enumerate(batch):
            verdict = verdicts.get(i)
            if verdict is None:
                continue
            if kind == "title":
                entry = {"label": labels[key], "function": verdict.function,
                         "function_relevant": verdict.function_relevant}
            else:
                entry = {"label": labels[key], "score": max(0, min(100, verdict.score))}
            entry.update({"reason": verdict.reason, "source": "llm", "judged_at": now_iso()})
            new_entries[key] = entry
    return new_entries


def needs_judging(kind: str, entry: dict | None) -> bool:
    """
    True if the LLM must judge this key: not in the map yet, or an old-format
    title entry (from before the function-only change) that a CAM did not edit.
    """
    if entry is None:
        return True
    return kind == "title" and entry.get("source") != "cam" and "function_relevant" not in entry


def fill_map(kind: str, data: dict, labels: dict[str, str], context: dict) -> None:
    """Judge only the keys missing from the map, add them, save. Prints new vs reused."""
    missing = {key: label for key, label in labels.items() if needs_judging(kind, data["items"].get(key))}
    reused = len(labels) - len(missing)
    print(f"{kind.capitalize()} map: {len(labels)} unique | reused {reused} | new to judge {len(missing)}")
    if not missing:
        return
    new_entries = judge_new_items(kind, missing, context)
    if not new_entries:
        raise ToolError(f"The {kind} judge could not reach OpenAI, so nothing was judged. "
                        "No files were changed. Check the internet connection, then run again.")
    if kind == "title":
        # Drop old-format entries (score/buying_role from the LLM); CAM edits are kept.
        data["items"] = {key: entry for key, entry in data["items"].items()
                         if not needs_judging(kind, entry)}
    data["items"].update(new_entries)
    data["customer"] = context.get("customer")                      # the run's customer name (metadata only)
    data["kind"] = kind
    save_map(kind, data)
    print(f"  Added {len(new_entries)} to {map_path(kind)}")


# ================= STEP 1: COMPANY JUDGE =================

# Words in Prospeo location names that are not part of the place name in an address.
LOCATION_NOISE = re.compile(r"\b(metropolitan area|metropolitan region|division|taluka|tehsil|western)\b", re.IGNORECASE)


def city_word(location: str) -> str:
    """
    A Prospeo location name as the word to find in addresses:
    'Surat Metropolitan Area, India' -> 'surat', 'Western Uttar Pradesh' -> 'uttar pradesh',
    'Delhi, India' -> 'delhi', 'Pune Division, Maharashtra, India' -> 'pune'.
    """
    name = re.sub(r",\s*india\s*$", "", location or "", flags=re.IGNORECASE).split(",")[0]
    return " ".join(LOCATION_NOISE.sub(" ", name).split()).lower()


def target_city_words(payload: dict) -> list[str]:
    """
    Searched locations as words to find in addresses: 'Delhi, India' -> 'delhi' (+ aliases).
    payload: the searched payload of the round being judged (from the task's ledger). Required.
    """
    words = []
    for location in payload.get("locations", []):
        city = city_word(location)
        if city:
            words.append(city)
            words.extend(CITY_ALIASES.get(city, []))
    if not words:
        raise ToolError("No target locations in the search payload (the task's ledger 'payload' step).")
    return words


def min_headcount(payload: dict) -> int:
    """Smallest size searched for: ['201-500', '501-1000'] -> 201. payload: as in target_city_words."""
    lows = [int(re.findall(r"\d+", size)[0]) for size in payload.get("company_size", []) if re.findall(r"\d+", size)]
    return min(lows) if lows else 0


def to_int(value) -> int | None:
    """Headcount may arrive as 1124, '1124' or '1,124'; return an int or None."""
    if isinstance(value, int):
        return value
    digits = re.sub(r"[^\d]", "", str(value or ""))
    return int(digits) if digits else None


def hard_check(company: dict, city_words: list[str], minimum: int) -> str | None:
    """Return a failure reason, or None if the company passes the code checks."""
    location = (company.get("LOCATION") or "").lower()
    if not any(word in location for word in city_words):
        return "Outside target geography"
    headcount = to_int(company.get("HEADCOUNT"))
    if headcount is not None and headcount < minimum:
        return f"Headcount {headcount} below minimum {minimum}"
    return None


def company_context(understanding: dict, work_order: dict) -> dict:
    """
    What the industry judge needs to know about the customer and its product.
    The orchestrator passes the run's understanding and the task's own work order (required).
    """
    return {
        "customer": understanding.get("customer_name"),
        "what_they_sell": understanding.get("business_summary"),
        "priority_segments": understanding.get("priority_segments", []),
        "target_tier": work_order.get("tier"),
    }


def company_verdict(entry: dict, label: str | None = None) -> dict:
    """Turn a map entry (or company override) into a company verdict. Code decides pass/fail."""
    score = entry_score(entry)
    source = entry.get("source", "llm")
    if score is None:
        return review("Map entry has no valid score; fix the map file", source)
    reason = entry.get("reason", "")
    if label:
        reason = f"{label}: {reason}"
    return {"status": "pass" if score >= PASS_SCORE else "fail", "score": score,
            "reason": reason, "checked_by": source}


def judge_companies(companies: list[dict], payload: dict, context: dict) -> list[dict]:
    """
    Judge every company; each gets a 'validation' block: status, score, reason, checked_by.
    payload / context: the task's ledger payload and company_context of the run (required).
    """
    city_words = target_city_words(payload)
    minimum = min_headcount(payload)
    print(f"Target city words: {city_words} | Minimum headcount: {minimum}")

    data = load_map("industry")
    overrides = data.get("company_overrides", {})
    validations, company_keys, labels = {}, {}, {}

    for index, company in enumerate(companies):
        failure = hard_check(company, city_words, minimum)
        if failure:
            validations[index] = {"status": "fail", "score": 0, "reason": failure, "checked_by": "code"}
            continue
        domain = clean_domain(company.get("WEBSITE"))
        if domain and domain in overrides:
            validations[index] = company_verdict(overrides[domain], "Company override")
            continue
        key = industry_key(company.get("INDUSTRY"))
        if not key:
            validations[index] = review("No industry to judge", "code")
            continue
        company_keys[index] = key
        labels.setdefault(key, company.get("INDUSTRY").strip())

    if labels:
        fill_map("industry", data, labels, context)
    print()

    for index, key in company_keys.items():
        entry = data["items"].get(key)
        if entry is None:
            validations[index] = review("Industry could not be judged (judge failed); retried next run", "llm")
        else:
            validations[index] = company_verdict(entry, entry.get("label"))

    return [{**company, "validation": validations[index]} for index, company in enumerate(companies)]


# ================= STEP 2: PEOPLE JUDGE =================

def linkedin_key(url: str | None) -> str:
    """'https://www.linkedin.com/company/Cipla/' -> 'linkedin.com/company/cipla'"""
    if not url:
        return ""
    return re.sub(r"^https?://(www\.)?", "", url.strip().lower()).rstrip("/")


def people_context(understanding: dict, work_order: dict) -> dict:
    """
    What the title judge needs: the customer's product and the target roles.
    understanding / work_order: as in company_context (the run's; required).
    """
    return {
        "customer": understanding.get("customer_name"),
        "what_they_sell": understanding.get("business_summary"),
        "target_roles_work_order": {
            "instructions": work_order.get("instructions"),
            "target": work_order.get("target"),
        },
    }


def person_verdict(key: str, entry: dict) -> dict:
    """Turn a title-map entry into a person verdict. Code decides seniority and pass/fail."""
    source = entry.get("source", "llm")
    result = title_role_and_score(key, entry)
    if result is None:
        if source == "cam":
            reason = "Title map 'cam' entry needs a valid buying_role and score; fix the map file"
        elif entry.get("function_relevant") is True:
            reason = "Relevant function, but no seniority word in the title; check by hand"
        else:
            reason = "Title not judged yet (old map entry or failed judge); retried next run"
        return review(reason, source, buying_role=None)
    role, score = result
    passed = score >= PEOPLE_PASS_SCORE and role != "not_relevant"
    function = entry.get("function") or "function"
    rule = None if source == "cam" else code_rule_reason(key)
    if rule:
        return {"status": "pass" if passed else "fail", "score": score, "buying_role": role,
                "reason": rule, "checked_by": "code"}
    if source == "cam":
        reason = entry.get("reason", "Set by CAM")
    elif role == "not_relevant":
        reason = f"{function}: not a target function"
    else:
        reason = f"{function} + {role.replace('_', ' ')} level"
    return {"status": "pass" if passed else "fail", "score": score, "buying_role": role,
            "reason": reason, "checked_by": source}


def judge_people(people: list[dict], company_keys: set[str], context: dict) -> list[dict]:
    """
    Judge every person; each gets a 'validation' block: status, score, buying_role, reason, checked_by.
    company_keys: domains / company LinkedIn pages allowed (the run's T3 companies; required).
    context: people_context of the run (required).
    """
    data = load_map("title")
    validations, person_keys, labels = {}, {}, {}

    for index, person in enumerate(people):
        domain = clean_domain(person.get("COMPANY URL"))
        company_page = linkedin_key(person.get("COMPANY SOCIAL LINK"))
        if domain not in company_keys and company_page not in company_keys:
            validations[index] = {"status": "fail", "score": 0, "buying_role": "not_relevant",
                                  "reason": "Company did not pass the company judge", "checked_by": "code"}
            continue
        title = (person.get("JOB POSITION") or "").strip()
        key = title_key(title)
        if not key:
            validations[index] = review("No job title to judge", "code", buying_role=None)
            continue
        person_keys[index] = key
        labels.setdefault(key, title)

    if labels:
        fill_map("title", data, labels, context)
    print()

    for index, key in person_keys.items():
        entry = data["items"].get(key)
        if entry is None:
            validations[index] = review("Job title could not be judged (judge failed); retried next run",
                                        "llm", buying_role=None)
        else:
            validations[index] = person_verdict(key, entry)

    return [{**person, "validation": validations[index]} for index, person in enumerate(people)]


# ================= MAP COMMANDS =================

def show_maps() -> None:
    """Print both maps for this customer, best score first."""
    for kind in ("industry", "title"):
        data = load_map(kind)
        rows = []
        for key, entry in data["items"].items():
            if kind == "title":
                result = title_role_and_score(key, entry)
                role, score = result if result else ("REVIEW", None)
                extra = f" {role:14} {entry.get('function', '')}"
            else:
                score, extra = entry_score(entry), ""
            rows.append((score, entry.get("source", "llm"), extra, entry.get("label")))
        rows.sort(key=lambda row: -(row[0] or 0))
        print(f"\n===== {kind.upper()} MAP: {len(rows)} entries ({map_path(kind)}) =====")
        for score, source, extra, label in rows:
            print(f"  {str(score):>4} [{source:3}]{extra}  |  {label}")
        overrides = data.get("company_overrides", {})
        if kind == "industry" and overrides:
            print(f"  Company overrides: {len(overrides)}")
            for domain, entry in overrides.items():
                print(f"  {str(entry.get('score')):>4} [{entry.get('source', 'cam'):3}]  {domain}")


def reset_maps() -> None:
    """Delete both maps for this customer, after the user types 'yes'."""
    paths = [map_path(kind) for kind in ("industry", "title") if os.path.exists(map_path(kind))]
    if not paths:
        print("No maps to delete for this customer.")
        return
    print("This deletes:\n  " + "\n  ".join(paths))
    if input("Type 'yes' to delete: ").strip().lower() != "yes":
        print("Nothing deleted.")
        return
    for path in paths:
        os.remove(path)
    print("Deleted. The next judge run rebuilds the maps.")


# ================= RUN =================


def selftest() -> None:
    """Pure-code checks of the title rules (no LLM, no files). Stops with SystemExit(1) on a failure."""
    relevant = {"function": "operations", "function_relevant": True, "source": "llm"}
    not_relevant = {"function": "it", "function_relevant": False, "source": "llm"}
    cases = [
        # (title, map entry, expected buying_role, should pass)
        ("EA to CEO", relevant, "user", False),
        ("Executive Assistant to the CEO", relevant, "user", False),
        ("Secretary to CFO", relevant, "user", False),
        ("Assistant to Managing Director", relevant, "user", False),
        ("PA to MD", relevant, "user", False),
        ("Executive Assistant", relevant, "user", False),
        ("CEO", not_relevant, "decision_maker", True),
        ("Managing Director", not_relevant, "decision_maker", True),
        ("Head - Procurement", relevant, "decision_maker", True),
        ("VP Supply Chain", relevant, "decision_maker", True),
        ("Assistant General Manager - Procurement", relevant, "influencer", True),
        ("Trainee Engineer", relevant, "user", False),
        ("EHS Manager", relevant, "not_relevant", False),
        ("Head Safety", relevant, "not_relevant", False),
        ("Plant Security Head", relevant, "not_relevant", False),
        ("Security Guard Supervisor", relevant, "not_relevant", False),
        ("Fire & Safety Officer", relevant, "not_relevant", False),
        ("Vigilance Manager", relevant, "not_relevant", False),
        ("Training Manager", relevant, "not_relevant", False),
        ("Learning & Development Head", relevant, "not_relevant", False),
        ("Medical Officer", relevant, "not_relevant", False),
        ("Head IT Security", not_relevant, "decision_maker", True),
        ("Information Security Manager", not_relevant, "influencer", True),
        ("Cyber Security Lead", not_relevant, "influencer", True),
        ("Data Security Director", not_relevant, "decision_maker", True),
        ("Network Security Engineer", relevant, "user", False),
        ("CISO", not_relevant, "decision_maker", True),
    ]
    failures = 0
    for title, entry, role, should_pass in cases:
        key = title_key(title)
        verdict = person_verdict(key, entry)
        ok = verdict["buying_role"] == role and (verdict["status"] == "pass") == should_pass
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {title:<42} -> {verdict['buying_role']:<15} {verdict['status']:<5} "
              f"({verdict['reason']})")
    cam = {"source": "cam", "buying_role": "influencer", "score": 70, "reason": "CAM: EA owns tool buying here"}
    ok = person_verdict(title_key("EA to CEO"), cam)["buying_role"] == "influencer"
    failures += not ok
    print(f"  {'PASS' if ok else 'FAIL'}  a 'cam' map entry still wins over the code rules")
    # Prospeo location names -> words to find in addresses (no file read).
    for location, expected in [("Surat Metropolitan Area, India", "surat"),
                               ("Western Uttar Pradesh", "uttar pradesh"),
                               ("Mumbai Metropolitan Region, India", "mumbai"),
                               ("Pune Division, Maharashtra, India", "pune"),
                               ("Haveli Taluka, India", "haveli"),
                               ("Sadar Tehsil", "sadar"),
                               ("Delhi, India", "delhi"),
                               ("Gujarat, India", "gujarat"),
                               ("Bharuch", "bharuch")]:
        ok = city_word(location) == expected
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  location '{location}' -> '{city_word(location)}' (expected '{expected}')")
    # Map file names follow CUSTOMER_KEY, never the customer's name (both checked in-process only).
    saved_key = os.environ.get("CUSTOMER_KEY")
    try:
        os.environ["CUSTOMER_KEY"] = "anvixa-ai-product"
        names = (os.path.basename(map_path("industry")), os.path.basename(map_path("title")))
        ok = names == ("anvixa_ai_product_industry_map.json", "anvixa_ai_product_title_map.json")
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  CUSTOMER_KEY anvixa-ai-product -> {names[0]}, {names[1]} (same as before)")
        # No customer NAME is read anywhere for the maps (the platform's 'Anvika Ai' cannot change them):
        # only CUSTOMER_KEY decides, so another key gives other files and the same key the same files.
        os.environ["CUSTOMER_KEY"] = "selftest-other-key"
        other = os.path.basename(map_path("industry"))
        os.environ["CUSTOMER_KEY"] = "anvixa-ai-product"
        ok = (other == "selftest_other_key_industry_map.json"
              and os.path.basename(map_path("industry")) == "anvixa_ai_product_industry_map.json"
              and "customer_name" not in globals())
        failures += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  map files follow CUSTOMER_KEY only (no customer name is read; "
              "'Anvika Ai' cannot change them)")
    finally:
        if saved_key is None:
            os.environ.pop("CUSTOMER_KEY", None)
        else:
            os.environ["CUSTOMER_KEY"] = saved_key
    print("VALIDATOR SELFTEST OK" if not failures else f"VALIDATOR SELFTEST FAILED ({failures})")
    if failures:
        sys.exit(1)


MODES = {"maps": show_maps, "reset-maps": reset_maps, "selftest": selftest}

if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        mode = sys.argv[1] if len(sys.argv) > 1 else ""
        if mode not in MODES:
            print("Usage: python validator.py " + " | ".join(MODES))
            sys.exit(1)
        try:
            MODES[mode]()
        except ToolError as error:
            print("\nSTOPPED:", error)
            sys.exit(1)

    run_cli(cli)
