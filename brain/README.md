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

## Choosing the customer from the database (`run_for.py`)

The platform now gives every project its own customer key when the project is created
(`revenueos_projects.brain_customer_key`, see `../supabase/migrations/0003_project_brain_key.sql`).
`run_for.py` reads the project and its key from there, so `REVENUEOS_PROJECT_ID`, `CUSTOMER_KEY` and
`CUSTOMER_DISPLAY_NAME` no longer have to be typed into `.env` to switch customers.

```powershell
python run_for.py selftest                 # fake data: no network, no LLM
python run_for.py list                     # ready projects, their key, whether the brain ran them
python run_for.py select new               # the ONE ready project with no run yet (never guesses between two)
python run_for.py select <project-id>      # or a specific project
python run_for.py show                     # which project is chosen and what the scripts will be given
python run_for.py check_system.py          # then run any script through it, unchanged
python run_for.py intake_adapter.py
python run_for.py master_agent.py --intake
python run_for.py orchestrator.py load 2026-10
```

It only SELECTs from `revenueos_projects` and `brain_customer_runs`, and writes one file,
`data/selected_project.json`. The scripts themselves are not changed: `run_for.py` sets the chosen
project's values in the process and then runs the script, and `load_dotenv()` does not overwrite values
that are already set. For a customer other than the one `.env` was written for, `CUSTOMER_LONG_NAMES`
and `CUSTOMER_WEBSITE` are emptied and `CUSTOMER_DISPLAY_NAME` is the project's customer name.


### The whole flow in one command

```powershell
python run_for.py                        # = start: select the new project, confirm, then run every step
python run_for.py start <project-id>     # a specific project
python run_for.py start --month 2026-11  # a month other than the current one
python run_for.py start --from load      # resume at a step: intake | plan | check | load | run
```

`start` selects the ready project the brain has not run yet, shows the customer, the month and whether
`TEST_MODE` is on, and waits for you to type the customer key. Then it runs `intake_adapter.py`,
`master_agent.py --intake`, `check_system.py`, `orchestrator.py load <month>` and
`orchestrator.py run <month> --until-wait`, each as its own process. The first step that fails stops the
flow and prints the command to resume from that step. No approval is skipped: `master_agent.py` still asks
a person to approve the plan, and `--until-wait` stops when a person is needed and before a new task that
would buy data.

### Every customer in its own folder, and a background worker

The scripts keep their working files in `./data`, so two customers in one folder overwrote each other.
`run_for.py` now runs every step inside the customer's own folder, `customers/<customer key>/`, which holds
that customer's `data/`, a copy of `knowledge/` and `logs/`. The scripts are unchanged. The customer `.env`
was written for keeps using this folder itself, so its existing `./data` is not moved. `customers/` is
git-ignored, like `data/`.

```powershell
python run_for.py work          # keeps running: acts on answers given in the platform
python run_for.py work --once   # look once, run what is due, then exit
```

`work` looks at every customer's active run every 30 seconds. When something changed for a customer (a
decision was answered in the platform, a task moved) it starts `orchestrator.py run <month> --until-wait`
for that customer in the background, in its own folder, with output in `customers/<key>/logs/`. Customers
run at the same time and never wait for each other. It only ever starts that safe step, which stops when a
person is needed and before a new task that would buy data. Ctrl+C lets running steps finish; it never
kills one.

### Started from the platform: nobody types a command

With the worker running, a customer is started from the platform's Dashboard:

1. A person clicks **Start execution**. That inserts a row in `revenueos_brain_requests` (status `requested`).
2. The worker claims it and runs, in the background and in that customer's folder, `intake_adapter.py`
   then `master_agent.py --intake`.
3. `master_agent.py` **publishes the month plan to the request row** instead of asking at its prompt, and
   waits. The person approves it on the Dashboard: approve, request changes, or approve anyway with a reason.
4. `check_system.py`, then `orchestrator.py load <month>`. The request ends `loaded` and the worker starts
   the run as it does for any active run.

What changed in the brain's own code, and what did not:

- `platform_approval.py` (new) supplies the answer to `plan_approval.ask_approval(errors, read=...)`, the
  reader argument that function already had. Every answer still goes through `plan_approval.decide()`:
  a plain yes is refused while rules are broken, an override needs a reason. The proof file is written by
  `master_agent.py` as before; `approved_by` is the platform user who approved.
- `master_agent.py`: 4 small edits in `main()`. They apply only when `BRAIN_REQUEST_ID` is set, which only
  the worker does. Run by hand, it asks at the keyboard and needs `PLAN_APPROVER` exactly as before.
- `revenueos_brain_requests` is the **one** `revenueos_*` table the brain writes to. Its statuses:
  `requested → planning → awaiting_approval → finishing → loaded`, or `failed` / `cancelled`.

A step that fails is shown on the Dashboard with its `STOPPED` line and a **Try again** button. A failure
after the approval (`check`, `load`) resumes there, so the plan is not written or approved twice. A
request the worker was planning when it was restarted is marked failed; the in-progress plan is lost with
the process, so it starts again from `intake`.

Keep the worker running as a Windows task (starts at sign-in, restarts if it stops, one per machine):

```powershell
.\install_worker.ps1            # install and start
.\install_worker.ps1 -Status    # installed? running? where is the log?
.\install_worker.ps1 -Remove
```

### Each customer's own email and calling accounts

`CLIENT_REFERENCE`, `EMAIL_TOOL_USER_EMAIL`, `QUICKHOWL_CLIENT_REFERENCE` and the four `QUICKHOWL_USER_*`
settings are no longer one shared set in `.env`. They are filled in per project on the platform (Tools >
Tool accounts), stored in `revenueos_brain_accounts`, and `run_for.py` hands each customer's own values to
the tools when it runs a step for that customer. `email_tool.py` and `quickhowl.py` are unchanged: they
still read the same setting names.

- A project with its own row always gets exactly that row; an empty field stays empty.
- A project without a row never gets another customer's accounts from `.env`. Only the customer `.env`
  was written for (its `CUSTOMER_KEY`) still falls back to `.env`, so an existing setup keeps working.
- `.env` keeps what is the same for every customer: tool URLs and API keys.
- To move the accounts already in `.env` onto their project and out of the file, once:
  `node scripts/import-brain-accounts.mjs <project-id>` (from the repo root).

### A new customer's email account, created by its first campaign

A customer with no existing email account gets its own, from what the platform collected:

- the **customer email** asked on the New project form (`revenueos_projects.customer_email`): the account's user
- the **Email sender setup** answers in its checklist (fixed codes `SENDER-NAME`, `SENDER-EMAIL`,
  `SENDER-SMTP-HOST`, `SENDER-SMTP-SECURITY`, `SENDER-SMTP-PASSWORD`, `SENDER-IMAP-HOST`), editable on the
  platform's Email page

`run_for.py` hands these to the email step as `CLIENT_REFERENCE` (the customer key), `EMAIL_TOOL_USER_EMAIL`,
`EMAIL_WORKSPACE_NAME` (the platform's customer name, so it never changes between months) and `SMTP_*`.
Changes in `email_tool.py`:

- `with_sender()` adds the `smtp` block **when the request is sent**, next to `client_reference`. The tool
  connects the mailbox on the first campaign and updates it after that, so nothing has to remember whether
  the account exists. The request the runner saves and resends never holds the mailbox password.
- `without_password()` removes the password from any tool reply before it reaches an error or a log.
- `smtp_settings()` also sends `imap_host` (port 993) when given, so the tool can read replies.
- The security choice is sent as the tool's own value: `STARTTLS` (587) or `IMPLICIT_TLS` (465).

A sender that is only partly filled in is never used: the email task then stops on the missing account,
as before. A customer with an existing account (Tool accounts) is unchanged and sends no `smtp`.

Selftests (fake data, no network, no LLM): `python platform_approval.py` and `python run_for.py selftest`.
