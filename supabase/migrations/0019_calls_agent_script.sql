-- Per-project edited AI calling script. When set, the Brain uses this opening line + instructions instead
-- of writing them with the LLM (quickhowl.build_request / make_profile). The script is written for a
-- language, so that is stored too: the Brain ignores the edit if the project's voice language no longer
-- matches (and falls back to an auto-written script in the right language). The edited text still goes
-- through the Brain's "no internal words" safety check before any call.
alter table revenueos_brain_accounts
  add column if not exists calls_welcome_message text,
  add column if not exists calls_system_prompt text,
  add column if not exists calls_script_language text;
