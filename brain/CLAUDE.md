# MOTM Revenue OS: rules for Claude

## Commands that need explicit permission

Never run a command that does any of the following unless the developer explicitly says **"run it"**
for that command:

- calls an LLM (OpenAI), e.g. `master_agent.py`, `e2e_test.py`, `orchestrator.py run` steps that judge, plan or review
- calls a paid tool (QuickICP / n8n webhooks, Prospeo, email tool, Quickhowl), including the manual
  tools below (`quickhowl.py pause` changes a live campaign; even the read-only ones call the tool's API)
- deletes or overwrites files (including anything in `data/`, which has no git backup)

Instead, give the exact PowerShell command and let the developer run it.
Test guards and logic by calling functions directly in a selftest (no LLM, no paid tool, throwaway
folder or `selftest-*` rows that are deleted at the end).

## Files

- **Run path:** `intake_adapter.py` -> `master_agent.py` (+ `plan_approval.py`) -> `orchestrator.py` ->
  `runner_quickicp_accounts.py`, `runner_quickicp_contacts.py`, `runner_email.py`, `runner_calls.py`
  (via `runner_kit.py`) -> `brain_review.py`. State: `state_store.py`, `supabase_store.py`.
- **Shared tool code:** `quickicp.py` (incl. the one LLM factory `llm()` / `ask_llm`), `company_loop.py`,
  `people_loop.py` (round code only, no CLI), `validator.py`, `email_tool.py`, `quickhowl.py`.
- **Tests and checks:** `orchestrator_selftest.py` (run with `python orchestrator.py selftest`), `e2e_test.py selftest`,
  `intake_adapter.py selftest`, `validator.py selftest`, `check_system.py`.
- **Manual tools (kept):** `quickhowl.py status / results / watch / pause --run <month> <task>` and
  `email_tool.py samples --run <month> <task>` (the campaign comes from the ledger, never from a data/
  file; pause needs the full reference typed), `validator.py maps / reset-maps`, `extract_html.py`
  (intake fallback). There are no CLI commands that launch, start, approve, buy or save outside the
  orchestrator: their old code is in `archive/legacy/*_cli.py`.
- **`archive/legacy/`:** the old standalone flow (`pipeline.py`, `goal_check.py`, `email_campaign_payload.py`,
  the old loop CLIs, the old CLI modes of quickhowl / email_tool / quickicp / validator / supabase_store). Reference only: never import it from, or copy it back into, the run path. The
  orchestrator selftest's "imports" part checks this.
