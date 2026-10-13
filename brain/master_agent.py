"""
master_agent.py
===============

The brain: reads the customer's strategy document, understands it, analyses revenue,
plans Month 1, asks a human to approve, then writes work orders and the approval proof.

FLOW
----
    read_document -> understand_document -> analyze_revenue -> plan_month -> validate_plan
        (code checks the plan; up to 3 attempts) -> human_review -> create_work_orders

APPROVAL (code decides, see plan_approval.py)
--------
    no rule errors   'yes' approves
    rule errors      'yes' is REFUSED; type changes, or 'override' + a required reason
    other text       feedback: the brain re-plans ('plan: ...' / 'analysis: ...')
    Who approves: PLAN_APPROVER in .env (required; checked BEFORE planning starts).

READS
-----
    data/extracted.txt            the customer's strategy document (text; default, from extract_html.py)
    data/intake_document.txt      with --intake: the platform plan as a document (from intake_adapter.py)
    data/intake_numbers.json      with --intake: revenue numbers taken by CODE from the platform plan (AOV,
                                  close rates, capacity, sales cycle, valid enquiries). They WIN over the
                                  LLM's numbers (settle_revenue_numbers); any other LLM number needs a quote
                                  found word for word in the document, else it is removed and listed as missing.
    knowledge/motm_context.md     MOTM context
    .env                          OPENAI_API_KEY, OPENAI_MODEL, PLAN_APPROVER

WRITES (old versions are MOVED, never deleted, to data/archive/<YYYYMMDD-HHMMSS>/ only AFTER approval
        and the work orders, just before the new files are saved; a stop or LLM timeout before that
        leaves the old plan files in place)
------
    data/understanding.json, data/revenue_analysis.json, data/month_plan.json
    data/work_orders.json         approved work orders (one per plan task)
    data/plan_approval.json       approval proof: approved_at, approved_by, answer, overridden,
                                  override_reason, validation_errors_at_approval, fingerprints
                                  of work_orders / month_plan / revenue_analysis

RUN
---
    python master_agent.py        uses LLM calls; after approval the current plan files are moved to data/archive/
    Importing this file runs nothing (the run is inside main()).
    LLM: quickicp.ask_llm / ask_llm_batch (OPENAI_MODEL, temperature 0, 2 retries): 120 s per attempt,
    300 s for the month plan and the work orders.

DEBUGGING
---------
    "STOPPED: PLAN_APPROVER is missing"   -> add PLAN_APPROVER=<your name> to .env
    "N plan rule(s) are still broken"     -> give changes, or type 'override' on purpose
    "STOPPED: Month plan: the LLM did not answer within 300 s (2 retries)" -> nothing was saved or moved;
        run again (check the internet connection / OpenAI status)
    Validation errors are printed per attempt ("Validation attempt N: ...").
    "T4 (QuickICP / contacts) target '...' has no number" -> a QuickICP accounts/contacts, Email Agent emails
        or Quickhowl calls task needs a count in its target (the runners stop without one); the brain
        gets the error and fixes the plan before approval (up to 3 attempts)
    Next step after approval: python orchestrator.py load <YYYY-MM>
"""

import logging
import json
import os
import re
import sys
import warnings
from typing import TypedDict, Literal

from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from plan_approval import (APPROVAL_FILE, archive_plan_files, ask_approval, describe, plan_approver,
                           write_proof)
from quickicp import (COUNTED_TOOL_TASKS, LLM_TIMEOUT_LONG, ToolError, ask_llm, ask_llm_batch, count_in,
                      percent_base, percent_of, usage_summary)
import platform_approval

load_dotenv()

warnings.filterwarnings("ignore", message=".*Deserializing unregistered type.*")
logging.getLogger("langgraph").setLevel(logging.ERROR)

MOTM_CONTEXT_PATH = "knowledge/motm_context.md"


# ================= 1. UNDERSTANDING: what the document says =================

class PrioritySegment(BaseModel):
    tier: str = Field(description="P1, P2 or P3")
    segment: str
    reason: str


class Target(BaseModel):
    milestone: str
    target: str
    period: str


class PhaseActivities(BaseModel):
    phase: str
    period: str
    activities: list[str]


class Geography(BaseModel):
    region: str
    priority: str = Field(description="High, Medium or Test, exactly as written")
    clusters: list[str] = Field(
        description="Each named industrial belt, state or city exactly as written, "
                    "e.g. 'Mumbai–Pune industrial belt', 'Ankleshwar', 'Dahej'. Do not include 'public_data_to_verify'."
    )
    relevant_demand: list[str] = Field(description="Industries present in these clusters")
    reason: str
    needs_verification: bool = Field(
        description="True if the document marks these clusters as public data to verify"
    )


class FunnelFact(BaseModel):
    metric: str
    value: str = Field(description="Exactly as written; empty string if the document leaves it blank")
    unit: str
    source: str
    confidence: str


class Understanding(BaseModel):
    customer_name: str
    business_summary: str
    priority_segments: list[PrioritySegment]
    geographies: list[Geography]
    current_lead_sources: list[str] = Field(
        description="How the customer sells today, before MOTM (e.g. referrals, tenders)"
    )
    motm_channels: list[str] = Field(
        description="Only the channels MOTM will run, from the Lead engine section"
    )
    targets: list[Target]
    roadmap_activities: list[PhaseActivities] = Field(
        description="Every roadmap phase with its listed activities, exactly as written"
    )
    economics: list[FunnelFact] = Field(
        description="Every customer number on order value, conversion rates (inbound and outbound), sales cycle, "
                    "approval layers, follow-ups, repeat orders, margin and capacity, exactly as written. "
                    "Check both the diagnosis cards and the funnel facts table."
    )
    customer_resources: list[str] = Field(
        description="What the customer has that MOTM can use: technical team, alliances, proof assets, "
                    "support, references, existing relationships"
    )
    customer_constraints: list[str] = Field(
        description="Customer-side limits: capacity, response time, approval layers, price sensitivity"
    )
    qualification_questions: list[str]
    grade_rules: list[str] = Field(description="Meaning of each lead grade (A, B, C) exactly as written")
    customer_inputs_needed: list[str] = Field(
        description="What MOTM needs from the customer: proof assets, approvals, operational support"
    )
    customer_responsibilities: list[str]
    data_gaps: list[str]


# ================= 2. REVENUE ANALYSIS =================

class NumberSource(BaseModel):
    field: str = Field(description="The field name, e.g. 'qualified_opportunities_low' or 'aov_low_inr'")
    quote: str = Field(description="The exact sentence from the source document the number comes from")


class RevenueInputs(BaseModel):
    revenue_goal: str = Field(description="The customer's revenue or growth goal as written; 'not stated' if absent")
    aov_as_written: str = Field(description="Average order value exactly as written, e.g. '₹30 lakh to ₹2 crore'; "
                                            "empty if not given")
    aov_low_inr: float | None = Field(description="Lower AOV in rupees. 1 lakh = 100000, 1 crore = 10000000. "
                                                  "Null if not given")
    aov_high_inr: float | None = Field(description="Upper AOV in rupees (same as low if a single value). "
                                                   "Null if not given")
    outbound_close_rate_low_pct: float | None = Field(
        description="Outbound (or overall, if no split) enquiry-to-order rate, lower bound. Null if not given")
    outbound_close_rate_high_pct: float | None = Field(
        description="Upper bound (same as low if a single value). Null if not given")
    inbound_close_rate_pct: float | None = Field(description="Inbound rate if the document gives one; otherwise null")
    capacity_per_month: str = Field(description="Customer delivery capacity exactly as written; 'not stated' if absent")
    sales_cycle_weeks: float | None = Field(description="Only if the document gives a number; otherwise null")
    qualified_opportunities_low: int | None = Field(
        description="Lower bound of the roadmap target the close rate applies to (for an enquiry-to-order rate: "
                    "valid enquiries; never prospects, pipeline opportunities or RFQs)")
    qualified_opportunities_high: int | None = Field(description="Upper bound of that same target")
    opportunities_period: str = Field(description="Period of that target, e.g. 'Month 1–3'")
    customer_resources_to_use: list[str] = Field(description="Which customer resources MOTM should use, and how")
    revenue_levers: list[str] = Field(
        description="Actions most likely to increase revenue for this customer, given MOTM's process and limitations"
    )
    revenue_risks: list[str]
    missing_for_revenue_forecast: list[str]
    number_sources: list[NumberSource] = Field(
        description="For EVERY number you fill: the field name and the exact sentence from the source document")


class RevenueAnalysis(RevenueInputs):
    expected_orders_low: float | None
    expected_orders_high: float | None
    expected_revenue_low_inr: float | None
    expected_revenue_high_inr: float | None
    forecast_note: str
    numbers_source: dict[str, str] = {}          # per number group: 'platform plan: ...' or 'LLM, quoted: ...'


# ================= REVENUE NUMBERS: code decides =================

# Number groups: the platform plan's values win; an LLM value must carry a quote found in the document.
QUOTE_GROUPS = {
    "aov": ("aov_low_inr", "aov_high_inr"),
    "outbound_close_rate": ("outbound_close_rate_low_pct", "outbound_close_rate_high_pct"),
    "inbound_close_rate": ("inbound_close_rate_pct",),
    "sales_cycle_weeks": ("sales_cycle_weeks",),
    "qualified_opportunities": ("qualified_opportunities_low", "qualified_opportunities_high"),
}
PLAN_TEXT_FIELDS = ("aov_as_written", "capacity_per_month", "opportunities_period")


def plan_numbers(document_path: str) -> dict | None:
    """data/intake_numbers.json next to the intake document (master_agent --intake); None for the fallback."""
    if not document_path.endswith("intake_document.txt"):
        return None
    path = os.path.join(os.path.dirname(document_path), "intake_numbers.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def squash(text: str | None) -> str:
    """Lowercase, single spaces: a quote matches the document regardless of line breaks or case."""
    return " ".join((text or "").lower().split())


def settle_revenue_numbers(inputs: RevenueInputs, plan: dict | None,
                           document_text: str) -> tuple[RevenueInputs, dict[str, str]]:
    """
    Code decides the revenue numbers (same input -> same numbers):
        1. a number group in the platform plan (intake_numbers.json) -> that value, whatever the LLM said
        2. else an LLM value is kept ONLY with a number_sources quote found word for word in the document;
           otherwise it is removed and listed in missing_for_revenue_forecast
    Returns (settled inputs, {group: where the number came from}).
    """
    plan = plan or {}
    plan_sources = plan.get("sources", {})
    values = inputs.model_dump()
    quotes: dict[str, list[str]] = {}
    for item in inputs.number_sources:
        quotes.setdefault(item.field.strip(), []).append(item.quote)
    document = squash(document_text)
    missing, settled = list(values["missing_for_revenue_forecast"]), {}
    for group, fields in QUOTE_GROUPS.items():
        if group in plan_sources:
            for field in fields:
                if field in plan:
                    values[field] = plan[field]
            settled[group] = f"platform plan: {plan_sources[group]}"
            continue
        if all(values[field] is None for field in fields):
            continue
        given = [quote for name in (group, *fields) for quote in quotes.get(name, [])]
        found = next((quote for quote in given if squash(quote) and squash(quote) in document), None)
        if found:
            settled[group] = f"LLM, quoted: '{found.strip()}'"
        else:
            for field in fields:
                values[field] = None
            missing.append(f"{group}: the LLM gave a number without a source quote found in the document "
                           "(removed by code)")
    for field in PLAN_TEXT_FIELDS:
        if field in plan:
            values[field] = plan[field]
    if "capacity_per_month" in plan_sources:
        settled["capacity_per_month"] = f"platform plan: {plan_sources['capacity_per_month']}"
    values["missing_for_revenue_forecast"] = missing
    return RevenueInputs(**values), settled


# ================= 3. AGENT CATALOGUE: who exists and what each returns =================

AgentName = Literal[
    "CAM",
    "Content & Training",
    "QuickICP",
    "Quickhowl",
    "Human Tele Calling",
    "Sales Engineer",
    "Human Field",
    "Email Agent",
    "WhatsApp Agent",
    "LinkedIn Agent",
    "Digital Visibility",
    "PSEO",
]

AGENT_CATALOG = {
    "CAM": {
        "requires": None,
        "does": "Key Account Executive: requirements, client coordination, approvals, team allocation, "
                "handover coordination, MIS and weekly reviews",
        "outputs": {
            "coordination": ["summary", "decisions", "approvals (item, status, approved_by, date)",
                             "pending_customer_inputs", "action_items (item, owner, due_date)"],
            "review": ["meeting_date", "attendees", "pipeline_summary", "kpi_status", "decisions",
                       "action_items (item, owner, due_date)"],
            "handover": ["opportunity_id", "lead_id", "handed_over_at",
                         "meeting_type (technical_discussion/site_visit)", "meeting_date",
                         "customer_owner", "customer_feedback_status"],
        },
    },
    "Content & Training": {
        "requires": None,
        "does": "Takes client product training; prepares scripts, email/LinkedIn content and AI calling "
                "flows; trains and assesses callers",
        "outputs": {
            "client_training": ["training_date", "trainer", "topics_covered", "faqs", "objections",
                                "knowledge_check_passed (yes/no)"],
            "assets": ["asset_refs (type, ref, version)", "approved_by"],
            "assessments": ["caller_name", "module_version", "score", "readiness (pass/fail)", "coaching_needed"],
        },
    },
    "QuickICP": {
        "requires": None,
        "does": "Data research: verify clusters, map and prioritise accounts, build and validate decision-maker contacts",
        "outputs": {
            "accounts": ["company_name", "website_domain", "industry", "segment", "tier", "cluster", "city", "state",
                         "company_size", "priority_rank", "fit_reason", "source", "cluster_verified (yes/no)"],
            "contacts": ["company_name", "website_domain", "contact_name", "designation", "buying_role", "phone",
                         "email", "linkedin_url", "source", "verified_status"],
        },
    },
    "Quickhowl": {
        "requires": "ai_calling_enabled",
        "does": "First-touch AI calls: introduce, check interest, capture callbacks. Never grades A/B/C",
        "outputs": {
            "calls": ["call_id", "contact_name", "company_name", "phone", "called_at",
                      "call_status (connected/no_answer/busy/wrong_number/not_reachable)",
                      "outcome (interested/callback/not_interested/technical_question)", "callback_at",
                      "summary", "recording_url", "transcript_url", "next_action"],
        },
    },
    "Human Tele Calling": {
        "requires": None,
        "does": "Human calls: first-touch when AI calling is off, follow-ups, qualification and A/B/C grading",
        "outputs": {
            "calls": ["call_id", "contact_name", "company_name", "phone", "called_at", "call_status", "outcome",
                      "callback_at", "summary", "next_action"],
            "qualified_leads": ["lead_id", "contact_name", "company_name", "phone", "email", "qualification_answers",
                                "trigger_confirmed (yes/no, type)", "decision_maker_engaged (yes/no)",
                                "next_step_agreed (yes/no, what)", "grade (A/B/C)", "grade_reason",
                                "next_action", "next_action_date"],
        },
    },
    "Sales Engineer": {
        "requires": None,
        "does": "Shared Sales Engineer: technical follow-up and conversion support on qualified leads. "
                "Limited capacity",
        "outputs": {
            "opportunities": ["opportunity_id", "lead_id", "company_name", "requirement_summary",
                              "stage (qualified/meeting/proposal/negotiation/won/lost)", "expected_value_inr",
                              "next_action", "next_action_date"],
        },
    },
    "Human Field": {
        "requires": None,
        "does": "Field visits supporting qualification and technical discussions",
        "outputs": {
            "visits": ["visit_id", "company_name", "visit_date", "attendees", "findings", "next_action"],
        },
    },
    "Email Agent": {
        "requires": None,
        "does": "Email campaigns and follow-up sequences",
        "outputs": {
            "emails": ["message_id", "contact_name", "email", "sent_at", "delivery_status (delivered/bounced)",
                       "opened (yes/no)", "reply_text",
                       "reply_type (interested/not_now/unsubscribe/wrong_person/out_of_office)", "next_action"],
        },
    },
    "WhatsApp Agent": {
        "requires": "whatsapp_enabled",
        "does": "WhatsApp follow-ups with approved templates",
        "outputs": {
            "messages": ["message_id", "contact_name", "phone", "sent_at", "template", "delivery_status",
                         "reply_text", "reply_type", "next_action"],
        },
    },
    "LinkedIn Agent": {
        "requires": None,
        "does": "LinkedIn outreach, direct messages and authority content posts",
        "outputs": {
            "outreach": ["contact_name", "linkedin_url", "connection_status", "message_reply", "interest (yes/no)"],
            "posts": ["post_url", "published_at", "post_engagement (views, reactions, comments)"],
        },
    },
    "Digital Visibility": {
        "requires": None,
        "does": "Website and LinkedIn audits, authority content, SEO",
        "outputs": {
            "audit": ["findings", "upgrade_plan_items (item, priority, owner)"],
            "published": ["published_items (url, date, type)"],
        },
    },
    "PSEO": {
        "requires": "pseo_enabled",
        "does": "Programmatic SEO pages with forms that create inbound leads",
        "outputs": {
            "pages": ["page_id", "url", "keyword", "city", "publish_status", "form_present (yes/no)"],
        },
    },
}


def enabled_agents(settings: dict) -> dict:
    return {name: info for name, info in AGENT_CATALOG.items()
            if info["requires"] is None or settings.get(info["requires"], False)}


def catalog_text(settings: dict) -> str:
    lines = []
    for name, info in enabled_agents(settings).items():
        outputs = " / ".join(f"{output}: {', '.join(fields)}" for output, fields in info["outputs"].items())
        lines.append(f"- {name}: {info['does']} | output types -> {outputs}")
    return "\n".join(lines)


# ================= 4. PLAN AND WORK ORDERS =================

TaskType = Literal[
    "requirements", "client_training", "team_allocation", "data_build", "data_approval", "content",
    "readiness", "outreach", "qualification", "handover", "conversion_support", "digital",
    "tracking", "review", "optimization", "coordination",
]


class PlannedTask(BaseModel):
    task_id: str = Field(description="Short id like T1, T2, T3")
    depends_on: list[str] = Field(description="task_ids that must finish first; empty if none")
    week: int = Field(description="Week number in the month, 1 to 4")
    agent: AgentName
    task_types: list[TaskType] = Field(
        description="All kinds of work this task covers, e.g. ['client_training', 'content']"
    )
    output_type: str = Field(description="Exactly one output type name of this agent, e.g. 'coordination'")
    task: str
    target: str = Field(
        description="Quantity from the understanding, 'contributes to X over <period>', or 'no numeric target'"
    )
    tier: str = Field(description="P1, P2, P3 or All")
    geography: str = Field(
        description="Region plus its specific clusters for place-based tasks; 'All' for tasks not about a place"
    )
    revenue_link: str = Field(description="How this task moves the customer toward revenue")
    reason: str


class MonthPlan(BaseModel):
    month: str
    objectives: list[str]
    tasks: list[PlannedTask]
    kpis_to_track: list[str] = Field(description="Leading and revenue indicators, from contact to order")
    optimization_rules: list[str] = Field(
        description="'If <signal> then <change>' rules for improving the plan, using document numbers where they exist"
    )
    review_notes: list[str] = Field(
        description="Notes for the human reviewer: assumptions, trade-offs, workload warnings, and any requested "
                    "change that conflicts with the rules and how it was handled"
    )
    blocked_items: list[str] = Field(description="Only data gaps that cannot be planned")


class WorkOrderDetails(BaseModel):
    task_id: str
    instructions: list[str] = Field(description="Clear steps the agent must follow to produce its fixed output fields")
    inputs_needed: list[str] = Field(
        description="Data, assets or approvals needed before starting, naming the task that provides each"
    )
    done_when: str = Field(description="How we know the task is complete and correct")
    escalate_if: str = Field(description="When to stop and escalate, and to whom")


class WorkOrder(BaseModel):
    work_order_id: str
    task_id: str
    customer: str
    agent: AgentName
    task_type: str
    output_type: str
    week: int
    depends_on: list[str]
    target: str
    tier: str
    geography: str
    revenue_link: str
    instructions: list[str]
    inputs_needed: list[str]
    expected_output: list[str]
    done_when: str
    escalate_if: str


# ================= 5. STATE =================

class MasterState(TypedDict):
    document_path: str
    engagement_settings: dict
    motm_context: str
    document_text: str
    understanding: Understanding
    revenue_analysis: RevenueAnalysis
    month_plan: MonthPlan
    plan_errors: list[str]
    plan_attempts: int
    user_feedback: str
    analysis_feedback: str
    plan_feedback: str
    plan_approved: bool
    work_orders: list[WorkOrder]


def motm_block(state: MasterState) -> str:
    return "MOTM operating context (always follow this; it is how MOTM works):\n" + state["motm_context"]


# ================= 6. NODES =================

def read_document(state: MasterState) -> dict:
    with open(state["document_path"], "r", encoding="utf-8") as file:
        text = file.read()
    with open(MOTM_CONTEXT_PATH, "r", encoding="utf-8") as file:
        motm_context = file.read()
    print("Read customer document characters:", len(text))
    print("Read MOTM context characters:", len(motm_context))
    return {"document_text": text, "motm_context": motm_context}


def understand_document(state: MasterState) -> dict:
    prompt = (
        "You are the Master Agent of MOTM Revenue OS. "
        "Read this approved go-to-market strategy and extract the facts exactly as written. "
        "Do not invent numbers. If something is missing, list it in data_gaps.\n\n"
        + state["document_text"]
    )
    return {"understanding": ask_llm(Understanding, prompt, "Understanding")}


def analyze_revenue(state: MasterState) -> dict:
    parts = [
        "You are the revenue analyst inside the Master Agent of MOTM Revenue OS. "
        "MOTM's end goal is revenue for the customer, not just leads.",
        motm_block(state),
        "Analyse the customer's data:\n"
        "- Extract revenue inputs as numbers only when the document gives them; otherwise null. Never guess numbers.\n"
        "- Convert Indian units exactly: 1 lakh = 100000, 1 crore = 10000000. Keep the original text in "
        "aov_as_written.\n"
        "- If the document gives separate inbound and outbound conversion rates, put them in the matching fields. "
        "MOTM's work is outbound.\n"
        "- For qualified opportunities, use the roadmap target that the close rate applies to (for an "
        "enquiry-to-order rate: valid enquiries; never prospects, pipeline opportunities or RFQs) and state its period.\n"
        "- For EVERY number you fill, add an entry to number_sources: the field name and the exact sentence from the "
        "source document it comes from. A number without a matching quote is removed.\n"
        "- List which customer resources MOTM should use, and how.\n"
        "- List the revenue levers that will most increase this customer's revenue, considering MOTM's "
        "limitations (lead quality, sales capacity, customer delivery capacity, client response time, "
        "technical training, channel limits). Levers are about the customer's business, never about plan "
        "scheduling or which week something starts.\n"
        "- List revenue risks and what is missing for a revenue forecast.",
        "Customer understanding:\n" + state["understanding"].model_dump_json(indent=2),
        "Source document:\n" + state["document_text"],
    ]

    analysis_fb = state.get("analysis_feedback", "")
    previous = state.get("revenue_analysis")
    if analysis_fb and previous:
        parts.append("Your previous analysis:\n" + previous.model_dump_json(indent=2))
        parts.append("The user asked for these corrections to the analysis. Apply them:\n" + analysis_fb)

    inputs = ask_llm(RevenueInputs, "\n\n".join(parts), "Revenue analysis")
    # Code decides the numbers: the platform plan's numbers win; an LLM number needs a quote from the document.
    inputs, numbers_source = settle_revenue_numbers(inputs, plan_numbers(state["document_path"]), state["document_text"])

    orders_low = orders_high = revenue_low = revenue_high = None
    opp_low, opp_high = inputs.qualified_opportunities_low, inputs.qualified_opportunities_high
    rate_low = inputs.outbound_close_rate_low_pct
    rate_high = inputs.outbound_close_rate_high_pct if inputs.outbound_close_rate_high_pct is not None else rate_low
    aov_low = inputs.aov_low_inr
    aov_high = inputs.aov_high_inr if inputs.aov_high_inr is not None else aov_low

    if rate_low is not None and opp_low is not None:
        orders_low = round(opp_low * rate_low / 100, 1)
    if rate_high is not None and opp_high is not None:
        orders_high = round(opp_high * rate_high / 100, 1)

    if aov_low is not None and orders_low is not None:
        revenue_low = orders_low * aov_low
    if aov_high is not None and orders_high is not None:
        revenue_high = orders_high * aov_high

    if aov_low is None:
        note = "Revenue forecast blocked: average order value is missing. The plan must include a task to obtain it."
    elif orders_low is None:
        note = "Revenue forecast blocked: close rate or opportunity target is missing."
    else:
        note = (f"Forecast over {inputs.opportunities_period}: qualified opportunities x outbound close rate x AOV "
                f"(AOV as written: {inputs.aov_as_written}). A wide AOV range makes the revenue range wide.")
    platform = sorted(group for group, source in numbers_source.items() if source.startswith("platform plan"))
    if platform:
        note += f" Numbers taken by code from the platform plan: {', '.join(platform)}."

    analysis = RevenueAnalysis(
        **inputs.model_dump(),
        expected_orders_low=orders_low,
        expected_orders_high=orders_high,
        expected_revenue_low_inr=revenue_low,
        expected_revenue_high_inr=revenue_high,
        forecast_note=note,
        numbers_source=numbers_source,
    )
    return {"revenue_analysis": analysis}


def plan_month(state: MasterState) -> dict:
    settings = state["engagement_settings"]

    parts = [
        "You are the Master Agent of MOTM Revenue OS.",
        motm_block(state),
        "Your end goal is revenue for the customer. Leads matter only when they become qualified opportunities "
        "and orders. Create the Month 1 plan that moves this customer toward revenue as fast and reliably as possible.",
        "Decide the tasks yourself: how many, what they are, their order, weeks and dependencies.",
        "Rules:\n"
        "- Assign every task to one of the available agents, give it all its task_types, and set output_type to "
        "exactly one output type name of that agent (e.g. 'coordination', not 'coordination: summary').\n"
        "- Each task has exactly one output type. If work produces two kinds of output (e.g. accounts and "
        "contacts), split it into two tasks.\n"
        "- Use every channel in the customer's lead engine unless the user removes one.\n"
        "- Include team allocation, and a readiness check of callers before human qualification.\n"
        "- Follow MOTM's process order from the operating context.\n"
        "- A task can never be in an earlier week than any task it depends on.\n"
        "- Every task must state its revenue_link. Prioritise the revenue levers from the revenue analysis.\n"
        "- Start with P1 segments and High priority geographies. Add other tiers or regions only if the user asks.\n"
        "- Place-based tasks must name the specific clusters or cities. Use 'All' for tasks not about a place.\n"
        "- Match lead volume to follow-up capacity (shared Sales Engineer, customer response time) and to the "
        "customer's delivery capacity. Do not plan more opportunities than can be followed up and delivered.\n"
        "- If average order value or other revenue inputs are missing, include a task to obtain them.\n"
        "- Use only targets from the understanding. Never invent numbers, day-level deadlines or customer "
        "commitments. Never mention a specific week or day in a target (no 'in Week 1', 'by Week 2', "
        "'end of week', 'within week'); the week field already says when.\n"
        "- A target whose period spans several months must be written as 'contributes to X over <period>'.\n"
        "- Outreach must come after data_build, data_approval, content and client_training. "
        "Qualification must come after outreach and readiness. Handover must come after qualification.\n"
        "- Only recurring weekly meetings get the 'review' type, and they start in week 1.\n"
        "- Never assign customer responsibilities (proposals, pricing, closing) to MOTM agents.\n"
        "- kpis_to_track must cover the full path from contact to order and revenue.\n"
        "- optimization_rules must be 'If <signal> then <change>', using document numbers where they exist.\n"
        "- When the user asks to start something earlier, first move its prerequisite tasks earlier too. "
        "Only if that is impossible, keep it later and explain why in review_notes.\n"
        "- If a user change conflicts with these rules, do not break the rule: plan the closest safe version "
        "and explain it in review_notes.\n"
        "- If the user asks for something that makes a week unrealistic, keep it safe and warn about the "
        "workload in review_notes.\n"
        "- blocked_items is only for data gaps that cannot be planned.",
        "Available agents:\n" + catalog_text(settings),
        "Engagement settings (decided by MOTM, follow them exactly):\n" + str(settings),
        "Revenue analysis:\n" + state["revenue_analysis"].model_dump_json(indent=2),
        "Customer understanding:\n" + state["understanding"].model_dump_json(indent=2),
    ]

    plan_fb = state.get("plan_feedback", "")
    analysis_fb = state.get("analysis_feedback", "")
    previous_plan = state.get("month_plan")
    if previous_plan and plan_fb:
        parts.append("Your previous plan:\n" + previous_plan.model_dump_json(indent=2))
        parts.append("The user reviewed it and asked for these changes. Apply them fully, "
                     "while keeping the rules:\n" + plan_fb)
    elif previous_plan and analysis_fb:
        parts.append("Your previous plan:\n" + previous_plan.model_dump_json(indent=2))
        parts.append("The revenue analysis was just corrected by the user. Keep the previous plan and change it "
                     "only where the new analysis requires it.")

    errors = state.get("plan_errors", [])
    if errors:
        parts.append("Your last plan failed these checks. Fix all of them:\n" + "\n".join(errors))

    return {"month_plan": ask_llm(MonthPlan, "\n\n".join(parts), "Month plan", timeout=LLM_TIMEOUT_LONG)}


def depends_on(task, check, tasks_by_id, seen):
    for dep_id in task.depends_on:
        if dep_id in seen or dep_id not in tasks_by_id:
            continue
        seen.add(dep_id)
        dep = tasks_by_id[dep_id]
        if check(dep) or depends_on(dep, check, tasks_by_id, seen):
            return True
    return False


ORDER_RULES = {
    "outreach": ("data_build", "data_approval", "content", "client_training"),
    "qualification": ("outreach", "readiness"),
    "handover": ("qualification",),
}

INVENTED_DEADLINE = re.compile(r"\b(by|end of|mid|start of|in|within|during)\s+(week|day)\s*\d", re.IGNORECASE)

CHANNEL_AGENTS = {
    "email": {"Email Agent"},
    "linkedin": {"LinkedIn Agent"},
    "call": {"Quickhowl", "Human Tele Calling"},
}


# Tool tasks the orchestrator runs by a COUNT: their target needs a number (the runners read the largest
# number in it, e.g. '150-200 accounts' -> 200). Without one the run stops at that task.
def target_number_errors(tasks) -> list[str]:
    """
    Every counted tool task whose target the runners cannot turn into a count (code; same reading as the
    runners): no number at all, or a % that is out of 1-100 or not clearly a share of ONE counted task
    (named in the text, e.g. '100% of T4 contacts', or the only counted task it depends on).
    """
    counted = {t.task_id for t in tasks if (t.agent, t.output_type) in COUNTED_TOOL_TASKS}
    errors = []
    for task in tasks:
        if (task.agent, task.output_type) not in COUNTED_TOOL_TASKS:
            continue
        head = f"{task.task_id} ({task.agent} / {task.output_type}) target '{task.target}'"
        share = percent_of(task.target)
        if share is not None and count_in(task.target) is None:
            if not 0 < share <= 100:
                errors.append(f"{head} is a share outside 1-100%.")
            elif percent_base(task.target, task.depends_on, counted) is None:
                errors.append(f"{head} is a share: name the task it is a share of (e.g. '100% of T4 contacts') "
                              "or depend on exactly one counted task.")
        elif count_in(task.target) is None:
            errors.append(f"{head} has no number. Tool tasks run by a count: give one from the document, "
                          "e.g. '150-200 accounts' or '250-350 contacts'.")
    return errors


def validate_plan(state: MasterState) -> dict:
    plan = state["month_plan"]
    enabled = enabled_agents(state["engagement_settings"])
    tasks_by_id = {task.task_id: task for task in plan.tasks}
    errors = []

    if len(tasks_by_id) != len(plan.tasks):
        errors.append("Two or more tasks share the same task_id.")

    for task in plan.tasks:
        if task.agent not in enabled:
            errors.append(f"{task.task_id} uses {task.agent}, which is not enabled for this customer.")
        elif task.output_type not in AGENT_CATALOG[task.agent]["outputs"]:
            valid = ", ".join(AGENT_CATALOG[task.agent]["outputs"])
            errors.append(f"{task.task_id} has output_type '{task.output_type}'; {task.agent} only has: {valid}.")

        for dep_id in task.depends_on:
            if dep_id not in tasks_by_id:
                errors.append(f"{task.task_id} depends on {dep_id}, which does not exist.")
            elif tasks_by_id[dep_id].week > task.week:
                errors.append(f"{task.task_id} is in week {task.week} but depends on {dep_id} in a later week.")

        for task_type in task.task_types:
            for needed in ORDER_RULES.get(task_type, ()):
                if needed in task.task_types:
                    continue
                if not depends_on(task, lambda t, n=needed: n in t.task_types, tasks_by_id, set()):
                    errors.append(f"{task.task_id} is {task_type} but does not come after a '{needed}' task.")

        if "review" in task.task_types and task.week != 1:
            errors.append(f"{task.task_id} is a recurring review; it must start in week 1.")

        if INVENTED_DEADLINE.search(task.target):
            errors.append(f"{task.task_id} target '{task.target}' mentions a week or day deadline. "
                          "Use only periods from the document.")

        if "outreach" in task.task_types and task.agent in ("Quickhowl", "Human Tele Calling", "Email Agent"):
            if not depends_on(task, lambda t: t.agent == "QuickICP" and t.output_type == "contacts",
                              tasks_by_id, set()):
                errors.append(f"{task.task_id} contacts prospects but no earlier QuickICP task outputs 'contacts'.")

    errors += target_number_errors(plan.tasks)

    planned_agents = {task.agent for task in plan.tasks}
    lead_engine = " ".join(state["understanding"].motm_channels).lower()
    feedback = state.get("plan_feedback", "").lower()
    for keyword, agents in CHANNEL_AGENTS.items():
        if keyword in lead_engine and not (agents & planned_agents) and keyword not in feedback:
            errors.append(f"The lead engine includes '{keyword}' but no task uses {', '.join(sorted(agents))}.")

    attempts = state.get("plan_attempts", 0) + 1
    print(f"Validation attempt {attempts}: {len(errors)} error(s)")
    for error in errors:
        print("  -", error)
    return {"plan_errors": errors, "plan_attempts": attempts}


def after_validation(state: MasterState) -> str:
    if state["plan_errors"] and state["plan_attempts"] < 3:
        return "plan_month"
    return "human_review"


def split_feedback(text: str) -> tuple[str, str]:
    analysis_match = re.search(r"analysis:(.*?)(?=plan:|$)", text, re.IGNORECASE | re.DOTALL)
    plan_match = re.search(r"plan:(.*?)(?=analysis:|$)", text, re.IGNORECASE | re.DOTALL)
    if not analysis_match and not plan_match:
        return "", text.strip()
    analysis = analysis_match.group(1).strip() if analysis_match else ""
    plan = plan_match.group(1).strip() if plan_match else ""
    return analysis, plan


def human_review(state: MasterState) -> dict:
    decision = interrupt({"question": "Approve this plan or describe the changes you want."})
    if decision.get("approved"):
        return {"plan_approved": True, "user_feedback": "", "analysis_feedback": "", "plan_feedback": ""}
    text = decision.get("feedback", "")
    analysis_fb, plan_fb = split_feedback(text)
    return {"plan_approved": False, "user_feedback": text, "analysis_feedback": analysis_fb,
            "plan_feedback": plan_fb, "plan_attempts": 0}


def after_review(state: MasterState) -> str:
    if state["plan_approved"]:
        return "create_work_orders"
    if state.get("analysis_feedback"):
        return "analyze_revenue"
    return "plan_month"


def create_work_orders(state: MasterState) -> dict:
    plan = state["month_plan"]
    understanding = state["understanding"]
    settings = state["engagement_settings"]

    shared = "\n\n".join([
        "You are the Master Agent of MOTM Revenue OS. Write the work order for ONE task of the approved plan.",
        motm_block(state),
        "Rules:\n"
        "- The task has a fixed agent and output_type. Write instructions that produce exactly that output "
        "type's fields from the catalogue, and nothing else. Never change, add or remove output fields.\n"
        "- Follow what the agent does and does NOT do in the catalogue.\n"
        "- Never mention WhatsApp, PSEO or other channels that are not listed in the agent catalogue.\n"
        "- CAM tasks escalate to MOTM leadership; all other agents escalate to CAM.\n"
        "- Keep the revenue goal in mind: instructions should protect lead quality and speed of follow-up.\n"
        "- Use only facts from the source document. Never invent names, numbers or claims.\n"
        "- If the task needs something another task produces, name that task_id in inputs_needed.\n"
        "- If the geography's clusters need verification, QuickICP must verify them before mapping accounts.\n"
        "- For qualification, use the customer's qualification questions and A/B/C grade rules.\n"
        "- Never ask MOTM agents to do the customer's responsibilities (proposals, pricing, closing).",
        "Agent catalogue:\n" + catalog_text(settings),
        "Full approved plan (for context):\n" + plan.model_dump_json(indent=2),
        "Source document:\n" + state["document_text"],
    ])

    prompts = [
        shared + "\n\nWrite the work order for this task only, with task_id exactly '"
        + task.task_id + "':\n" + task.model_dump_json(indent=2)
        for task in plan.tasks
    ]

    results = ask_llm_batch(WorkOrderDetails, prompts, "Work orders", timeout=LLM_TIMEOUT_LONG, max_concurrency=5)

    work_orders = []
    for task, details in zip(plan.tasks, results):
        outputs = AGENT_CATALOG[task.agent]["outputs"]
        work_orders.append(WorkOrder(
            work_order_id=f"WO-{task.task_id}",
            task_id=task.task_id,
            customer=understanding.customer_name,
            agent=task.agent,
            task_type=", ".join(task.task_types),
            output_type=task.output_type,
            week=task.week,
            depends_on=task.depends_on,
            target=task.target,
            tier=task.tier,
            geography=task.geography,
            revenue_link=task.revenue_link,
            instructions=details.instructions,
            inputs_needed=details.inputs_needed,
            expected_output=outputs.get(task.output_type, []),
            done_when=details.done_when,
            escalate_if=details.escalate_if,
        ))

    print(f"Work orders written: {len(work_orders)} of {len(plan.tasks)}")
    return {"work_orders": work_orders}


# ================= 7. GRAPH =================

builder = StateGraph(MasterState)
builder.add_node("read_document", read_document)
builder.add_node("understand_document", understand_document)
builder.add_node("analyze_revenue", analyze_revenue)
builder.add_node("plan_month", plan_month)
builder.add_node("validate_plan", validate_plan)
builder.add_node("human_review", human_review)
builder.add_node("create_work_orders", create_work_orders)

builder.add_edge(START, "read_document")
builder.add_edge("read_document", "understand_document")
builder.add_edge("understand_document", "analyze_revenue")
builder.add_edge("analyze_revenue", "plan_month")
builder.add_edge("plan_month", "validate_plan")
builder.add_conditional_edges("validate_plan", after_validation, ["plan_month", "human_review"])
builder.add_conditional_edges("human_review", after_review,
                              ["analyze_revenue", "plan_month", "create_work_orders"])
builder.add_edge("create_work_orders", END)

graph = builder.compile(checkpointer=MemorySaver())


# ================= 8. RUN =================

def inr(value: float | None) -> str:
    if value is None:
        return "not available"
    if value >= 10_000_000:
        return f"₹{value / 10_000_000:.2f} crore"
    return f"₹{value / 100_000:.2f} lakh"


def print_analysis(analysis: RevenueAnalysis) -> None:
    print("\n===== REVENUE ANALYSIS =====")
    print("MOTM objective: maximise the customer's revenue, as fast as possible")
    print("Customer's revenue target:", analysis.revenue_goal)
    print("AOV as written:", analysis.aov_as_written or "not given")
    print(f"AOV: {inr(analysis.aov_low_inr)} – {inr(analysis.aov_high_inr)}")
    print(f"Outbound close rate (%): {analysis.outbound_close_rate_low_pct} – {analysis.outbound_close_rate_high_pct}")
    print("Inbound close rate (%):", analysis.inbound_close_rate_pct)
    print("Capacity:", analysis.capacity_per_month)
    print("Sales cycle (weeks):", analysis.sales_cycle_weeks)
    print(f"Qualified opportunities: {analysis.qualified_opportunities_low}–"
          f"{analysis.qualified_opportunities_high} over {analysis.opportunities_period}")
    print(f"Expected orders: {analysis.expected_orders_low} – {analysis.expected_orders_high}")
    print(f"Expected revenue: {inr(analysis.expected_revenue_low_inr)} – {inr(analysis.expected_revenue_high_inr)}")
    print("Forecast note:", analysis.forecast_note)
    print("Revenue levers:")
    for lever in analysis.revenue_levers:
        print(" -", lever)
    print("Customer resources to use:")
    for resource in analysis.customer_resources_to_use:
        print(" -", resource)
    print("Revenue risks:")
    for risk in analysis.revenue_risks:
        print(" -", risk)
    print("Missing for forecast:")
    for item in analysis.missing_for_revenue_forecast:
        print(" -", item)


def print_plan(plan: MonthPlan) -> None:
    print(f"\n===== {plan.month} =====")
    print("Objectives:")
    for objective in plan.objectives:
        print(" -", objective)
    for week in sorted({task.week for task in plan.tasks}):
        print(f"\n--- Week {week} ---")
        for task in plan.tasks:
            if task.week == week:
                deps = ", ".join(task.depends_on) if task.depends_on else "none"
                print(f"{task.task_id} [{task.agent} / {', '.join(task.task_types)} / {task.output_type}] "
                      f"{task.task} | target: {task.target} | {task.tier} | {task.geography} | after: {deps}")
                print(f"    revenue link: {task.revenue_link}")
    print("\nKPIs to track:")
    for kpi in plan.kpis_to_track:
        print(" -", kpi)
    print("\nOptimization rules:")
    for rule in plan.optimization_rules:
        print(" -", rule)
    print("\nNotes for reviewer:")
    for note in plan.review_notes:
        print(" -", note)
    print("\nBlocked:")
    for item in plan.blocked_items:
        print(" -", item)


def choose_document(argv: list[str]) -> str:
    """--intake -> the platform plan document (intake_adapter.py); otherwise the scraped document."""
    return "data/intake_document.txt" if "--intake" in argv else "data/extracted.txt"


def check_document(path: str) -> None:
    """Stop BEFORE planning (and before any file is moved) if the input document is missing."""
    if not os.path.exists(path):
        hint = "python intake_adapter.py" if path.endswith("intake_document.txt") else "python extract_html.py"
        raise ToolError(f"STOPPED: {path} is missing. Run first: {hint}")


def main(document_path: str = "data/extracted.txt") -> None:
    """Plan a month: read the document, plan, ask the human to approve, write work orders + approval proof."""
    # Who approves must be known BEFORE planning, so we never plan for nothing.
    # Started for a platform request (run_for.py sets BRAIN_REQUEST_ID): the plan is approved in the
    # platform, and the approver is whoever approves it there. Otherwise: the typed prompt, as before.
    platform = platform_approval.from_environment()
    approver = platform.PENDING if platform else plan_approver()
    check_document(document_path)

    config = {"configurable": {"thread_id": "month-1-run"}}

    state = graph.invoke({
        "document_path": document_path,
        "engagement_settings": {
            "ai_calling_enabled": True,
            "calling_split": "Quickhowl makes first-touch calls to the full database; "
                             "Human Tele Calling handles interested, callback and technical conversations",
            "whatsapp_enabled": False,
            "pseo_enabled": False,
        },
    }, config)

    approval, errors_at_approval = None, []
    while "__interrupt__" in state:
        print_analysis(state["revenue_analysis"])
        print_plan(state["month_plan"])
        errors = state.get("plan_errors") or []
        if errors:
            print("\n⚠ The plan still breaks these rules after 3 attempts. Tell the agent how to fix them:")
            for error in errors:
                print("  -", error)
        # Code decides: a plain 'yes' is refused while rules are broken; 'override' needs a reason.
        if platform:
            reader = platform.reader(state, errors)
            approval, errors_at_approval = ask_approval(errors, read=reader), errors
            approver = reader.approved_by or approver
        else:
            approval, errors_at_approval = ask_approval(errors), errors
        state = graph.invoke(Command(resume=approval["resume"]), config)

    # Approved and the work orders written: only NOW are the old plan files MOVED (never deleted) to
    # data/archive/<YYYYMMDD-HHMMSS>/, the proof first, so a stale proof never sits next to a new plan.
    # An LLM timeout or a stop before this point leaves the old plan files exactly where they were.
    archived = archive_plan_files()
    if archived:
        print(f"Old plan files moved to {archived}")

    for key, path in [("understanding", "data/understanding.json"),
                      ("revenue_analysis", "data/revenue_analysis.json"),
                      ("month_plan", "data/month_plan.json")]:
        if key in state:
            with open(path, "w", encoding="utf-8") as file:
                file.write(state[key].model_dump_json(indent=2))
            print(f"Saved {key} to {path}")

    work_orders = state.get("work_orders", [])
    if work_orders:
        with open("data/work_orders.json", "w", encoding="utf-8") as file:
            file.write("[\n" + ",\n".join(wo.model_dump_json(indent=2) for wo in work_orders) + "\n]")
        print("Saved work orders to data/work_orders.json")

        print("\n===== WORK ORDERS =====")
        for wo in work_orders:
            deps = ", ".join(wo.depends_on) if wo.depends_on else "none"
            print(f"\n{wo.work_order_id} -> {wo.agent} / {wo.output_type} (week {wo.week}, after: {deps})")
            print(f"  Target: {wo.target}")
            print(f"  Revenue link: {wo.revenue_link}")
            print("  Instructions:")
            for i, step in enumerate(wo.instructions, start=1):
                print(f"    {i}. {step}")
            print("  Needs:", "; ".join(wo.inputs_needed))
            print("  Returns:", "; ".join(wo.expected_output))
            print("  Done when:", wo.done_when)
            print("  Escalate if:", wo.escalate_if)

        # Proof LAST: it fingerprints the work_orders / month_plan / revenue_analysis files just written.
        proof = write_proof(approval, errors_at_approval, approver, len(work_orders))
        print(f"\nApproval proof saved to {APPROVAL_FILE}: {describe(proof)}")
        print("Next: python orchestrator.py load <YYYY-MM>")


if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        try:
            main(choose_document(sys.argv))
        except (ToolError, platform_approval.Stop) as error:
            print(error)
            sys.exit(1)
        finally:
            print()
            print(f"{usage_summary()}   (every call: data/llm_usage.jsonl)")

    run_cli(cli)
