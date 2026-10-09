// Shapes shared by the plan API routes and the plan page.

export type PlanStatus =
  | "queued"
  | "diagnosis_running"
  | "blocked"
  | "strategy_running"
  | "execution_running"
  | "ready"
  | "failed";

export const PLAN_STEPS = ["diagnosis", "strategy", "execution"] as const;
export type PlanStep = (typeof PLAN_STEPS)[number];

export const RUNNING_STATUSES: PlanStatus[] = [
  "queued",
  "diagnosis_running",
  "strategy_running",
  "execution_running",
];

/** A plan "running" with no progress for this long is treated as crashed and can be resumed. */
export const STALE_RUN_MS = 20 * 60 * 1000;

export type Blocker = { blocker: string; question_to_resolve: string };

export type StepProgress = {
  step: PlanStep;
  state: "pending" | "running" | "done" | "failed";
  /** ISO time the running step started. */
  runningSince: string | null;
  /** Total time spent in finished AI calls for this step. */
  durationMs: number;
};

export type PlanProgress = {
  id: string;
  version: number;
  status: PlanStatus;
  error: string | null;
  blockers: Blocker[];
  steps: StepProgress[];
  /** Marked running but silent for too long: offer "Resume". */
  stale: boolean;
  serverTime: string;
};

export const STEP_LABELS: Record<PlanStep, string> = {
  diagnosis: "Diagnosis",
  strategy: "Strategy",
  execution: "Execution",
};
