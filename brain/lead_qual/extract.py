"""
lead_qual/extract.py  -  the lead's facts: the fields the lead already gives WIN; the LLM only fills the missing
ones, with a quote from the lead's text (at most 1 call per lead, 0 when nothing is missing). No verdicts here.

A lead (data the person gives):
    {"lead_id", "source", "text", optional "company", "industry", "state", "city", "country", "product", "quantity",
     "employees", "company_size"}
Facts returned per lead (each with where it came from: 'given' or 'llm'):
    product (one of the criteria's product names, or 'other: ...'), specs {name: {value, unit}}, state, city,
    country, industry, quantity {value, unit}, employees, company_size
An LLM value whose quote is not in the lead's text is thrown away (the fact stays missing -> 'unknown').
"""

import re

from pydantic import BaseModel, Field

from lead_qual.criteria import squash
from lead_qual.regions import is_india

QUANTITY = re.compile(r"(-?\d[\d,]*(?:\.\d+)?)\s*([A-Za-z%][A-Za-z %/.]*)?")


class SpecValue(BaseModel):
    name: str = Field(description="The specification's name, exactly as in the list you were given")
    value: str
    unit: str = ""
    quote: str = Field(description="The exact words of the lead this comes from")


class LeadFacts(BaseModel):
    product: str | None = Field(default=None, description="One of the given product names, or 'other: <what it is>'")
    product_quote: str = ""
    specs: list[SpecValue] = Field(default_factory=list)
    state: str | None = Field(default=None, description="The Indian state of the delivery location")
    city: str | None = None
    country: str | None = Field(default=None, description="The country of the delivery location")
    location_quote: str = ""
    industry: str | None = Field(default=None, description="One of the given industry names, or 'other: <what it is>'")
    industry_quote: str = ""
    quantity: float | None = None
    quantity_unit: str = Field(default="", description="e.g. 'tonnes', or 'lakh ₹' / 'crore ₹' / '₹' for money")
    quantity_quote: str = ""
    employees: int | None = Field(default=None, description="Number of employees, only if the lead states it")
    company_size: str | None = Field(default=None, description="How the lead describes its size, e.g. 'SME', 'large enterprise'")
    size_quote: str = ""


def parse_quantity(text) -> dict | None:
    """'500 tonnes' -> {'value': 500.0, 'unit': 'tonnes'}; 1200 -> {'value': 1200.0, 'unit': ''}; else None."""
    if isinstance(text, (int, float)):
        return {"value": float(text), "unit": ""}
    match = QUANTITY.search(str(text or ""))
    if not match:
        return None
    return {"value": float(match[1].replace(",", "")), "unit": (match[2] or "").strip()}


def spec_names(criteria: dict) -> list[str]:
    return sorted({s["name"] for p in criteria.get("products") or [] for s in p.get("specs") or []})


def industry_names(criteria: dict) -> list[str]:
    industries = criteria.get("industries") or {}
    return list(industries.get("allowed") or []) + list(industries.get("excluded") or [])


def given_facts(lead: dict) -> dict:
    """The fields the lead itself gives: they win over anything the LLM reads."""
    facts = {}
    for name in ("product", "state", "city", "country", "industry", "company_size"):
        if (lead.get(name) or "").strip():
            facts[name] = {"value": lead[name].strip(), "from": "given"}
    if lead.get("quantity") not in (None, ""):
        quantity = parse_quantity(lead["quantity"])
        if quantity:
            facts["quantity"] = {**quantity, "from": "given"}
    if str(lead.get("employees") or "").replace(",", "").strip().isdigit():
        facts["employees"] = {"value": int(str(lead["employees"]).replace(",", "")), "from": "given"}
    return facts


def missing(lead: dict, criteria: dict) -> list[str]:
    """What the comparison needs and the lead does not give (specs are never given fields)."""
    have = given_facts(lead)
    need = []
    if (criteria.get("products") or criteria.get("not_offered")) and "product" not in have:
        need.append("product")
    if spec_names(criteria):
        need.append("specs")
    abroad = "country" in have and not is_india(have["country"]["value"])
    if criteria.get("delivery") and "state" not in have and not abroad:     # a foreign country needs no state
        need.append("state")
    if criteria.get("industries") and "industry" not in have:
        need.append("industry")
    if criteria.get("order_size") and "quantity" not in have:
        need.append("quantity")
    if criteria.get("company_size") and not ({"employees", "company_size"} & set(have)):
        need.append("company_size")
    return need


def prompt_for(lead: dict, criteria: dict, need: list[str]) -> str:
    names = [p["name"] + (f" (leads may also say: {', '.join(p['aliases'])})" if p.get("aliases") else "")
             for p in criteria.get("products") or []] + list((criteria.get("not_offered") or {}).get("names") or [])
    asks = {"product": "product: what the lead wants, as ONE of these names (answer with the name, not the other "
                       "words): " + "; ".join(names) + " - or 'other: <what it is>' if none fits",
            "specs": "specs: a value for each of these specifications if the lead states it: " + ", ".join(spec_names(criteria)),
            "state": "location: the country where it must be delivered, and if in India the Indian state (and the "
                     "city, if given)",
            "industry": "industry: the lead's industry as ONE of these names: " + ", ".join(industry_names(criteria))
                        + " - or 'other: <what it is>' if none fits",
            "quantity": "quantity: how much the lead wants (or its budget), as a number and its unit, e.g. 50 + "
                        "'lakh ₹' or 500 + 'tonnes'",
            "company_size": "company size: the number of employees if stated, and how the lead describes its size "
                            "(e.g. 'SME', 'large enterprise')"}
    return ("Read this sales lead and copy out ONLY these facts, each with the exact words of the lead it comes from. "
            "Leave a fact empty if the lead does not say it; never guess.\n- " + "\n- ".join(asks[n] for n in need)
            + "\n\nLEAD:\n" + (lead.get("text") or ""))


def facts_from_llm(answer: LeadFacts, text: str, need: list[str]) -> dict:
    """Only answers whose quote is really in the lead's text, and only for what was asked."""
    inside = lambda quote: bool(squash(quote)) and squash(quote) in squash(text)
    facts = {}
    if "product" in need and answer.product and inside(answer.product_quote):
        facts["product"] = {"value": answer.product, "from": "llm", "quote": answer.product_quote}
    if "state" in need and answer.state and inside(answer.location_quote):
        facts["state"] = {"value": answer.state, "from": "llm", "quote": answer.location_quote}
        if answer.city:
            facts["city"] = {"value": answer.city, "from": "llm", "quote": answer.location_quote}
    if "state" in need and answer.country and inside(answer.location_quote):
        facts["country"] = {"value": answer.country, "from": "llm", "quote": answer.location_quote}
    if "industry" in need and answer.industry and inside(answer.industry_quote):
        facts["industry"] = {"value": answer.industry, "from": "llm", "quote": answer.industry_quote}
    if "quantity" in need and answer.quantity is not None and inside(answer.quantity_quote):
        facts["quantity"] = {"value": answer.quantity, "unit": answer.quantity_unit, "from": "llm",
                             "quote": answer.quantity_quote}
    if "company_size" in need and inside(answer.size_quote):
        if answer.employees is not None:
            facts["employees"] = {"value": answer.employees, "from": "llm", "quote": answer.size_quote}
        if answer.company_size:
            facts["company_size"] = {"value": answer.company_size, "from": "llm", "quote": answer.size_quote}
    if "specs" in need:
        facts["specs"] = {s.name: {"value": s.value, "unit": s.unit, "from": "llm", "quote": s.quote}
                          for s in answer.specs if inside(s.quote)}
    return facts


def extract(leads: list[dict], criteria: dict, ask_batch=None) -> tuple[list[dict], int]:
    """(facts per lead, number of LLM calls). One batch: one call per lead that misses something."""
    needs = [missing(lead, criteria) for lead in leads]
    asked = [i for i, need in enumerate(needs) if need]
    answers = {}
    if asked:
        if ask_batch is None:
            from quickicp import ask_llm_batch as ask_batch
        replies = ask_batch(LeadFacts, [prompt_for(leads[i], criteria, needs[i]) for i in asked], "Lead facts")
        answers = dict(zip(asked, replies))
    result = []
    for i, lead in enumerate(leads):
        facts = facts_from_llm(answers[i], lead.get("text") or "", needs[i]) if i in answers else {}
        facts.update(given_facts(lead))                               # given fields always win
        result.append(facts)
    return result, len(asked)
