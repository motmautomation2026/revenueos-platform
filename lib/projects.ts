export const ENGAGEMENT_TYPES = [
  "Full funnel",
  "Lead generation only",
  "Digital / authority only",
  "Market research only",
  "Other",
] as const;

export type ProjectStatus =
  | "draft"
  | "uploading"
  | "checklist_generating"
  | "checklist_ready"
  | "checklist_submitted"
  | "plan_running"
  | "plan_blocked"
  | "plan_ready"
  | "failed";

export type Project = {
  id: string;
  owner_id: string;
  customer_name: string;
  engagement_type: string;
  description: string | null;
  status: ProjectStatus;
  created_at: string;
  updated_at: string;
};

export const STEPS = [
  { key: "upload", number: 1, label: "Upload", path: "upload" },
  { key: "checklist", number: 2, label: "Checklist", path: "checklist" },
  { key: "plan", number: 3, label: "Plan", path: "plan" },
  { key: "review", number: 4, label: "Review", path: "plan" },
] as const;

export type StepKey = (typeof STEPS)[number]["key"];

/** Highest step the project has reached (1-4). A step unlocks only when the previous one is done. */
export function reachedStep(status: ProjectStatus): number {
  switch (status) {
    case "checklist_ready":
      return 2;
    case "checklist_submitted":
    case "plan_running":
    case "plan_blocked":
    case "failed": // only the plan pipeline sets this
      return 3;
    case "plan_ready":
      return 4;
    default:
      return 1;
  }
}

export function currentStep(status: ProjectStatus) {
  return STEPS[reachedStep(status) - 1];
}

const STATUS_LABELS: Record<ProjectStatus, string> = {
  draft: "Not started",
  uploading: "Uploading documents",
  checklist_generating: "Writing checklist",
  checklist_ready: "Checklist to fill in",
  checklist_submitted: "Checklist submitted",
  plan_running: "Writing plan",
  plan_blocked: "Plan needs more information",
  plan_ready: "Plan ready",
  failed: "Plan failed",
};

export function statusLabel(status: ProjectStatus) {
  return STATUS_LABELS[status];
}
