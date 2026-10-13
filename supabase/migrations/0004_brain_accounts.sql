-- Each customer's own tool accounts, instead of one shared set typed into the Brain's .env.
--
-- The email tool and the calling tool each work under an account. Until now there was one of
-- each, in the Brain's settings file, so every customer would have emailed and called from the
-- same account. One row here per project holds that customer's accounts; the Brain's worker
-- hands them to the tools when it runs that customer (brain/run_for.py).
--
-- Written by the app (Tools > Tool accounts), read by the Brain. Both use the server-side key;
-- there are no policies, so the browser can never read this table, and the app never sends the
-- password back to the browser.

create table if not exists public.revenueos_brain_accounts (
  project_id uuid primary key references public.revenueos_projects (id) on delete cascade,

  -- Email tool: the existing account campaigns are sent from, and its admin user.
  email_client_reference text,
  email_user_email text,

  -- Calling tool (Quickhowl): the account and the user campaigns are created under.
  calls_client_reference text,
  calls_user_email text,
  calls_user_password text,
  calls_user_full_name text,
  calls_user_company text,

  updated_by uuid references auth.users (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

drop trigger if exists set_updated_at on public.revenueos_brain_accounts;
create trigger set_updated_at before update on public.revenueos_brain_accounts
  for each row execute function public.revenueos_set_updated_at();

alter table public.revenueos_brain_accounts enable row level security;
revoke all on public.revenueos_brain_accounts from anon, authenticated;
