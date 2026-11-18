-- Account-making acknowledgement: the last outcome of creating each tool account for a customer.
--
-- A project's Mail + Password (revenueos_projects.customer_email + revenueos_brain_accounts.
-- workspace_password) is used to open that customer's own account on each tool. Until now there was
-- no way to tell, in the app, whether that account was actually created, reused, or failed. These
-- columns hold the last known outcome per tool so the UI can acknowledge it. They are set by the
-- platform (lib/brain/account-status.ts, read back from the Brain's brain_tool_calls ledger) and are
-- purely informational — the Brain does not read them.
--
-- status: 'created' | 'reused' | 'error' | 'unknown' (null = never checked).

alter table public.revenueos_brain_accounts
  add column if not exists email_account_status text,
  add column if not exists email_account_detail text,
  add column if not exists email_account_checked_at timestamptz,
  add column if not exists calls_account_status text,
  add column if not exists calls_account_detail text,
  add column if not exists calls_account_checked_at timestamptz;
