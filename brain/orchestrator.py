"""
orchestrator.py
===============

The brain's run controller. It loads the approved plan into a run, shows where the run stands,
records human answers, and moves the run forward ONE task per 'run' call, under a run lock.
Human tasks wait for a person's answer. Tool tasks run through a runner (WIRED_RUNNERS):
wired: quickicp_accounts (runner_quickicp_accounts.py), quickicp_contacts (runner_quickicp_contacts.py),
email (runner_email.py), calls (runner_calls.py).

WHY
---
Supabase (via state_store.py) is the single source of truth. Every task of the approved plan
becomes a row with a status, so the system can stop at any point and resume exactly there.
Tool runners follow runner_kit.py: save before acting, reuse existing Supabase data before any
paid call, a crash during a paid call -> unknown + a human decides. The brain review is code:
achieved vs target, at most 2 extra rounds per try, then a goal_check decision.

FLOW
----
    master_agent.py  ->  data/work_orders.json (approved)  ->  python orchestrator.py load 2026-10
        1. check the plan with CODE: unique task ids, every depends_on exists, no loops
        2. create the run  <CUSTOMER_KEY>-<YYYY-MM>  (e.g. anvixa-ai-product-2026-10), status 'planning'
        3. add one task per work order (status 'pending')
        4. record the plan approval decision (answered 'yes')
        5. run status -> 'running'
    Every step is safe to repeat: after a crash, 'load' again finishes what is missing.
    If work_orders.json CHANGED after the run was loaded, 'load' stops (never merges silently).

    A NEW PLAN FOR THE SAME MONTH (never mixed with the old run)
        python orchestrator.py close 2026-10 "replaced by the intake plan"   (type the run key to confirm)
            under the run lock: open decisions answered stop / no / reject (by 'user', note 'run closed:
            <reason>'), unfinished tasks -> 'stopped', run -> 'stopped' with 'CLOSED by user: <reason>'.
            Kept as is: done tasks, answered decisions, the ledger. Closing twice changes nothing.
        python orchestrator.py load 2026-10-r2   -> a separate run anvixa-ai-product-2026-10-r2 (needs proof)
            commands without a month (status, next, answer T8) then use it: it is the latest run.
        NOTE: the new run does not know whom the old run already emailed or called. Before REAL
        prospects, an 'already contacted in an earlier run' filter is needed (separate step, not built).

    status / next / decisions   read-only
    answer <id> <answer> "note" records one human answer (human tasks need a note)

    run 2026-10  (one step per call; nothing loops automatically)
        1. apply answered decisions:  done -> task done | skip -> task done, result.skipped = true
                                      stop -> ONLY that task stopped (note in last_error)
        2. crash recovery: a human task left 'running' gets its decision (the same one if it exists)
        3. start at most ONE ready task (earliest week first):
              human task (CAM, Content & Training, Human Tele Calling, LinkedIn Agent, Sales Engineer)
                  -> running -> decision opened -> waiting_human; the person does it, then answers
                  gate tasks (data_approval, content, client_training, readiness): done / stop only
              tool task  -> its runner (if wired): done / waiting for a decision / stopped / failed;
                            not wired yet: stays pending, printed as waiting
           at most ONE tool runner per call (continuing one, or starting one)
        4. run status: 'done' when every task is done; 'stopped' only when no task can move
           (nothing ready, waiting or running) and some task is stopped; stop_reason lists them
        Tasks that depend on a stopped task never start. Unrelated tasks keep going.
        RUN LOCK (sql/003): one 'run' at a time per run; a crashed run's lock expires after 30 min,
        or: python orchestrator.py unlock <YYYY-MM>  (type the run key to confirm)
        Tool decisions: goal_check (continue/more/stop), goal_check_final (continue/stop),
        paid_call_unknown (rerun/skip/stop), task_failed (retry/skip/stop),
        email_approve (yes/no: samples shown), call_start (yes/no: contacts shown).
        A tool task may also stay 'running' (e.g. calls in progress): the next 'run' continues it.

    CRASH SAFETY (part 1)
        crash while applying an answer        -> task still waiting_human: applied again next run
        crash after 'running', no decision    -> next run opens the decision, sets waiting_human
        crash after the decision was opened   -> the SAME decision is reused (one per task+kind)
        ... and answered before the next run  -> the answer is used in that run, never asked again
        tool task (not wired)                 -> nothing was written; it is reported again
        tool task (wired)                     -> see the CRASH SAFETY table in runner_quickicp_accounts.py

PLAN APPROVAL PROOF
-------------------
    master_agent.py writes data/plan_approval.json after approval (who, when, rule errors at
    approval, override + reason, fingerprints of work_orders.json and month_plan.json).
    'load' needs that proof whenever it records a NEW approval, checks it still matches the files,
    and stores it in the plan_approval decision. Without proof it stops and writes nothing.
    A run approved before proof existed (anvixa-ai-product-2026-10) keeps loading, and 'status'
    shows it honestly: "yes, approved BEFORE approval proof existed (no proof)". Proof is never
    invented afterwards.

WHO DECIDES WHAT
----------------
    Code : plan checks, run key, task order, which task is ready, allowed answers
    Human: approves the plan (master_agent.py), answers decisions here

READS
-----
    .env                         CUSTOMER_KEY, STATE_BACKEND (+ SUPABASE_* for the supabase backend)
    data/work_orders.json        approved work orders (master_agent.py)
    data/month_plan.json         the approved plan (saved into the run's plan JSON)
    data/revenue_analysis.json   the revenue analysis (saved in the run's plan JSON under 'revenue_analysis';
                                 required for a new run)
    data/understanding.json      the brain's understanding (saved into the run)
    data/plan_approval.json      approval proof (master_agent.py), checked by plan_approval.read_proof()
    state store                  brain_customer_runs, brain_plan_tasks, brain_decisions

WRITES (state store only)
-------------------------
    load       1 run, 1 task per work order, 1 plan_approval decision
    answer     the answer on one open decision
    close      answers on the run's open decisions, unfinished tasks + run -> 'stopped'
    selftest   rows of one selftest-* run, deleted at the end

RUN
---
    python orchestrator.py selftest                        test everything on a throwaway selftest-* run
                                                           (the tests live in orchestrator_selftest.py)
    python orchestrator.py load 2026-10                    load the approved plan as the run for that month
    python orchestrator.py status [2026-10]                tasks, next ready task, open decisions (read-only)
    python orchestrator.py next [2026-10]                  the next ready task in full (read-only, not started)
    python orchestrator.py decisions                       open decisions of this customer (read-only)
    python orchestrator.py answer <id> <answer> ["note"]   answer one open decision (note required for human tasks)
    python orchestrator.py run 2026-10                     one step: apply answers, start one ready task
    python orchestrator.py run 2026-10 --until-wait        steps until a person is needed, a tool is still
                                                           running, nothing is left, or the NEXT task would
                                                           buy data (QuickICP): that one needs a plain 'run'
    python orchestrator.py answer T11 yes "note"           a task id works when it has exactly ONE open decision
    python orchestrator.py run 2026-10 --dry               what the next tool task WOULD buy (LLM + Prospeo,
                                                           no paid search, nothing recorded)
    python orchestrator.py run 2026-10 --dry T7            the same for one wired tool task (e.g. T11, T7)
    python orchestrator.py review 2026-10                  the brain reviews done tool tasks against the plan:
                                                           code numbers + 1 LLM call (2 if it invents a number);
                                                           max 3 suggested actions -> accept / reject decisions
    python orchestrator.py unlock 2026-10                  free the run lock after a crash
    python orchestrator.py close 2026-10 "reason"          close a run a new plan replaces (then: load 2026-10-r2)
    selftest: fake QuickICP toolset, network guard (only Supabase allowed); on STATE_BACKEND=sqlite
              it uses a throwaway temp file, never data/brain_state.db
    Without a month, status/next use this customer's latest run.

DEBUGGING
---------
    "STOPPED: CUSTOMER_KEY is missing"         -> add CUSTOMER_KEY=<key> to .env
    "STOPPED: month must look like 2026-10"    -> give the month as YYYY-MM, or a revision 2026-10-r2
    "STOPPED: a reason is required"            -> close <YYYY-MM> "why this run is replaced"
    "STOPPED: tool task(s) ... still running"  -> close refused: let the tool finish (run again) first
    "STOPPED: run is busy: locked by ..."      -> another 'run' is working; close waits for it
    "STOPPED: plan check failed"               -> the printed task ids / depends_on are wrong in
                                                  data/work_orders.json; re-run master_agent.py
    "STOPPED: the approved plan changed ..."   -> work_orders.json differs from the loaded run;
                                                  load it as another month, or ask to replace the run
    "STOPPED: no approval proof"               -> run master_agent.py and approve the plan
    "STOPPED: data/revenue_analysis.json is missing" -> a new run needs it; re-run master_agent.py
    "STOPPED: ... changed after approval"      -> work_orders.json / month_plan.json edited after
                                                  approval; re-run master_agent.py
    "STOPPED: no run ... load it first"        -> run: python orchestrator.py load <YYYY-MM>
    "STOPPED: decision N belongs to another customer" -> check CUSTOMER_KEY in .env
    Look at the rows: Supabase tables brain_customer_runs / brain_plan_tasks / brain_decisions.

WHERE TO LOOK (the code lives in orch/, one file per job; this file is the entry point and re-exports
every name, so 'from orchestrator import X' keeps working)
-------------
    symptom                                             file             function
    load refuses / plan check fails / plan changed      orch/load.py     load_run, plan_errors, changed_tasks
    "no run ... load it first" / wrong run picked       orch/common.py   find_run, check_month, run_key_for
    status / next / decisions look wrong                orch/show.py     show_status, show_next, cmd_decisions
    a question shows wrong (samples, contacts)          orch/show.py     print_question
    an answer is refused / note rules                   orch/answer.py   cmd_answer, cmd_answer_task
    run stuck / wrong next task / human task            orch/run.py      run_step, apply_answers, start_human_task
    a tool task: which runner, its result, failed       orch/run.py      route, invoke_runner, apply_result, apply_task_failed
    resumes after crash / answered tool decision        orch/run.py      tool_task_to_resume
    --until-wait stops (run_for parses its text)        orch/run.py      until_wait_stop, cmd_run_until_wait
    --dry / review / unlock                             orch/run.py      cmd_dry, cmd_review, cmd_unlock
    close / replace a month                             orch/close.py    close_run, cmd_close, next_revision (orch/load.py)
    the command line itself                             orchestrator.py  cli (bottom of this file)
"""

import os
import re
import sys

from plan_approval import describe, read_proof
from quickicp import TEST_MODE, ToolError, load_json
from state_store import (
    ALLOWED_ANSWERS, answer_decision, add_tasks, check_customer_key, create_run, current_customer,
    force_unlock, get_decisions, get_run, get_runs, get_tasks, list_open_decisions, lock_holder,
    now_iso, open_decision, ready_tasks, record_decision, release_run_lock, renew_run_lock, take_run_lock, update_run,
    update_task,
)
from runner_kit import RunnerContext, RunnerResult, run_understanding
from runner_quickicp_accounts import RealAccountsTools, run_quickicp_accounts
from runner_quickicp_contacts import RealContactsTools, run_quickicp_contacts
from runner_email import RealEmailTools, run_email
from runner_calls import RealCallTools, run_calls

# Everything below the imports lives in orch/ (pure move); re-exported so every old name still works.
from orch.common import *  # noqa: F401,F403
from orch.load import *  # noqa: F401,F403
from orch.show import *  # noqa: F401,F403
from orch.run import *  # noqa: F401,F403
from orch.answer import *  # noqa: F401,F403
from orch.close import *  # noqa: F401,F403


# ================= RUN =================

USAGE = """Usage:
  python orchestrator.py selftest
  python orchestrator.py load <YYYY-MM>
  python orchestrator.py status [YYYY-MM]
  python orchestrator.py next [YYYY-MM]
  python orchestrator.py decisions
  python orchestrator.py answer <decision_id | task id like T11> <answer> ["note"]
  python orchestrator.py run <YYYY-MM> [--dry [TASK] | --until-wait]
  python orchestrator.py review <YYYY-MM>
  python orchestrator.py unlock <YYYY-MM>
  python orchestrator.py close <YYYY-MM> "reason"
  <YYYY-MM> may be a revision: 2026-10-r2"""

if __name__ == "__main__":
    from quickicp import run_cli

    def cli() -> None:
        """The command line; run_cli turns a network drop into one STOPPED line (--debug: traceback)."""
        args = sys.argv[1:]
        command = args[0] if args else ""
        try:
            if command == "selftest":
                from orchestrator_selftest import selftest      # the selftests live in their own file
                sys.exit(0 if selftest() else 1)
            elif command == "load" and len(args) == 2:
                cmd_load(args[1])
            elif command == "status" and len(args) <= 2:
                show_status(find_run(current_customer(), args[1] if len(args) == 2 else None))
            elif command == "next" and len(args) <= 2:
                show_next(find_run(current_customer(), args[1] if len(args) == 2 else None))
            elif command == "run" and len(args) in (3, 4) and args[2] == "--dry":
                cmd_dry(current_customer(), check_month(args[1]), args[3] if len(args) == 4 else None)
            elif command == "run" and len(args) == 2:
                cmd_run(current_customer(), check_month(args[1]))
            elif command == "run" and len(args) == 3 and args[2] == "--until-wait":
                cmd_run_until_wait(current_customer(), check_month(args[1]))
            elif command == "review" and len(args) == 2:
                cmd_review(current_customer(), check_month(args[1]))
            elif command == "unlock" and len(args) == 2:
                cmd_unlock(current_customer(), check_month(args[1]))
            elif command == "close" and len(args) == 3:
                cmd_close(current_customer(), args[1], args[2])
            elif command == "decisions":
                cmd_decisions(current_customer())
            elif command == "answer" and len(args) in (3, 4) and args[1].isdigit():
                cmd_answer(current_customer(), int(args[1]), args[2], args[3] if len(args) == 4 else None)
            elif command == "answer" and len(args) in (3, 4) and re.fullmatch(r"T\d+", args[1]):
                cmd_answer_task(current_customer(), args[1], args[2], args[3] if len(args) == 4 else None)
            else:
                print(USAGE)
                sys.exit(1)
        except ToolError as error:
            print(error)
            sys.exit(1)

    run_cli(cli)
