"""
lead_qual/criteria.py  -  build the qualification criteria (1 LLM call), keep only what the document says,
approve them (a person), and check the approval before any lead is checked.

RULES (code)
    - only HARD restrictions are criteria (the prompt says so; e.g. 'India only'): sales priorities are not
    - every criterion carries 1-3 word-for-word pieces of the document ('quotes'); case and spacing are ignored,
      ₹ / Rs / Rs. / INR / the broken '�' count as the same mark, and list markers in front ('-', '->', '1.',
      'Q-12 ... ->') and . , ; at the end are removed first. Each piece is checked on its own: a piece not found is
      dropped; an item with no piece left is dropped; each value (industry name, excluded size, order text) must
      appear in a kept piece, else that value is dropped. All of it is listed under 'dropped' with the reason.
    - an excluded 'industry' that is a company size is moved to company_size by code
    - questions_for_cam (gaps in the criteria) block approval until answered and removed; lead_questions do not
    - weights: one per criterion a lead is checked against ('product', 'spec <name>', 'delivery', 'industry',
      'company size', 'order size'): {weight > 0, hard true/false}. The LLM proposes them; one it did not weigh gets
      config.DEFAULT_WEIGHT from code plus a question for the CAM. They are part of the approved sha256; criteria
      without weights (approved before scoring) must be approved again
    - order size: the LLM copies the text ('₹30 lakh to ₹2 crore'); CODE turns it into numbers (INR via
      intake_adapter.rupees_per_unit, or another unit such as tonnes)
    - a person may edit the file before approving: an edited or added item says  "source": "cam"  (no quote needed)
    - approve: the person types 'yes' and a name -> status approved, approved_by / approved_at and the sha256 of the
      criteria; approved() refuses criteria that are not approved or were changed after approval
"""

import os
import re

from pydantic import BaseModel, Field

from lead_qual import store

MIN_QUOTE = 8
MAX_PIECES = 3
QUOTES = Field(description="1 to 3 pieces of the document copied EXACTLY (word for word) that say this")


class Spec(BaseModel):
    name: str = Field(description="Something a LEAD states about itself, e.g. 'platform used' - never a product feature")
    allowed_values: list[str] = Field(default_factory=list, description="The values a lead may have")
    min: float | None = Field(default=None, description="Lowest allowed number, if the document gives a range")
    max: float | None = Field(default=None, description="Highest allowed number, if the document gives a range")
    unit: str = Field(default="", description="Unit of min/max, e.g. 'mm'; empty if none")
    quotes: list[str] = QUOTES


class Product(BaseModel):
    name: str = Field(description="A product the customer sells, as the document names it")
    specs: list[Spec] = Field(default_factory=list)
    quotes: list[str] = QUOTES


class NotOffered(BaseModel):
    names: list[str] = Field(default_factory=list, description="Products the document says it does NOT offer")
    quotes: list[str] = QUOTES


class Delivery(BaseModel):
    served: list[str] = Field(default_factory=list, description="Where it CAN deliver: states, regions, or 'pan-India'")
    not_served: list[str] = Field(default_factory=list, description="Where it can NOT deliver")
    quotes: list[str] = QUOTES


class Industries(BaseModel):
    allowed: list[str] = Field(default_factory=list, description="Industries it sells to, ONLY if it sells to no others")
    excluded: list[str] = Field(default_factory=list, description="Industries it does NOT sell to")
    quotes: list[str] = QUOTES


class CompanySize(BaseModel):
    min_employees: int | None = None
    max_employees: int | None = None
    excluded: list[str] = Field(default_factory=list, description="Company sizes it does not sell to, e.g. 'small and medium'")
    quotes: list[str] = QUOTES


class OrderSize(BaseModel):
    text: str = Field(description="The order size exactly as the document writes it, e.g. '₹30 lakh to ₹2 crore'")
    quotes: list[str] = QUOTES


class Weight(BaseModel):
    criterion: str = Field(description="'product', 'spec <spec name>', 'delivery', 'industry', 'company size' or 'order size'")
    weight: float = Field(description="How much this criterion counts, 1 (little) to 10 (a lot)")
    hard: bool = Field(default=False, description="true ONLY for an absolute restriction: failing it rules the lead out")
    reason: str = ""


class Criteria(BaseModel):
    products: list[Product] = Field(default_factory=list)
    not_offered: NotOffered | None = None
    delivery: Delivery | None = None
    industries: Industries | None = None
    company_size: CompanySize | None = None
    order_size: OrderSize | None = None
    questions_for_cam: list[str] = Field(default_factory=list, description=(
        "Gaps in THESE criteria that the customer's team must answer before leads can be checked (e.g. 'Is delivery "
        "outside India possible?'). Only about what a lead must or must not be."))
    weights: list[Weight] = Field(default_factory=list, description="One weight for every criterion you give")
    lead_questions: list[str] = Field(default_factory=list, description=(
        "Questions to ask a LEAD later to decide if it fits (e.g. 'Which platform do you use?'). Not about the plan."))


PROMPT = (
    "Read the customer's document and write the criteria a sales lead MUST meet to be a lead this customer can "
    "actually supply.\n"
    "- Only HARD restrictions are criteria: what the customer cannot or will not do (e.g. 'India only', 'no "
    "defence', 'minimum order 5 tonnes'). Sales priorities, preferred or priority regions, segments or tiers are "
    "NOT restrictions: leave them out.\n"
    "- Products: what it sells (and not_offered: only products the document says it does NOT offer). Specs: only things a LEAD can state about itself or its order (e.g. the platform "
    "the lead uses: SAP, Salesforce; a steel grade it needs), never features of the customer's own product.\n"
    "- Delivery: where it can and cannot deliver. Industries: excluded ones; allowed ones ONLY if it sells to no "
    "others. Company size: min/max employees, or sizes it does not sell to (e.g. 'small and medium'); keep it "
    "separate from industries. Order size: copy the text exactly (e.g. '₹30 lakh to ₹2 crore'); do not convert it.\n"
    "- For every item give 1 to 3 pieces of the document copied EXACTLY, word for word.\n"
    "- weights: for EVERY criterion you give, one entry: criterion ('product', 'spec <spec name>', 'delivery', "
    "'industry', 'company size' or 'order size'), weight 1-10 (how much it matters for a good lead), hard = true "
    "only if failing it rules the lead out completely (e.g. outside India), and a short reason.\n"
    "- Two kinds of questions, both ONLY about whether a lead fits: questions_for_cam = gaps in these criteria the "
    "customer's team must answer (they block approval until answered); lead_questions = what to ask a lead later. "
    "Nothing about planning, pricing, staff, deadlines or targets. Never guess.")

MONEY_MARK = re.compile(r"(?<![a-z])(rs\.?|inr)(?![a-z])|�")
AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|cr\b)?", re.IGNORECASE)
LOWER_WORDS = ("minimum", "min", "at least", "from", "above", "over", "more than")
UPPER_WORDS = ("maximum", "max", "up to", "upto", "below", "under", "less than")


def squash(text: str) -> str:
    """Lower case, single spaces, and ₹ / Rs / Rs. / INR / '�' as one mark ('rs' inside a word is left alone)."""
    return " ".join(MONEY_MARK.sub("₹", (text or "").lower()).split()).replace("₹ ", "₹")


LIST_MARKER = re.compile(r"^\s*(?:->|Q-\d+\b[^\n]*?->|[-*\u2022]|\d+[.)](?=\s))\s*")
SIZE_WORDS = {"small", "medium", "large", "micro", "mid", "midsize", "sme", "smes", "msme", "msmes"}
SCALE_WORDS = {"company", "companies", "scale", "size", "sized", "business", "businesses", "enterprise", "enterprises",
               "firm", "firms", "organisation", "organisations", "organization", "organizations"}


def clean_piece(piece: str) -> str:
    """A quote piece without list markers in front ('-', '*', '\u2022', '->', '1.', 'Q-12 ... ->') or . , ; at the end."""
    text = piece or ""
    while True:
        shorter = LIST_MARKER.sub("", text, count=1)
        if shorter == text:
            break
        text = shorter
    return text.strip().rstrip(".,; \t").strip()


def looks_like_size(text: str) -> bool:
    """'Small and medium-scale companies', 'SME', 'large enterprises' -> True; 'Enterprise operations' -> False."""
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return bool(words & {"sme", "smes", "msme", "msmes"}) or bool(words & SIZE_WORDS and words & SCALE_WORDS)


def pieces(item: dict | BaseModel) -> list[str]:
    """The item's quotes (an older file has a single 'quote')."""
    data = item if isinstance(item, dict) else item.model_dump()
    return list(data.get("quotes") or ([data["quote"]] if data.get("quote") else []))


def quote_problem(quotes: list[str], document: str) -> str | None:
    """None if 1-3 pieces are each word for word in the document; else why not."""
    if not quotes:
        return "no quote"
    if len(quotes) > MAX_PIECES:
        return f"more than {MAX_PIECES} pieces"
    for piece in quotes:
        problem = piece_problem(piece, document)
        if problem:
            return problem
    return None


def piece_problem(piece: str, document: str) -> str | None:
    """None if this one piece (list markers and end punctuation removed) is word for word in the document."""
    text = clean_piece(piece)
    if len(squash(text)) < MIN_QUOTE:
        return f"piece too short: '{piece}'"
    if squash(text) not in squash(document):
        return f"not word for word in the document: '{piece}'"
    return None


def amount_range(text: str) -> dict | None:
    """
    CODE reads an order size: '₹30 lakh to ₹2 crore' / '30 to 200 lakh ₹' -> {min 3000000, max 20000000, unit INR};
    '5 to 500 tonnes' -> {min 5, max 500, unit tonnes}; 'minimum ₹10 lakh' -> {min 1000000}; unreadable -> None.
    A number without its own lakh/crore takes the next one ('30 to 200 lakh'). Money via intake_adapter.rupees_per_unit.
    """
    from intake_adapter import rupees_per_unit
    lowered = squash(text)
    found = [(float(m[1].replace(",", "")), (m[2] or "").lower(), m.end()) for m in AMOUNT.finditer(lowered)]
    if not found:
        return None
    money = "₹" in lowered or any(word for _, word, _ in found)
    if money:
        values = []
        for i, (number, word, _) in enumerate(found):
            word = word or next((w for _, w, _ in found[i + 1:] if w), "")
            values.append(number * (rupees_per_unit(word or "₹") or 1))
        unit = "INR"
    else:
        values = [number for number, _, _ in found]
        unit = (re.match(r"\s*([a-z%]+)", lowered[found[-1][2]:]) or [None, ""])[1]
    if len(values) >= 2:
        return {"min": min(values), "max": max(values), "unit": unit}
    if any(word in lowered for word in LOWER_WORDS):
        return {"min": values[0], "max": None, "unit": unit}
    if any(word in lowered for word in UPPER_WORDS):
        return {"min": None, "max": values[0], "unit": unit}
    return None


def values_of(name: str, item: dict) -> list[tuple[str, str]]:
    """(field, value) pairs that must each appear in a matched piece: industry names, excluded sizes, the order text."""
    if name == "industries":
        return [("allowed", v) for v in item.get("allowed") or []] + [("excluded", v) for v in item.get("excluded") or []]
    if name == "company_size":
        return [("excluded", v) for v in item.get("excluded") or []]
    if name == "order_size":
        return [("text", item.get("text") or "")]
    if name == "not_offered":
        return [("names", v) for v in item.get("names") or []]
    return []


def keep_quoted(found: Criteria, document: str) -> tuple[dict, list[dict], list[str]]:
    """
    CODE keeps what the document says:
        a piece that is not word for word (after list markers / end punctuation are removed) -> that piece dropped
        an item with no matching piece -> the item dropped (with the LLM's quotes and what it found)
        a value (industry name, excluded size, order text) in no matched piece -> that value dropped
        an excluded 'industry' that is a company size ('small and medium-scale companies') -> moved to company_size
    Returns (criteria, dropped [{item, ..., reason}], questions for the CAM about what was dropped).
    """
    dropped, questions = [], []

    def matched(label: str, item: dict) -> list[str]:
        good = []
        for piece in pieces(item):
            problem = piece_problem(piece, document)
            if problem:
                dropped.append({"item": label, "piece": piece, "reason": problem})
            else:
                good.append(piece)
        if not good:
            dropped.append({"item": label, "quotes": pieces(item), "reason": "no piece is word for word in the document",
                            "as_found": item})
            questions.append(f"The document gives no exact source for the {label}: confirm it (source 'cam') or remove it.")
        return good

    products = []
    for product in found.products:
        good = matched(f"product '{product.name}'", product.model_dump())
        if not good:
            continue
        specs = []
        for spec in product.specs:
            spec_good = matched(f"spec '{spec.name}' of '{product.name}'", spec.model_dump())
            if spec_good:
                specs.append({**spec.model_dump(), "quotes": spec_good, "source": "document"})
        products.append({"name": product.name, "quotes": good, "source": "document", "specs": specs})
    criteria = {"products": products}

    items = {name: getattr(found, name).model_dump() if getattr(found, name) else None
             for name in ("not_offered", "delivery", "industries", "company_size", "order_size")}
    industries, company = items["industries"], items["company_size"]
    if industries:
        sizes = [v for v in industries.get("excluded") or [] if looks_like_size(v)]
        if sizes:                                   # a company size is not an industry: code moves it
            industries["excluded"] = [v for v in industries["excluded"] if v not in sizes]
            company = company or {"min_employees": None, "max_employees": None, "excluded": [], "quotes": []}
            known = {squash(v) for v in company.get("excluded") or []}
            company["excluded"] = list(company.get("excluded") or []) + [v for v in sizes if squash(v) not in known]
            company["quotes"] = list(company.get("quotes") or []) + [p for p in pieces(industries)
                                                                      if any(squash(v) in squash(p) for v in sizes)
                                                                      and p not in (company.get("quotes") or [])]
            dropped.append({"item": "industries", "value": ", ".join(sizes),
                            "reason": "a company size, not an industry: moved to company_size"})
            items["company_size"] = company

    for name in ("not_offered", "delivery", "industries", "company_size", "order_size"):
        item = items[name]
        if not item:
            criteria[name] = None
            continue
        good = matched(name, item)
        if not good:
            criteria[name] = None
            continue
        for field, value in values_of(name, item):
            if not any(squash(clean_piece(value)) in squash(clean_piece(piece)) for piece in good):
                dropped.append({"item": name, "value": value, "reason": "not in any matched piece of the document"})
                if field == "text":
                    item = None
                    break
                item[field] = [v for v in item[field] if v != value]
        if item is None or (name in ("industries",) and not (item.get("allowed") or item.get("excluded"))) or (
                name == "not_offered" and not item.get("names")) or (
                name == "company_size" and not item.get("excluded") and item.get("min_employees") is None
                and item.get("max_employees") is None):
            criteria[name] = None
            questions.append(f"Nothing of the {name} is left after the checks: confirm it (source 'cam') or remove it.")
            continue
        criteria[name] = {**item, "quotes": good, "source": "document"}

    criteria["weights"], weight_notes = weights_for(criteria, found.weights)
    dropped += weight_notes["dropped"]
    questions += weight_notes["questions"]

    order = criteria.get("order_size")
    if order:
        numbers = amount_range(order["text"])
        criteria["order_size"] = {**order, **(numbers or {"min": None, "max": None, "unit": ""})}
        if not numbers:
            questions.append(f"The order size '{order['text']}' cannot be read as numbers: give a min and/or max.")
    return criteria, dropped, questions


def criterion_names(criteria: dict) -> list[str]:
    """The criteria a lead is checked against, by the names check.judge() gives its results."""
    names = ["product"] if criteria.get("products") or criteria.get("not_offered") else []
    names += [f"spec {s['name']}" for p in criteria.get("products") or [] for s in p.get("specs") or []]
    for key, name in (("delivery", "delivery"), ("industries", "industry"), ("company_size", "company size"),
                      ("order_size", "order size")):
        if criteria.get(key):
            names.append(name)
    return names


def weights_for(criteria: dict, proposed: list) -> tuple[dict, dict]:
    """{criterion: {weight, hard, source, reason}} for every criterion present (code fills what the LLM left out)."""
    from lead_qual import config
    given = {w.criterion.strip().lower(): w for w in proposed}
    weights, notes = {}, {"dropped": [], "questions": []}
    for name in criterion_names(criteria):
        w = given.pop(name.lower(), None)
        if w is not None and w.weight > 0:
            weights[name] = {"weight": float(w.weight), "hard": bool(w.hard), "source": "llm", "reason": w.reason}
        else:
            weights[name] = {"weight": config.DEFAULT_WEIGHT, "hard": False, "source": "code", "reason": ""}
            notes["questions"].append(f"Confirm the weight of '{name}' (set by code: {config.DEFAULT_WEIGHT:g}, not hard), "
                                      "then mark it \"source\": \"cam\".")
    notes["dropped"] += [{"item": "weights", "value": name, "reason": "no such criterion"} for name in given]
    return weights, notes


def weight_problems(criteria: dict) -> list[str]:
    """Why these weights cannot be used (empty = fine): one per criterion present, numbers > 0, hard true/false."""
    weights = criteria.get("weights")
    if not isinstance(weights, dict):
        return ["the criteria have no weights (scoring)"]
    names = criterion_names(criteria)
    problems = [f"no weight for '{name}'" for name in names if name not in weights]
    problems += [f"a weight for '{name}', which is not a criterion" for name in weights if name not in names]
    for name, w in weights.items():
        value = w.get("weight") if isinstance(w, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            problems.append(f"the weight of '{name}' must be a number above 0 (it is {value!r})")
        if not isinstance(w, dict) or not isinstance(w.get("hard"), bool):
            problems.append(f"'hard' of '{name}' must be true or false")
    return problems


def understanding_text(key: str) -> str:
    """The latest run's understanding for this customer (read only), or '' when there is none."""
    from state_store import get_runs
    runs = [run for run in get_runs(key) if run.get("understanding")]
    return str(runs[-1]["understanding"]) if runs else ""


def build(key: str, ask=None, understanding: str | None = None) -> dict:
    """1 LLM call: the criteria from data/intake_document.txt (+ the run's understanding). Saved as a draft."""
    if ask is None:
        from quickicp import ask_llm as ask
    if not os.path.exists(store.DOCUMENT):
        raise store.Stop(f"STOPPED: {store.DOCUMENT} is missing. Run intake_adapter.py for this customer first.")
    document = open(store.DOCUMENT, encoding="utf-8").read()
    understanding = understanding_text(key) if understanding is None else understanding
    prompt = (PROMPT + "\n\nDOCUMENT:\n" + document
              + ("\n\nWHAT THE BRAIN UNDERSTOOD (for context only; quotes must come from the DOCUMENT):\n"
                 + understanding if understanding else ""))
    found = ask(Criteria, prompt, "Lead criteria")
    criteria, dropped, questions = keep_quoted(found, document)
    data = {"customer": key, "status": "draft", "built_at": store.now(), "document": store.DOCUMENT,
            "criteria": criteria, "dropped": dropped,
            "questions_for_cam": list(found.questions_for_cam) + questions,     # block approval until answered / removed
            "lead_questions": list(found.lead_questions),                        # to ask leads later; never block
            "approval": None}
    store.write(store.criteria_path(key), data)
    return data


def item_problems(criteria: dict, document: str) -> list[str]:
    """Items that have neither exact document quotes nor source 'cam' (they cannot be approved)."""
    items = [(f"product '{p.get('name')}'", p) for p in criteria.get("products") or []]
    items += [(f"spec '{s.get('name')}' of '{p.get('name')}'", s) for p in criteria.get("products") or []
              for s in p.get("specs") or []]
    items += [(name, criteria[name]) for name in ("not_offered", "delivery", "industries", "company_size", "order_size")
              if criteria.get(name)]
    return [f"{label}: {quote_problem(pieces(item), document)} and not marked \"source\": \"cam\""
            for label, item in items if item.get("source") != "cam" and quote_problem(pieces(item), document)]


def approve(key: str, ask=input) -> dict:
    """A person approves the criteria: typed 'yes' + name. Items without exact document quotes must say source 'cam'."""
    path = store.criteria_path(key)
    data = store.read(path, "run: python -m lead_qual criteria")
    document = open(store.DOCUMENT, encoding="utf-8").read() if os.path.exists(store.DOCUMENT) else ""
    problems = item_problems(data["criteria"], document) + weight_problems(data["criteria"])
    open_for_cam = list(data.get("questions_for_cam") or []) + list(data.get("questions") or [])   # 'questions': older file
    if open_for_cam:
        problems += [f"open question for the CAM (answer it in the criteria, then remove it): {q}" for q in open_for_cam]
    if problems:
        raise store.Stop("STOPPED: cannot approve:\n  - " + "\n  - ".join(problems))
    print(f"Criteria for {key} ({path}):")
    print(json_text(data["criteria"]))
    print("  weights:")
    for name, w in data["criteria"]["weights"].items():
        print(f"    {name:<28} {w['weight']:>5g}  {'HARD' if w['hard'] else '    '}  [{w.get('source')}] {w.get('reason') or ''}")
    for item in data.get("dropped") or []:
        print(f"  dropped: {item['item']} {item.get('value') or item.get('piece') or ''} ({item['reason']})")
    for question in data.get("lead_questions") or []:
        print(f"  for leads later: {question}")
    if ask("Approve these criteria? Type yes: ").strip().lower() != "yes":
        raise store.Stop("STOPPED: not approved. Nothing changed.")
    name = (os.getenv("PLAN_APPROVER") or "").strip() or ask("Your name: ").strip()
    if not name:
        raise store.Stop("STOPPED: a name is needed for the approval. Nothing changed.")
    data["status"] = "approved"
    data["approval"] = {"approved_by": name[:80], "approved_at": store.now(), "sha256": store.fingerprint(data["criteria"]),
                        "lead_questions": list(data.get("lead_questions") or [])}
    store.write(path, data)
    return data


def approved(key: str) -> dict:
    """The approved criteria, or STOPPED (missing, not approved, or changed after approval)."""
    data = store.read(store.criteria_path(key), "run: python -m lead_qual criteria, then approve")
    if data.get("status") != "approved" or not data.get("approval"):
        raise store.Stop("STOPPED: the lead criteria are not approved. Run: python -m lead_qual approve")
    if "weights" not in data["criteria"]:
        raise store.Stop("STOPPED: these criteria were approved before scoring and have no weights. Add weights (or "
                         "rebuild: python -m lead_qual criteria), then approve again: python -m lead_qual approve")
    if data["approval"].get("sha256") != store.fingerprint(data["criteria"]):
        raise store.Stop("STOPPED: the lead criteria changed after they were approved. Approve them again: "
                         "python -m lead_qual approve")
    problems = weight_problems(data["criteria"])
    if problems:
        raise store.Stop("STOPPED: the weights cannot be used: " + "; ".join(problems) + ". Fix them and approve again.")
    return data


def json_text(value) -> str:
    import json
    return json.dumps(value, indent=2, ensure_ascii=False)
