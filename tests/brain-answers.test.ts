import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { ALLOWED_ANSWERS, afterAnswer, answerOptions, answerProblem, noteRequired } from "../lib/brain/answers";
import { settingsCustomerFor } from "../lib/brain/read";

test("the app allows exactly the answers the Brain's own code allows", () => {
  // Read the Brain's table straight from its source, so a change on either side fails this test.
  const source = readFileSync("brain/state_store.py", "utf8");
  const block = source.match(/^ALLOWED_ANSWERS = \{[\s\S]*?^\}/m)?.[0] ?? "";
  assert.ok(block, "ALLOWED_ANSWERS not found in brain/state_store.py");
  const brain: Record<string, string[]> = {};
  for (const match of block.matchAll(/"(\w+)":\s*\{([^}]*)\}/g)) {
    brain[match[1]] = [...match[2].matchAll(/"(\w+)"/g)].map((m) => m[1]).sort();
  }
  // These two are recorded by the Brain itself and are never open for a person.
  delete brain.brain_review;
  delete brain.run_review;

  const app = Object.fromEntries(Object.entries(ALLOWED_ANSWERS).map(([kind, answers]) => [kind, [...answers].sort()]));
  assert.deepEqual(app, brain);
});

test("the app requires a note for the same kinds the Brain does", () => {
  const source = readFileSync("brain/orchestrator.py", "utf8");
  assert.match(source, /HUMAN_KINDS = \{"human_task", "human_gate_task"\}/);
  assert.match(source, /NOTE_REQUIRED_KINDS = HUMAN_KINDS \| \{"review_action_1", "review_action_2", "review_action_3"\}/);
  for (const kind of ["human_task", "human_gate_task", "review_action_1", "review_action_2", "review_action_3"]) {
    assert.ok(noteRequired(kind), kind);
  }
  for (const kind of ["email_approve", "call_start", "task_failed", "goal_check"]) assert.ok(!noteRequired(kind), kind);
});

test("answers are refused unless allowed, with a note where one is required", () => {
  assert.equal(answerProblem("email_approve", "yes", ""), null);
  assert.match(answerProblem("email_approve", "done", "")!, /Choose one of: yes, no/);
  assert.match(answerProblem("human_gate_task", "skip", "note")!, /Choose one of: done, stop/);
  assert.match(answerProblem("human_task", "done", "  ")!, /Add a short note/);
  assert.equal(answerProblem("human_task", "done", "Meeting held 10 Oct"), null);
  assert.match(answerProblem("run_review", "saved", "")!, /cannot be answered/);
  assert.match(answerProblem("something_new", "yes", "")!, /cannot be answered/);
});

test("buttons: positive answer first, and risky answers ask for confirmation", () => {
  assert.deepEqual(answerOptions("email_approve", ["no", "yes"]).map((o) => [o.answer, o.label, !!o.confirm]), [
    ["yes", "Approve", true],
    ["no", "Do not approve", false],
  ]);
  assert.deepEqual(answerOptions("human_gate_task", ["done", "stop"]).map((o) => [o.answer, o.tone, !!o.confirm]), [
    ["done", "primary", false],
    ["stop", "danger", true],
  ]);
  assert.match(answerOptions("paid_call_unknown", []).find((o) => o.answer === "rerun")!.confirm!, /twice/);
  // Only what the Brain offered for this question is shown.
  assert.deepEqual(answerOptions("human_task", ["done", "stop"]).map((o) => o.answer), ["done", "stop"]);
  assert.deepEqual(answerOptions("run_review", ["saved"]), []);
});

test("the message after answering says nothing runs from the app", () => {
  assert.match(afterAnswer("review_action_2"), /nothing runs automatically/);
  assert.match(afterAnswer("call_start"), /next time it runs/);
});

test("fallback: projects are matched to Brain customers from settings", () => {
  const a = "11111111-1111-4111-8111-111111111111";
  const b = "22222222-2222-4222-8222-222222222222";
  const c = "33333333-3333-4333-8333-333333333333";
  process.env.BRAIN_PROJECTS = ` ${a}=first-customer, ${b.toUpperCase()}=second-customer ,not-a-uuid=x, ${c}=Bad Key`;
  process.env.REVENUEOS_PROJECT_ID = c;
  process.env.CUSTOMER_KEY = "third-customer";
  assert.equal(settingsCustomerFor(a), "first-customer");
  assert.equal(settingsCustomerFor(b), "second-customer");
  assert.equal(settingsCustomerFor(c), "third-customer");
  assert.equal(settingsCustomerFor("44444444-4444-4444-8444-444444444444"), null);
});
