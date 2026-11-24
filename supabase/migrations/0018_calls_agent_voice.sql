-- Per-project AI calling voice. The Brain uses the agent's default voice (Simran, Hinglish) unless the
-- project picks another here. The voice id is a Sarvam persona valid only for its language, and the
-- agent's name/script must match the voice's persona, so the name, gender and language are stored too
-- (so the Brain needs no copy of the voice catalogue). Read by the Brain when it builds a call campaign
-- (quickhowl.build_request).
alter table revenueos_brain_accounts
  add column if not exists calls_voice_id text,
  add column if not exists calls_language text,
  add column if not exists calls_voice_name text,
  add column if not exists calls_voice_gender text;
