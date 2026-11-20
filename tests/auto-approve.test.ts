import { test } from "node:test";
import assert from "node:assert/strict";
import { autoApproveColumns, readAutoApprove, AUTO_APPROVE_DEFAULT_SECONDS } from "../lib/brain/auto-approve";

test("on writes the number of seconds; off writes null", () => {
  assert.deepEqual(autoApproveColumns({ enabled: true, seconds: 5 }), { auto_approve_seconds: 5 });
  assert.deepEqual(autoApproveColumns({ enabled: false, seconds: 5 }), { auto_approve_seconds: null });
});

test("reads a saved positive value as enabled", () => {
  assert.deepEqual(readAutoApprove({ auto_approve_seconds: 5 }), { available: true, enabled: true, seconds: 5 });
});

test("null is available but off, with the default shown in the field", () => {
  assert.deepEqual(readAutoApprove({ auto_approve_seconds: null }), {
    available: true,
    enabled: false,
    seconds: AUTO_APPROVE_DEFAULT_SECONDS,
  });
});

test("a missing column (before the migration) is not available", () => {
  const state = readAutoApprove({});
  assert.equal(state.available, false);
  assert.equal(state.enabled, false);
});

test("zero or negative is treated as off", () => {
  assert.equal(readAutoApprove({ auto_approve_seconds: 0 }).enabled, false);
  assert.equal(readAutoApprove({ auto_approve_seconds: -1 }).enabled, false);
});
