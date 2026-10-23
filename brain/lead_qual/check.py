"""
lead_qual/check.py  -  CODE compares a lead's facts with the approved criteria. No LLM here.

Per criterion: pass / fail / unknown (with the question a person must answer).
    product      one of the criteria's product names or aliases -> pass; in 'not_offered' -> fail; anything else ('other: ...')
                 or no product -> unknown (a person decides)
    specs        the matched product's specs: allowed values, or min..max in the SAME unit (other unit -> unknown)
    delivery     a country other than India -> fail; not_served wins; served by state, by its region, or pan-India;
                 state missing / not a state -> unknown
    industry     excluded -> fail; allowed (or no allowed list) -> pass; 'other: ...' -> unknown
    company size an excluded size class (e.g. 'SME' vs 'small and medium') -> fail; employees outside min..max -> fail
    order size   min..max in the same unit; INR criteria: the lead's lakh / crore / ₹ amount converted by code
Score % (code): pass = full weight, unknown = half, fail = 0; earned / total x 100. Verdict:
    a HARD criterion failed -> not_verified (whatever the score)
    score >= config.VERIFIED_AT -> verified, >= config.NEEDS_HUMAN_AT -> needs_human; below that: not_verified
    only if a criterion FAILED (only unknowns -> needs_human: nothing is wrong, too much is missing)
    a HARD criterion unknown -> at most needs_human (never verified); nothing to score -> needs_human
A person can then answer a not_verified or needs_human lead ONCE (answer()).
"""

import math
import os
import re

from lead_qual import config, regions, store

VERDICTS = ("verified", "not_verified", "needs_human")
MAX_NOTE = 1000


def same(a, b) -> bool:
    return " ".join(str(a or "").lower().split()) == " ".join(str(b or "").lower().split())


def named(product: str, wanted: str) -> bool:
    """The lead names this product: the same words, or one name contains the other ('TMT bars' / 'Fe 500 TMT bars')."""
    a, b = " ".join(product.lower().split()), " ".join(wanted.lower().split())
    return a == b or a in b or b in a


def number(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def in_range(name: str, value, unit: str, rule: dict) -> tuple[str, str]:
    """pass / fail / unknown for a number against min..max (+ unit; INR: lakh / crore / ₹ converted by code)."""
    amount = number(value)
    if amount is None:
        return "unknown", f"Is '{value}' a number for {name}?"
    if rule.get("unit") == "INR":
        from intake_adapter import rupees_per_unit
        per_unit = rupees_per_unit(unit)
        if per_unit is None:
            return "unknown", f"{name}: is '{f'{value} {unit}'.strip()}' an amount in rupees (lakh / crore / ₹)?"
        amount, unit = amount * per_unit, "INR"
    elif (rule.get("unit") or "") and not same(unit, rule["unit"]):
        return "unknown", f"{name}: the lead says '{value} {unit}', the criteria use {rule['unit']}. Same thing?"
    low, high = rule.get("min"), rule.get("max")
    if (low is not None and amount < low) or (high is not None and amount > high):
        show = lambda v: "-" if v is None else readable(v, unit)
        return "fail", f"{name} {readable(amount, unit)} is outside {show(low)} .. {show(high)}"
    return "pass", ""


def plain(value: float) -> str:
    """1000000 -> '1,000,000'; 2.5 -> '2.5' (never '1e+06')."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}".rstrip("0").rstrip(".")


def readable(value: float, unit: str = "") -> str:
    """INR -> '₹10 lakh (1,000,000 INR)' / '₹2 crore (20,000,000 INR)' / '₹5,000 (5,000 INR)'; else '500 tonnes'."""
    if unit != "INR":
        return f"{plain(value)} {unit}".strip()
    for size, word in ((10_000_000, "crore"), (100_000, "lakh")):
        if value >= size:
            return f"₹{plain(value / size)} {word} ({plain(value)} INR)"
    return f"₹{plain(value)} ({plain(value)} INR)"


def judge(facts: dict, criteria: dict) -> list[dict]:
    """[{criterion, result, reason}] for one lead."""
    results = []

    def add(criterion, result, reason=""):
        results.append({"criterion": criterion, "result": result, "reason": reason})

    products = criteria.get("products") or []
    not_offered = (criteria.get("not_offered") or {}).get("names") or []
    if products or not_offered:
        wanted = (facts.get("product") or {}).get("value")
        other = bool(wanted) and wanted.lower().startswith("other:")
        text = wanted.split(":", 1)[1].strip() if other else wanted
        # a pick matches a product's name or one of its aliases (CAM words); an 'other: ...' answer only if exactly so
        match = next((p for p in products if text and any(
            (same if other else named)(name, text) for name in [p["name"], *(p.get("aliases") or [])])), None)
        if not wanted:
            add("product", "unknown", "Which product does this lead want?")
        elif any(named(name, text) for name in not_offered if text):
            add("product", "fail", f"'{text}' is not offered")
        elif not match:
            add("product", "unknown", f"Is '{text}' something this customer sells (products: "
                                      + ", ".join(p["name"] for p in products) + ")?")
        else:
            add("product", "pass", match["name"])
            specs = (facts.get("specs") or {})
            for spec in match.get("specs") or []:
                given = next((v for k, v in specs.items() if same(k, spec["name"])), None)
                label = f"spec {spec['name']}"
                if not given:
                    add(label, "unknown", f"What {spec['name']} does this lead need?")
                elif spec.get("allowed_values"):                  # a list of allowed values wins over a range
                    if any(same(given["value"], allowed) for allowed in spec["allowed_values"]):
                        add(label, "pass")
                    else:
                        add(label, "fail", f"{given['value']} is not one of: " + ", ".join(spec["allowed_values"]))
                else:
                    add(label, *in_range(spec["name"], given["value"], given.get("unit", ""), spec))

    delivery = criteria.get("delivery")
    if delivery:
        state = (facts.get("state") or {}).get("value")
        country = (facts.get("country") or {}).get("value")
        if country and not regions.is_india(country):
            add("delivery", "fail", f"{country} is outside India")
        elif not state or not regions.is_state(state):
            city = (facts.get("city") or {}).get("value")
            add("delivery", "unknown", f"Which Indian state is the delivery in{(' (city: ' + city + ')') if city else ''}?")
        elif regions.place_matches(state, delivery.get("not_served") or []):
            add("delivery", "fail", f"{state} ({regions.region_of(state)}) is not served")
        elif regions.place_matches(state, delivery.get("served") or []):
            add("delivery", "pass", f"{state} ({regions.region_of(state)})")
        else:
            add("delivery", "fail", f"{state} ({regions.region_of(state)}) is not in the served places: "
                                    + ", ".join(delivery.get("served") or []))

    industries = criteria.get("industries")
    if industries:
        industry = (facts.get("industry") or {}).get("value")
        if not industry:
            add("industry", "unknown", "Which industry is this lead in?")
        elif any(same(industry, name) for name in industries.get("excluded") or []):
            add("industry", "fail", f"{industry} is excluded")
        elif not industries.get("allowed") or any(same(industry, name) for name in industries["allowed"]):
            add("industry", "pass", industry)
        else:
            add("industry", "unknown", f"Is '{industry}' an industry this customer sells to?")

    company = criteria.get("company_size")
    if company:
        employees = (facts.get("employees") or {}).get("value")
        described = (facts.get("company_size") or {}).get("value")
        excluded = set().union(*[size_classes(text) for text in company.get("excluded") or []])
        has_range = company.get("min_employees") is not None or company.get("max_employees") is not None
        if described and size_classes(described) & excluded:
            add("company size", "fail", f"'{described}' is an excluded size ({', '.join(company['excluded'])})")
        elif employees is not None and has_range:
            add("company size", *in_range("employees", employees, "",
                                          {"min": company.get("min_employees"), "max": company.get("max_employees")}))
        elif described and size_classes(described) and not has_range:
            add("company size", "pass", described)
        else:
            add("company size", "unknown", "How large is this company (employees, or small / medium / large)?")

    size = criteria.get("order_size")
    if size:
        quantity = facts.get("quantity")
        if not quantity:
            add("order size", "unknown", "How much does this lead want to buy?")
        else:
            add("order size", *in_range("order size", quantity["value"], quantity.get("unit", ""), size))
    return results


def size_classes(text: str | None) -> set[str]:
    """'small and medium' / 'SME' -> {small, medium}; 'large enterprise' -> {large}; nothing known -> set()."""
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    classes = set()
    if words & {"small", "micro", "sme", "smes", "msme", "msmes"}:
        classes.add("small")
    if words & {"medium", "mid", "sme", "smes", "msme", "msmes"}:
        classes.add("medium")
    if words & {"large", "billion", "conglomerate"} or ("enterprise" in words and not classes):
        classes.add("large")
    return classes


POINTS = {"pass": 1.0, "unknown": 0.5, "fail": 0.0}


def shown(score: float) -> str:
    """79.95 -> '79.9%' (rounded DOWN, so a shown 80.0% always means verified)."""
    return f"{math.floor(score * 10 + 1e-9) / 10:.1f}%"


def decide(results: list[dict], weights: dict) -> dict:
    """{verdict, score, earned, total, reason}: the score and the verdict rules above. Code only."""
    missing = [r["criterion"] for r in results if r["criterion"] not in weights]
    if missing:
        raise store.Stop(f"STOPPED: no weight for {', '.join(missing)}. Add weights and approve again.")
    earned = sum(weights[r["criterion"]]["weight"] * POINTS[r["result"]] for r in results)
    total = sum(weights[r["criterion"]]["weight"] for r in results)
    score = earned / total * 100 if total else 0.0
    hard_failed = [r for r in results if r["result"] == "fail" and weights[r["criterion"]]["hard"]]
    hard_unknown = [r for r in results if r["result"] == "unknown" and weights[r["criterion"]]["hard"]]
    band = (f"score {shown(score)} (verified from {config.VERIFIED_AT:g}%, needs a person from "
            f"{config.NEEDS_HUMAN_AT:g}%)")
    if hard_failed:
        verdict, reason = "not_verified", "hard criterion failed: " + "; ".join(
            f"{r['criterion']} ({r['reason']})" for r in hard_failed)
    elif not total:
        verdict, reason = "needs_human", "nothing to score"
    elif score >= config.VERIFIED_AT and hard_unknown:
        verdict, reason = "needs_human", f"{band}, but a hard criterion is unknown: " + ", ".join(
            r["criterion"] for r in hard_unknown)
    elif score >= config.VERIFIED_AT:
        verdict, reason = "verified", band
    elif score >= config.NEEDS_HUMAN_AT:
        verdict, reason = "needs_human", band
    elif any(r["result"] == "fail" for r in results):
        verdict, reason = "not_verified", band
    else:
        verdict, reason = "needs_human", f"{band}, but nothing failed: too much is unknown, a person decides"
    return {"verdict": verdict, "score": round(score, 4), "earned": earned, "total": total, "reason": reason}


def verdict(results: list[dict], weights: dict) -> str:
    return decide(results, weights)["verdict"]


def evaluate(leads: list[dict], approved: dict, ask_batch=None) -> tuple[list[dict], int]:
    """(one row per lead: verdict, score, results, facts ..., LLM calls). The ONE place leads are judged and scored."""
    from lead_qual import extract
    ids = [lead.get("lead_id") for lead in leads]
    if not leads or any(not lead_id for lead_id in ids) or len(set(ids)) != len(ids):
        raise store.Stop("STOPPED: every lead needs a unique lead_id (and the file must not be empty).")
    facts, calls = extract.extract(leads, approved["criteria"], ask_batch)
    rows = []
    for lead, lead_facts in zip(leads, facts):
        results = judge(lead_facts, approved["criteria"])
        decided = decide(results, approved["criteria"]["weights"])
        rows.append({"lead_id": lead["lead_id"], "source": lead.get("source"), "company": lead.get("company"),
                     "verdict": decided["verdict"], "score": decided["score"], "score_shown": shown(decided["score"]),
                     "earned": decided["earned"], "total": decided["total"], "verdict_reason": decided["reason"],
                     "results": results,
                     "questions": [r["reason"] for r in results if r["result"] == "unknown"], "facts": lead_facts,
                     "human_verdict": None})
    return rows, calls


def check(leads: list[dict], key: str, approved: dict, ask_batch=None) -> dict:
    """evaluate() every lead of a file; the old verdicts file is archived, never overwritten."""
    rows, calls = evaluate(leads, approved, ask_batch)
    data = {"customer": key, "checked_at": store.now(), "criteria_sha256": approved["approval"]["sha256"],
            "thresholds": {"verified_at": config.VERIFIED_AT, "needs_human_at": config.NEEDS_HUMAN_AT},
            "llm_calls": calls, "leads": rows}
    moved = store.archive_verdicts()
    store.write(store.VERDICTS, data)
    data["archived_previous"] = moved
    return data


def answer(lead_id: str, human: str, note: str, by: str | None = None) -> dict:
    """A person decides a not_verified / needs_human lead: once, with a note."""
    if human not in ("verified", "not_verified"):
        raise store.Stop("STOPPED: answer verified or not_verified.")
    if not (note or "").strip() or len(note) > MAX_NOTE:
        raise store.Stop(f"STOPPED: a note is required (at most {MAX_NOTE} characters), saying why.")
    inbox = store.read_inbox()
    in_inbox = next((lead for lead in inbox["leads"] if lead["lead_id"] == lead_id), None)
    if in_inbox:                                          # an inbox lead: its check is kept with it
        if not in_inbox.get("check"):
            raise store.Stop(f"STOPPED: lead '{lead_id}' is not checked yet. Run: python -m lead_qual check-inbox")
        data, row = inbox, in_inbox["check"]
    else:
        data = store.read(store.VERDICTS, "run: python -m lead_qual check <leads.json>")
        row = next((r for r in data["leads"] if r["lead_id"] == lead_id), None)
    if not row:
        raise store.Stop(f"STOPPED: no lead '{lead_id}' in {store.INBOX} or {store.VERDICTS}.")
    if row.get("human_verdict"):
        raise store.Stop(f"STOPPED: lead '{lead_id}' was already answered ({row['human_verdict']} by "
                         f"{row.get('answered_by')} at {row.get('answered_at')}).")
    if row["verdict"] == "verified":
        raise store.Stop(f"STOPPED: lead '{lead_id}' is verified by the checks; only not_verified or needs_human "
                         "leads are answered.")
    row.update(human_verdict=human, human_note=note.strip(), answered_at=store.now(),
               answered_by=(by or os.getenv("PLAN_APPROVER") or "user").strip()[:80])
    if in_inbox:
        store.save_inbox(data)
    else:
        store.write(store.VERDICTS, data)
    return {**row, "lead_id": lead_id}
