-- 001_brain_state.sql
-- State tables for the MOTM Revenue OS brain: customer runs, plan tasks, tool calls, decisions.
--
-- Run in Supabase on 2026-10-09. The code uses it with STATE_BACKEND=supabase (state_store.py);
-- the same 4 tables can also live in a local SQLite fallback file (data/brain_state.db).
--
-- It only CREATES new brain_* tables (+ 1 trigger function). It does not touch any existing table.
-- The QuickICP customer column is a separate file: 002_proposal_quickicp_customer.sql
-- Runs in one transaction: if any line fails, nothing is created.

begin;

-- =====================================================================
-- updated_at trigger function (shared by brain_customer_runs and brain_plan_tasks)
-- =====================================================================
create or replace function brain_set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

comment on function brain_set_updated_at() is
  'Sets updated_at to now() on every update of brain_customer_runs and brain_plan_tasks.';


-- =====================================================================
-- 1. brain_customer_runs: one customer + one month plan
-- =====================================================================
create table brain_customer_runs (
  id            bigint generated always as identity,
  run_key       text not null,
  customer      text not null,
  status        text not null default 'planning',
  understanding jsonb,
  plan          jsonb,
  test_mode     boolean not null default true,
  stop_reason   text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint brain_customer_runs_pkey           primary key (id),
  constraint brain_customer_runs_run_key_unique unique (run_key),
  constraint brain_customer_runs_status_check
    check (status in ('planning','waiting_human','running','done','stopped'))
);

create trigger brain_customer_runs_set_updated_at before update on brain_customer_runs
  for each row execute function brain_set_updated_at();

comment on table brain_customer_runs is
  'One row per customer per month plan: the top-level record the brain resumes from. '
  'Written only by the MOTM Revenue OS code (state_store.py).';
comment on column brain_customer_runs.id is
  'Row id, set by the database. Example: 1';
comment on column brain_customer_runs.run_key is
  'Unique name of the run: <customer>-<YYYY-MM>. Used to find the run again after a stop. Example: anvixa-ai-product-2026-10';
comment on column brain_customer_runs.customer is
  'Customer key (lowercase, dashes), same value as the customer column in the QuickICP tables. Example: anvixa-ai-product';
comment on column brain_customer_runs.status is
  'Where the run stands. Allowed: planning, waiting_human, running, done, stopped. Example: running';
comment on column brain_customer_runs.understanding is
  'The brain''s reading of the customer strategy document (JSON). Example: {"customer_name": "Anvixa AI Product", ...}';
comment on column brain_customer_runs.plan is
  'The approved month plan (JSON): objectives, tasks, KPIs. Null until the plan exists. Example: {"month": "October 2026", "tasks": [...]}';
comment on column brain_customer_runs.test_mode is
  'true = test inboxes and test phone numbers only, small volumes; false = real prospects. Example: true';
comment on column brain_customer_runs.stop_reason is
  'Why the run stopped, in plain words; null while it is not stopped. Example: STOPPED: human chose stop at goal check (T3)';
comment on column brain_customer_runs.created_at is
  'When the run was created (UTC). Example: 2026-10-09 10:30:00+00';
comment on column brain_customer_runs.updated_at is
  'Last change to this row (UTC), set by trigger brain_customer_runs_set_updated_at. Example: 2026-10-09 11:02:13+00';


-- =====================================================================
-- 2. brain_plan_tasks: one row per work order of the plan (T1..T13)
-- =====================================================================
create table brain_plan_tasks (
  id              bigint generated always as identity,
  run_id          bigint not null,
  task_id         text not null,
  agent           text not null,
  output_type     text not null,
  depends_on      jsonb not null default '[]',
  week            int,
  work_order      jsonb not null,
  target_number   int,
  achieved_number int,
  status          text not null default 'pending',
  attempts        int not null default 0,
  waiting_for     text,
  result          jsonb,
  last_error      text,
  started_at      timestamptz,
  finished_at     timestamptz,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  constraint brain_plan_tasks_pkey             primary key (id),
  constraint brain_plan_tasks_run_id_fkey      foreign key (run_id) references brain_customer_runs(id),
  constraint brain_plan_tasks_run_task_unique  unique (run_id, task_id),
  constraint brain_plan_tasks_status_check
    check (status in ('pending','running','waiting_human','done','failed','stopped'))
);

create trigger brain_plan_tasks_set_updated_at before update on brain_plan_tasks
  for each row execute function brain_set_updated_at();

comment on table brain_plan_tasks is
  'One row per task (work order) in a customer run, with its status, target and result, so the brain knows '
  'what is done and what to run next. Written only by the MOTM Revenue OS code (state_store.py).';
comment on column brain_plan_tasks.id is
  'Row id, set by the database. Other tables point here with task_row_id. Example: 5';
comment on column brain_plan_tasks.run_id is
  'The run this task belongs to (brain_customer_runs.id). Example: 1';
comment on column brain_plan_tasks.task_id is
  'Task id from the plan, unique within a run. Example: T3';
comment on column brain_plan_tasks.agent is
  'Who does the task, as named in the plan. Examples: QuickICP, Email Agent, Quickhowl, CAM';
comment on column brain_plan_tasks.output_type is
  'The one output this task produces, as named in the plan. Examples: accounts, contacts, emails, calls';
comment on column brain_plan_tasks.depends_on is
  'JSON list of task_ids that must be done before this task can start; [] if none. Example: ["T5", "T2"]';
comment on column brain_plan_tasks.week is
  'Week of the month the plan puts this task in, 1 to 4. Example: 2';
comment on column brain_plan_tasks.work_order is
  'The full work order from the brain (JSON): instructions, inputs, done_when, escalate_if. Example: {"work_order_id": "WO-T3", ...}';
comment on column brain_plan_tasks.target_number is
  'Target as a number, taken by code from the work order target (largest number); null if the target has no number. '
  'Example: 200 (from "150-200 accounts")';
comment on column brain_plan_tasks.achieved_number is
  'How many were actually achieved, counted by code from the results; null until counted. Example: 46';
comment on column brain_plan_tasks.status is
  'Allowed: pending (not started), running, waiting_human (paused for a decision), done, failed, stopped. Example: waiting_human';
comment on column brain_plan_tasks.attempts is
  'How many times the brain has retried this task; code allows at most 2. Example: 1';
comment on column brain_plan_tasks.waiting_for is
  'Decision kind the task is paused on, only while status = waiting_human; otherwise null. '
  'Allowed: plan_approval, goal_check, email_approve, call_start, paid_call_unknown, task_failed. Example: email_approve';
comment on column brain_plan_tasks.result is
  'Summary of what the task produced, which the brain compares with the target (JSON). Example: {"passed": 46, "target": 60}';
comment on column brain_plan_tasks.last_error is
  'Last error message, if any; null when there is none. Example: STOPPED: Company search: request rejected (502)';
comment on column brain_plan_tasks.started_at is
  'When the task first started running (UTC); null if not started. Example: 2026-10-09 10:31:00+00';
comment on column brain_plan_tasks.finished_at is
  'When the task reached done, failed or stopped (UTC); null before that. Example: 2026-10-09 10:58:40+00';
comment on column brain_plan_tasks.created_at is
  'When the task row was created (UTC). Example: 2026-10-09 10:30:05+00';
comment on column brain_plan_tasks.updated_at is
  'Last change to this row (UTC), set by trigger brain_plan_tasks_set_updated_at. Example: 2026-10-09 10:58:40+00';


-- =====================================================================
-- 3. brain_tool_calls: one row per external tool call (stops paid calls running twice)
-- =====================================================================
create table brain_tool_calls (
  id              bigint generated always as identity,
  task_row_id     bigint not null,
  step            text not null,
  idempotency_key text not null,
  tool            text not null,
  paid            boolean not null default false,
  status          text not null default 'running',
  external_ref    text,
  request         jsonb,
  response        jsonb,
  output          jsonb,
  error           text,
  started_at      timestamptz not null default now(),
  finished_at     timestamptz,
  constraint brain_tool_calls_pkey                   primary key (id),
  constraint brain_tool_calls_task_row_id_fkey       foreign key (task_row_id) references brain_plan_tasks(id),
  constraint brain_tool_calls_idempotency_key_unique unique (idempotency_key),
  constraint brain_tool_calls_status_check
    check (status in ('running','done','failed','unknown'))
);

comment on table brain_tool_calls is
  'One row per call to an external tool (QuickICP, email tool, Quickhowl, Supabase save), saved BEFORE the call is made, '
  'so a crash never repeats a paid call by accident. Written only by the MOTM Revenue OS code (state_store.py).';
comment on column brain_tool_calls.id is
  'Row id, set by the database. Example: 12';
comment on column brain_tool_calls.task_row_id is
  'The task this call belongs to (brain_plan_tasks.id). Example: 5';
comment on column brain_tool_calls.step is
  'Which step of the task this call is. Examples: companies, people, emails, phones, save, launch, samples, approve, start';
comment on column brain_tool_calls.idempotency_key is
  'Unique key of this call. Email tool and Quickhowl: their campaign reference. QuickICP: our own key '
  '<run_key>:<task_id>:<step>:<round>. Example: motm-anvixa-call-20261009-103248';
comment on column brain_tool_calls.tool is
  'Which tool was called. Allowed: quickicp, email_tool, quickhowl, supabase. Example: quickhowl';
comment on column brain_tool_calls.paid is
  'true if the call uses paid credits (then it is never repeated without a human). Example: true';
comment on column brain_tool_calls.status is
  'Allowed: running (saved, call in progress or crashed during it), done, failed, '
  'unknown (crashed during a call with no reference: we cannot know if it ran; a human decides). Example: done';
comment on column brain_tool_calls.external_ref is
  'Id the tool gave back, if any; null otherwise. Example: a0a23d5e-e697-44c9-b4c3-97f94155eb62 (email campaign_id)';
comment on column brain_tool_calls.request is
  'Exactly what was sent (JSON), with secrets removed; resent unchanged on resume. Example: {"reference": "...", "contacts": [...]}';
comment on column brain_tool_calls.response is
  'The tool''s reply (JSON), secrets removed; null until the call finishes. Example: {"duplicate": false, "start_status": "pending"}';
comment on column brain_tool_calls.output is
  'What the call produced for the next steps (JSON). For QuickICP rows it records which company/people ids belong to which '
  'customer and run. Example: {"customer": "anvixa-ai-product", "run_key": "anvixa-ai-product-2026-10", "company_ids": [3, 7], "people_ids": [21]}';
comment on column brain_tool_calls.error is
  'Error message if the call failed or is unknown; null otherwise. Example: crash during call (no reply saved)';
comment on column brain_tool_calls.started_at is
  'When the row was saved, just before the call (UTC). Example: 2026-10-09 10:32:48+00';
comment on column brain_tool_calls.finished_at is
  'When the call finished or was marked unknown (UTC); null while running. Example: 2026-10-09 10:32:50+00';


-- =====================================================================
-- 4. brain_decisions: every human / auto / brain decision
-- =====================================================================
create table brain_decisions (
  id          bigint generated always as identity,
  run_id      bigint not null,
  task_row_id bigint,
  kind        text not null,
  status      text not null default 'open',
  question    jsonb,
  answer      text,
  decided_by  text,
  reason      text,
  created_at  timestamptz not null default now(),
  answered_at timestamptz,
  constraint brain_decisions_pkey             primary key (id),
  constraint brain_decisions_run_id_fkey      foreign key (run_id) references brain_customer_runs(id),
  constraint brain_decisions_task_row_id_fkey foreign key (task_row_id) references brain_plan_tasks(id),
  constraint brain_decisions_status_check     check (status in ('open','answered'))
);

-- At most one OPEN question per run + task + kind.
create unique index brain_decisions_one_open_question
  on brain_decisions (run_id, coalesce(task_row_id, 0), kind) where status = 'open';

comment on table brain_decisions is
  'Every decision in a run: questions waiting for a human (open), and answers by a human, by auto mode, or by the brain. '
  'An open row pauses its task; the next run continues once it is answered. Written only by the MOTM Revenue OS code (state_store.py).';
comment on column brain_decisions.id is
  'Row id, set by the database; the number typed to answer it. Example: 7 (python orchestrator.py answer 7 yes)';
comment on column brain_decisions.run_id is
  'The run this decision belongs to (brain_customer_runs.id). Example: 1';
comment on column brain_decisions.task_row_id is
  'The task it is about (brain_plan_tasks.id); null for plan approval, which is about the whole run. Example: 5';
comment on column brain_decisions.kind is
  'What is being decided. Allowed (checked in code): plan_approval, goal_check, email_approve, call_start, '
  'paid_call_unknown, task_failed, brain_review. Example: email_approve';
comment on column brain_decisions.status is
  'Allowed: open (waiting for an answer), answered. Example: open';
comment on column brain_decisions.question is
  'What was shown to the decider, with the allowed answers (JSON). Example: {"text": "Approve the sample emails?", "allowed_answers": ["no", "yes"]}';
comment on column brain_decisions.answer is
  'The answer; null while open. Allowed per kind (checked in code): yes/no; continue/more/stop; rerun/skip/stop; '
  'retry/skip/stop; done/retry/ask_human. Example: yes';
comment on column brain_decisions.decided_by is
  'Who answered; null while open. Allowed: user, auto, brain. Example: user';
comment on column brain_decisions.reason is
  'Why, in plain words (for plan_approval "no": the requested changes); may be null. Example: samples look right';
comment on column brain_decisions.created_at is
  'When the question was asked or the decision recorded (UTC). Example: 2026-10-09 10:33:00+00';
comment on column brain_decisions.answered_at is
  'When it was answered (UTC); null while open. Example: 2026-10-09 10:40:12+00';


-- The browser (anon) key cannot read these; the service key used by our code still can.
alter table brain_customer_runs enable row level security;
alter table brain_plan_tasks    enable row level security;
alter table brain_tool_calls    enable row level security;
alter table brain_decisions     enable row level security;

commit;
