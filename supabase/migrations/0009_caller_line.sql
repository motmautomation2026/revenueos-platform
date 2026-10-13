-- A customer's own caller line: the phone number AI calls are made FROM.
--
-- A new calling account (Quickhowl) has no phone number connected. Quickhowl takes one, on the
-- account's first campaign, as three values from the telephony provider (Vobiz): the account id,
-- its auth token, and the number itself (the DID). They are kept with the customer's other tool
-- accounts; the Brain's worker hands them to the calling step (brain/run_for.py), which sends
-- them only when a campaign is sent.
--
-- The alternative, using a calling account that already has a line, needs nothing here: the
-- existing account's details are copied into the calls_* columns this table already has.

alter table public.revenueos_brain_accounts
  add column if not exists calls_vobiz_auth_id text,
  add column if not exists calls_vobiz_auth_token text,
  add column if not exists calls_did text;
