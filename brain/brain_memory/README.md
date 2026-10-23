# brain_memory: lessons from earlier months

After a month's run, code collects its facts and the LLM writes up to 8 short lessons. A person approves them, and
from then on `master_agent` adds the approved lessons to its revenue-analysis and month-plan prompts.

## Commands (from the brain folder, CUSTOMER_KEY from .env)

    python -m brain_memory build --run 2026-10      facts (code, Supabase read-only) + 1 LLM call -> a DRAFT
    python -m brain_memory approve --run 2026-10    shows facts + lessons; type yes + your name -> APPROVED
    python -m brain_memory show                     the drafts, and the lessons master_agent would get now
    python -m brain_memory selftest                 fake reads, fake LLM, temp folder: no network, no LLM

`build` calls the LLM and `approve` writes one Supabase row, so both need the developer's "run it".

## How it decides

- **Facts are written by code.** Each fact gets an id (F1, F2, ...). They come from:
  - the run's tasks: target vs achieved;
  - call outcomes;
  - the run review's objections and data quality;
  - review actions a person accepted or rejected, with the note;
  - lead_qual verdicts from `data/leads_inbox.json`.
- **Tasks with no achieved number** (human / gate tasks) get only their status and the person's note, never "unmet".
- **TEST runs** (the run's `test_mode`):
  - Volume facts carry no numbers and say "test run: the volume is a test cap, not a result".
  - The LLM is told so.
  - The entry is marked `test: true`.
- **Lead facts** only count leads from THIS run's calls (call ids in its calls tasks' ledger). Other runs' calls and
  tele-calling / manual leads are left out.
- **Lessons are checked by code.** A lesson is dropped if it cites no fact id, cites an unknown id, or uses a number
  that is not in the facts it cites. Only the first 8 are kept. Every dropped lesson is listed with its reason.
- **Drafts** are saved in `data/memory/<customer>_memory.json`. Every build adds an entry, so the history is kept.
- **Approval** writes ONE answered `brain_decisions` row for that month's run, with these fields:
  - kind `memory`, answer `approved`, decided_by `user`, reason `approved by <name>`;
  - question = the entry (facts, lessons, approved_by, approved_at, sha256).

  It is never an open decision: `state_store` refuses to open kind `memory`.
- **Reading** (`lessons_text`, the only function master_agent uses) works like this:
  - It reads only approved rows of this CUSTOMER_KEY, and only those whose sha256 still matches (it covers `test`).
  - It leaves out TEST-run entries. master_agent includes them only in test mode with `--allow-test`
    (`python master_agent.py --intake --allow-test`), so a real plan never gets them.
  - It takes the latest approval of each month, for the 3 most recent months, newest first.
  - The text is capped at about 800 tokens (3200 characters), and the oldest lessons are cut first.
  - It ends with: "The customer's document and this month's numbers always win over these lessons."
- **No approved memory, or the read fails?** Then it returns `""`, and master_agent's prompts are exactly as before.
  Planning never stops for memory.

## Isolation (enforced by `python tools/baseline.py check-imports`)

- brain_memory may only use:
  - from `quickicp`: `ask_llm`, `run_cli`, `ToolError`;
  - from `state_store`: `get_runs`, `get_tasks`, `get_decisions`, `get_steps` and `record_decision`;
  - `ALLOWED_ANSWERS` and `open_decision`, in its selftest only.
- Nothing outside imports brain_memory except `master_agent.py`, and it imports only `lessons_text`.
