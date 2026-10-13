-- The customer's email, asked when a project is created.
--
-- It is the login of the customer's own account in the email tool: the Brain sends it as the
-- account's user when it launches that customer's first campaign, and must keep sending exactly
-- the same one afterwards (the tool refuses a different user for the same account).

alter table public.revenueos_projects add column if not exists customer_email text;

comment on column public.revenueos_projects.customer_email is
  'The customer''s email: the login of their account in the email tool. Asked on the New project form.';
