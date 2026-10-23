-- The password a client's tool workspaces are created with.
--
-- When a project is created, the admin sets a password for the client. A tool opens the client's
-- own account on its first launch (see brain/run_for.py): until now the first password was the
-- client's email itself. This column holds the admin's chosen password instead, so it is used when
-- the calling tool (Quickhowl) creates the account. It is written by the app with the server-side
-- key and read only by the Brain; like the other secrets here it is never sent back to the browser.
--
-- One password for both tools by design (same credential for the email and calling workspaces).
-- The email tool takes no login password yet, so today only the calling tool uses it.

alter table public.revenueos_brain_accounts
  add column if not exists workspace_password text;
