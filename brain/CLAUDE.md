# MOTM Revenue OS: rules for Claude

## Scope

We work only on brain logic and cost. We do NOT touch the platform connection or the platform
developers' work: `run_for.py`, `platform_approval.py`, `install_worker.ps1`, the worker, live/test
settings (`BRAIN_TEST_MODE`, staging limits, `REAL_CALLS_ENABLED`) or call/email go-live. Their code
stays exactly as they wrote it; a problem in it is reported to them, never fixed here.

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

- **Run path:** `intake_adapter.py` -> `master_agent.py` (+ `plan_approval.py`) -> `orchestrator.py` (entry point; its code is in `orch/`, one file per job, all re-exported) ->
  `runner_quickicp_accounts.py`, `runner_quickicp_contacts.py`, `runner_email.py`, `runner_calls.py`
  (via `runner_kit.py`) -> `brain_review.py`. State: `state_store.py`, `supabase_store.py`.
  Log: `orch/brain_log.py` -> `logs/brain-YYYY-MM-DD.log` (file only, masked; never stdout/stderr, never stops a run).
- **Shared tool code:** `quickicp.py` (incl. the one LLM factory `llm()` / `ask_llm`), `company_loop.py`,
  `people_loop.py` (round code only, no CLI), `validator.py`, `email_tool.py`, `quickhowl.py`.
- **Tests and checks:** `orchestrator_selftest.py` (run with `python orchestrator.py selftest`), `e2e_test.py selftest`,
  `intake_adapter.py selftest`, `validator.py selftest`, `check_system.py`.
- **Manual tools (kept):** `quickhowl.py status / results / watch / pause --run <month> <task>` and
  `email_tool.py samples --run <month> <task>` (the campaign comes from the ledger, never from a data/
  file; pause needs the full reference typed), `validator.py maps / reset-maps`, `extract_html.py`
  (intake fallback). `tools/email_live_check.py`: ONE real test-mode email check (email runner only,
  key `motm-emailcheck`, 3 read-only anvixa-ai-product people on our 3 test '+' inboxes; needs "run it").
  There are no CLI commands that launch, start, approve, buy or save outside the
  orchestrator: their old code is in `archive/legacy/*_cli.py`.
- **`archive/legacy/`:** the old standalone flow (`pipeline.py`, `goal_check.py`, `email_campaign_payload.py`,
  the old loop CLIs, the old CLI modes of quickhowl / email_tool / quickicp / validator / supabase_store). Reference only: never import it from, or copy it back into, the run path. The
  orchestrator selftest's "imports" part checks this.
- **Theirs, read-only (platform developers):** `run_for.py` (project choice, per-customer folders,
  the worker), `platform_approval.py` (plan approval in the platform), `install_worker.ps1`. See Scope.

## Sync with the platform repo (brain/ in the platform repo)

Patches only. Every sync point is a line in `PLATFORM_SYNC.txt`
(`date | direction | platform commit | master-agent commit | note`).

1. **Pull first, always.** Fetch the platform repo and run
   `git diff <last platform sync point>..origin/<their branch> -- brain/`. If anything is there,
   bring it in unchanged before any push: a new branch here, `git apply -p2 --cached --check <patch>`,
   then apply, write the files out, byte-compare every file with theirs (ignoring CRLF only), commit
   "Platform brain changes <sha>, unchanged", and add a line to `PLATFORM_SYNC.txt`.
2. **Then push, as a patch on a PR branch.** `git diff <last master-agent sync point>..master` here;
   in the platform clone a new branch `brain-sync-<date>` from their latest `origin/version1`;
   `git apply --directory=brain --3way --check`, then apply and commit. Pushing that branch and
   opening the PR need the developer's go. PRs target `version1` (the developers' rule for now), never main. After they merge, record the
   merge commit as the new platform sync point (so the next pull does not bring our changes back).
3. **Never:** copy whole files or folders over `brain/`, mirror, or `--delete`; apply a patch that
   touches their files or settings (see Scope): stop and ask. Any conflict: stop and report; it is
   never resolved by overwriting.
