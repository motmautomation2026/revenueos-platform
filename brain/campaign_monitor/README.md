# campaign_monitor: track every campaign until it really ends

A calls or email campaign keeps working for days, or for a month, after the brain launched it. The calls and email
runners stop watching on day 1: calls when Quickhowl says finished, email as soon as the campaign is RUNNING.
`campaign_monitor` keeps checking every launched campaign until the tool says finished AND nothing is left. When a
day had expected activity but none happened (a stall), it logs the stall and raises it to a person.

## Commands (CUSTOMER_KEY from .env)

    python -m campaign_monitor check [--run 2026-10-r2]   check every tracked campaign now
    python -m campaign_monitor status                     tracked / finished, last check, stalls, callbacks (ledger only)
    python -m campaign_monitor selftest                   fakes only: no network, no tool, no Supabase

- `check` makes only FREE reads (Quickhowl status + calls). Its first real run still needs the developer's "run it"
  (CLAUDE.md: even read-only tool commands call the tool's API).
- Exit codes of `check`:
  - 0 = every campaign on track;
  - 2 = a NEW stall was raised;
  - 1 = stopped, or a read failed.
- The last line always starts with `MONITOR:`.

## How it decides (code, `expect.py`, India time)

- **Which campaigns** (`find.py`, from the ledger, any run status):
  - calls: the done `start` step of a Quickhowl calls task;
  - email: an approved `launch` of an Email Agent task.
  - A campaign is tracked until its last monitor step says finished.
- **A day is judged only after its window has closed.**
- **Calls:**
  - Activity is expected on a calling day: the tool's window, else 09:00-19:00 Mon-Sat.
  - It is not expected after the campaign ran out of work (finished, or nothing left to call), while it is paused,
    or on a launch day with less than 1 hour of window left.
  - Finished = status completed / failed / paused, 0 still to call, every call finished, nothing scheduled.
  - `callback_requested` calls are listed for the lead inbox in the check's output (`callbacks`). There is no
    lead_qual import.
- **Email:**
  - Step 1 is due on the launch day. Step k is due on (the day step k-1 was sent + its `wait_days`), moved to the
    next sending weekday (the schedule's weekdays: Mon-Fri).
  - Bounced, replied and unsubscribed people leave the sequence.
  - A day expects the people due (plus carry-over), at most `daily_limit`.
  - Results are read per recipient and per step: sent, delivered, bounced, opened, replied, unsubscribed.
  - **Not available yet:** the real email adapter (`tools.py`) returns "not available" for status and results
    until the results endpoint exists (part 7). The check says so and never calls that a stall.
- **Stall** = a judged day with expected activity but none.

## What a check writes

- **The ledger:** one FREE step on the campaign's own task. Step `monitor`, key `monitor:<reference>:<n>`. Its
  output holds the per-day expected vs actual, finished, the stalled days, the alerts and the callbacks.
- **The log:** one line per check in `logs/brain-YYYY-MM-DD.log` (WARNING when stalled).
- **The alert:** one `brain_decisions` row per campaign per stalled day:
  - kind `campaign_stalled`, answer `raised`, decided_by `brain`, with the reason and the day;
  - it is ANSWERED, never open (`state_store` refuses to open the kind), so it blocks nothing;
  - it shows in the platform's activity feed as "Campaign stalled".
- **The open-alert switch:** `config.OPEN_ALERTS` (off) would open alerts for a person instead. Switch it on
  only after:
  - the platform knows the kind (`lib/brain/answers.ts` and the People's tasks screen);
  - `state_store` lets it be opened, with a closing answer (e.g. `seen` / `stop`).
- **Never touched:** task states, runs, the orchestrator's output, run_for and the worker.

## For the worker (their side, later; no existing command changes)

- In each customer's folder, with that customer's `.env`, run `python -m campaign_monitor check` every 2-3 hours,
  plus once after 19:30 India time.
- Show the last line (`MONITOR: ...`).
- Exit code 2 = a new stall was raised.

## Isolation (enforced by `python tools/baseline.py check-imports`)

- campaign_monitor may only use:
  - from `quickicp`: `run_cli`, `ToolError`;
  - from `state_store`: reads, `start_step`, `finish_step`, `record_decision`, `open_decision`,
    `current_customer`;
  - from `quickhowl`: `campaign_status`, `cmd_results` (free reads only);
  - from `orch`: `brain_log.write`, `check_month`.
- Nothing imports campaign_monitor.
