# MOTM Revenue OS: rules for Claude

## Commands that need explicit permission

Never run a command that does any of the following unless the developer explicitly says **"run it"**
for that command:

- calls an LLM (OpenAI), e.g. `master_agent.py`, `email_tool.py preview`, `quickhowl.py preview`, `pipeline.py` steps that judge or search
- calls a paid tool (QuickICP / n8n webhooks, Prospeo, email tool, Quickhowl)
- deletes or overwrites files (including anything in `data/`, which has no git backup)

Instead, give the exact PowerShell command and let the developer run it.
Test guards and logic by calling functions directly in a selftest (no LLM, no paid tool, throwaway
folder or `selftest-*` rows that are deleted at the end).
