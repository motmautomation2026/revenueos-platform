// Turns the Brain's rows (brain_customer_runs / brain_plan_tasks / brain_decisions /
// brain_tool_calls) into what the Execution page shows. Pure functions: no database, no React.
// The Brain's JSON columns are written by another codebase, so every read here is tolerant.

export type RunStatus = "planning" | "waiting_human" | "running" | "done" | "stopped";
export type TaskStatus = "pending" | "running" | "waiting_human" | "done" | "failed" | "stopped";

export type BrainRunRow = {
  id: number;
  run_key: string;
  customer: string;
  status: RunStatus;
  test_mode: boolean;
  stop_reason: string | null;
  plan: unknown;
  created_at: string;
  updated_at: string;
};

export type BrainTaskRow = {
  id: number;
  run_id: number;
  task_id: string;
  agent: string;
  output_type: string;
  depends_on: unknown;
  week: number | null;
  work_order: unknown;
  target_number: number | null;
  achieved_number: number | null;
  status: TaskStatus;
  waiting_for: string | null;
  result: unknown;
  last_error: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type BrainDecisionRow = {
  id: number;
  run_id: number;
  task_row_id: number | null;
  kind: string;
  status: "open" | "answered";
  question: unknown;
  answer: string | null;
  decided_by: string | null;
  reason: string | null;
  created_at: string;
  answered_at: string | null;
};

export type BrainToolCallRow = {
  id: number;
  task_row_id: number;
  step: string;
  tool: string;
  paid: boolean;
  status: string;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

// ---------------------------------------------------------------------------
// View model
// ---------------------------------------------------------------------------

export type ColumnKey = "next" | "running" | "person" | "done" | "stopped";

export type Stat = { label: string; value: string };

export type ToolCallView = {
  id: number;
  tool: string;
  step: string;
  paid: boolean;
  status: string;
  error: string | null;
  startedAt: string;
};

export type TaskCard = {
  rowId: number;
  taskId: string;
  title: string;
  agent: string;
  /** Who does the work: one of the connected tools, or a person. */
  actor: "tool" | "person";
  outputType: string;
  week: number | null;
  status: TaskStatus;
  column: ColumnKey;
  /** The target as the plan words it, e.g. "150 to 200 accounts". */
  targetText: string;
  achieved: number | null;
  /** The target this run was actually held to (smaller than the plan target in test mode). */
  target: number | null;
  planTarget: number | null;
  percent: number | null;
  note: string;
  /** Tasks this one is still waiting on. */
  blockedBy: string[];
  waitingFor: string | null;
  lastError: string | null;
  instructions: string[];
  doneWhen: string;
  escalateIf: string;
  revenueLink: string;
  tier: string;
  geography: string;
  stats: Stat[];
  calls: ToolCallView[];
};

/** The task a decision is about, from the month plan and its work order. */
export type DecisionTask = {
  /** What the task is, in the plan's own words. */
  title: string;
  /** Who does it, e.g. "CAM". */
  agent: string;
  steps: string[];
  doneWhen: string;
  target: string;
  /** Tasks that cannot start until this one is finished. */
  blocks: string[];
};

export type DecisionCard = {
  id: number;
  kind: string;
  title: string;
  taskId: string | null;
  task: DecisionTask | null;
  text: string;
  why: string;
  followsRule: string;
  allowed: string[];
  samples: { to: string; subject: string; preheader: string; text: string }[];
  contacts: { name: string; company: string; phone: string }[];
  /** Email sample review: which version, how many rounds of changes are left, what our checks found. */
  review: {
    version: number;
    roundsLeft: number;
    checks: string[];
    status: string;
    previousFeedback: string[];
    /** Samples the tool returned without an email ("Pranav, ... at Armstrong Dematic: email 3"). */
    missing: string[];
  } | null;
  open: boolean;
  answer: string | null;
  decidedBy: string | null;
  reason: string | null;
  askedAt: string;
};

export type Kpi = { label: string; achieved: number; target: number | null; planTarget: number | null; percent: number | null };

export type ReviewView = {
  reviewedAt: string;
  worked: string[];
  short: string[];
  actions: { id: number | null; text: string; why: string; taskId: string; state: "open" | "accepted" | "rejected" | "suggested" }[];
};

export type ExecutionView = {
  runKey: string;
  month: string;
  status: RunStatus;
  testMode: boolean;
  stopReason: string | null;
  approval: string | null;
  startedAt: string;
  counts: { total: number; done: number };
  kpis: Kpi[];
  columns: { key: ColumnKey; title: string; hint: string; cards: TaskCard[] }[];
  weeks: { week: number | null; cards: TaskCard[] }[];
  openDecisions: DecisionCard[];
  review: ReviewView | null;
  plan: { objectives: string[]; blocked: string[]; kpisToTrack: string[]; revenue: Stat[] };
};

// ---------------------------------------------------------------------------
// Tolerant readers
// ---------------------------------------------------------------------------

type Json = Record<string, unknown>;
const obj = (v: unknown): Json => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown): string => (v == null ? "" : typeof v === "string" ? v.trim() : String(v));
const strings = (v: unknown): string[] => list(v).map(str).filter(Boolean);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
const rupees = (v: unknown) => (num(v) == null ? "" : `₹${inr.format(num(v)!)}`);
const range = (low: string, high: string) => (low && high && low !== high ? `${low} – ${high}` : low || high);
const words = (key: string) => {
  const text = key.replace(/_/g, " ");
  return text[0].toUpperCase() + text.slice(1);
};

const TOOL_AGENTS = ["quickicp", "quickhowl", "email agent", "linkedin agent", "pseo", "email tool"];
export const isToolAgent = (agent: string) => TOOL_AGENTS.includes(agent.trim().toLowerCase());

/** Keeps the last four digits: the board is for tracking work, not for reading numbers off. */
export function maskPhone(phone: string): string {
  const digits = phone.replace(/\D/g, "");
  return digits.length <= 4 ? phone : `${"•".repeat(Math.min(digits.length - 4, 6))}${digits.slice(-4)}`;
}

const COLUMNS: { key: ColumnKey; title: string; hint: string }[] = [
  { key: "next", title: "Up next", hint: "Not started yet" },
  { key: "running", title: "Running", hint: "The Brain is working on it" },
  { key: "person", title: "Needs a person", hint: "Waiting for an answer or for work to be done" },
  { key: "done", title: "Done", hint: "Finished" },
  { key: "stopped", title: "Stopped", hint: "Stopped or failed" },
];

const COLUMN_OF: Record<TaskStatus, ColumnKey> = {
  pending: "next",
  running: "running",
  waiting_human: "person",
  done: "done",
  failed: "stopped",
  stopped: "stopped",
};

const OUTPUT_LABELS: Record<string, string> = {
  accounts: "Companies found",
  contacts: "Contacts found",
  emails: "Emails sent",
  calls: "Calls made",
  outreach: "People reached",
  qualified_leads: "Qualified leads",
  opportunities: "Opportunities",
};

const DECISION_TITLES: Record<string, string> = {
  plan_approval: "Approve the month plan",
  email_approve: "Approve the sample emails",
  call_start: "Approve calling",
  goal_check: "Target not reached: decide what to do",
  paid_call_unknown: "A paid call may or may not have gone through",
  task_failed: "A task failed: decide what to do",
  human_task: "Task for your team",
  human_gate_task: "Task for your team",
  brain_review: "Brain check of a task",
  run_review: "Review of the run",
};

export function decisionTitle(kind: string): string {
  if (kind.startsWith("review_action")) return "Suggested action";
  return DECISION_TITLES[kind] ?? words(kind);
}

const STAT_LABELS: [string, string][] = [
  ["reused", "Reused from earlier data"],
  ["bought", "Newly bought"],
  ["searches", "Searches run"],
  ["people_searches", "People searches run"],
  ["shortfall", "Short of target by"],
  ["with_email", "With email"],
  ["with_phone", "With phone"],
  ["mobile", "Mobile numbers"],
  ["landline", "Landlines"],
  ["unknown_phone", "Unknown phone type"],
  ["rejected_do_not_call", "Rejected: do not call"],
  ["rejected_by_quickhowl", "Rejected by Quickhowl"],
  ["campaign_status", "Campaign status"],
  ["reference", "Campaign reference"],
];

function statsOf(result: Json): Stat[] {
  const stats: Stat[] = [];
  for (const [key, label] of STAT_LABELS) {
    const value = result[key];
    if (typeof value === "number" || (typeof value === "string" && value)) {
      stats.push({ label, value: typeof value === "number" ? inr.format(value) : value });
    }
  }
  for (const [outcome, count] of Object.entries(obj(result.outcome_counts))) {
    if (typeof count === "number") stats.push({ label: `Calls: ${words(outcome).toLowerCase()}`, value: inr.format(count) });
  }
  const recipients = list(result.recipients).length;
  if (recipients) stats.push({ label: "Recipients", value: inr.format(recipients) });
  const landlines = list(result.skipped_landlines).length;
  if (landlines) stats.push({ label: "Landlines skipped", value: inr.format(landlines) });
  return stats;
}

function decisionCard(
  row: BrainDecisionRow,
  taskIdOf: Map<number, string>,
  taskOf: Map<string, DecisionTask> = new Map()
): DecisionCard {
  const q = obj(row.question);
  const taskId = (row.task_row_id != null && taskIdOf.get(row.task_row_id)) || str(q.task_id) || null;
  return {
    id: row.id,
    kind: row.kind,
    title: decisionTitle(row.kind),
    taskId,
    task: (taskId && taskOf.get(taskId)) || null,
    text: str(q.action) || str(q.text),
    why: str(q.why),
    followsRule: str(q.follows_rule),
    allowed: strings(q.allowed_answers),
    samples: list(q.samples)
      .map(obj)
      .map((s) => ({ to: str(s.for) || str(s.email), subject: str(s.subject), preheader: str(s.preheader), text: str(s.text) }))
      .filter((s) => s.subject || s.text),
    review:
      typeof q.round === "number"
        ? {
            version: q.round,
            roundsLeft: typeof q.feedback_rounds_left === "number" ? q.feedback_rounds_left : 0,
            checks: strings(q.checks),
            status: str(q.samples_status),
            previousFeedback: strings(q.previous_feedback),
            missing: list(q.samples)
              .map(obj)
              .filter((s) => !str(s.subject) && !str(s.text))
              .map((s) => `${str(s.for) || str(s.email) || "A recipient"}${typeof s.email === "number" ? `: email ${s.email}` : ""}`),
          }
        : null,
    contacts: list(q.contacts)
      .map(obj)
      .map((c) => ({ name: str(c.name), company: str(c.company), phone: maskPhone(str(c.phone)) }))
      .filter((c) => c.name || c.company),
    open: row.status === "open",
    answer: row.answer,
    decidedBy: row.decided_by,
    reason: row.reason,
    askedAt: row.created_at,
  };
}

export function monthOfRunKey(runKey: string, customer: string): string {
  return runKey.startsWith(`${customer}-`) ? runKey.slice(customer.length + 1) : runKey;
}

export function buildExecutionView(input: {
  run: BrainRunRow;
  tasks: BrainTaskRow[];
  decisions: BrainDecisionRow[];
  toolCalls: BrainToolCallRow[];
}): ExecutionView {
  const { run, tasks, decisions, toolCalls } = input;
  const plan = obj(run.plan);
  const planTasks = new Map(list(plan.tasks).map(obj).map((t) => [str(t.task_id), t]));
  const statusOf = new Map(tasks.map((t) => [t.task_id, t.status]));
  const taskIdOf = new Map(tasks.map((t) => [t.id, t.task_id]));

  // What each task is, for the decisions asked about it.
  const taskOf = new Map<string, DecisionTask>(
    tasks.map((task) => {
      const order = obj(task.work_order);
      return [
        task.task_id,
        {
          title: str(planTasks.get(task.task_id)?.task) || `${task.agent}: ${words(task.output_type).toLowerCase()}`,
          agent: task.agent,
          steps: strings(order.instructions),
          doneWhen: str(order.done_when),
          target: str(order.target),
          blocks: tasks.filter((other) => strings(other.depends_on).includes(task.task_id)).map((other) => other.task_id),
        },
      ];
    })
  );

  const callsOf = new Map<number, ToolCallView[]>();
  for (const call of toolCalls) {
    const view: ToolCallView = {
      id: call.id,
      tool: call.tool,
      step: call.step,
      paid: call.paid,
      status: call.status,
      error: call.error,
      startedAt: call.started_at,
    };
    callsOf.set(call.task_row_id, [...(callsOf.get(call.task_row_id) ?? []), view]);
  }

  const cards: TaskCard[] = tasks.map((task) => {
    const order = obj(task.work_order);
    const result = obj(task.result);
    const achieved = task.achieved_number ?? num(result.achieved);
    const target = num(result.target) ?? task.target_number;
    return {
      rowId: task.id,
      taskId: task.task_id,
      title: str(planTasks.get(task.task_id)?.task) || `${task.agent}: ${words(task.output_type).toLowerCase()}`,
      agent: task.agent,
      actor: isToolAgent(task.agent) ? "tool" : "person",
      outputType: task.output_type,
      week: task.week,
      status: task.status,
      column: COLUMN_OF[task.status] ?? "next",
      targetText: str(order.target),
      achieved,
      target,
      planTarget: task.target_number,
      percent: achieved != null && target ? Math.min(100, Math.round((achieved / target) * 100)) : null,
      note: str(result.note),
      blockedBy: strings(task.depends_on).filter((id) => statusOf.get(id) !== "done"),
      waitingFor: task.waiting_for,
      lastError: task.last_error,
      instructions: strings(order.instructions),
      doneWhen: str(order.done_when),
      escalateIf: str(order.escalate_if),
      revenueLink: str(order.revenue_link),
      tier: str(order.tier),
      geography: str(order.geography),
      stats: statsOf(result),
      calls: callsOf.get(task.id) ?? [],
    };
  });

  const byWeek = new Map<number | null, TaskCard[]>();
  for (const card of cards) byWeek.set(card.week, [...(byWeek.get(card.week) ?? []), card]);

  const kpis: Kpi[] = cards
    .filter((c) => c.actor === "tool" && c.achieved != null)
    .map((c) => ({
      label: OUTPUT_LABELS[c.outputType] ?? words(c.outputType),
      achieved: c.achieved!,
      target: c.target,
      planTarget: c.planTarget,
      percent: c.percent,
    }));

  const approvalRow = decisions.find((d) => d.kind === "plan_approval" && d.answer === "yes");
  const proof = obj(obj(approvalRow?.question).proof);
  const approval = approvalRow
    ? [str(proof.approved_by) && `Approved by ${str(proof.approved_by)}`, proof.overridden === true && "with an override"]
        .filter(Boolean)
        .join(" ") || "Approved"
    : null;

  const reviewRow = [...decisions].reverse().find((d) => d.kind === "run_review");
  const review = reviewRow ? buildReview(reviewRow, decisions) : null;

  const revenue = obj(plan.revenue_analysis);
  const revenueStats: Stat[] = [
    { label: "Order value", value: str(revenue.aov_as_written) || range(rupees(revenue.aov_low_inr), rupees(revenue.aov_high_inr)) },
    { label: "Revenue goal", value: str(revenue.revenue_goal) },
    {
      label: `Qualified opportunities${str(revenue.opportunities_period) ? ` (${str(revenue.opportunities_period)})` : ""}`,
      value: range(str(revenue.qualified_opportunities_low), str(revenue.qualified_opportunities_high)),
    },
    { label: "Expected orders", value: range(str(revenue.expected_orders_low), str(revenue.expected_orders_high)) },
    { label: "Expected revenue", value: range(rupees(revenue.expected_revenue_low_inr), rupees(revenue.expected_revenue_high_inr)) },
    { label: "Delivery capacity", value: str(revenue.capacity_per_month) },
  ].filter((s) => s.value);

  return {
    runKey: run.run_key,
    month: monthOfRunKey(run.run_key, run.customer),
    status: run.status,
    testMode: run.test_mode,
    stopReason: run.stop_reason,
    approval,
    startedAt: run.created_at,
    counts: { total: cards.length, done: cards.filter((c) => c.status === "done").length },
    kpis,
    columns: COLUMNS.map((column) => ({ ...column, cards: cards.filter((c) => c.column === column.key) })),
    weeks: [...byWeek.entries()]
      .sort(([a], [b]) => (a ?? 99) - (b ?? 99))
      .map(([week, weekCards]) => ({ week, cards: weekCards })),
    openDecisions: decisions.filter((d) => d.status === "open").map((d) => decisionCard(d, taskIdOf, taskOf)),
    review,
    plan: {
      objectives: strings(plan.objectives),
      blocked: strings(plan.blocked_items),
      kpisToTrack: strings(plan.kpis_to_track),
      revenue: revenueStats,
    },
  };
}

function buildReview(reviewRow: BrainDecisionRow, decisions: BrainDecisionRow[]): ReviewView {
  const q = obj(reviewRow.question);
  const explanation = obj(q.explanation);
  const actionIds = list(q.action_decision_ids).filter((id): id is number => typeof id === "number");
  const actionRows = decisions.filter((d) => actionIds.includes(d.id));

  const actions: ReviewView["actions"] = actionRows.length
    ? actionRows.map((row) => {
        const a = obj(row.question);
        return {
          id: row.id,
          text: str(a.action) || str(a.text),
          why: str(a.why),
          taskId: str(a.task_id),
          state: row.status === "open" ? "open" : row.answer === "accept" ? "accepted" : "rejected",
        };
      })
    : list(explanation.next_actions)
        .map(obj)
        .map((a) => ({ id: null, text: str(a.action), why: str(a.why), taskId: str(a.task_id), state: "suggested" as const }));

  return {
    reviewedAt: str(q.reviewed_at) || reviewRow.created_at,
    worked: strings(explanation.what_worked),
    short: strings(explanation.whats_short),
    actions: actions.filter((a) => a.text),
  };
}

const MONTH_NAME = new Intl.DateTimeFormat("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });

/**
 * A run's month in plain words: "2026-10-r4" is the fourth plan written for October 2026.
 * Anything that does not look like that is shown as it is.
 */
export function describeMonth(month: string): { month: string; plan: number | null } {
  const match = month.match(/^(\d{4})-(\d{2})(?:-r(\d+))?$/);
  if (!match) return { month, plan: null };
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, 1));
  return { month: MONTH_NAME.format(date), plan: match[3] ? Number(match[3]) : 1 };
}
