/** Every project is a full-funnel engagement; there is nothing to choose. */
export const ENGAGEMENT_TYPE = "Full funnel";

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
  /** The customer's email: their email and calling accounts are opened with it. */
  customer_email?: string | null;
  /** Staging or production, and the staging limits. Absent until 0008_project_run_mode.sql is applied. */
  run_mode?: "staging" | "production" | null;
  staging_companies?: number | null;
  staging_people?: number | null;
  staging_phones?: string | null;
  staging_emails?: string | null;
  /** Set while the project is archived. Absent until supabase/migrations/0006_project_archive.sql is applied. */
  archived_at?: string | null;
};

export const isArchived = (project: Pick<Project, "archived_at">) => !!project.archived_at;

export const STEPS = [
  { key: "upload", number: 1, label: "Upload", path: "upload" },
  { key: "checklist", number: 2, label: "Checklist", path: "checklist" },
  { key: "plan", number: 3, label: "Plan", path: "plan" },
  { key: "review", number: 4, label: "Review", path: "plan" },
  // Opens once the plan is ready. The work itself is run by the Brain (brain/), not by this app.
  { key: "execution", number: 5, label: "Execution", path: "execution" },
] as const;

export type StepKey = (typeof STEPS)[number]["key"];

/** Highest plan-building step the project has reached (1-4). A step unlocks only when the previous one is done. */
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

const BUSY: ProjectStatus[] = ["checklist_generating", "plan_running"];
const NEEDS_ATTENTION: ProjectStatus[] = ["plan_blocked", "failed"];

/** Colour classes for a status pill: amber needs attention, green is ready, indigo is working. */
export function statusTone(status: ProjectStatus) {
  if (NEEDS_ATTENTION.includes(status)) return "bg-amber-500/15 text-amber-800";
  if (status === "plan_ready") return "bg-emerald-500/15 text-emerald-800";
  if (BUSY.includes(status)) return "bg-primary/10 text-primary";
  return "bg-muted text-muted-foreground";
}

/**
 * What a person has to do next on a project that is still being planned, and on which page.
 * Null while RevenueOS itself is working (writing the checklist or the plan) or once the plan is
 * ready: from then on the Brain's own questions say what is needed.
 */
export function planningNextStep(status: ProjectStatus): { label: string; path: string } | null {
  switch (status) {
    case "draft":
    case "uploading":
      return { label: "Upload the handover and proposal", path: "upload" };
    case "checklist_ready":
      return { label: "Fill in and submit the checklist", path: "checklist" };
    case "checklist_submitted":
      return { label: "Generate the GTM plan", path: "plan" };
    case "plan_blocked":
      return { label: "Answer the plan's questions", path: "plan" };
    case "failed":
      return { label: "Resume the plan", path: "plan" };
    default:
      return null;
  }
}
