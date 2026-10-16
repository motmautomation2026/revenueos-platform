"""
intake_adapter.py
=================

Reads the customer's READY plan from the RevenueOS platform (our team's app, same Supabase project)
and writes the document master_agent.py reads, instead of the scraped strategy page
(extract_html.py -> data/extracted.txt, which stays as the fallback).

WHY
---
The platform's three agents (diagnosis, strategy, execution) already turn the customer's documents and
filled checklist into structured JSON. This file turns that JSON into a clean, labelled text document
BY CODE (no LLM): numbers are copied exactly, and every gap is said out loud.

READ-ONLY ON THEIR TABLES
-------------------------
    Only SELECTs, through one helper (ReadOnly). This file never inserts, updates or deletes anything
    of theirs. The selftest proves it with a fake client that records every call.
        revenueos_projects          the project: id, customer_name, engagement_type, status
        revenueos_plans             the LATEST version with status 'ready' of that project
        revenueos_checklists        the project's ACTIVE checklist
        revenueos_checklist_items   its questions + answers (internal 'reason' notes are not read)

WHICH CUSTOMER
--------------
    REVENUEOS_PROJECT_ID in .env = the platform project of THIS CUSTOMER_KEY. Explicit, never derived
    from a name. Their customer name is shown only as 'Platform project name ... (not used as a key)'.

MISSING FIELDS (code, no LLM)
-----------------------------
    revenue target + period, engagement period, sales cycle, company size:
        1. the plan JSON (engagement_period; a funnel fact named like 'sales cycle')
        2. the checklist: the ANSWER of the matching question (code: category + keywords)
        3. else its prefilled answer, marked '(prefilled, not confirmed by the customer)'
        4. else a line under 'DATA GAPS FOR A HUMAN'

FLOW
----
    python intake_adapter.py            read -> data/intake_document.txt + data/intake_source.json
    python master_agent.py --intake     plans from data/intake_document.txt (approval unchanged)
    Older intake files are MOVED to data/archive/<YYYYMMDD-HHMMSS>-intake/ first (never overwritten).

WRITES
------
    data/intake_document.txt   the document (sections: source, business, segments, geographies,
                               economics, targets + roadmap, channels, qualification, responsibilities,
                               constraints, personas / buying / triggers / objections, the four fields,
                               the filled checklist, data gaps)
    data/intake_source.json    {project_id, plan_id, plan_version, plan_finished_at, checklist_id, read_at,
                                customer_key, fields_from_plan, fields_from_checklist, fields_missing,
                                document_sha256, revenue_numbers_from_plan}: exactly which plan we used
    data/intake_numbers.json   revenue inputs taken by CODE from the plan JSON, each with its source:
                               AOV (funnel fact 'Order value', lakh/crore converted), outbound / inbound
                               close rate, capacity, sales cycle, and the opportunity count = the EARLIEST
                               roadmap milestone counting ENQUIRIES (matches the enquiry-to-order rate).
                               master_agent --intake uses these numbers instead of the LLM's.

RUN
---
    python intake_adapter.py            reads Supabase only: no LLM, no paid tool
    python intake_adapter.py selftest   fake read-only client + throwaway folder: no network at all

DEBUGGING
---------
    "STOPPED: REVENUEOS_PROJECT_ID is missing"   -> add it to .env (the project's id in revenueos_projects)
    "STOPPED: no project ..."                    -> wrong id, or the platform's tables are elsewhere
    "STOPPED: no READY plan ..."                 -> finish the plan in the platform (status 'ready')
    A number looks wrong                         -> compare with revenueos_plans.diagnosis / strategy /
                                                    execution: this file copies, never computes
"""

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone

from dotenv import load_dotenv

from quickicp import ToolError

load_dotenv()

PROJECTS, PLANS, CHECKLISTS, ITEMS = "revenueos_projects", "revenueos_plans", "revenueos_checklists", "revenueos_checklist_items"
DATA_DIR = "data"
DOC_NAME, SOURCE_NAME = "intake_document.txt", "intake_source.json"
NUMBERS_NAME = "intake_numbers.json"   # revenue inputs taken by code (master_agent --intake)
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PREFILLED_NOTE = "(prefilled, not confirmed by the customer)"

# The four fields master_agent needs that the plan JSON may not hold. Code matches checklist questions
# by category (None = any) and keywords in the question, all lowercase.
MISSING_FIELDS = {
    "revenue_target": {"label": "Revenue target and its period", "categories": {"numbers for targets"},
                       "keywords": ("business goal", "revenue target", "target revenue", "'revenue' mean")},
    "engagement_period": {"label": "Engagement period", "categories": None,
                          "keywords": ("engagement period", "engagement duration", "duration of the engagement",
                                       "contract length", "contract period")},
    "sales_cycle": {"label": "Sales cycle length", "categories": None,
                    "keywords": ("sales cycle", "time to close", "first enquiry to order")},
    "company_size": {"label": "Target company size (employees / turnover)", "categories": {"ideal customers"},
                     "keywords": ("company size", "employee count", "turnover", "headcount")},
}


# ================= READ-ONLY ACCESS =================

class ReadOnly:
    """The ONLY way this file touches the platform's tables: SELECT with equality filters."""

    def __init__(self, client):
        self._client = client

    def select(self, table: str, columns: str, **equal) -> list[dict]:
        query = self._client.table(table).select(columns)
        for column, value in equal.items():
            query = query.eq(column, value)
        return query.execute().data or []


def project_id() -> str:
    """REVENUEOS_PROJECT_ID from .env: the platform project of THIS CUSTOMER_KEY."""
    value = (os.getenv("REVENUEOS_PROJECT_ID") or "").strip().lower()
    if not value:
        raise ToolError("STOPPED: REVENUEOS_PROJECT_ID is missing in .env (the project's id in revenueos_projects).")
    if not UUID.match(value):
        raise ToolError(f"STOPPED: REVENUEOS_PROJECT_ID is not a valid id: '{value}'.")
    return value


def read_platform(reader: ReadOnly, project: str) -> dict:
    """The project, its latest READY plan, and its active checklist with items. Select only."""
    projects = reader.select(PROJECTS, "id, customer_name, engagement_type, status", id=project)
    if not projects:
        raise ToolError(f"STOPPED: no project {project} in {PROJECTS}.")
    plans = reader.select(PLANS, "id, project_id, version, status, finished_at, diagnosis, strategy, execution",
                          project_id=project, status="ready")
    plans = [p for p in plans if p["project_id"] == project and p["status"] == "ready"]
    if not plans:
        raise ToolError(f"STOPPED: no READY plan for project {project} in {PLANS}. Finish the plan in the platform.")
    plan = max(plans, key=lambda p: p["version"])
    checklists = reader.select(CHECKLISTS, "id, project_id, is_active, status, submitted_at", project_id=project, is_active=True)
    checklist = checklists[0] if checklists else None
    items = (reader.select(ITEMS, "code, category, question, answer, prefilled_answer, position",
                           checklist_id=checklist["id"]) if checklist else [])
    return {"project": projects[0], "plan": plan, "checklist": checklist,
            "items": sorted(items, key=lambda i: i.get("position") or 0)}


# ================= THE FOUR FIELDS (code) =================

# The checklist may ask for a secret (e.g. the SMTP password of the customer's sending mailbox).
# Such an answer must never reach data/intake_document.txt or the LLM. Same wording rule as the
# platform (lib/checklist-secrets.ts): keep the two in step.
SECRET_QUESTION = re.compile(
    r"\b(pass\s?word|pass\s?code|app\s?password|secret\s?key|api\s?key|auth(?:entication)?\s?token)\b", re.I)
SECRET_PROVIDED = "(provided; kept hidden)"


def answer_of(item: dict) -> str | None:
    """The customer's answer, else the prefilled answer marked as not confirmed, else None.
    A secret answer (see SECRET_QUESTION) is replaced by SECRET_PROVIDED and never returned."""
    answer = (item.get("answer") or "").strip()
    if SECRET_QUESTION.search(item.get("question") or ""):
        return SECRET_PROVIDED if answer else None
    if answer:
        return answer
    prefilled = (item.get("prefilled_answer") or "").strip()
    return f"{prefilled} {PREFILLED_NOTE}" if prefilled else None


def from_plan(field: str, plan: dict) -> str | None:
    """The field from the plan JSON itself, if it is there."""
    if field == "engagement_period":
        return (plan["strategy"] or {}).get("engagement_period") or None
    if field == "sales_cycle":
        for fact in (plan["diagnosis"] or {}).get("funnel_facts", []):
            if "sales cycle" in (fact.get("metric") or "").lower() and (fact.get("value_low") is not None
                                                                         or fact.get("value_high") is not None):
                return funnel_line(fact)
    return None


def find_fields(plan: dict, items: list[dict]) -> dict:
    """For each of the four fields: where it comes from (plan / checklist / missing) and its text."""
    found = {}
    for field, rule in MISSING_FIELDS.items():
        value = from_plan(field, plan)
        if value:
            found[field] = {"label": rule["label"], "source": "plan", "lines": [value]}
            continue
        lines = []
        for item in items:
            question = (item.get("question") or "").lower()
            category_ok = rule["categories"] is None or (item.get("category") or "").lower() in rule["categories"]
            if category_ok and any(k in question for k in rule["keywords"]):
                answer = answer_of(item)
                if answer:
                    lines.append(f"{item['code']} {item['question']} -> {answer}")
        found[field] = ({"label": rule["label"], "source": "checklist", "lines": lines} if lines
                        else {"label": rule["label"], "source": "missing", "lines": []})
    return found


# ================= REVENUE NUMBERS (code, no LLM) =================

def rupees_per_unit(unit: str | None) -> float | None:
    """'lakh ₹' -> 100000, 'crore' -> 10000000, '₹' / 'INR' / 'Rs' -> 1; anything else -> None (not money)."""
    text = (unit or "").lower()
    if "crore" in text:
        return 10_000_000
    if "lakh" in text:
        return 100_000
    if any(mark in text for mark in ("₹", "inr", "rs")):
        return 1
    return None


def revenue_numbers(plan: dict) -> dict:
    """
    The revenue inputs the platform plan already holds, taken by CODE (same plan -> same numbers):
        AOV, outbound / inbound close rate, capacity, sales cycle   from diagnosis.funnel_facts
        qualified opportunities = the EARLIEST roadmap milestone counting ENQUIRIES (it matches the
        enquiry-to-order close rate; never prospects, pipeline opportunities or RFQs)
    Only numbers that are there; each with its source under 'sources'. master_agent uses these
    instead of the LLM's numbers (see analyze_revenue).
    """
    diagnosis, execution = plan.get("diagnosis") or {}, plan.get("execution") or {}
    numbers, sources = {}, {}

    def fact(*words):
        for f in diagnosis.get("funnel_facts", []):
            metric = (f.get("metric") or "").lower()
            if all(w in metric for w in words) and (f.get("value_low") is not None or f.get("value_high") is not None):
                return f
        return None

    def low_high(f):
        low = f.get("value_low") if f.get("value_low") is not None else f.get("value_high")
        high = f.get("value_high") if f.get("value_high") is not None else low
        return low, high

    def source(f):
        return f"funnel_facts: {f.get('metric')} (source: {f.get('source')}, confidence: {f.get('confidence')})"

    order_value = fact("order value")
    if order_value and rupees_per_unit(order_value.get("unit")):
        low, high = low_high(order_value)
        rate = rupees_per_unit(order_value.get("unit"))
        numbers.update(aov_low_inr=float(low * rate), aov_high_inr=float(high * rate),
                       aov_as_written=f"{range_text(order_value.get('value_low'), order_value.get('value_high'))} "
                                      f"{order_value.get('unit')}".strip())
        sources["aov"] = source(order_value)
    for side in ("outbound", "inbound"):
        rate_fact = fact("conversion", side)
        if rate_fact:
            low, high = low_high(rate_fact)
            if side == "outbound":
                numbers.update(outbound_close_rate_low_pct=float(low), outbound_close_rate_high_pct=float(high))
            else:
                numbers["inbound_close_rate_pct"] = float(low)
            sources[f"{side}_close_rate"] = source(rate_fact)
    capacity = fact("capacity")
    if capacity:
        numbers["capacity_per_month"] = f"{range_text(capacity.get('value_low'), capacity.get('value_high'))} " \
                                        f"{capacity.get('unit') or ''}".strip()
        sources["capacity_per_month"] = source(capacity)
    cycle = fact("sales cycle")
    if cycle:
        low, high = low_high(cycle)
        unit = (cycle.get("unit") or "").lower()
        weeks = high / 7 if "day" in unit else high if "week" in unit else None
        if weeks is not None:
            numbers["sales_cycle_weeks"] = round(float(weeks), 1)
            sources["sales_cycle_weeks"] = source(cycle)
    for phase in execution.get("roadmap", []):
        enquiries = [m for m in phase.get("milestones", [])
                     if "enquir" in (m.get("metric") or "").lower() and m.get("value_low") is not None]
        if enquiries:
            m = enquiries[0]
            numbers.update(qualified_opportunities_low=int(m["value_low"]),
                           qualified_opportunities_high=int(m.get("value_high") if m.get("value_high") is not None
                                                            else m["value_low"]),
                           opportunities_period=phase.get("months") or phase.get("phase") or "")
            sources["qualified_opportunities"] = (f"roadmap {phase.get('months')}: {m.get('milestone')} "
                                                  f"[{m.get('metric')}, basis {m.get('basis')}]")
            break
    return {**numbers, "sources": sources}


# ================= THE DOCUMENT (code, no LLM) =================

def number(value) -> str:
    """Numbers exactly as given (no rounding, no units added)."""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def range_text(low, high) -> str:
    """'3000000 to 20000000', one value if only one is given or both are equal, else 'not given'."""
    if low is None and high is None:
        return "not given"
    if low is None or high is None or low == high:
        return number(high if low is None else low)
    return f"{number(low)} to {number(high)}"


def funnel_line(fact: dict) -> str:
    value = range_text(fact.get("value_low"), fact.get("value_high"))
    return (f"{fact.get('metric')}: {value} {fact.get('unit') or ''} (source: {fact.get('source')}, "
            f"confidence: {fact.get('confidence')})").replace("  ", " ")


def section(title: str, lines: list[str]) -> str:
    body = [f"- {line}" for line in lines if line] or ["- (none given)"]
    return f"\n== {title} ==\n" + "\n".join(body)


def build_document(data: dict, customer_key: str, read_at: str) -> tuple[str, dict]:
    """(document text, the four fields). Every value is copied from their JSON."""
    project, plan, items = data["project"], data["plan"], data["items"]
    d, s, e = plan["diagnosis"] or {}, plan["strategy"] or {}, plan["execution"] or {}
    fields = find_fields(plan, items)
    bu = d.get("business_understanding") or {}
    parts = [
        "CUSTOMER STRATEGY DOCUMENT (built by code from the RevenueOS platform plan; no LLM)",
        f"CUSTOMER: {d.get('customer_name') or project['customer_name']}",
        f"Platform project name: {project['customer_name']} (not used as a key) | our CUSTOMER_KEY: {customer_key}",
        f"Engagement type: {project.get('engagement_type')}",
        f"Source: platform project {project['id']}, plan {plan['id']} version {plan['version']} "
        f"(ready, finished {plan.get('finished_at')}), read {read_at}",
        section("BUSINESS SUMMARY", [bu.get("summary"), f"Customer solution: {(bu.get('value_chain') or {}).get('customer_solution')}"]
                + [f"Offering: {o.get('name')} ({o.get('type')}) {o.get('notes') or ''}" for o in bu.get("offerings", [])]),
        section("PRIORITY SEGMENTS", [f"{p.get('tier')}: {p.get('segment')} | reason: {p.get('rationale')}"
                                      for p in s.get("priorities", [])]),
        section("GEOGRAPHIES", [f"{g.get('region')} (priority: {g.get('priority')}) | clusters (public data to verify): "
                                f"{g.get('clusters_public_data')} | relevant demand: {g.get('relevant_demand')} | "
                                f"why: {g.get('why')} | applies to: {g.get('applies_to')}" for g in s.get("geography", [])]),
        section("ECONOMICS / FUNNEL FACTS", [funnel_line(f) for f in d.get("funnel_facts", [])]),
        section("KEY NUMBERS", [f"{k.get('label')}: {k.get('value')} (source: {k.get('source_ref')})"
                                for k in d.get("key_numbers", [])]),
        section("CAPACITY", [(d.get("capacity") or {}).get("summary"),
                             f"Capacity limits volume targets: {(d.get('capacity') or {}).get('limits_volume_targets')}"]),
        section("TARGETS AND ROADMAP", [
            f"{r.get('phase')} ({r.get('months')}): activities: {'; '.join(r.get('activities', []))} | milestones: "
            + "; ".join(f"{m.get('milestone')} [{m.get('metric')}: {range_text(m.get('value_low'), m.get('value_high'))}"
                        f", basis {m.get('basis')}]" for m in r.get("milestones", []))
            for r in e.get("roadmap", [])] + [f"Guardrail: {e.get('roadmap_guardrail')}"]),
        section("CHANNELS (MOTM lead engine)", [f"{c.get('channel')}: {c.get('job')} | audience: {c.get('audience')} | "
                                               f"scope item: {c.get('scope_item')}" for c in e.get("channels", [])]),
        section("QUALIFICATION QUESTIONS", (e.get("qualification") or {}).get("questions", [])),
        section("LEAD GRADES", [f"Grade {g[-1].upper()}: {(e.get('qualification') or {}).get(g)}"
                                for g in ("grade_a", "grade_b", "grade_c")]),
        section("CUSTOMER RESPONSIBILITIES", (e.get("accountability") or {}).get("customer", [])),
        section("CUSTOMER RESOURCES / PREREQUISITES",
                [f"Proof asset: {x}" for x in (e.get("prerequisites") or {}).get("proof_assets", [])]
                + [f"Operational support: {x}" for x in (e.get("prerequisites") or {}).get("operational_support", [])]
                + [f"Governance input: {x}" for x in (e.get("prerequisites") or {}).get("governance_inputs", [])]),
        section("HOW THEY SELL TODAY (current lead sources)", [f"{h.get('label')}: {h.get('answer')}"
                                                                for h in d.get("how_they_sell_today", [])]),
        section("CONSTRAINTS", [f"Conflict ({c.get('conflict_type')}{', blocking' if c.get('blocking') else ''}): "
                                f"{c.get('field')}: proposal says '{c.get('bd_or_proposal_value')}', customer says "
                                f"'{c.get('customer_value')}'" for c in d.get("conflicts", [])]
                + [f"Blocker: {b.get('blocker')}" for b in (d.get("readiness") or {}).get("blockers", [])]),
        section("PERSONAS", [f"{p.get('persona')}: {p.get('role_in_buying')} | concern: {p.get('main_concern')}"
                             for p in s.get("personas", [])]),
        section("BUYING BEHAVIOUR", [f"{b.get('company_type')}: {b.get('how_they_buy')} | barrier: {b.get('typical_barrier')} | "
                                     f"best way in: {b.get('best_way_in')}" for b in s.get("buying_behaviour", [])]),
        section("DEMAND TRIGGERS", (s.get("demand_triggers") or {}).get("visible", [])
                + (s.get("demand_triggers") or {}).get("hidden_early", [])),
        section("PRICE OBJECTIONS", e.get("price_objection_playbook", [])),
        section("REVENUE TARGET, ENGAGEMENT PERIOD, SALES CYCLE, COMPANY SIZE",
                [f"{f['label']} (from {f['source']}): {line}" for f in fields.values() for line in f["lines"]]
                + [f"{f['label']}: NOT GIVEN (see data gaps)" for f in fields.values() if f["source"] == "missing"]),
        section("FILLED CHECKLIST", [f"{i.get('code')} [{i.get('category')}] {i.get('question')} -> "
                                     f"{answer_of(i) or '(not answered)'}" for i in items]),
        section("DATA GAPS FOR A HUMAN", [f"{g.get('item')}: {g.get('why_it_matters')}" for g in d.get("data_gaps", [])]
                + [f"{f['label']}: not in the platform plan or the checklist; ask the customer"
                   for f in fields.values() if f["source"] == "missing"]
                + [f"Open research: {x}" for x in e.get("open_research", [])]),
    ]
    return "\n".join(parts) + "\n", fields


# ================= WRITE (archive first, never overwrite) =================

def archive_old(data_dir: str) -> str | None:
    """MOVE older intake files to data/archive/<stamp>-intake[-n]/. Returns the folder, or None."""
    present = [n for n in (DOC_NAME, SOURCE_NAME, NUMBERS_NAME) if os.path.exists(os.path.join(data_dir, n))]
    if not present:
        return None
    base = os.path.join(data_dir, "archive", datetime.now().strftime("%Y%m%d-%H%M%S") + "-intake")
    folder, number_ = base, 1
    while os.path.exists(folder):
        number_ += 1
        folder = f"{base}-{number_}"
    os.makedirs(folder)
    for name in present:
        os.replace(os.path.join(data_dir, name), os.path.join(folder, name))
    return folder


def run_intake(reader: ReadOnly, project: str, customer_key: str, data_dir: str = DATA_DIR) -> dict:
    """Read the platform (select only), build the document, archive old files, write the new ones."""
    data = read_platform(reader, project)
    read_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    document, fields = build_document(data, customer_key, read_at)
    numbers = revenue_numbers(data["plan"])
    source = {
        "project_id": data["project"]["id"], "plan_id": data["plan"]["id"], "plan_version": data["plan"]["version"],
        "plan_finished_at": data["plan"].get("finished_at"),
        "checklist_id": (data["checklist"] or {}).get("id"), "read_at": read_at, "customer_key": customer_key,
        "platform_customer_name": data["project"]["customer_name"],
        "fields_from_plan": [k for k, f in fields.items() if f["source"] == "plan"],
        "fields_from_checklist": [k for k, f in fields.items() if f["source"] == "checklist"],
        "fields_missing": [k for k, f in fields.items() if f["source"] == "missing"],
        "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
        "revenue_numbers_from_plan": sorted(numbers["sources"]),
    }
    archived = archive_old(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, DOC_NAME), "w", encoding="utf-8") as file:
        file.write(document)
    with open(os.path.join(data_dir, SOURCE_NAME), "w", encoding="utf-8") as file:
        json.dump(source, file, indent=2, ensure_ascii=False)
    with open(os.path.join(data_dir, NUMBERS_NAME), "w", encoding="utf-8") as file:
        json.dump(numbers, file, indent=2, ensure_ascii=False)
    return {"source": source, "document": document, "archived": archived, "numbers": numbers}


def main() -> None:
    from state_store import current_customer
    from supabase_store import db
    result = run_intake(ReadOnly(db), project_id(), current_customer())
    s = result["source"]
    print(f"Platform plan {s['plan_id']} version {s['plan_version']} (project {s['project_id']}) -> "
          f"{DATA_DIR}/{DOC_NAME} ({len(result['document'])} chars) + {DATA_DIR}/{SOURCE_NAME}")
    print(f"  from the plan: {s['fields_from_plan'] or 'none'} | from the checklist: {s['fields_from_checklist'] or 'none'}"
          f" | missing (data gaps): {s['fields_missing'] or 'none'}")
    if result["archived"]:
        print(f"  older intake files moved to {result['archived']}")
    print("Next: python master_agent.py --intake")


# ================= SELFTEST (fake read-only client, throwaway folder) =================

class FakeClient:
    """Mimics supabase: table().select().eq().execute().data. Records every call; writes are errors."""

    def __init__(self, tables: dict):
        self.tables, self.calls, self.write_attempts = tables, [], []

    def table(self, name):
        client = self

        class Query:
            def __init__(self):
                self.filters = {}

            def select(self, columns):
                client.calls.append(("select", name, columns))
                return self

            def eq(self, column, value):
                self.filters[column] = value
                return self

            def execute(self):
                rows = [r for r in client.tables.get(name, []) if all(r.get(k) == v for k, v in self.filters.items())]

                class Result:
                    data = [dict(r) for r in rows]
                return Result()

            def __getattr__(self, attr):                        # insert / update / delete / upsert ...
                client.write_attempts.append((attr, name))
                raise AssertionError(f"write attempted: {attr} on {name}")
        return Query()


def revenue_selftest(expect, folder: str) -> None:
    """
    Revenue numbers: code takes them from the platform plan JSON (valid enquiries, never prospects / pipeline /
    RFQs); master_agent's analyze_revenue uses them whatever the (fake) LLM says; an LLM number needs a quote.
    No LLM, no network; files only in the throwaway folder.
    """
    from types import SimpleNamespace
    import master_agent

    def f(metric, low, high, unit):
        return {"metric": metric, "value_low": low, "value_high": high, "unit": unit, "source": "customer",
                "confidence": "high"}

    def m(milestone, metric, low, high):
        return {"milestone": milestone, "metric": metric, "value_low": low, "value_high": high, "basis": "motm_proposal"}
    anvixa_like = {
        "diagnosis": {"funnel_facts": [f("Order value", 30, 200, "lakh ₹"),
                                       f("Enquiry-to-order conversion rate (inbound)", 60, 60, "%"),
                                       f("Enquiry-to-order conversion rate (outbound)", 40, 50, "%"),
                                       f("Capacity (new projects per month)", 3, 4, "projects/clients")]},
        "execution": {"roadmap": [
            {"phase": "Foundation", "months": "Months 1–3",
             "milestones": [m("Valid enquiries generated", "enquiries", 10, 12),
                            m("Active prospects in pipeline", "prospects", 20, 30)]},
            {"phase": "Pipeline", "months": "Months 4–6",
             "milestones": [m("Active RFQs received", "RFQs", 3, 6),
                            m("Active opportunities in pipeline", "opportunities", 20, 25)]}]}}
    numbers = revenue_numbers(anvixa_like)
    expect("revenue numbers by code: AOV 30-200 lakh -> 3000000-20000000; outbound 40-50 %, inbound 60 %; capacity",
           numbers["aov_low_inr"] == 3_000_000 and numbers["aov_high_inr"] == 20_000_000
           and numbers["outbound_close_rate_low_pct"] == 40 and numbers["outbound_close_rate_high_pct"] == 50
           and numbers["inbound_close_rate_pct"] == 60 and numbers["capacity_per_month"] == "3 to 4 projects/clients")
    expect("opportunities = VALID ENQUIRIES 10-12 (Months 1-3), never prospects 20-30, pipeline 20-25 or RFQs 3-6",
           (numbers["qualified_opportunities_low"], numbers["qualified_opportunities_high"]) == (10, 12)
           and numbers["opportunities_period"] == "Months 1–3"
           and "Valid enquiries generated" in numbers["sources"]["qualified_opportunities"])
    expect("each number records its source; no sales cycle in the plan -> none invented",
           set(numbers["sources"]) == {"aov", "outbound_close_rate", "inbound_close_rate", "capacity_per_month",
                                       "qualified_opportunities"} and "sales_cycle_weeks" not in numbers)

    def llm_answer(low, high, quote="", fill=True):
        """A fake LLM RevenueInputs answer (only the opportunity numbers differ)."""
        return master_agent.RevenueInputs(
            revenue_goal="30X-60X", aov_as_written="LLM text", aov_low_inr=1.0 if fill else None,
            aov_high_inr=2.0 if fill else None, outbound_close_rate_low_pct=10.0 if fill else None,
            outbound_close_rate_high_pct=20.0 if fill else None, inbound_close_rate_pct=None,
            capacity_per_month="LLM capacity", sales_cycle_weeks=None, qualified_opportunities_low=low,
            qualified_opportunities_high=high, opportunities_period="LLM period", customer_resources_to_use=[],
            revenue_levers=[], revenue_risks=[], missing_for_revenue_forecast=[],
            number_sources=[master_agent.NumberSource(field="qualified_opportunities_low", quote=quote)] if quote else [])

    document = ("== TARGETS AND ROADMAP ==\n- Foundation (Months 1–3): milestones: Valid enquiries generated "
                "[enquiries: 10 to 12]; Active prospects in pipeline [prospects: 20 to 30]\n")
    with open(os.path.join(folder, NUMBERS_NAME), "w", encoding="utf-8") as file:
        json.dump(numbers, file)
    real_ask = master_agent.ask_llm
    results = []
    try:
        for low, high in ((20, 30), (10, 12)):                  # the LLM changes its mind between runs
            master_agent.ask_llm = lambda schema, prompt, name, low=low, high=high: llm_answer(low, high)
            state = {"document_path": os.path.join(folder, DOC_NAME), "document_text": document, "motm_context": "x",
                     "understanding": SimpleNamespace(model_dump_json=lambda indent=None: "{}")}
            results.append(master_agent.analyze_revenue(state)["revenue_analysis"])
        a, b = results
        expect("same platform plan -> SAME numbers although the LLM said 20-30 then 10-12: 10-12 enquiries x 40-50 % "
               "-> 4-6 orders -> Rs 1.2-12 crore",
               (a.expected_orders_low, a.expected_orders_high) == (b.expected_orders_low, b.expected_orders_high) == (4.0, 6.0)
               and a.expected_revenue_low_inr == b.expected_revenue_low_inr == 12_000_000
               and a.expected_revenue_high_inr == b.expected_revenue_high_inr == 120_000_000
               and a.capacity_per_month == "3 to 4 projects/clients" and a.opportunities_period == "Months 1–3")
        expect("the analysis says where each number came from (platform plan) and the note says so",
               a.numbers_source["qualified_opportunities"].startswith("platform plan: roadmap Months 1–3: Valid enquiries")
               and "Numbers taken by code from the platform plan" in a.forecast_note)

        # Fallback document (no platform numbers): an LLM number needs a quote found in the document
        fallback = {"document_path": os.path.join(folder, "extracted.txt"), "document_text": document,
                    "motm_context": "x", "understanding": SimpleNamespace(model_dump_json=lambda indent=None: "{}")}
        master_agent.ask_llm = lambda schema, prompt, name: llm_answer(20, 30, quote="We expect about 25 deals", fill=False)
        unquoted = master_agent.analyze_revenue(fallback)["revenue_analysis"]
        expect("fallback: an LLM number whose quote is NOT in the document -> removed, listed as missing",
               unquoted.qualified_opportunities_low is None and unquoted.qualified_opportunities_high is None
               and any(x.startswith("qualified_opportunities: the LLM gave a number without a source quote")
                       for x in unquoted.missing_for_revenue_forecast))
        master_agent.ask_llm = lambda schema, prompt, name: llm_answer(
            10, 12, quote="valid enquiries generated [enquiries: 10 to 12]", fill=False)
        quoted = master_agent.analyze_revenue(fallback)["revenue_analysis"]
        expect("fallback: an LLM number with a REAL quote (any case / spacing) -> kept, its source recorded",
               (quoted.qualified_opportunities_low, quoted.qualified_opportunities_high) == (10, 12)
               and quoted.numbers_source["qualified_opportunities"].startswith("LLM, quoted:"))
    finally:
        master_agent.ask_llm = real_ask


def llm_selftest(expect, raises) -> None:
    """
    The one LLM factory (quickicp.llm) against a FAKE SLOW LLM server on 127.0.0.1: the real OpenAI client
    times out for real, retries LLM_MAX_RETRIES times, and the caller gets one STOPPED line. Every client
    here points at the local server with a dummy key: no real LLM call is possible.
    master_agent.main() with a timeout: nothing archived, nothing written.
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from pydantic import BaseModel
    import master_agent
    import quickicp
    import validator

    seen = {"requests": 0, "bodies": [], "slow": True, "usage": True}

    class FakeLLM(BaseHTTPRequestHandler):
        """POST /v1/chat/completions: slow mode sleeps past the client timeout; fast mode answers at once."""

        def do_POST(self):
            seen["requests"] += 1
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            seen["bodies"].append(body)
            if seen["slow"]:
                threading.Event().wait(1.5)          # longer than the test timeout (0.3 s)
            reply = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": body.get("model"),
                                "choices": [{"index": 0, "finish_reason": "stop",
                                             "message": {"role": "assistant", "content": json.dumps({"value": "ok"})}}],
                                **({"usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14,
                                              "prompt_tokens_details": {"cached_tokens": 7}}} if seen["usage"] else {})}).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)
            except OSError:
                pass                                 # the client already gave up (timeout)

        def log_message(self, *args):
            pass

    class Answer(BaseModel):
        value: str

    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    real_llm = quickicp.llm
    real_usage_file, usage_folder = quickicp.USAGE_FILE, tempfile.mkdtemp(prefix="usage_selftest_")
    quickicp.USAGE_FILE = os.path.join(usage_folder, "llm_usage.jsonl")    # never the real data/llm_usage.jsonl
    before_total = dict(quickicp.USAGE_TOTAL)
    quickicp.llm = lambda timeout=quickicp.LLM_TIMEOUT_SECONDS, **options: real_llm(
        timeout=min(timeout, 0.3), base_url=f"http://127.0.0.1:{server.server_port}/v1", api_key="selftest", **options)
    saved_model = os.environ.get("OPENAI_MODEL")
    os.environ["OPENAI_MODEL"] = saved_model or "selftest-model"
    real_main_parts = {name: getattr(master_agent, name)
                       for name in ("graph", "plan_approver", "check_document", "archive_plan_files", "write_proof")}
    try:
        client = real_llm()
        expect("LLM factory: OPENAI_MODEL, temperature 0, 2 retries, 120 s; 300 s for the month plan + work orders",
               client.model_name == os.environ["OPENAI_MODEL"] and client.temperature == 0
               and client.max_retries == quickicp.LLM_MAX_RETRIES == 2 and client.request_timeout == 120
               and real_llm(quickicp.LLM_TIMEOUT_LONG).request_timeout == 300)

        message = ""
        try:
            quickicp.ask_llm(Answer, "hello", "Fake step")
        except ToolError as error:
            message = str(error)
        expect("slow LLM: 1 try + 2 retries, then ONE line 'STOPPED: Fake step: the LLM did not answer within ...'",
               seen["requests"] == 1 + quickicp.LLM_MAX_RETRIES
               and message.startswith("STOPPED: Fake step: the LLM did not answer within")
               and "Nothing was saved for this step; run it again." in message)
        expect("slow LLM in a batch (work orders): STOPPED, nothing returned",
               raises(lambda: quickicp.ask_llm_batch(Answer, ["a", "b"], "Work orders")))
        judged = validator.judge_in_batches(Answer, ["a"])
        expect("slow LLM in the judge: the failed batch comes back as an error (-> 'review', map not half-saved)",
               len(judged) == 1 and isinstance(judged[0], Exception))

        seen["slow"], seen["bodies"] = False, []
        answer = quickicp.ask_llm(Answer, "hello", "Fake step")
        expect("fast fake LLM: the answer parses; the request has OPENAI_MODEL and temperature 0",
               answer.value == "ok" and seen["bodies"][-1].get("model") == os.environ["OPENAI_MODEL"]
               and seen["bodies"][-1].get("temperature") == 0)

        # Usage log: one line per call (no prompt text), 'n/a' when usage is missing, never stops a call
        def usage_lines():
            if not os.path.exists(quickicp.USAGE_FILE):
                return []
            with open(quickicp.USAGE_FILE, encoding="utf-8") as file:
                return [json.loads(line) for line in file]
        first = usage_lines()
        expect("usage log: one line for that call: name, model, input 11, cached input 7, output 3, seconds; "
               "no prompt text in the file",
               len(first) == 1 and first[0]["call"] == "Fake step" and first[0]["model"] == os.environ["OPENAI_MODEL"]
               and (first[0]["input_tokens"], first[0]["cached_input_tokens"], first[0]["output_tokens"]) == (11, 7, 3)
               and isinstance(first[0]["seconds"], float) and "hello" not in open(quickicp.USAGE_FILE, encoding="utf-8").read())
        quickicp.ask_llm_batch(Answer, ["hello a", "hello b"], "Work orders")
        expect("usage log: a batch of 2 -> 2 lines, one per call ('Work orders')",
               [line["call"] for line in usage_lines()] == ["Fake step", "Work orders", "Work orders"])
        seen["usage"] = False
        answer = quickicp.ask_llm(Answer, "hello", "No usage")
        expect("usage missing in the reply -> 'n/a' in the line; the answer is still returned",
               answer.value == "ok" and usage_lines()[-1]["input_tokens"] == "n/a"
               and usage_lines()[-1]["output_tokens"] == "n/a")
        seen["usage"] = True
        logged_file = quickicp.USAGE_FILE
        quickicp.USAGE_FILE = usage_folder                 # a folder: appending must fail
        answer = quickicp.ask_llm(Answer, "hello", "Broken log")
        quickicp.USAGE_FILE = logged_file
        expect("a usage log that cannot be written never stops the call (the answer is still returned)",
               answer.value == "ok" and len(usage_lines()) == 4)
        total = quickicp.USAGE_TOTAL
        expect("usage total counts every logged call; 'n/a' adds nothing; usage_summary() prints it",
               total["calls"] - before_total["calls"] == 5
               and total["input_tokens"] - before_total["input_tokens"] == 11 * 4
               and total["cached_input_tokens"] - before_total["cached_input_tokens"] == 7 * 4
               and quickicp.usage_summary().startswith("LLM usage total:"))

        # master_agent.main(): the plan step times out -> STOPPED; nothing archived, nothing written
        seen["slow"] = True
        archived, proofs = [], []
        plan_files = [os.path.join("data", n) for n in ("understanding.json", "revenue_analysis.json",
                                                         "month_plan.json", "work_orders.json", "plan_approval.json")]
        before = {p: os.path.getmtime(p) for p in plan_files if os.path.exists(p)}

        class SlowGraph:
            def invoke(self, *args, **kwargs):
                return master_agent.understand_document({"document_text": "test"})   # the real node -> slow LLM
        master_agent.graph = SlowGraph()
        master_agent.plan_approver = lambda: "Test Person"
        master_agent.check_document = lambda path: None
        master_agent.archive_plan_files = lambda *a, **k: archived.append(1)
        master_agent.write_proof = lambda *a, **k: proofs.append(1)
        message = ""
        try:
            master_agent.main("data/intake_document.txt")
        except ToolError as error:
            message = str(error)
        after = {p: os.path.getmtime(p) for p in plan_files if os.path.exists(p)}
        expect("master_agent: an LLM timeout -> 'STOPPED: Understanding: ...'; NOTHING archived or written "
               "(the old plan files stay in place)",
               message.startswith("STOPPED: Understanding: the LLM did not answer within")
               and archived == [] and proofs == [] and after == before)
        source = open(master_agent.__file__, encoding="utf-8").read()
        main_body = source[source.index("def main("):]
        expect("master_agent: the archive step comes AFTER approval (the resume loop), right before saving",
               main_body.index("archive_plan_files()") > main_body.index("ask_approval(errors)")
               and main_body.index("archive_plan_files()") < main_body.index("Saved {key} to {path}"))
    finally:
        quickicp.llm = real_llm
        quickicp.USAGE_FILE = real_usage_file
        shutil.rmtree(usage_folder, ignore_errors=True)
        for name, value in real_main_parts.items():
            setattr(master_agent, name, value)
        if saved_model is None:
            os.environ.pop("OPENAI_MODEL", None)
        else:
            os.environ["OPENAI_MODEL"] = saved_model
        server.shutdown()
        server.server_close()


def selftest() -> bool:
    """Fake platform data; no network, no LLM; files only in a throwaway folder."""
    project = "60d91130-c1fd-4d9f-b955-b121eb46480e"
    other = "11111111-2222-3333-4444-555555555555"

    def plan(plan_id, version, status, engagement=None, cycle=False):
        return {"id": plan_id, "project_id": project, "version": version, "status": status, "finished_at": "2026-10-09",
                "diagnosis": {"customer_name": "Anvika Ai",
                              "business_understanding": {"summary": "AI browser automation", "offerings": []},
                              "funnel_facts": [{"metric": "Order value", "value_low": 3000000, "value_high": 20000000,
                                                "unit": "INR", "source": "customer", "confidence": "medium"}]
                              + ([{"metric": "Sales cycle length", "value_low": 60, "value_high": 90, "unit": "days",
                                   "source": "customer", "confidence": "high"}] if cycle else []),
                              "capacity": {"summary": "3-4 new projects per month", "limits_volume_targets": False},
                              "data_gaps": [{"item": "Pricing position", "why_it_matters": "objections"}]},
                "strategy": {"engagement_period": engagement,
                             "priorities": [{"tier": "P1", "segment": "Large enterprises", "rationale": "best margin"}],
                             "geography": [{"region": "West India", "priority": "High", "clusters_public_data": "Mumbai, Pune",
                                            "relevant_demand": "manufacturing", "why": "HQs", "applies_to": "services"}]},
                "execution": {"channels": [{"channel": "Email outreach", "job": "first touch", "audience": "CIOs"}],
                              "qualification": {"questions": ["Do you use SAP?"], "grade_a": "budget + need",
                                                "grade_b": "need", "grade_c": "interest"},
                              "roadmap": [{"phase": "Phase 1", "months": "Month 1", "activities": ["build list"],
                                           "milestones": [{"milestone": "Accounts built", "metric": "accounts",
                                                           "value_low": 150, "value_high": 200, "basis": "motm_proposal"}]}]}}

    def item(code, category, question, answer=None, prefilled=None, position=0):
        return {"code": code, "category": category, "question": question, "answer": answer,
                "prefilled_answer": prefilled, "position": position, "checklist_id": "c-1"}

    tables = {
        PROJECTS: [{"id": project, "customer_name": "Anvika Ai", "engagement_type": "Full funnel", "status": "plan_ready"}],
        PLANS: [plan("p-1", 1, "ready"), plan("p-2", 2, "ready"), plan("p-3", 3, "blocked"),
                {**plan("p-x", 9, "ready"), "project_id": other}],
        CHECKLISTS: [{"id": "c-old", "project_id": project, "is_active": False},
                     {"id": "c-1", "project_id": project, "is_active": True, "status": "submitted", "submitted_at": "x"}],
        ITEMS: [item("Q-31", "Numbers for targets", "What is your main business goal for this engagement?", "Rs 2 crore", position=31),
                item("Q-32", "Numbers for targets", "What is the period for your main business goal?", "annual", position=32),
                item("Q-36", "Numbers for targets", "What is your typical sales cycle length (days from first enquiry to order)?",
                     None, "90 days", position=36),
                item("Q-14", "Ideal customers", "What is the minimum and maximum company size (by employee count or turnover)?",
                     "500+ employees", position=14)],
    }
    failures = 0

    def expect(label, condition):
        nonlocal failures
        failures += not condition
        print(f"  {'PASS' if condition else 'FAIL'}  {label}")

    def raises(function):
        try:
            function()
        except ToolError:
            return True
        return False

    print("Intake adapter selftest (fake read-only client, throwaway folder; no network, no LLM)")
    folder = tempfile.mkdtemp(prefix="intake_selftest_")
    saved = os.environ.get("REVENUEOS_PROJECT_ID")
    try:
        client = FakeClient(tables)
        result = run_intake(ReadOnly(client), project, "anvixa-ai-product", folder)
        doc, src = result["document"], result["source"]
        expect("the LATEST READY version is used (v2; v3 is blocked; another project's v9 ignored)",
               src["plan_id"] == "p-2" and src["plan_version"] == 2 and "plan p-2 version 2" in doc)
        expect("the ACTIVE checklist is used; source records project, plan, version, checklist, customer_key",
               src["checklist_id"] == "c-1" and src["project_id"] == project and src["customer_key"] == "anvixa-ai-product")
        expect("their name is shown but never used as the key",
               "Platform project name: Anvika Ai (not used as a key) | our CUSTOMER_KEY: anvixa-ai-product" in doc)
        expect("numbers copied exactly: 3000000 to 20000000 INR, milestone 150 to 200",
               "Order value: 3000000 to 20000000 INR" in doc and "accounts: 150 to 200" in doc)
        expect("revenue target + period from the checklist ANSWERS (Q-31, Q-32)",
               "Q-31" in doc and "Rs 2 crore" in doc and "Q-32" in doc and "revenue_target" in src["fields_from_checklist"])
        expect("sales cycle only prefilled -> used, marked '(prefilled, not confirmed by the customer)'",
               f"90 days {PREFILLED_NOTE}" in doc and "sales_cycle" in src["fields_from_checklist"])
        expect("company size from the checklist (Q-14)", "500+ employees" in doc and "company_size" in src["fields_from_checklist"])
        expect("engagement period nowhere -> a data gap for a human",
               src["fields_missing"] == ["engagement_period"]
               and "Engagement period: not in the platform plan or the checklist; ask the customer" in doc)
        expect("every section is there",
               all(f"== {t} ==" in doc for t in ("BUSINESS SUMMARY", "PRIORITY SEGMENTS", "GEOGRAPHIES",
                                                 "ECONOMICS / FUNNEL FACTS", "TARGETS AND ROADMAP", "CHANNELS (MOTM lead engine)",
                                                 "QUALIFICATION QUESTIONS", "LEAD GRADES", "FILLED CHECKLIST",
                                                 "DATA GAPS FOR A HUMAN")))
        expect("read-only: only SELECTs, zero insert / update / delete",
               client.write_attempts == [] and all(c[0] == "select" for c in client.calls))

        tables[PLANS].append(plan("p-4", 4, "ready", engagement="6 months", cycle=True))
        result = run_intake(ReadOnly(FakeClient(tables)), project, "anvixa-ai-product", folder)
        src = result["source"]
        expect("values IN the plan win: engagement period + sales cycle from plan v4",
               src["plan_version"] == 4 and sorted(src["fields_from_plan"]) == ["engagement_period", "sales_cycle"]
               and src["fields_missing"] == [] and "Sales cycle length: 60 to 90 days" in result["document"])
        expect("the older intake files were MOVED to data/archive/<time>-intake/, not overwritten",
               result["archived"] is not None and os.path.exists(os.path.join(result["archived"], DOC_NAME))
               and os.path.exists(os.path.join(result["archived"], SOURCE_NAME))
               and os.path.exists(os.path.join(result["archived"], NUMBERS_NAME))
               and result["numbers"]["aov_low_inr"] == 3_000_000 and "aov" in result["source"]["revenue_numbers_from_plan"])

        expect("no project -> STOPPED", raises(lambda: run_intake(ReadOnly(FakeClient({**tables, PROJECTS: []})),
                                                                   project, "k", folder)))
        expect("no READY plan -> STOPPED", raises(lambda: run_intake(
            ReadOnly(FakeClient({**tables, PLANS: [plan("p-3", 3, "blocked")]})), project, "k", folder)))
        os.environ.pop("REVENUEOS_PROJECT_ID", None)
        expect("REVENUEOS_PROJECT_ID missing -> STOPPED", raises(project_id))
        os.environ["REVENUEOS_PROJECT_ID"] = "Anvika Ai"
        expect("REVENUEOS_PROJECT_ID that is not an id (e.g. a name) -> STOPPED", raises(project_id))

        # master_agent.py: the --intake choice, tested WITHOUT running the brain (importing runs nothing)
        import master_agent
        expect("master_agent: no flag -> data/extracted.txt (fallback unchanged); --intake -> data/intake_document.txt",
               master_agent.choose_document(["master_agent.py"]) == "data/extracted.txt"
               and master_agent.choose_document(["master_agent.py", "--intake"]) == "data/intake_document.txt")
        expect("master_agent: --intake with no intake document -> STOPPED before planning",
               raises(lambda: master_agent.check_document(os.path.join(folder, "missing.txt"))))
        from types import SimpleNamespace
        from state_store import target_number

        def planned(task_id, agent, output, target):
            return SimpleNamespace(task_id=task_id, agent=agent, output_type=output, target=target)
        tasks = [planned("T3", "QuickICP", "accounts", "150-200 large enterprises"),
                 planned("T4", "QuickICP", "contacts", "Complete contact list for all target accounts"),
                 planned("T5", "Email Agent", "emails", "Email campaigns launched to all validated contacts"),
                 planned("T6", "Quickhowl", "calls", "Calls to 250-350 contacts"),
                 planned("T7", "Quickhowl", "calls", "no numeric target"),
                 planned("T1", "CAM", "coordination", "no numeric target"),
                 planned("T8", "Human Tele Calling", "qualified_leads", "contributes to enquiries over the month")]
        errors = master_agent.target_number_errors(tasks)
        flagged = [error.split(" ")[0] for error in errors]
        expect("plan rule: QuickICP accounts/contacts, emails, calls without a number -> T4, T5, T7 flagged; "
               "with a number (T3, T6) and non-tool tasks (CAM, tele-calling) are fine",
               flagged == ["T4", "T5", "T7"]
               and "T4 (QuickICP / contacts) target 'Complete contact list for all target accounts' has no number"
               in errors[0])
        expect("plan rule reads numbers like the runners: flagged <-> target_number() is None",
               all((t.task_id in flagged) == (target_number(t.target) is None)
                   for t in tasks if (t.agent, t.output_type) in master_agent.COUNTED_TOOL_TASKS))

        def share(task_id, agent, output, target, deps):
            return SimpleNamespace(task_id=task_id, agent=agent, output_type=output, target=target, depends_on=deps)
        shares = [planned("T3", "QuickICP", "accounts", "150-200 accounts"),
                  share("T4", "QuickICP", "contacts", "250-350 contacts", ["T3"]),
                  share("T5", "Email Agent", "emails", "Emails to 100% of validated contacts", ["T4"]),
                  share("T6", "Quickhowl", "calls", "Calls to 100% of T4 contacts", ["T4", "T5"]),
                  share("T7", "Quickhowl", "calls", "Calls to 100% of contacts", ["T4", "T5"]),
                  share("T8", "Email Agent", "emails", "150% of T4 contacts", ["T4"]),
                  share("T9", "Email Agent", "emails", "100% of T3 accounts", ["T4"])]
        share_errors = " | ".join(master_agent.target_number_errors(shares))
        expect("plan rule for % targets: one counted dependency (T5) or a NAMED dependency (T6) is fine; two "
               "counted dependencies and none named (T7), 150% (T8), a named task it does not depend on (T9) -> errors",
               "T5 " not in share_errors and "T6 " not in share_errors and "T7 (Quickhowl / calls)" in share_errors
               and "T8 (Email Agent / emails) target '150% of T4 contacts' is a share outside 1-100%" in share_errors
               and "T9 (Email Agent / emails)" in share_errors)
        expect("a % is a share, not a count: '100% of contacts' -> no count; '100% of 250 contacts' -> 250",
               target_number("100% of contacts") is None and target_number("100% of 250 contacts") == 250
               and target_number("150-200 accounts") == 200)

        # Plan rule: CAM data approval, Email Agent emails and Quickhowl calls list the contacts task DIRECTLY
        def task(task_id, agent, output, types, deps, week=1, target="no numeric target"):
            return SimpleNamespace(task_id=task_id, agent=agent, output_type=output, task_types=types,
                                   depends_on=deps, week=week, target=target)

        def plan_of(approval, calls, emails, contacts=("T4",)):
            """The r6 / r7 shape: T4 contacts, T5 data approval, T6 calls, T7 emails, with the given depends_on."""
            return [task("T1", "CAM", "coordination", ["requirements", "coordination"], []),
                    task("T2", "Content & Training", "client_training", ["client_training", "content"], ["T1"]),
                    task("T3", "QuickICP", "accounts", ["data_build"], ["T1"], target="150-200 accounts"),
                    *[task(c, "QuickICP", "contacts", ["data_build"], ["T3"], target="250-350 contacts") for c in contacts],
                    task("T5", "CAM", "coordination", ["data_approval", "coordination"], approval),
                    task("T6", "Quickhowl", "calls", ["outreach"], calls, week=2, target="Calls to 250-350 contacts"),
                    task("T7", "Email Agent", "emails", ["outreach"], emails, week=2, target="Emails to 250-350 contacts")]

        def flagged(tasks):
            return [e.split(" ")[0] for e in master_agent.contacts_dependency_errors(tasks)]
        r6 = plan_of(["T2"], ["T4", "T5"], ["T6"])                     # anvixa r6: T5 on T2 only, T7 on T6 only
        r6_errors = master_agent.contacts_dependency_errors(r6)
        expect("plan rule (direct contacts dependency), r6 shape: T5 data approval (on T2 only) and T7 emails (on T6 "
               "only) flagged; T6 calls (lists T4) fine; each error names T4 and says to add it",
               flagged(r6) == ["T5", "T7"]
               and r6_errors[0] == "T5 (CAM / data_approval) must list the QuickICP contacts task T4 directly in "
                                   "depends_on (it approves those contacts); it now depends on T2 only. Add T4 to its "
                                   "depends_on."
               and r6_errors[1].startswith("T7 (Email Agent / emails) must list the QuickICP contacts task T4 directly"))
        r7 = plan_of(["T4"], ["T5", "T2"], ["T6"])                     # anvixa r7: calls after approval, emails after calls
        expect("plan rule, r7 shape: data approval lists the contacts task (fine); calls on the approval only and "
               "emails on the calls only -> both flagged (strict: reaching it through another task is not enough)",
               flagged(r7) == ["T6", "T7"])
        good = plan_of(["T4"], ["T4", "T5"], ["T4", "T6"])
        expect("plan rule: a correct plan (approval, calls and emails each list T4) -> no error",
               master_agent.contacts_dependency_errors(good) == [])
        two = plan_of(["T9"], ["T4", "T5"], ["T9", "T6"], contacts=("T4", "T9"))
        two_bad = plan_of(["T2"], ["T4"], ["T6"], contacts=("T4", "T9"))
        expect("plan rule with two contacts tasks: either one is enough; the error names both",
               flagged(two) == [] and "the QuickICP contacts task one of T4, T9 directly" in
               master_agent.contacts_dependency_errors(two_bad)[0])
        none = [t for t in plan_of(["T2"], ["T5"], ["T6"]) if t.task_id != "T4"]
        expect("plan rule with NO contacts task: data approval -> 'needs a QuickICP contacts task to approve'; calls / "
               "emails only get the existing 'no earlier QuickICP task outputs contacts' error (not twice)",
               master_agent.contacts_dependency_errors(none)
               == ["T5 (CAM / data_approval) needs a QuickICP contacts task to approve: add one and list it in depends_on."])

        # The same rule inside validate_plan, and its text in the RETRY prompt (fake LLM: no network, no model)
        state = {"month_plan": SimpleNamespace(tasks=r6), "engagement_settings": {},
                 "understanding": SimpleNamespace(motm_channels=[], model_dump_json=lambda indent=2: "{}"),
                 "revenue_analysis": SimpleNamespace(model_dump_json=lambda indent=2: "{}"),
                 "motm_context": "selftest", "plan_feedback": ""}
        with contextlib.redirect_stdout(io.StringIO()):
            checked = master_agent.validate_plan(state)
        prompts, real_ask = [], master_agent.ask_llm
        master_agent.ask_llm = lambda schema, prompt, name, timeout=None: prompts.append(prompt) or SimpleNamespace(tasks=[])
        try:
            master_agent.plan_month({**state, "plan_errors": checked["plan_errors"]})
        finally:
            master_agent.ask_llm = real_ask
        retry = prompts[0].split("Your last plan failed these checks. Fix all of them:\n", 1)[-1] if prompts else ""
        expect("validate_plan reports both r6 errors; the retry prompt (fake LLM) sends exactly that text, and the "
               "planning rules tell the brain the rule up front",
               r6_errors[0] in checked["plan_errors"] and r6_errors[1] in checked["plan_errors"]
               and r6_errors[0] in retry and r6_errors[1] in retry
               and "Quickhowl calls tasks list the QuickICP contacts task directly in depends_on" in prompts[0])
        revenue_selftest(expect, folder)
        llm_selftest(expect, raises)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        if saved is None:
            os.environ.pop("REVENUEOS_PROJECT_ID", None)
        else:
            os.environ["REVENUEOS_PROJECT_ID"] = saved
    print("INTAKE ADAPTER SELFTEST OK" if not failures else f"INTAKE ADAPTER SELFTEST FAILED ({failures})")
    return not failures


if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        command = sys.argv[1] if len(sys.argv) > 1 else "run"
        try:
            if command == "selftest":
                sys.exit(0 if selftest() else 1)
            elif command == "run":
                main()
            else:
                print("Usage: python intake_adapter.py [run | selftest]")
                sys.exit(1)
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli)
