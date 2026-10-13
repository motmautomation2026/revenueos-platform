# archive/legacy: the old standalone flow

Moved here on 2026-10-13. Replaced by the orchestrator (`orchestrator.py`) and its runners
(`runner_quickicp_accounts.py`, `runner_quickicp_contacts.py`, `runner_email.py`, `runner_calls.py`),
which save every paid call in the ledger (`brain_tool_calls`) and ask every human decision through
`brain_decisions`.

| File | What it was |
|---|---|
| `pipeline.py` | ran companies -> people -> emails -> phones -> save from `data/` files |
| `goal_check.py` | the terminal goal-check prompt of the old loops (now the orchestrator's `goal_check` decision) |
| `email_campaign_payload.py` | an early email payload builder; nothing used it |
| `company_loop_cli.py` | `run_loop()` + `save_results()` + `__main__`, cut from `company_loop.py` word for word |
| `people_loop_cli.py` | `run_people_loop()` + `company_queue()` + `save_results()` + `__main__`, cut from `people_loop.py` |
| `quickhowl_cli.py` | `preview` / `launch` / `start` (stored and **dialled** outside the ledger and `call_start`) and the old state-file `status` / `results` / `watch` / `pause` |
| `email_tool_cli.py` | `preview` / `launch` / `approve` (**sent** outside the ledger and `email_approve`) and the old state-file `samples` |
| `quickicp_cli.py` | `companies` / `people` / `emails` / `phones` / `all` (**paid** QuickICP calls outside the ledger) |
| `validator_cli.py` | `companies` / `people` / `all` (judged stale `data/` files) and their file helpers |
| `supabase_store_cli.py` | the `save` command (wrote `data/` files to Supabase outside the ledger) |

The kept manual tools now take their campaign from the ledger: `python quickhowl.py status|results|watch|pause
--run <month> <task>` and `python email_tool.py samples --run <month> <task>`.

Kept for reference only. **Not runnable as is**, and never imported by the run path
(the orchestrator selftest's "imports" part checks this). Do not copy code from here back into the run path.
