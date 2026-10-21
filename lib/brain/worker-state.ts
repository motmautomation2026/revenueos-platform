// Is the Brain worker running? Pure rules over the heartbeat rows (lib/brain/worker-status.ts reads them).

export type WorkerRow = { worker: string; started_at: string; last_seen_at: string; note: string | null };

export type WorkerState =
  /** The heartbeat table is not there yet, or could not be read: say nothing. */
  | { state: "unknown" }
  | { state: "online"; worker: string; lastSeen: string; note: string }
  /** More than one worker is running: each customer's steps could run twice. */
  | { state: "several"; workers: string[] }
  | { state: "offline"; worker: string | null; lastSeen: string | null };

/** The worker writes every 30 seconds; two missed beats in a row and it counts as stopped. */
export const ONLINE_WITHIN_MS = 90_000;

export function workerState(rows: WorkerRow[], now: number): WorkerState {
  const fresh = rows.filter((row) => now - Date.parse(row.last_seen_at) <= ONLINE_WITHIN_MS);
  if (fresh.length > 1) return { state: "several", workers: fresh.map((row) => row.worker) };
  if (fresh.length === 1) {
    return { state: "online", worker: fresh[0].worker, lastSeen: fresh[0].last_seen_at, note: fresh[0].note ?? "" };
  }
  const latest = [...rows].sort((a, b) => Date.parse(b.last_seen_at) - Date.parse(a.last_seen_at))[0];
  return { state: "offline", worker: latest?.worker ?? null, lastSeen: latest?.last_seen_at ?? null };
}
