import { test } from "node:test";
import assert from "node:assert/strict";
import { isCallSyncStale } from "../lib/brain/call-sync";

test("a campaign long after the last saved call is stale", () => {
  assert.equal(isCallSyncStale("2026-11-17T00:00:00Z", "2026-11-03T00:00:00Z"), true);
});

test("a campaign with no saved calls at all is stale", () => {
  assert.equal(isCallSyncStale("2026-11-17T00:00:00Z", null), true);
});

test("a call saved around/after the campaign is fine", () => {
  assert.equal(isCallSyncStale("2026-11-17T00:00:00Z", "2026-11-17T01:00:00Z"), false);
});

test("within the grace window is not flagged", () => {
  assert.equal(isCallSyncStale("2026-11-17T06:00:00Z", "2026-11-17T03:00:00Z"), false);
});

test("no campaign means nothing to flag", () => {
  assert.equal(isCallSyncStale(null, "2026-11-03T00:00:00Z"), false);
});

test("a bad timestamp never throws or false-alarms", () => {
  assert.equal(isCallSyncStale("not-a-date", "2026-11-03T00:00:00Z"), false);
});
