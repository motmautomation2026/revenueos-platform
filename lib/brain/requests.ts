// "Start execution" requests: the mailbox between the app and the Brain
// (public.revenueos_brain_requests, supabase/migrations/0003_project_brain_key.sql).
//
// Pure logic only, so it can be tested and used on both sides of the server boundary. The reads
// and writes live in request-store.ts.

export const REQUEST_STATUSES = [
  "requested",
  "planning",
  "awaiting_approval",
  "finishing",
  "loaded",
  "failed",
  "cancelled",
] as const;
export type RequestStatus = (typeof REQUEST_STATUSES)[number];

/** A request the Brain is still working on. At most one per project. */
export const ACTIVE_STATUSES: readonly RequestStatus[] = ["requested", "planning", "awaiting_approval", "finishing"];

export type PlanAnswer = "yes" | "override" | "changes";
export const MAX_ANSWER_TEXT = 2000;
/** The Brain's proof keeps at most this much of the approver's name (plan_approval.APPROVER_MAX). */
export const APPROVER_MAX = 80;
/** A request nobody picked up for this long means the Brain's worker is not running. */
export const PICKUP_GRACE_MS = 2 * 60 * 1000;

export type BrainRequestRow = {
  id: string;
  project_id: string;
  customer_key: string;
  status: RequestStatus;
  month: string | null;
  test_mode: boolean | null;
  requested_by_name: string | null;
  plan: unknown;
  revenue_analysis: unknown;
  plan_errors: unknown;
  brain_message: string | null;
  answer: PlanAnswer | null;
  approved_by: string | null;
  approved_at: string | null;
  failed_step: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type PlanTask = {
  id: string;
  week: number | null;
  agent: string;
  task: string;
  target: string;
  dependsOn: string[];
};

export type MonthPlanView = {
  month: string;
  objectives: string[];
  tasks: PlanTask[];
  blocked: string[];
  kpis: string[];
  reviewNotes: string[];
  revenue: { label: string; value: string }[];
};

const text = (value: unknown): string => (typeof value === "string" ? value.trim() : "");
const list = (value: unknown): string[] => (Array.isArray(value) ? value.map(text).filter(Boolean) : []);
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};

/** The month plan the Brain published (master_agent.MonthPlan), read tolerantly: it is another codebase's JSON. */
export function readMonthPlan(plan: unknown, revenueAnalysis: unknown): MonthPlanView | null {
  const source = record(plan);
  const tasks = Array.isArray(source.tasks) ? source.tasks : [];
  if (!tasks.length && !list(source.objectives).length) return null;

  const revenue = record(revenueAnalysis);
  const inputs = record(revenue.inputs ?? revenue);
  const revenueFacts = [
    { label: "Revenue goal", value: text(inputs.revenue_goal) },
    { label: "Average order value", value: text(inputs.aov_as_written) },
    { label: "Capacity per month", value: text(inputs.capacity_per_month) },
    { label: "Forecast", value: text(revenue.forecast_note) },
  ].filter((fact) => fact.value && fact.value.toLowerCase() !== "not stated");

  return {
    month: text(source.month),
    objectives: list(source.objectives),
    tasks: tasks.map((item, index) => {
      const task = record(item);
      const week = Number(task.week);
      return {
        id: text(task.task_id) || `T${index + 1}`,
        week: Number.isInteger(week) && week > 0 ? week : null,
        agent: text(task.agent),
        task: text(task.task),
        target: text(task.target),
        dependsOn: list(task.depends_on),
      };
    }),
    blocked: list(source.blocked_items),
    kpis: list(source.kpis_to_track),
    reviewNotes: list(source.review_notes),
    revenue: revenueFacts,
  };
}

export type RequestStage =
  | "none"
  | "waiting_for_brain"
  | "worker_missing"
  | "planning"
  | "needs_approval"
  | "answer_sent"
  | "finishing"
  | "loaded"
  | "failed"
  | "cancelled";

export type RequestView = {
  id: string | null;
  stage: RequestStage;
  month: string | null;
  testMode: boolean | null;
  requestedBy: string | null;
  plan: MonthPlanView | null;
  /** Plan rules the month plan still breaks. A plain approval is refused while there are any. */
  planErrors: string[];
  /** What the Brain last said, e.g. why an answer was refused. */
  brainMessage: string | null;
  approvedBy: string | null;
  failedStep: string | null;
  error: string | null;
  /** True while the Brain is working and this request blocks a new one. */
  active: boolean;
};

const EMPTY: RequestView = {
  id: null,
  stage: "none",
  month: null,
  testMode: null,
  requestedBy: null,
  plan: null,
  planErrors: [],
  brainMessage: null,
  approvedBy: null,
  failedStep: null,
  error: null,
  active: false,
};

/** What the Dashboard shows for a project's latest request. */
export function buildRequestView(row: BrainRequestRow | null, now: number = Date.now()): RequestView {
  if (!row) return EMPTY;

  let stage: RequestStage;
  switch (row.status) {
    case "requested":
      // updated_at, not created_at: "Try again" hands an old request back to the Brain.
      stage = now - Date.parse(row.updated_at) > PICKUP_GRACE_MS ? "worker_missing" : "waiting_for_brain";
      break;
    case "planning":
      stage = "planning";
      break;
    case "awaiting_approval":
      stage = row.answer ? "answer_sent" : "needs_approval";
      break;
    case "finishing":
      stage = "finishing";
      break;
    case "loaded":
      stage = "loaded";
      break;
    case "failed":
      stage = "failed";
      break;
    default:
      stage = "cancelled";
  }

  return {
    id: row.id,
    stage,
    month: row.month,
    testMode: row.test_mode,
    requestedBy: row.requested_by_name,
    plan: row.status === "awaiting_approval" ? readMonthPlan(row.plan, row.revenue_analysis) : null,
    planErrors: row.status === "awaiting_approval" ? list(row.plan_errors) : [],
    brainMessage: text(row.brain_message) || null,
    approvedBy: row.approved_by,
    failedStep: row.failed_step,
    error: row.error,
    active: ACTIVE_STATUSES.includes(row.status),
  };
}

/**
 * Why an answer to the month plan cannot be sent, or null if it can. The same rules as the
 * Brain's own plan_approval.decide(), which checks the answer again when it reads it:
 *   yes        refused while plan rules are broken
 *   override   only when rules are broken, and only with a reason
 *   changes    needs the changes in words
 */
export function planAnswerProblem(answer: string, note: string, brokenRules: number): string | null {
  const words = note.trim();
  if (words.length > MAX_ANSWER_TEXT) return `Keep it under ${MAX_ANSWER_TEXT} characters.`;
  if (answer === "yes") {
    return brokenRules > 0
      ? `${brokenRules} plan rule${brokenRules === 1 ? " is" : "s are"} still broken. Ask for changes, or approve anyway with a reason.`
      : null;
  }
  if (answer === "override") {
    if (brokenRules === 0) return "The plan breaks no rules, so it can simply be approved.";
    return words ? null : "Give the reason for approving a plan that breaks rules.";
  }
  if (answer === "changes") {
    if (!words) return "Describe the changes you want.";
    // These words alone mean something else to the Brain; as "changes" they would be misread.
    return ["yes", "y", "approve", "approved", "override"].includes(words.toLowerCase())
      ? "Describe the changes in a sentence."
      : null;
  }
  return "Choose approve, approve anyway, or request changes.";
}

/** The steps a request goes through, in plain words, for the progress line. */
export const REQUEST_STEPS: { key: string; label: string }[] = [
  { key: "requested", label: "Requested" },
  { key: "planning", label: "Brain writes the month plan" },
  { key: "approval", label: "You approve it" },
  { key: "finishing", label: "Run is prepared" },
  { key: "running", label: "Execution starts" },
];

/** How far along a request is, as an index into REQUEST_STEPS. */
export function requestStepIndex(stage: RequestStage): number {
  switch (stage) {
    case "waiting_for_brain":
    case "worker_missing":
      return 0;
    case "planning":
      return 1;
    case "needs_approval":
    case "answer_sent":
      return 2;
    case "finishing":
      return 3;
    case "loaded":
      return 4;
    default:
      return -1;
  }
}

const STEP_WORDS: Record<string, string> = {
  start: "starting",
  intake: "reading the GTM plan",
  plan: "writing the month plan",
  check: "checking the Brain's setup",
  load: "preparing the run",
};

/** "The Brain stopped while checking its setup." */
export function failedWhile(step: string | null): string {
  return `The Brain stopped while ${STEP_WORDS[step ?? ""] ?? "working on this"}.`;
}
