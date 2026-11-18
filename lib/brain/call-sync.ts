import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { brainCustomerFor } from "./read";

// Call-sync health. The voice platform (Quickhowl) writes call records into the shared `calls` table;
// the CRM and this app read them. If that write path stops (as it did ~Nov 2-3 2026), calls silently
// vanish from the Calls page and the CRM. This surfaces that: it compares when this customer last
// launched a call campaign (from the Brain ledger, which the app CAN read) with the newest row in the
// `calls` table. A campaign launched well after the last saved call means calls are not syncing.
//
// We cannot read the CRM's brain_feed_* views here (migration 018 revokes them from service_role), so
// we read the underlying `calls` table and `brain_tool_calls` directly.

/** Grace for processing lag: a campaign can be a few hours ahead of its first saved call legitimately. */
export const CALL_SYNC_GRACE_MS = 6 * 60 * 60 * 1000;

export type CallSyncHealth =
  | { state: "ok" }
  | { state: "none" } // no call campaign for this customer, so nothing to check
  | { state: "stale"; lastCallAt: string | null; lastCampaignAt: string };

/**
 * Pure rule: a campaign launched more than the grace period after the newest saved call (or with no
 * saved calls at all) means the voice platform is not writing call records. Exported for tests.
 */
export function isCallSyncStale(
  lastCampaignAt: string | null,
  lastCallAt: string | null,
  graceMs: number = CALL_SYNC_GRACE_MS
): boolean {
  if (!lastCampaignAt) return false;
  const campaignMs = new Date(lastCampaignAt).getTime();
  if (!Number.isFinite(campaignMs)) return false;
  const callMs = lastCallAt ? new Date(lastCallAt).getTime() : 0;
  return campaignMs - (Number.isFinite(callMs) ? callMs : 0) > graceMs;
}

export async function loadCallSyncHealth(projectId: string): Promise<CallSyncHealth> {
  const customer = await brainCustomerFor(projectId);
  if (!customer) return { state: "none" };

  try {
    const admin = createAdminClient();

    const { data: runs } = await admin
      .from("brain_customer_runs")
      .select("id")
      .eq("customer", customer)
      .order("id", { ascending: false })
      .limit(10)
      .returns<{ id: number }[]>();
    if (!runs?.length) return { state: "none" };

    const { data: tasks } = await admin
      .from("brain_plan_tasks")
      .select("id")
      .in("run_id", runs.map((r) => r.id))
      .returns<{ id: number }[]>();
    const taskIds = (tasks ?? []).map((t) => t.id);
    if (!taskIds.length) return { state: "none" };

    // When this customer last handed a call campaign to Quickhowl.
    const { data: launches } = await admin
      .from("brain_tool_calls")
      .select("finished_at")
      .in("task_row_id", taskIds)
      .eq("tool", "quickhowl")
      .in("step", ["store", "start"])
      .eq("status", "done")
      .order("id", { ascending: false })
      .limit(1)
      .returns<{ finished_at: string | null }[]>();
    const lastCampaignAt = launches?.[0]?.finished_at ?? null;
    if (!lastCampaignAt) return { state: "none" };

    // Newest call record the voice platform has written (shared across customers; the write path is
    // all-or-nothing, so a global freshness check reliably catches an outage).
    const { data: latest } = await admin
      .from("calls")
      .select("created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .returns<{ created_at: string | null }[]>();
    const lastCallAt = latest?.[0]?.created_at ?? null;

    return isCallSyncStale(lastCampaignAt, lastCallAt)
      ? { state: "stale", lastCallAt, lastCampaignAt }
      : { state: "ok" };
  } catch {
    return { state: "none" };
  }
}
