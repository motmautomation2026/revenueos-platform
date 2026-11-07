# Diagnostics & per-tool testing — Phase 1 build spec

**Status:** draft for build · **Scope:** Phase 1 only (read-only diagnostics surface)
**Written for:** whoever builds the Diagnostics page (platform dev / the Brain team).

## Why
Today the tools (QuickICP data → Email → Calls, driven by the AI Brain) run as an automated
sequence. When the Brain or one tool stalls, a user cannot see **which** tool is stuck or verify a
tool **before** a run. This adds a user-facing way to **see per-tool health and status**, reusing the
machinery that already exists. Later phases add safe per-tool test runs and in-UI retry.

Most of the plumbing already exists — Phase 1 mostly **surfaces** it:

| Need | Already exists | Phase 1 action |
|---|---|---|
| Is the Brain alive? | `revenueos_brain_workers` heartbeat | Show online/offline |
| Which step failed / is waiting? | `brain_decisions` (`task_failed`, `email_approve`, `call_start`, `human_task`) | List open/failed decisions |
| Per-tool step results & errors | `brain_tool_calls` (`tool`, `step`, `status`, `error`) | Per-tool last status |
| Run state | `revenueos_brain_requests`, `brain_customer_runs` | Show run status |
| Config/connectivity health | `brain/check_system.py` (PASS/WARN/FAIL) | Phase 2 (see below) |
| Retry / skip / stop a step | `brain_decisions` answers (`retry`/`skip`/`stop`) | Phase 3 (show only in P1) |

## Phase 1 scope (and non-scope)
**In:** a **read-only** Diagnostics tab per project that shows Brain liveness, run status, per-tool
last step + error, and the list of open/failed decisions. No writes, no tool calls, no new Brain code.

**Out (later phases):** triggering safe test runs (Phase 2), in-UI Retry/Skip/Stop (Phase 3),
run-a-tool-in-isolation with a fixture (Phase 4).

## Where it lives
New route in the platform (Next.js App Router), under the existing execution area:
`app/(app)/projects/[id]/execution/diagnostics/page.tsx`, linked from the execution nav next to
Dashboard / Email / Calls. Owner + salesperson only (not `client`), same gating as the other
execution pages.

## Data sources (all reads; Supabase project `kiythcbtgvvkxdxwdmok`)
Resolve the project's `customer_key` from `revenueos_brain_requests.customer_key` (or the latest
request for `project_id`). Then:

1. **Brain worker** — `revenueos_brain_workers`: latest `last_seen_at`. Online if seen < 90s ago
   (matches the existing worker-online rule in the platform; reuse `lib/brain/status.ts`).
2. **Run** — `brain_customer_runs` (status) and `revenueos_brain_requests` (status, month,
   test_mode, error) for this `customer_key`.
3. **Per-tool steps** — `brain_tool_calls` joined to this run's tasks, grouped by `tool`
   (`quickicp*`, `email_tool`, `quickhowl`): latest `step`, `status`, `error`, `finished_at`.
4. **Decisions** — `brain_decisions` (join `brain_customer_runs` on `run_id`) where
   `status='open'` or `kind='task_failed'`: `kind`, `status`, `question.text`, `created_at`.

> Reads are server-side via the service-role client (`lib/supabase/admin`), the same as the other
> execution pages, because `brain_*` tables have no browser policies.

## UI (one page, four blocks)
```
┌─ Diagnostics ─ Raj multitech 2 ──────────────────────────────┐
│ Brain worker:  ● Online (seen 14s ago)                       │
│ Run:           running · month 2026-11 · TEST MODE           │
├──────────────────────────────────────────────────────────────┤
│ Tools                                                        │
│  ● Data (QuickICP)   last: T4 store · done        2026-11-02 │
│  ● Email (Outly)     last: T7 approve · done      2026-11-03 │
│  ⚠ Calls (Quickhowl) last: T8 start · blocked     2026-11-03 │
│                      error: "phone_number failed to connect" │
├──────────────────────────────────────────────────────────────┤
│ Waiting / failed                                             │
│  • T10 handover — human_task — OPEN since 2026-11-03         │
│  • T8 calls — task_failed — needs retry                      │
└──────────────────────────────────────────────────────────────┘
```
- Status dot: green = last step `done`; amber = `blocked`/open decision; red = `failed`/error or
  worker offline.
- Each tool row shows the **last** `brain_tool_calls` row for that tool, and its `error` if present.
- "Waiting / failed" lists open/failed `brain_decisions` with a plain-language line and a link to the
  Dashboard decision (where Retry/Skip/Stop already live today).

## Acceptance criteria
- [ ] Page loads for a `plan_ready` project; shows worker online/offline correctly.
- [ ] Each of the three tools shows its latest step + status; a tool with an error shows the error text.
- [ ] A blocked/failed step is visibly amber/red; a clean run is all green.
- [ ] Open decisions (incl. `human_task` / `task_failed`) are listed with a link to the Dashboard.
- [ ] Read-only: no writes, no tool calls, no Brain-state edits.
- [ ] `client` role cannot reach the page; owner + salesperson can.

## Later phases (noted, not built here)
- **Phase 2 — Safe smoke test per tool.** A "Test" button per tool that runs a harmless check:
  *Email* = generate samples / store a DRAFT (`auto_start:false`, nothing sent); *Calls* = store
  campaign `auto_start:false` (no dial) or a test-mode call to a TEST number; *QuickICP* = a tiny
  lookup (⚠ costs credits — explicit confirm). Surface `check_system.py` config health here too.
  **Use dedicated test accounts + fixtures, never live customer data.**
- **Phase 3 — In-UI Retry / Skip / Stop** wired to the existing `brain_decisions` answers.
- **Phase 4 — Run one tool in isolation** with a saved test fixture (handles the data dependency
  that, e.g., T7 email needs T4 contacts).

## Constraints
- **Data dependencies are real** — a tool can't truly run standalone without inputs; isolation
  (Phase 4) needs test fixtures/test mode.
- **Shared Brain state** — `brain_*` tables are a shared contract; any state change must go through
  the decision model (retry/skip/stop), never a hand-edit.
- **Separate test vs real** — Phase 2 must clearly label SAFE test (no sends/calls) vs a real run.
