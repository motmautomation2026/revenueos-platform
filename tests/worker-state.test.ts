import assert from "node:assert/strict";
import { test } from "node:test";
import { lastActivityOf } from "../lib/brain/tools";
import { ONLINE_WITHIN_MS, workerState } from "../lib/brain/worker-state";

const now = Date.parse("2026-10-21T10:00:00Z");
const seen = (msAgo: number) => new Date(now - msAgo).toISOString();
const row = (worker: string, msAgo: number) => ({ worker, started_at: seen(3_600_000), last_seen_at: seen(msAgo), note: "1 step(s) running, 0 planning" });

test("a worker that reported in the last 90 seconds is online", () => {
  const state = workerState([row("revenueos-worker", 20_000)], now);
  assert.equal(state.state, "online");
  assert.equal(state.state === "online" && state.worker, "revenueos-worker");
});

test("two missed beats and the worker counts as stopped, with when it was last seen", () => {
  const state = workerState([row("revenueos-worker", ONLINE_WITHIN_MS + 1_000), row("DESKTOP-1MUJE7J", 3 * 86_400_000)], now);
  assert.deepEqual(state, { state: "offline", worker: "revenueos-worker", lastSeen: seen(ONLINE_WITHIN_MS + 1_000) });
  assert.deepEqual(workerState([], now), { state: "offline", worker: null, lastSeen: null });
});

test("two workers online at once are reported: a client's steps could run twice", () => {
  assert.deepEqual(workerState([row("revenueos-worker", 5_000), row("DESKTOP-1MUJE7J", 10_000)], now), {
    state: "several",
    workers: ["revenueos-worker", "DESKTOP-1MUJE7J"],
  });
});

test("a client's last activity is the latest thing that happened in its run", () => {
  const at = lastActivityOf({
    tasks: [{ started_at: "2026-10-20T09:00:00Z", finished_at: null }],
    decisions: [{ created_at: "2026-10-20T10:00:00Z", answered_at: "2026-10-21T08:30:00Z" }],
    toolCalls: [{ started_at: "2026-10-21T08:00:00Z", finished_at: "2026-10-21T08:05:00Z" }],
  } as never);
  assert.equal(at, "2026-10-21T08:30:00Z");
  assert.equal(lastActivityOf({ tasks: [], decisions: [], toolCalls: [] }), null);
});
