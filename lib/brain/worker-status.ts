import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { workerState, type WorkerRow, type WorkerState } from "./worker-state";

/**
 * Whether a Brain worker is running, from the heartbeat it writes every 30 seconds
 * (supabase/migrations/0012_brain_worker_heartbeat.sql). Read with the server-side key: the
 * table has no policies.
 */
export async function loadWorkerStatus(): Promise<WorkerState> {
  try {
    const { data, error } = await createAdminClient()
      .from("revenueos_brain_workers")
      .select("worker, started_at, last_seen_at, note")
      .order("last_seen_at", { ascending: false })
      .limit(10)
      .returns<WorkerRow[]>();
    if (error) return { state: "unknown" };
    return workerState(data ?? [], Date.now());
  } catch {
    return { state: "unknown" };
  }
}
