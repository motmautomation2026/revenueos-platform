import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  ACTIVE_STATUSES,
  APPROVER_MAX,
  PICKUP_GRACE_MS,
  REQUEST_STATUSES,
  REQUEST_STEPS,
  buildRequestView,
  failedWhile,
  planAnswerProblem,
  readMonthPlan,
  requestStepIndex,
  type BrainRequestRow,
} from "../lib/brain/requests";

const NOW = Date.parse("2026-10-12T10:00:00Z");

const row = (fields: Partial<BrainRequestRow> = {}): BrainRequestRow => ({
  id: "99999999-9999-4999-8999-999999999999",
  project_id: "22222222-2222-4222-8222-222222222222",
  customer_key: "new-co-222222",
  status: "requested",
  month: null,
  test_mode: null,
  requested_by_name: "Asha Rao",
  plan: null,
  revenue_analysis: null,
  plan_errors: [],
  brain_message: null,
  answer: null,
  approved_by: null,
  approved_at: null,
  failed_step: null,
  error: null,
  created_at: "2026-10-12T09:59:30Z",
  updated_at: "2026-10-12T09:59:30Z",
  ...fields,
});

const PLAN = {
  month: "Month 1",
  objectives: ["Book 10 qualified meetings"],
  tasks: [
    { task_id: "T1", depends_on: [], week: 1, agent: "QuickICP", task: "Build the account list", target: "200 accounts" },
    { task_id: "T2", depends_on: ["T1"], week: 2, agent: "Email", task: "Send the first sequence", target: "150 contacts" },
  ],
  kpis_to_track: ["Replies"],
  blocked_items: ["Case studies not shared yet"],
  review_notes: [],
};

test("the app, the database and the Brain agree on the request statuses", () => {
  const sql = readFileSync("supabase/migrations/0003_project_brain_key.sql", "utf8");
  const check = sql.match(/check \(status in \(([^)]*)\)\)/)?.[1] ?? "";
  assert.deepEqual([...check.matchAll(/'(\w+)'/g)].map((m) => m[1]), [...REQUEST_STATUSES]);

  const brain = readFileSync("brain/platform_approval.py", "utf8");
  const active = brain.match(/^ACTIVE = \(([^)]*)\)/m)?.[1] ?? "";
  assert.deepEqual([...active.matchAll(/"(\w+)"/g)].map((m) => m[1]), [...ACTIVE_STATUSES]);
  assert.match(brain, new RegExp(`^APPROVER_MAX = ${APPROVER_MAX}\\b`, "m"));

  // One request in progress per project: the index covers exactly the active statuses.
  const index = sql.match(/one_active_idx[\s\S]*?where status in \(([^)]*)\)/)?.[1] ?? "";
  assert.deepEqual([...index.matchAll(/'(\w+)'/g)].map((m) => m[1]), [...ACTIVE_STATUSES]);
});

test("the app refuses the same month-plan answers the Brain's own code refuses", () => {
  // The Brain checks again (plan_approval.decide); this keeps the two in step.
  const source = readFileSync("brain/plan_approval.py", "utf8");
  assert.match(source, /YES_WORDS = \{"yes", "y", "approve", "approved"\}/);
  assert.match(source, /OVERRIDE_WORD = "override"/);

  assert.equal(planAnswerProblem("yes", "", 0), null);
  assert.match(planAnswerProblem("yes", "", 2)!, /2 plan rules are still broken/);
  assert.match(planAnswerProblem("override", "client agreed", 0)!, /breaks no rules/);
  assert.match(planAnswerProblem("override", "  ", 1)!, /reason/);
  assert.equal(planAnswerProblem("override", "client agreed", 1), null);
  assert.match(planAnswerProblem("changes", " ", 0)!, /Describe the changes/);
  assert.equal(planAnswerProblem("changes", "plan: fewer calls in week 1", 3), null);
  // As "changes", these words alone would be read by the Brain as an approval or an override.
  for (const word of ["yes", "Y", "Approve", "approved", "override"]) {
    assert.match(planAnswerProblem("changes", word, 1)!, /in a sentence/, word);
  }
  assert.match(planAnswerProblem("maybe", "", 0)!, /Choose/);
  assert.match(planAnswerProblem("changes", "x".repeat(2001), 0)!, /under 2000/);
});

test("a request moves through the stages the Dashboard shows", () => {
  assert.equal(buildRequestView(null).stage, "none");
  assert.equal(buildRequestView(row(), NOW).stage, "waiting_for_brain");
  assert.equal(buildRequestView(row(), NOW + PICKUP_GRACE_MS).stage, "worker_missing");
  assert.equal(buildRequestView(row({ status: "planning" }), NOW).stage, "planning");
  assert.equal(buildRequestView(row({ status: "awaiting_approval", plan: PLAN }), NOW).stage, "needs_approval");
  assert.equal(buildRequestView(row({ status: "awaiting_approval", plan: PLAN, answer: "yes" }), NOW).stage, "answer_sent");
  assert.equal(buildRequestView(row({ status: "finishing", approved_by: "Asha Rao" }), NOW).stage, "finishing");
  assert.equal(buildRequestView(row({ status: "loaded" }), NOW).stage, "loaded");
  assert.equal(buildRequestView(row({ status: "failed" }), NOW).stage, "failed");
  assert.equal(buildRequestView(row({ status: "cancelled" }), NOW).stage, "cancelled");
});

test("only requests the Brain is still working on block a new one", () => {
  for (const status of REQUEST_STATUSES) {
    assert.equal(buildRequestView(row({ status }), NOW).active, ACTIVE_STATUSES.includes(status), status);
  }
});

test("'Try again' on an old request is not reported as a missing worker straight away", () => {
  const retried = row({ created_at: "2026-10-11T08:00:00Z", updated_at: "2026-10-12T09:59:50Z" });
  assert.equal(buildRequestView(retried, NOW).stage, "waiting_for_brain");
});

test("the published month plan is read as tasks by week", () => {
  const view = buildRequestView(
    row({ status: "awaiting_approval", plan: PLAN, plan_errors: ["T2 has no earlier contacts task."], month: "2026-10" }),
    NOW
  );
  assert.equal(view.plan?.tasks.length, 2);
  assert.deepEqual(view.plan?.tasks[1], {
    id: "T2",
    week: 2,
    agent: "Email",
    outputType: "",
    task: "Send the first sequence",
    target: "150 contacts",
    dependsOn: ["T1"],
  });
  assert.deepEqual(view.plan?.blocked, ["Case studies not shared yet"]);
  assert.deepEqual(view.planErrors, ["T2 has no earlier contacts task."]);
});

test("a plan is shown only while it waits for approval", () => {
  assert.equal(buildRequestView(row({ status: "planning", plan: PLAN, plan_errors: ["old"] }), NOW).plan, null);
  assert.deepEqual(buildRequestView(row({ status: "planning", plan: PLAN, plan_errors: ["old"] }), NOW).planErrors, []);
});

test("another codebase's JSON is read tolerantly", () => {
  assert.equal(readMonthPlan(null, null), null);
  assert.equal(readMonthPlan("not a plan", 7), null);
  assert.equal(readMonthPlan({ tasks: "none", objectives: [] }, null), null);
  const odd = readMonthPlan({ tasks: [{ week: "x", task: 5 }, null], objectives: [1, " keep "] }, { revenue_goal: "not stated", forecast_note: "Low confidence" });
  assert.deepEqual(odd?.tasks.map((t) => [t.id, t.week, t.task]), [["T1", null, ""], ["T2", null, ""]]);
  assert.deepEqual(odd?.objectives, ["keep"]);
  assert.deepEqual(odd?.revenue, [{ label: "Forecast", value: "Low confidence" }]);
});

test("revenue facts are taken as written, and 'not stated' is left out", () => {
  const plan = readMonthPlan(PLAN, { revenue_goal: "₹2 crore this year", aov_as_written: "", capacity_per_month: "not stated", forecast_note: "" });
  assert.deepEqual(plan?.revenue, [{ label: "Revenue goal", value: "₹2 crore this year" }]);
});

test("the progress line follows the request", () => {
  assert.equal(REQUEST_STEPS.length, 5);
  assert.deepEqual(
    (["waiting_for_brain", "planning", "needs_approval", "answer_sent", "finishing", "loaded", "failed", "none"] as const).map(requestStepIndex),
    [0, 1, 2, 2, 3, 4, -1, -1]
  );
});

test("a failure is described by the step it happened in", () => {
  assert.equal(failedWhile("check"), "The Brain stopped while checking the Brain's setup.");
  assert.equal(failedWhile("load"), "The Brain stopped while preparing the run.");
  assert.equal(failedWhile(null), "The Brain stopped while working on this.");
  // Every step the worker can report has its own words.
  const worker = readFileSync("brain/run_for.py", "utf8");
  const steps = worker.match(/^REQUEST_STEPS = \[([^\]]*)\]/m)?.[1] ?? "";
  for (const [, step] of steps.matchAll(/"(\w+)"/g)) assert.doesNotMatch(failedWhile(step), /working on this/, step);
});

test("the app, the Brain's worker and the import script name each tool account the same way", async () => {
  const { ACCOUNT_FIELDS, accountsUpdate, buildAccountsView, missingAccountFields } = await import("../lib/brain/accounts");
  const app = Object.fromEntries(ACCOUNT_FIELDS.map((field) => [field.column, field.setting]));

  const worker = readFileSync("brain/run_for.py", "utf8").match(/^ACCOUNT_SETTINGS = \{([\s\S]*?)^\}/m)?.[1] ?? "";
  assert.deepEqual(Object.fromEntries([...worker.matchAll(/"(\w+)":\s*"(\w+)"/g)].map((m) => [m[1], m[2]])), app);

  const script = readFileSync("scripts/import-brain-accounts.mjs", "utf8").match(/^const SETTINGS = \{([\s\S]*?)^\};/m)?.[1] ?? "";
  assert.deepEqual(Object.fromEntries([...script.matchAll(/(\w+):\s*"(\w+)"/g)].map((m) => [m[2], m[1]])), app);

  // Every column exists in the table.
  const sql = readFileSync("supabase/migrations/0004_brain_accounts.sql", "utf8");
  for (const column of Object.keys(app)) assert.match(sql, new RegExp(`^ +${column} text`, "m"), column);

  // Everything the Brain's setup check demands per customer is asked for.
  const check = readFileSync("brain/check_system.py", "utf8");
  for (const setting of ["EMAIL_TOOL_USER_EMAIL", "CLIENT_REFERENCE", "QUICKHOWL_CLIENT_REFERENCE", "QUICKHOWL_USER_PASSWORD"]) {
    assert.ok(check.includes(`"${setting}"`) && (Object.values(app) as string[]).includes(setting), setting);
  }

  const row = { email_client_reference: "c-1", email_user_email: "a@b.co", calls_user_password: "secret-pass" };
  const view = buildAccountsView(row);
  assert.equal(view.values.calls_user_password, "", "the password never leaves the server");
  assert.equal(view.saved.calls_user_password, true);
  assert.equal(view.values.email_client_reference, "c-1");
  assert.deepEqual(view.missing, ["Calls: Account reference", "Calls: User email", "Calls: User full name", "Calls: Company name"]);
  assert.equal(missingAccountFields(null).length, ACCOUNT_FIELDS.length);

  const input = { email_client_reference: " c-2 ", email_user_email: "", calls_client_reference: "", calls_user_email: "", calls_user_password: "", calls_user_full_name: "", calls_user_company: "" };
  const update = accountsUpdate(input);
  assert.equal(update.email_client_reference, "c-2");
  assert.equal(update.email_user_email, null);
  assert.ok(!("calls_user_password" in update), "an empty password keeps the saved one");
  assert.equal(accountsUpdate({ ...input, calls_user_password: "new-secret" }).calls_user_password, "new-secret");
});

test("a plan whose email or calls have no people-search step before them can never be approved", async () => {
  const { compulsoryProblems, suggestedChange } = await import("../lib/brain/requests");
  // The plan that broke today: companies, then calls and email with no contacts task.
  const broken = [
    { id: "T1", agent: "QuickICP", outputType: "accounts", dependsOn: [] },
    { id: "T2", agent: "Content & Training", outputType: "client_training", dependsOn: ["T1"] },
    { id: "T4", agent: "Quickhowl", outputType: "calls", dependsOn: ["T2"] },
    { id: "T6", agent: "Email Agent", outputType: "emails", dependsOn: ["T2"] },
  ];
  const problems = compulsoryProblems(broken, []);
  assert.deepEqual(
    problems.filter((p) => /^T\d/.test(p)).map((p) => p.split(" ")[0]),
    ["T4", "T6"]
  );
  assert.ok(problems.some((p) => /no step to find people with email and phone/.test(p)), "the people search itself is required");
  assert.ok(problems.some((p) => /has no earlier step that finds people's email and phone/.test(p)));
  assert.match(planAnswerProblem("override", "go anyway", 3, problems.length)!, /cannot be approved: ask for changes/);
  assert.match(planAnswerProblem("yes", "", 0, problems.length)!, /cannot be approved/);
  assert.equal(planAnswerProblem("changes", "plan: add a contacts task", 3, problems.length), null);
  assert.match(suggestedChange(broken), /all four tools in this order: 1\) a QuickICP accounts task .* 2\) a QuickICP contacts task/);

  const fixed = [...broken.slice(0, 2), { id: "T3", agent: "QuickICP", outputType: "contacts", dependsOn: ["T1"] },
    { id: "T4", agent: "Quickhowl", outputType: "calls", dependsOn: ["T3", "T2"] },
    { id: "T6", agent: "Email Agent", outputType: "emails", dependsOn: ["T4"] }];
  assert.deepEqual(compulsoryProblems(fixed, []), [], "an earlier contacts task, directly or through another task, is enough");
  assert.equal(compulsoryProblems(fixed, ["T4 (Quickhowl / calls) target 'x' has no number. Tool tasks run by a count: give one"]).length, 1);
  assert.equal(compulsoryProblems(fixed, ["T5 is qualification but does not come after a 'outreach' task."]).length, 0,
    "other broken rules may still be approved anyway");

  // The Brain's worker applies exactly the same rule.
  const brain = readFileSync("brain/platform_approval.py", "utf8");
  assert.match(brain, /^NEEDS_PEOPLE = \("Quickhowl", "Email Agent"\)/m);
  assert.match(brain, /has no number\\\. Tool tasks run by a count\|which does not exist\|"\s*\n\s*r"is not enabled for this customer\|only has:/);
});

test("every month plan must use all four connected tools, in order", async () => {
  const { compulsoryProblems } = await import("../lib/brain/requests");
  const all = [
    { id: "T1", agent: "QuickICP", outputType: "accounts", dependsOn: [] },
    { id: "T2", agent: "QuickICP", outputType: "contacts", dependsOn: ["T1"] },
    { id: "T3", agent: "Email Agent", outputType: "emails", dependsOn: ["T2"] },
    { id: "T4", agent: "Quickhowl", outputType: "calls", dependsOn: ["T2"] },
  ];
  assert.deepEqual(compulsoryProblems(all, []), []);
  for (const left of ["T1", "T2", "T3", "T4"]) {
    const without = all.filter((task) => task.id !== left).map((task) => ({ ...task, dependsOn: task.dependsOn.filter((d) => d !== left) }));
    assert.ok(compulsoryProblems(without, []).some((p) => /Every month plan must have it/.test(p)), `without ${left}`);
  }
  const peopleFirst = all.map((task) => (task.id === "T2" ? { ...task, dependsOn: [] } : task));
  assert.deepEqual(compulsoryProblems(peopleFirst, []), ["T2 (find people) must come after the step that finds companies."]);
  // The Brain's worker requires the same four steps.
  const brain = readFileSync("brain/platform_approval.py", "utf8");
  for (const [agent, output] of [["QuickICP", "accounts"], ["QuickICP", "contacts"], ["Email Agent", "emails"], ["Quickhowl", "calls"]]) {
    assert.ok(brain.includes(`("${agent}", "${output}",`), `${agent} ${output}`);
  }
});
