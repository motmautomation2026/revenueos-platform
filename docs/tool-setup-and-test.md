# "Set up & test your tools" — build spec

**Status:** draft for build · **Written for:** the platform dev + the Brain team who build this.

## Why
Execution blasts email and AI calls to the real audience. This session showed the failures that causes
when a tool isn't proven first: a shared/colliding email sender, email landing who-knows-where, and —
repeatedly — **calls that never connect because no working caller line (Vobiz DID) was set.**

This feature adds a gate **after the GTM plan, before execution**: the user confirms the **sender
details** for the email and call tools, then sends **a real test to their own inbox / their own phone**.
Nothing hits the real audience until the user has seen a test email arrive and heard a test call.

## Where it lives
The **test actions live on the existing cards**, right where the sender/line is configured — not on a
separate page:
- **Email page** → the **"Sender account" card** (`components/brain/sender-card.tsx`): a **"Send test
  email"** action next to *Update sender details*.
- **Calls page** → the **"Calling account" card** (`components/brain/caller-card.tsx`): a **"Call my
  number"** action next to *Change*.

The **"Set up tools" page** (Phase 1, already built) stays as the **pre-run readiness overview + gate**:
it shows whether each tool is ready and routes to those cards. Owner-only writes (same as today). A soft
gate: starting execution warns (or blocks, configurable) if sender details are incomplete; testing is
strongly recommended. Putting the test on the Calling account card is especially valuable — it proves a
**borrowed** account (e.g. "Anvika Ai's calling account / motm-tech") actually dials, which the readiness
flag alone cannot.

## Scope
**In:** confirm/edit sender details for both tools; one-click **test email** to a user-supplied address;
one-click **test call** to a user-supplied number; store and surface a "tested ✓" result.
**Out (later):** scheduled re-tests, deliverability scoring, multi-recipient tests.

---

## Part A — Sender details (mostly exists; surface + require)
These already live in `public.revenueos_brain_accounts` and are edited on **Execution → Accounts**
(`lib/brain/accounts.ts`, `ACCOUNT_FIELDS`). Reuse that form here.

- **Email:** `email_client_reference`, `email_user_email` (the sending account).
- **Calls:** `calls_client_reference`, `calls_user_email`, `calls_user_full_name`, `calls_user_company`,
  and the **caller line** — `calls_vobiz_auth_id`, `calls_vobiz_auth_token`, `calls_did`.

`missingAccountFields()` (already in `accounts.ts`) computes which are empty — use it to drive the
"complete?" state and the execution-start warning. Writes are server-side (service-role key), owner-only,
exactly as the Accounts page does today (secrets never returned to the browser).

---

## Part B — The test actions (new; the valuable part)
The platform **cannot send** — it has no tool credentials. The **Brain** does. So a test is a request the
Brain fulfils and reports back.

### Data model — new table `public.revenueos_brain_tool_tests`
```sql
create table public.revenueos_brain_tool_tests (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references public.revenueos_projects(id) on delete cascade,
  tool        text not null check (tool in ('email','calls')),
  target      text not null,                 -- the user's own email or E.164 phone
  status      text not null default 'requested'
              check (status in ('requested','running','passed','failed')),
  result      jsonb,                          -- tool response (below)
  error       text,
  requested_by uuid,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
-- No browser policies: written by the app (service-role) and the Brain worker only.
```

### Flow
1. **Platform** (owner-only server action): validate `target` (email / E.164), insert a `requested` row.
   Refuse a second test for the same tool while one is `requested`/`running`.
2. **Brain worker**: in its loop, pick up `requested` rows for its customers, set `running`, run the tool
   in **test mode to the single `target`**, then write `passed`/`failed` + `result`/`error`.
3. **Platform**: the page polls the row (or revalidates) and shows the outcome + a persistent
   "Email tested ✓ · 2 min ago" once `passed`.

### Email test — Brain side
Send **one** message to `target` immediately (no audience, no approval round). Reuse `email_tool.py` with
a one-recipient payload; **confirm the exact "send test" path with the email-tool owner** — options:
a one-recipient campaign with immediate start, or a dedicated test endpoint if the v2 API exposes one.
`result` carries `{ message_status, provider_message_id?, mailbox_status, error? }`. UI copy on success:
*"Sent — check your inbox **and spam**."* (deliverability is the sender domain's reputation).

### Call test — Brain side (the big win)
Place **one** real call to `target` via `quickhowl.py`: a one-contact campaign, `auto_start: true`, test
mode, using the project's **caller line** (Vobiz). The Brain polls the campaign's calls endpoint and writes
`result = { status, outcome, duration_seconds, has_recording, start_status, error }`.
- If the caller line is missing/invalid, the launch returns `start_status: "blocked"` with the reason →
  `failed` with that exact message. **This is precisely the check that catches the "no working line"
  failure before a campaign.**
- On connect, the user hears the agent (voice + script quality check) and the row is `passed`.

---

## "Tested ✓" surfacing
- On this page: a green badge per tool with the timestamp and (for calls) duration/outcome.
- On **Diagnostics**: add a line per tool — "Email: tested ✓ 5 min ago" / "Calls: not tested yet".
- **Execution start gate:** `requestExecution` warns if sender details are incomplete
  (`missingAccountFields`) and shows "tools not tested yet" — block on incomplete, warn on untested
  (final policy is a product choice).

## Safety
- **Test target is the user's own address/number only**, entered each time, clearly labelled **TEST**.
- One in-flight test per tool; rate-limit (e.g. ≤5/hour/project).
- A test call costs one call and needs the caller line set first — which is the point.
- Secrets (Vobiz token, password) never leave the server; same handling as Accounts.

## Acceptance criteria
- [ ] After plan-ready, the Setup page shows sender details (prefilled) and flags any missing field.
- [ ] "Send test email" to a valid address inserts a request; the Brain sends one email; the page shows
      passed/failed with the tool's message status.
- [ ] "Call my number" to a valid E.164 inserts a request; the Brain places one real call; the page shows
      connected/duration or the exact block reason (e.g. caller line not set).
- [ ] A passed test persists as "tested ✓ <time>" here and on Diagnostics.
- [ ] Owner-only writes; `client` role cannot reach it; secrets never sent to the browser.
- [ ] Starting execution with incomplete sender details is warned/blocked per the chosen policy.

## Phasing
1. **Setup page + sender details** (reuses Accounts form) + the completeness gate. Platform-only.
2. **Test table + Brain worker handler + the two test functions** (email send, call). Brain + migration.
3. **"Tested ✓" surfacing** on Diagnostics + the execution-start gate.

## Open questions for the owners
- Email: the exact one-recipient **immediate** send path in the v2 email API (no approval round)?
- Calls: confirm a one-contact `auto_start` test call is billed/cleaned up as expected.
- Policy: **block** vs **warn** on "not tested yet" before first execution.
