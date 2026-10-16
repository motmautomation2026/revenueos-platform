import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildExecutionView,
  decisionTitle,
  describeMonth,
  isToolAgent,
  maskPhone,
  monthOfRunKey,
  type BrainDecisionRow,
  type BrainRunRow,
  type BrainTaskRow,
} from "../lib/brain/view";

const run: BrainRunRow = {
  id: 1,
  run_key: "example-co-2026-10-r2",
  customer: "example-co",
  status: "waiting_human",
  test_mode: true,
  stop_reason: null,
  created_at: "2026-10-12T06:40:27Z",
  updated_at: "2026-10-12T07:00:00Z",
  plan: {
    tasks: [{ task_id: "T1", task: "Build the account list" }],
    objectives: ["Map accounts"],
    blocked_items: ["Revenue target base value"],
    revenue_analysis: { aov_as_written: "₹30 lakh to ₹2 crore", expected_revenue_low_inr: 24000000, expected_revenue_high_inr: 250000000 },
  },
};

const task = (over: Partial<BrainTaskRow>): BrainTaskRow => ({
  id: 10,
  run_id: 1,
  task_id: "T1",
  agent: "QuickICP",
  output_type: "accounts",
  depends_on: [],
  week: 1,
  work_order: { target: "150 to 200 accounts", instructions: ["Research P1 segments"], done_when: "List validated" },
  target_number: 200,
  achieved_number: null,
  status: "pending",
  waiting_for: null,
  result: null,
  last_error: null,
  started_at: null,
  finished_at: null,
  ...over,
});

const decision = (over: Partial<BrainDecisionRow>): BrainDecisionRow => ({
  id: 100,
  run_id: 1,
  task_row_id: null,
  kind: "plan_approval",
  status: "answered",
  question: null,
  answer: "yes",
  decided_by: "user",
  reason: null,
  created_at: "2026-10-12T06:40:00Z",
  answered_at: "2026-10-12T06:40:14Z",
  ...over,
});

const tasks = [
  task({ id: 10, task_id: "T1", status: "done", achieved_number: 181, result: { target: 40, achieved: 181, reused: 181, bought: 0, note: "181 of 40" } }),
  task({ id: 11, task_id: "T2", agent: "Email Agent", output_type: "emails", week: 2, depends_on: ["T1"], status: "waiting_human", waiting_for: "email_approve", target_number: 350 }),
  task({ id: 12, task_id: "T3", agent: "CAM", output_type: "handover", week: 4, depends_on: ["T1", "T2"], target_number: null, work_order: {} }),
  task({ id: 13, task_id: "T4", agent: "Quickhowl", output_type: "calls", week: 3, status: "failed", last_error: "STOPPED: request rejected" }),
];

const decisions = [
  decision({ id: 100, question: { proof: { approved_by: "A Reviewer", overridden: false } } }),
  decision({
    id: 101,
    task_row_id: 11,
    kind: "email_approve",
    status: "open",
    answer: null,
    question: {
      text: "T2: approve these sample emails",
      allowed_answers: ["no", "yes"],
      samples: [{ for: "Head of Ops", email: "a@example.com", subject: "Hello", text: "Body" }],
    },
  }),
  decision({
    id: 102,
    task_row_id: 13,
    kind: "call_start",
    status: "open",
    answer: null,
    question: { text: "Start calling", contacts: [{ name: "R Rao", company: "Example Ltd", phone: "+91 98765 43210" }] },
  }),
  decision({ id: 103, kind: "review_action_1", status: "open", answer: null, question: { action: "Refine sources", why: "32% failed", task_id: "T1", allowed_answers: ["accept", "reject"] } }),
  decision({
    id: 104,
    kind: "run_review",
    answer: "saved",
    decided_by: "brain",
    question: { reviewed_at: "2026-10-12T06:59:50Z", action_decision_ids: [103], explanation: { what_worked: ["Accounts exceeded"], whats_short: ["Volume capped"] } },
  }),
];

const view = buildExecutionView({ run, tasks, decisions, toolCalls: [{ id: 1, task_row_id: 10, step: "reuse", tool: "supabase", paid: false, status: "done", error: null, started_at: "2026-10-12T06:41:00Z", finished_at: null }] });

test("tasks land in the right board column", () => {
  const where = Object.fromEntries(view.columns.map((c) => [c.key, c.cards.map((card) => card.taskId)]));
  assert.deepEqual(where, { next: ["T3"], running: [], person: ["T2"], done: ["T1"], stopped: ["T4"] });
  assert.deepEqual(view.counts, { total: 4, done: 1 });
});

test("progress uses the run's effective target, and keeps the plan target for context", () => {
  const card = view.columns.find((c) => c.key === "done")!.cards[0];
  assert.equal(card.title, "Build the account list");
  assert.equal(card.achieved, 181);
  assert.equal(card.target, 40);
  assert.equal(card.planTarget, 200);
  assert.equal(card.percent, 100);
  assert.deepEqual(view.kpis, [{ label: "Companies found", achieved: 181, target: 40, planTarget: 200, percent: 100 }]);
});

test("a waiting task lists only the dependencies that are not done yet", () => {
  const card = view.columns.find((c) => c.key === "next")!.cards[0];
  assert.deepEqual(card.blockedBy, ["T2"]);
  assert.equal(card.actor, "person");
  assert.equal(card.title, "CAM: handover");
});

test("tasks are also grouped by week, in order", () => {
  assert.deepEqual(view.weeks.map((w) => [w.week, w.cards.map((c) => c.taskId)]), [[1, ["T1"]], [2, ["T2"]], [3, ["T4"]], [4, ["T3"]]]);
});

test("open decisions carry their question, samples and masked phone numbers", () => {
  assert.deepEqual(view.openDecisions.map((d) => [d.id, d.title, d.taskId]), [
    [101, "Approve the sample emails", "T2"],
    [102, "Approve calling", "T4"],
    [103, "Suggested action", "T1"],
  ]);
  assert.deepEqual(view.openDecisions[0].samples, [{ to: "Head of Ops", subject: "Hello", preheader: "", text: "Body" }]);
  assert.equal(view.openDecisions[0].review, null, "an old-style question has no version or rounds");
  assert.deepEqual(view.openDecisions[0].allowed, ["no", "yes"]);
  assert.equal(view.openDecisions[1].contacts[0].phone, "••••••3210");
  assert.ok(!JSON.stringify(view).includes("98765"));
});

test("plan approval and the review are summarised", () => {
  assert.equal(view.approval, "Approved by A Reviewer");
  assert.deepEqual(view.review?.worked, ["Accounts exceeded"]);
  assert.deepEqual(view.review?.actions, [{ id: 103, text: "Refine sources", why: "32% failed", taskId: "T1", state: "open" }]);
  assert.deepEqual(view.plan.revenue, [
    { label: "Order value", value: "₹30 lakh to ₹2 crore" },
    { label: "Expected revenue", value: "₹2,40,00,000 – ₹25,00,00,000" },
  ]);
});

test("malformed rows do not break the page", () => {
  const empty = buildExecutionView({
    run: { ...run, plan: "not json" },
    tasks: [task({ work_order: null, result: "oops", depends_on: "T1" })],
    decisions: [decision({ status: "open", answer: null, question: 42 })],
    toolCalls: [],
  });
  assert.equal(empty.columns[0].cards[0].title, "QuickICP: accounts");
  assert.equal(empty.openDecisions[0].text, "");
  assert.equal(empty.review, null);
});

test("helpers", () => {
  assert.equal(monthOfRunKey("example-co-2026-10-r2", "example-co"), "2026-10-r2");
  assert.equal(maskPhone("1234"), "1234");
  assert.ok(isToolAgent("Email Agent") && !isToolAgent("Sales Engineer"));
  assert.equal(decisionTitle("goal_check"), "Target not reached: decide what to do");
  assert.equal(decisionTitle("something_new"), "Something new");
});

test("run months are described in plain words", () => {
  assert.deepEqual(describeMonth("2026-10-r4"), { month: "October 2026", plan: 4 });
  assert.deepEqual(describeMonth("2026-10"), { month: "October 2026", plan: 1 });
  assert.deepEqual(describeMonth("something-else"), { month: "something-else", plan: null });
});
