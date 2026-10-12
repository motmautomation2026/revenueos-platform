# MOTM Revenue OS: the brain

## What it is

The brain turns a customer's ready plan from the RevenueOS platform into real work. It plans the month, a
person approves the plan, and then the brain runs it task by task: it finds companies and people
(QuickICP), sends emails (email tool), makes calls (Quickhowl), and reviews the results against the plan.
Everything it does is saved in Supabase (`brain_*` tables), so it can stop at any point and continue
exactly there, never runs a task twice, and never repeats a paid call by accident. Code decides numbers,
limits and pass/fail; the LLM only writes text and makes judgment calls. A person approves before
anything is sent or called.

## Flow

```
platform plan (revenueos_*, read-only)
  -> intake_adapter.py   reads the latest READY plan + checklist -> data/intake_document.txt
  -> master_agent.py     LLM writes the month plan + work orders -> a PERSON approves (proof saved)
  -> orchestrator.py     loads the approved plan as a run, then runs it one task at a time:
       QuickICP     companies and people (paid data; existing Supabase data is reused first)
       Email tool   campaign stored as draft -> sample emails -> a PERSON approves -> sent
       Quickhowl    call campaign stored -> a PERSON approves -> calls -> results
       human tasks  (CAM, training, tele-calling, ...) wait for a person's answer
  -> brain_review.py     code computes the numbers, the LLM explains them, max 3 suggested actions
```

## What we read from your side (read-only)

We only run SELECTs on your tables. We never insert, update or delete anything of yours.
`REVENUEOS_PROJECT_ID` in our `.env` says which project is ours; we never match on a customer name.

| Table | Columns we read | Filter |
|---|---|---|
| `revenueos_projects` | `id, customer_name, engagement_type, status` | `id` = our project |
| `revenueos_plans` | `id, project_id, version, status, finished_at, diagnosis, strategy, execution` | `project_id`, `status = 'ready'`; we take the highest `version` |
| `revenueos_checklists` | `id, project_id, is_active, status, submitted_at` | `project_id`, `is_active = true` |
| `revenueos_checklist_items` | `code, category, question, answer, prefilled_answer, position` | `checklist_id` |

**Plan JSON keys we depend on** (missing keys are shown as "none given", but renamed keys silently lose data):

- `diagnosis`: `customer_name`, `business_understanding.summary`, `.value_chain.customer_solution`,
  `.offerings[].name/type/notes`, `funnel_facts[].metric/value_low/value_high/unit/source/confidence`,
  `key_numbers[].label/value/source_ref`, `capacity.summary/limits_volume_targets`,
  `how_they_sell_today[].label/answer`,
  `conflicts[].conflict_type/blocking/field/bd_or_proposal_value/customer_value`,
  `readiness.blockers[].blocker`, `data_gaps[].item/why_it_matters`
- `strategy`: `engagement_period`, `priorities[].tier/segment/rationale`,
  `geography[].region/priority/clusters_public_data/relevant_demand/why/applies_to`,
  `personas[].persona/role_in_buying/main_concern`,
  `buying_behaviour[].company_type/how_they_buy/typical_barrier/best_way_in`,
  `demand_triggers.visible/hidden_early`
- `execution`: `roadmap[].phase/months/activities/milestones[].milestone/metric/value_low/value_high/basis`,
  `roadmap_guardrail`, `channels[].channel/job/audience/scope_item`,
  `qualification.questions/grade_a/grade_b/grade_c`, `accountability.customer`,
  `prerequisites.proof_assets/operational_support/governance_inputs`, `price_objection_playbook`,
  `open_research`

We also find four answers in the checklist by `category` + words in `question`: the revenue target,
the engagement period, the sales cycle and the company size. The categories we look for are
"numbers for targets" and "ideal customers".

## Please tell us BEFORE you

- rename or remove any of the tables, columns or JSON keys above, change `status = 'ready'` /
  `is_active`, or reword those checklist categories and questions;
- touch our tables in the same Supabase project. They are written only by our code:
  `brain_customer_runs`, `brain_plan_tasks`, `brain_tool_calls`, `brain_decisions`,
  `quickicp_revenueos_client_company_data`, `quickicp_revenueos_client_people_data`.
  The SQL for them is in `sql/` (we run it by hand).
- run Alembic autogenerate: it would try to DROP our tables. Exclude them in `env.py`:

```python
OUR_TABLES = {"quickicp_revenueos_client_company_data", "quickicp_revenueos_client_people_data"}

def include_object(obj, name, type_, reflected, compare_to):
    """Leave the MOTM brain's tables (and their columns, indexes, constraints) alone."""
    table = name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    return not (table and (table.startswith("brain_") or table in OUR_TABLES))

context.configure(..., include_object=include_object)   # in both run_migrations_offline and _online
```

## Setup (Windows / PowerShell)

```powershell
py -3.14 -m venv .venv                 # tested on Python 3.14
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `.env` (never commit it). Ask us for the values; the key names are:

- **Brain + state:** `CUSTOMER_KEY`, `STATE_BACKEND` (`supabase`), `SUPABASE_URL`, `SUPABASE_KEY`,
  `REVENUEOS_PROJECT_ID`, `PLAN_APPROVER`, `OPENAI_API_KEY`, `OPENAI_MODEL`
- **Customer names:** `CUSTOMER_DISPLAY_NAME`, `CUSTOMER_LONG_NAMES` (optional), `CUSTOMER_WEBSITE` (optional)
- **QuickICP / Prospeo:** `QUICKICP_WEBHOOK_URL`, `QUICKICP_PEOPLE_WEBHOOK_URL`,
  `QUICKICP_EMAIL_WEBHOOK_URL`, `QUICKICP_PHONE_WEBHOOK_URL`, `PROSPEO_API_KEY`
- **Email tool:** `EMAIL_TOOL_URL`, `EMAIL_TOOL_KEY`, `EMAIL_TOOL_USER_EMAIL`, `CLIENT_REFERENCE`,
  `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_DISPLAY_NAME`
- **Quickhowl:** `QUICKHOWL_URL`, `QUICKHOWL_KEY`, `QUICKHOWL_CLIENT_REFERENCE`, `QUICKHOWL_USER_EMAIL`,
  `QUICKHOWL_USER_PASSWORD`, `QUICKHOWL_USER_FULL_NAME`, `QUICKHOWL_USER_COMPANY`, `QUICKHOWL_DND_HANDLED`
- **Test mode:** `TEST_RECIPIENTS`, `TEST_WORKSPACE_NAME`, `TEST_CALL_NUMBERS`, `TEST_TARGET_OVERRIDE` (optional)

## Run

```powershell
python check_system.py                    # checks .env + connections; prints names, never secret values
python orchestrator.py selftest           # fake tools, throwaway selftest-* rows: no LLM, no paid call
python intake_adapter.py                  # read your plan -> data/intake_document.txt (Supabase reads only)
python master_agent.py --intake           # LLM plans the month; a person approves (costs LLM calls)
python orchestrator.py load 2026-10       # approved plan -> run (a replacement plan: load 2026-10-r2)
python orchestrator.py status             # where the run stands (read-only)
python orchestrator.py run 2026-10 --dry  # what the next tool task WOULD buy (nothing bought or recorded)
python orchestrator.py run 2026-10        # one step; --until-wait = until a person is needed
python orchestrator.py decisions          # open questions for a person
python orchestrator.py answer <id> <answer> "note"
python orchestrator.py review 2026-10     # brain review of the done tool tasks
```

## Safety

- **Test mode** (on in `quickicp.py`): small caps; emails go only to our team inboxes (`TEST_RECIPIENTS`)
  and calls only to our numbers (`TEST_CALL_NUMBERS`). No real prospect is contacted.
  Real calls also stay off (`REAL_CALLS_ENABLED = False` in `quickhowl.py`) until we switch them on at go-live.
- **A person approves** the month plan (name + time saved as proof) and every email campaign and call
  campaign before it starts. Answers are recorded in `brain_decisions`.
- **Secrets only in `.env`**: never in code, `data/`, logs or the `brain_tool_calls` ledger (code checks this).
- **No double work**: every tool call is saved before it is made, so a crash never repeats a paid call.
