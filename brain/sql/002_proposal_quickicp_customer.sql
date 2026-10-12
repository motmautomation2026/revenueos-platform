-- 002_proposal_quickicp_customer.sql
-- Adds a customer column to the two QuickICP tables, so customers never mix.
--
-- Why: this Supabase project will hold many customers. Today the two QuickICP tables have no
-- customer column, so customer A's companies/people and customer B's would mix, and a company
-- saved for A would be skipped (seen as a duplicate) when B needs it.
--
-- What it does (nothing else in these tables is changed or commented):
--   1. adds a 'customer' column to both tables; existing rows (all Anvixa today) get 'anvixa-ai-product'
--   2. makes duplicates impossible PER CUSTOMER (the same company may exist once for each customer)
--   3. describes the new columns
--
-- Checked on 2026-10-09 (read-only): 192 companies, 0 duplicate websites, 0 empty websites;
-- 22 people, 0 duplicate user_social, 0 empty user_social. So the unique rules apply cleanly.
--
-- Until this is run, our code keeps customers apart in its own state (brain_tool_calls.output records
-- which company/people ids belong to which run and customer).
-- Runs in one transaction: if any line fails, nothing is changed.

begin;

alter table quickicp_revenueos_client_company_data
  add column customer text not null default 'anvixa-ai-product';
alter table quickicp_revenueos_client_people_data
  add column customer text not null default 'anvixa-ai-product';

alter table quickicp_revenueos_client_company_data
  add constraint quickicp_company_customer_website_unique unique (customer, website);
alter table quickicp_revenueos_client_people_data
  add constraint quickicp_people_customer_user_social_unique unique (customer, user_social);

comment on column quickicp_revenueos_client_company_data.customer is
  'Which MOTM customer this company was found for (lowercase, dashes); same value as brain_customer_runs.customer. '
  'One company may exist once per customer (unique with website). Example: anvixa-ai-product';
comment on column quickicp_revenueos_client_people_data.customer is
  'Which MOTM customer this person was found for (lowercase, dashes); same value as brain_customer_runs.customer. '
  'One person may exist once per customer (unique with user_social). Example: anvixa-ai-product';

commit;
