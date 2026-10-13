"""
email_campaign_payload.py  (temporary, delete after sending)

Builds the email-campaign payload the developer asked for, from what the
Master Agent already saved. No Master Agent rerun needed.

Reads:  data/understanding.json, data/revenue_analysis.json,
        data/work_orders.json, data/extracted.txt
Writes: data/email_campaign_payload.json

Run:    python email_campaign_payload.py
"""

import json
import os
from datetime import date

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

load_dotenv()


# ---------- What the LLM fills ----------

class CompanyInfo(BaseModel):
    company_name: str
    url: str = Field(description="Customer website only if written in the document, else empty string")
    summary: str
    services: list[str]
    industries: list[str]
    audience: str
    tone_of_voice: str
    key_messages: list[str] = Field(description="Proof points from the document only, never invented")


class Goal(BaseModel):
    objective: str
    offer: str
    cta: str
    target: str
    problem_solved: str
    tone: str
    must_mention: list[str]
    never_say: list[str]


class Email(BaseModel):
    subject: str
    body_html: str = Field(description="Short HTML email using <p> tags and the {{first_name}} variable")
    wait_days_before: int = Field(description="0 for the first email, then days after the previous email")


class LLMPart(BaseModel):
    campaign_name: str = Field(description="'<Customer> - <Target role> - <Month Year>'")
    company: CompanyInfo
    goal: Goal
    emails: list[Email] = Field(description="3 emails: intro, value follow-up, short final nudge")


# ---------- Helpers ----------

def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def email_work_order(work_orders):
    for work_order in work_orders:
        if work_order["agent"] == "Email Agent":
            return work_order
    return {}


# ---------- Build ----------

def build_payload():
    understanding = load_json("data/understanding.json")
    revenue = load_json("data/revenue_analysis.json")
    work_order = email_work_order(load_json("data/work_orders.json"))
    with open("data/extracted.txt", "r", encoding="utf-8") as file:
        document = file.read()

    prompt = (
        "You are the Master Agent of MOTM Revenue OS. Fill an email campaign for this customer.\n"
        "Rules:\n"
        "- Use only facts from the customer document and understanding. Never invent claims, "
        "numbers, certifications or delivery times.\n"
        "- The audience and target come from the email work order and the customer's P1 segment.\n"
        "- Emails: short, plain business tone, one clear CTA, use {{first_name}} only as a variable.\n"
        "- never_say: claims the customer cannot prove, e.g. 'cheapest', 'guaranteed', '100%'.\n"
        f"- Today is {date.today():%B %Y}; use it in the campaign name.\n\n"
        "Email work order:\n" + json.dumps(work_order, ensure_ascii=False)
        + "\n\nCustomer understanding:\n" + json.dumps(understanding, ensure_ascii=False)
        + "\n\nRevenue levers:\n" + json.dumps(revenue.get("revenue_levers", []), ensure_ascii=False)
        + "\n\nSource document:\n" + document
    )

    llm = ChatOpenAI(model=os.getenv("OPENAI_MODEL"), temperature=0)
    part = llm.with_structured_output(LLMPart).invoke(prompt)

    return {
        "campaign": {
            "name": part.campaign_name,
            "description": f"RevenueOS strategy {work_order.get('work_order_id', '')}".strip(),
            "campaign_type": "HYPER_PERSONALIZED",
        },
        "company": {"source": "MANUAL", **part.company.model_dump()},
        "goal": part.goal.model_dump(),
        "emails": [email.model_dump() for email in part.emails],
        "schedule": {
            "timezone": "Asia/Kolkata",
            "weekdays": [1, 2, 3, 4, 5],
            "window_start_local": "09:30:00",
            "window_end_local": "17:30:00",
            "daily_limit": 50,
        },
        "audience": {"list_ids": ["<lead list id>"], "lead_ids": []},
        "mailbox_id": "<connected mailbox id>",
    }


if __name__ == "__main__":
    payload = build_payload()
    with open("data/email_campaign_payload.json", "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print("\nSaved to data/email_campaign_payload.json")