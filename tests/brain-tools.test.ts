import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildActivity,
  filterActivity,
  buildCallTiles,
  buildCallsReport,
  buildCompanyBreakdown,
  buildEmailReport,
  emailPlanOf,
  htmlToText,
  sendingProgress,
  buildFunnel,
  buildResearchReport,
  buildResultTiles,
  placeOf,
  toolsInRun,
  type RunRows,
} from "../lib/brain/tools";
import type { BrainDecisionRow, BrainTaskRow } from "../lib/brain/view";

const task = (over: Partial<BrainTaskRow>): BrainTaskRow => ({
  id: 1,
  run_id: 1,
  task_id: "T1",
  agent: "QuickICP",
  output_type: "accounts",
  depends_on: [],
  week: 1,
  work_order: {},
  target_number: null,
  achieved_number: null,
  status: "done",
  waiting_for: null,
  result: null,
  last_error: null,
  started_at: null,
  finished_at: null,
  ...over,
});

const decision = (over: Partial<BrainDecisionRow>): BrainDecisionRow => ({
  id: 1,
  run_id: 1,
  task_row_id: null,
  kind: "plan_approval",
  status: "answered",
  question: null,
  answer: "yes",
  decided_by: "user",
  reason: null,
  created_at: "2026-10-12T06:00:00Z",
  answered_at: "2026-10-12T06:01:00Z",
  ...over,
});

const rows: RunRows = {
  tasks: [
    task({ id: 1, task_id: "T1", target_number: 200, achieved_number: 181, result: { target: 40, reused: 181, bought: 0, company_ids: [3, 7] } }),
    task({
      id: 2,
      task_id: "T2",
      output_type: "contacts",
      target_number: 350,
      achieved_number: 15,
      result: { target: 10, with_email: 15, mobile: 12, landline: 3, people_ids: [21], phone_types: { "21": "mobile" } },
    }),
    task({
      id: 3,
      task_id: "T3",
      agent: "Email Agent",
      output_type: "emails",
      target_number: 350,
      achieved_number: 3,
      result: { target: 3, recipients: 3, rejected: 0, reference: "REF-1", campaign_status: "RUNNING", test_mode: true },
    }),
    task({
      id: 4,
      task_id: "T4",
      agent: "Quickhowl",
      output_type: "calls",
      achieved_number: 1,
      result: {
        target: 1,
        reference: "CALL-1",
        outcomes: [{ name: "R Rao", company: "Example Ltd", outcome: "not_interested", status: "completed", duration_seconds: 5, summary: "No engagement." }],
        outcome_counts: { not_interested: 1 },
        skipped_landlines: [{ name: "A B", phone_type: "landline" }],
        rejected_do_not_call: 0,
        rejected_by_quickhowl: 0,
        dnd_confirmation: "DND handled",
      },
    }),
    task({ id: 5, task_id: "T5", agent: "LinkedIn Agent", output_type: "outreach", status: "pending" }),
  ],
  decisions: [
    decision({ id: 10, task_row_id: 3, kind: "email_approve", reason: "samples ok", question: { samples: [{ for: "Head of Ops", subject: "Hello", text: "Body" }] } }),
    decision({ id: 11, task_row_id: 4, kind: "call_start", question: { contacts: [{ name: "R Rao" }] } }),
    decision({ id: 12, kind: "review_action_1", status: "open", answer: null, answered_at: null, created_at: "2026-10-12T07:00:00Z", question: { task_id: "T2" } }),
    decision({
      id: 13,
      kind: "run_review",
      answer: "saved",
      decided_by: "brain",
      question: {
        numbers: {
          email_results: "not available yet",
          data_quality: {
            companies_judged: 192,
            people_judged: 22,
            people_failed_judge_pct: 32,
            landline_pct: 20,
            thresholds_pct: { people_failed_judge_pct: 30, landline_pct: 20 },
            flagged: [{ signal: "people_failed_judge_pct" }],
          },
          objections: { counts: { not_interested: 1 }, quotes: [{ name: "R Rao", label: "not_interested", quote: "No response", task_id: "T4" }] },
        },
      },
    }),
  ],
  toolCalls: [
    { id: 1, task_row_id: 3, step: "launch", tool: "email_tool", paid: true, status: "done", error: null, started_at: "2026-10-12T06:30:00Z", finished_at: null },
    { id: 2, task_row_id: 4, step: "start", tool: "quickhowl", paid: true, status: "failed", error: "rejected (502)", started_at: "2026-10-12T06:40:00Z", finished_at: null },
  ],
};

test("data research: targets, sources, quality flags and used rows", () => {
  const r = buildResearchReport(rows);
  assert.deepEqual([r.accounts?.achieved, r.accounts?.target, r.accounts?.planTarget, r.accounts?.reused], [181, 40, 200, 181]);
  assert.deepEqual([r.contacts?.withEmail, r.contacts?.mobile, r.contacts?.landline], [15, 12, 3]);
  assert.deepEqual(r.judged, { companies: 192, people: 22 });
  assert.deepEqual(r.quality, [
    { label: "Contacts that failed the fit check", valuePct: 32, thresholdPct: 30, flagged: true },
    { label: "Contacts with only a landline", valuePct: 20, thresholdPct: 20, flagged: false },
  ]);
  assert.deepEqual([r.usedCompanyIds, r.usedPeopleIds, r.phoneTypes], [[3, 7], [21], { "21": "mobile" }]);
});

test("email: campaign facts, approved samples and the honest results note", () => {
  const { campaigns, resultsNote } = buildEmailReport(rows);
  const c = campaigns[0];
  assert.deepEqual([c.taskId, c.reference, c.campaignStatus, c.recipients, c.rejected, c.testMode], ["T3", "REF-1", "RUNNING", 3, 0, true]);
  assert.deepEqual(c.samples, [{ to: "Head of Ops", email: null, subject: "Hello", preheader: "", text: "Body", written: true }]);
  assert.deepEqual([c.approval?.answer, c.approval?.reason, c.approval?.open], ["yes", "samples ok", false]);
  assert.deepEqual(c.steps.map((s) => s.step), ["launch"]);
  assert.equal(resultsNote, "not available yet");
});

test("calls: every call, outcome counts, skipped landlines and objections", () => {
  const { campaigns, objections } = buildCallsReport(rows);
  const c = campaigns[0];
  assert.deepEqual(c.outcomes, [
    { name: "R Rao", company: "Example Ltd", outcome: "Not interested", status: "Completed", durationSeconds: 5, summary: "No engagement." },
  ]);
  assert.deepEqual(c.outcomeCounts, [{ label: "Not interested", count: 1 }]);
  assert.deepEqual([c.skippedLandlines.length, c.rejectedDoNotCall, c.contactsApproved, c.dnd], [1, 0, 1, "DND handled"]);
  assert.deepEqual(objections.quotes, [{ name: "R Rao", label: "Not interested", quote: "No response", taskId: "T4" }]);
});

test("activity: newest first, with problems, paid steps and open questions marked", () => {
  const events = buildActivity(rows);
  assert.deepEqual(
    events.map((e) => [e.kind, e.tone, e.paid, e.taskId]).slice(0, 3),
    [
      ["question", "attention", false, "T2"],
      ["tool", "problem", true, "T4"],
      ["tool", "normal", true, "T3"],
    ]
  );
  assert.deepEqual([events[1].title, events[1].source, events[1].detail], ["Started calling", "Quickhowl", "rejected (502)"]);
  assert.deepEqual([events[2].title, events[2].source], ["Saved the campaign as a draft", "Email tool"]);
  const approved = events.find((e) => e.key === "answer-10")!;
  assert.deepEqual([approved.title, approved.answer, approved.source, approved.detail], ["Approve the sample emails", "Approved", "Person", "samples ok"]);
  assert.ok(events.every((e, i) => i === 0 || Date.parse(events[i - 1].at) >= Date.parse(e.at)));
});

test("tools without stored data are named, not given an empty screen", () => {
  assert.deepEqual(toolsInRun(rows.tasks), { research: true, email: true, calls: true, unconnected: ["LinkedIn Agent"] });
  assert.deepEqual(buildEmailReport({ tasks: [], decisions: [], toolCalls: [] }), { campaigns: [], resultsNote: "" });
});

test("pipeline: stages in order, real numbers only, and no number is shown as none", () => {
  const funnel = buildFunnel([...rows.tasks, task({ id: 6, task_id: "T6", agent: "Human Tele Calling", output_type: "qualified_leads", target_number: 12 })]);
  assert.deepEqual(
    funnel.map((s) => [s.label, s.achieved, s.target, s.planTarget]),
    [
      ["Companies found", 181, 40, 200],
      ["Contacts found", 15, 10, 350],
      ["Emails sent", 3, 3, 350],
      ["Calls made", 1, 1, null],
      ["Qualified leads", null, 12, 12],
    ]
  );
});

test("addresses are read from the end into city and region", () => {
  assert.deepEqual(placeOf("Ashford Centre, Lower Parel, Mumbai, Maharashtra 400013, IN"), { city: "Mumbai", region: "Maharashtra" });
  assert.deepEqual(placeOf("Alkem House, Mumbai, 400013, IN"), { city: "Alkem House", region: "Mumbai" });
  assert.deepEqual(placeOf("Pune, Maharashtra, India"), { city: "Maharashtra", region: "India" });
  assert.deepEqual(placeOf(null), { city: "", region: "" });
});

test("company list analysis: top groups, an Other bucket, and size bands in order", () => {
  const rows = [
    ...Array.from({ length: 5 }, () => ({ industry: "Chemicals", location: "A, Pune, Maharashtra 411001, IN", headcount: 800 })),
    ...Array.from({ length: 3 }, () => ({ industry: "Pharma", location: "B, Gurgaon, Haryana 122001, IN", headcount: 2500 })),
    { industry: null, location: null, headcount: null },
    { industry: "Steel", location: "C, Delhi, Delhi 110034, IN", headcount: 12000 },
  ];
  const b = buildCompanyBreakdown(rows);
  assert.equal(b.total, 10);
  assert.deepEqual(b.industries.slice(0, 2), [
    { label: "Chemicals", count: 5, percent: 50 },
    { label: "Pharma", count: 3, percent: 30 },
  ]);
  assert.deepEqual(b.places.map((p) => p.label), ["Maharashtra", "Haryana", "Not recorded", "Delhi"]);
  assert.deepEqual(b.sizes.map((s) => [s.label, s.count]), [
    ["Under 1,000 employees", 5],
    ["1,000 to 4,999", 3],
    ["10,000 and above", 1],
  ]);
  const many = buildCompanyBreakdown(Array.from({ length: 9 }, (_, i) => ({ industry: `I${i}`, location: null, headcount: 1 })));
  assert.equal(many.industries.length, 7);
  assert.deepEqual(many.industries.at(-1), { label: "Other", count: 3, percent: 33 });
});

test("result tiles measure against the plan target and explain the test cap", () => {
  const tiles = buildResultTiles(buildResearchReport(rows));
  assert.deepEqual(tiles.map((t) => [t.key, t.value, t.of, t.ofLabel, t.percent, t.verdict?.text]), [
    ["companies", 181, 200, "planned", 91, "Test cap reached"],
    ["contacts", 15, 350, "planned", 4, "Test cap reached"],
    ["email", 15, 15, "contacts", 100, "All of them"],
    ["mobile", 12, 15, "contacts", 80, "3 cannot"],
  ]);
  assert.deepEqual(tiles[0].lines, [
    "Test run: capped at 40, and the cap was reached.",
    "All reused from data already held; nothing newly bought.",
  ]);
  assert.deepEqual(tiles[2].lines, ["Every contact has an email address."]);
  assert.deepEqual(tiles[3].lines, ["3 have only a landline and are not called."]);
});

test("result tiles: target met, shortfall, mixed sources, and unfinished work", () => {
  const base = { taskId: "T1", status: "done" as const, searches: null, shortfall: null, note: "" };
  const report = (accounts: object, contacts: object | null) =>
    ({ accounts: { ...base, ...accounts }, contacts: contacts && { ...base, ...contacts }, judged: { companies: null, people: null }, quality: [], usedCompanyIds: [], usedPeopleIds: [], phoneTypes: {} }) as unknown as Parameters<typeof buildResultTiles>[0];

  const met = buildResultTiles(report({ achieved: 210, target: 200, planTarget: 200, reused: 60, bought: 150 }, null))[0];
  assert.deepEqual([met.verdict, met.percent, met.lines], [{ text: "Target met", tone: "good" }, 100, ["60 reused from data already held, 150 newly bought."]]);

  const short = buildResultTiles(report({ achieved: 120, target: 200, planTarget: 200, reused: 0, bought: 120 }, null))[0];
  assert.deepEqual([short.verdict, short.percent, short.lines], [{ text: "80 short", tone: "attention" }, 60, ["All 120 newly bought."]]);

  const running = buildResultTiles(report({ status: "running", achieved: null, target: null, planTarget: 200, reused: null, bought: null }, null))[0];
  assert.deepEqual([running.value, running.of, running.verdict?.text, running.lines], [null, 200, "In progress", []]);

  const people = buildResultTiles(
    report({ achieved: 1, target: 1, planTarget: 1, reused: 1, bought: 0 }, { achieved: 10, target: 10, planTarget: 10, reused: 10, bought: 0, withEmail: 8, mobile: 6, landline: 1 })
  );
  assert.deepEqual(people[2].lines, ["2 contacts have no email address."]);
  assert.deepEqual(people[3].lines, ["1 has only a landline and is not called.", "3 have no phone number."]);
});

test("email campaign: stages in order and tiles measured against the plan", () => {
  const calls = (steps: string[]) =>
    steps.map((step, i) => ({ id: 100 + i, task_row_id: 3, step, tool: "email_tool", paid: false, status: "done", error: null, started_at: `2026-10-12T06:3${i}:00Z`, finished_at: null }));
  const c = buildEmailReport({ ...rows, toolCalls: calls(["request", "launch", "samples", "samples", "approve"]) }).campaigns[0];
  assert.deepEqual(c.stages.map((s) => [s.label, s.state]), [
    ["Campaign prepared", "done"],
    ["Saved in the email tool as a draft", "done"],
    ["Sample emails written", "done"],
    ["Approved by a person", "done"],
    ["Sending started", "done"],
  ]);
  assert.equal(c.stages[3].note, "samples ok");
  assert.deepEqual(c.tiles.map((t) => [t.key, t.value, t.of, t.percent, t.verdict?.text]), [
    ["recipients", 3, 350, 1, "Test cap reached"],
    ["accepted", 3, 3, 100, "All accepted"],
    ["samples", 1, null, null, "Approved"],
  ]);
  assert.deepEqual(c.tiles[0].lines, ["Test run: capped at 3 recipients.", "Sent only to MOTM team inboxes, not to prospects."]);
});

test("email campaign waiting for approval, and one that was refused", () => {
  const open = rows.decisions.map((d) => (d.kind === "email_approve" ? { ...d, status: "open" as const, answer: null, answered_at: null } : d));
  const waiting = buildEmailReport({ ...rows, decisions: open, toolCalls: [] }).campaigns[0];
  assert.deepEqual(waiting.stages.map((s) => s.state), ["todo", "todo", "todo", "waiting", "todo"]);
  assert.equal(waiting.tiles.find((t) => t.key === "samples")?.verdict?.text, "Waiting for approval");

  const no = rows.decisions.map((d) => (d.kind === "email_approve" ? { ...d, answer: "no", reason: "tone is wrong" } : d));
  const refused = buildEmailReport({ ...rows, decisions: no, toolCalls: [] }).campaigns[0];
  assert.deepEqual([refused.stages[3].label, refused.stages[3].state, refused.stages[3].note], ["Not approved", "refused", "tone is wrong"]);
});

test("calling campaign: stages, and tiles that explain themselves", () => {
  const calls = ["contacts", "request", "store", "start", "poll", "poll"].map((step, i) => ({
    id: 200 + i, task_row_id: 4, step, tool: "quickhowl", paid: false, status: "done", error: null, started_at: `2026-10-12T06:4${i}:00Z`, finished_at: null,
  }));
  const campaign = buildCallsReport({ ...rows, toolCalls: calls }).campaigns[0];
  assert.deepEqual(campaign.stages.map((s) => [s.label, s.state]), [
    ["Contact list chosen", "done"],
    ["Campaign saved in Quickhowl", "done"],
    ["Approved by a person", "done"],
    ["Calling started", "done"],
    ["Calls finished", "done"],
  ]);

  const tiles = buildCallTiles({ ...campaign, planTarget: 350, testMode: true }, { interested: 0, completedCalls: 1, talkSeconds: 5, calls: 1 });
  assert.deepEqual(tiles.map((t) => [t.key, t.value, t.of, t.verdict?.text]), [
    ["called", 1, 350, "Test cap reached"],
    ["interested", 0, 1, "None yet"],
    ["notCalled", 1, null, "Check the numbers"],
  ]);
  assert.deepEqual(tiles[0].lines, ["Test run: capped at 1 call.", "Calls went only to MOTM test numbers, not to prospects."]);
  assert.deepEqual(tiles[1].lines, ["Nobody showed interest on these calls.", "Total talk time 5 seconds."]);
  assert.deepEqual(tiles[2].lines, ["1 has only a landline."]);

  const good = buildCallTiles({ ...campaign, skippedLandlines: [] }, { interested: 2, completedCalls: 4, talkSeconds: 185, calls: 4 });
  assert.deepEqual([good[1].verdict?.text, good[1].lines], ["50% interested", ["2 of 4 completed calls showed interest.", "Total talk time 3 min 5 sec."]]);
  assert.deepEqual([good[2].value, good[2].verdict?.text, good[2].lines], [0, "None", ["Everyone on the list could be called."]]);

  const unknown = buildCallTiles(campaign, null);
  assert.deepEqual([unknown[1].value, unknown[1].verdict], [null, null]);
  assert.match(unknown[1].lines[0], /no record of this campaign/);
});

test("activity: repeated steps become one line, and filters narrow the list", () => {
  const poll = (id: number, minute: number) => ({ id, task_row_id: 4, step: "poll", tool: "quickhowl", paid: false, status: "done", error: null, started_at: `2026-10-12T06:5${minute}:00Z`, finished_at: null });
  const events = buildActivity({ ...rows, toolCalls: [...rows.toolCalls, poll(30, 1), poll(31, 2), poll(32, 3)] });
  const polls = events.filter((e) => e.title === "Checked for call results");
  assert.deepEqual(polls.map((e) => [e.times, e.at]), [[3, "2026-10-12T06:53:00Z"]]);

  assert.ok(filterActivity(events, "tools").every((e) => e.kind === "tool"));
  assert.ok(filterActivity(events, "decisions").every((e) => e.kind !== "tool"));
  assert.deepEqual(filterActivity(events, "problems").map((e) => e.tone).sort(), ["attention", "problem"]);
  assert.equal(filterActivity(events, "all").length, events.length);
});

test("email plan: sequence, schedule and recipients from the Brain's request, nothing else", () => {
  const plan = emailPlanOf({
    body: {
      campaign: { name: "Anvixa - October", description: "Three emails" },
      schedule: { timezone: "Asia/Kolkata", weekdays: [1, 2, 3, 4, 5], daily_limit: 50, window_start_local: "09:30:00", window_end_local: "17:30:00" },
      objective: { cta: "A 15-minute demo?", tone: "direct" },
      emails: [
        { subject: "Hi {{company}}", body_html: "<p>Hi {{first_name}},</p><p>Line one<br>Line two &amp; more</p>", preheader: "Pre", wait_days_before: 0 },
        { subject: "Again", body_html: "<p>Two</p>", wait_days_before: 3 },
      ],
      recipients: [{ first_name: "Asha", last_name: "Rao", title: "CTO", company: "Example Ltd", email: "a@example.com", city: "Pune" }],
      smtp: { password: "never-read" },
    },
  });
  assert.ok(plan);
  assert.deepEqual(
    [plan.name, plan.timezone, plan.weekdays, plan.dailyLimit, plan.windowStart, plan.windowEnd, plan.cta],
    ["Anvixa - October", "Asia/Kolkata", [1, 2, 3, 4, 5], 50, "09:30", "17:30", "A 15-minute demo?"]
  );
  assert.deepEqual(plan.emails.map((e) => [e.subject, e.text, e.waitDays]), [
    ["Hi {{company}}", "Hi {{first_name}},\n\nLine one\nLine two & more", 0],
    ["Again", "Two", 3],
  ]);
  assert.deepEqual(plan.recipients, [{ name: "Asha Rao", title: "CTO", company: "Example Ltd", email: "a@example.com" }]);
  assert.ok(!JSON.stringify(plan).includes("never-read"), "only the named fields are kept");
  assert.equal(emailPlanOf({}), null);
  assert.equal(htmlToText("<p>a&nbsp;b&zwnj;</p>"), "a b");
});

test("sending: the whole sequence counts, and a next email two hours past due is overdue", () => {
  // Anvixa on 21 Oct: email 1 sent to 5 people, email 2 due 19 Oct and still queued.
  const stats = { recipients: 5, messages: 10, sent: 5, failed: 0, nextDueAt: "2026-10-19T05:59:55Z", status: "Running" };
  const now = Date.parse("2026-10-21T08:00:00Z");
  assert.deepEqual(sendingProgress(stats, 3, now), {
    total: 15,
    sent: 5,
    failed: 0,
    toSend: 10,
    overdueSince: "2026-10-19T05:59:55Z",
    finished: false,
  });
  // Within two hours of the due time it is not overdue yet; without the plan the tool's own count is used.
  assert.equal(sendingProgress(stats, 3, Date.parse("2026-10-19T07:00:00Z")).overdueSince, null);
  assert.equal(sendingProgress(stats, null, now).total, 10);
  // Everything sent: finished, never overdue.
  const done = sendingProgress({ ...stats, sent: 15, messages: 15 }, 3, now);
  assert.deepEqual([done.toSend, done.finished, done.overdueSince], [0, true, null]);
});
