-- Counts-only window onto the email tool's records, for the RevenueOS Email page.
--
-- The email tool keeps its tables closed to the Supabase API roles on purpose: it has its own
-- restricted roles and row-level policies. This function does not change any of that. It
-- returns numbers only for the campaigns asked for: how many recipients and messages are in
-- each state, and how many events, outcomes and replies were recorded. No email address,
-- subject or message text can be read through it.
--
-- Only the server-side key (service_role) may call it.

create or replace function public.revenueos_email_campaign_stats(p_campaign_ids uuid[])
returns table (
  campaign_id uuid,
  campaign_status text,
  recipients integer,
  recipients_by_state jsonb,
  messages integer,
  messages_by_status jsonb,
  accepted integer,
  first_accepted_at timestamptz,
  last_accepted_at timestamptz,
  next_due_at timestamptz,
  failure_reasons jsonb,
  events_by_kind jsonb,
  outcomes_by_kind jsonb,
  replies integer
)
language sql
stable
security definer
set search_path = ''
as $$
  select
    c.id,
    c.status,
    (select count(*)::int from public.campaign_enrollments e where e.campaign_id = c.id),
    coalesce((
      select jsonb_object_agg(s.state, s.n)
      from (select e.state, count(*) n from public.campaign_enrollments e where e.campaign_id = c.id group by e.state) s
    ), '{}'::jsonb),
    (select count(*)::int from public.messages m where m.campaign_id = c.id),
    coalesce((
      select jsonb_object_agg(s.status, s.n)
      from (select m.status, count(*) n from public.messages m where m.campaign_id = c.id group by m.status) s
    ), '{}'::jsonb),
    (select count(*)::int from public.messages m where m.campaign_id = c.id and m.accepted_at is not null),
    (select min(m.accepted_at) from public.messages m where m.campaign_id = c.id),
    (select max(m.accepted_at) from public.messages m where m.campaign_id = c.id),
    (select min(m.due_at) from public.messages m where m.campaign_id = c.id and m.accepted_at is null and m.terminal_reason is null),
    coalesce((
      select jsonb_object_agg(s.reason, s.n)
      from (
        select m.terminal_reason reason, count(*) n
        from public.messages m
        where m.campaign_id = c.id and m.terminal_reason is not null
        group by m.terminal_reason
      ) s
    ), '{}'::jsonb),
    coalesce((
      select jsonb_object_agg(s.kind, s.n)
      from (
        select ev.kind, count(distinct ev.message_id) n
        from public.message_events ev
        join public.messages m on m.id = ev.message_id
        where m.campaign_id = c.id
        group by ev.kind
      ) s
    ), '{}'::jsonb),
    coalesce((
      select jsonb_object_agg(s.kind, s.n)
      from (
        select o.kind, count(distinct o.enrollment_id) n
        from public.recipient_outcomes o
        join public.campaign_enrollments e on e.id = o.enrollment_id
        where e.campaign_id = c.id
        group by o.kind
      ) s
    ), '{}'::jsonb),
    (
      select count(*)::int
      from public.inbound_messages im
      where im.conversation_id in (
        select m.conversation_id from public.messages m
        where m.campaign_id = c.id and m.conversation_id is not null
      )
    )
  from public.campaigns c
  -- At most 50 campaigns per call, so it cannot be used to sweep the whole table.
  where c.id = any (p_campaign_ids[1:50]);
$$;

comment on function public.revenueos_email_campaign_stats(uuid[]) is
  'Counts only (no addresses or message text) for the given email campaigns, for the RevenueOS Email page. Callable by service_role only.';

revoke all on function public.revenueos_email_campaign_stats(uuid[]) from public, anon, authenticated;
grant execute on function public.revenueos_email_campaign_stats(uuid[]) to service_role;
