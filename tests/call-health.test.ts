import { test } from "node:test";
import assert from "node:assert/strict";
import { callHealth, type CallOutcome } from "../lib/brain/tools";

const o = (status: string, durationSeconds: number | null = null): CallOutcome => ({
  name: "x",
  company: "y",
  outcome: "pending",
  status,
  durationSeconds,
  summary: "",
});

test("every call failed -> noneConnected", () => {
  const h = callHealth([o("Failed"), o("Failed"), o("Failed"), o("Failed"), o("Failed")]);
  assert.deepEqual(h, { attempted: 5, connected: 0, failed: 5, noneConnected: true });
});

test("a call with talk time counts as connected", () => {
  const h = callHealth([o("Failed"), o("Completed", 42)]);
  assert.equal(h.connected, 1);
  assert.equal(h.noneConnected, false);
});

test("a completed status counts as connected even with null duration", () => {
  assert.equal(callHealth([o("Completed")]).connected, 1);
});

test("no calls yet is not flagged", () => {
  assert.deepEqual(callHealth([]), { attempted: 0, connected: 0, failed: 0, noneConnected: false });
});

test("status matching ignores case and spacing", () => {
  assert.equal(callHealth([o("  failed ")]).failed, 1);
  assert.equal(callHealth([o("COMPLETED")]).connected, 1);
});
