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
                                document_sha256}: exactly which plan we used

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

import hashlib
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
    present = [n for n in (DOC_NAME, SOURCE_NAME) if os.path.exists(os.path.join(data_dir, n))]
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
    source = {
        "project_id": data["project"]["id"], "plan_id": data["plan"]["id"], "plan_version": data["plan"]["version"],
        "plan_finished_at": data["plan"].get("finished_at"),
        "checklist_id": (data["checklist"] or {}).get("id"), "read_at": read_at, "customer_key": customer_key,
        "platform_customer_name": data["project"]["customer_name"],
        "fields_from_plan": [k for k, f in fields.items() if f["source"] == "plan"],
        "fields_from_checklist": [k for k, f in fields.items() if f["source"] == "checklist"],
        "fields_missing": [k for k, f in fields.items() if f["source"] == "missing"],
        "document_sha256": hashlib.sha256(document.encode("utf-8")).hexdigest(),
    }
    archived = archive_old(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, DOC_NAME), "w", encoding="utf-8") as file:
        file.write(document)
    with open(os.path.join(data_dir, SOURCE_NAME), "w", encoding="utf-8") as file:
        json.dump(source, file, indent=2, ensure_ascii=False)
    return {"source": source, "document": document, "archived": archived}


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
               and os.path.exists(os.path.join(result["archived"], SOURCE_NAME)))

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
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        if saved is None:
            os.environ.pop("REVENUEOS_PROJECT_ID", None)
        else:
            os.environ["REVENUEOS_PROJECT_ID"] = saved
    print("INTAKE ADAPTER SELFTEST OK" if not failures else f"INTAKE ADAPTER SELFTEST FAILED ({failures})")
    return not failures


if __name__ == "__main__":
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
