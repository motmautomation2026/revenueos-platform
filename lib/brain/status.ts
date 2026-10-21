import "server-only";
import { loadExecution } from "./read";
import { loadPaidStep } from "./paid-steps";
import { loadRequest } from "./request-store";
import { lastActivityOf } from "./tools";
import { describeMonth } from "./view";

export type ExecutionStatus = { label: string; tone: string };

/**
 * Where a project stands once its plan is ready, from the Brain's latest month plan.
 * Returns null when the Brain has not started for this project, so the caller falls back to
 * the planning status ("Plan ready").
 */
export async function executionStatus(projectId: string): Promise<ExecutionStatus | null> {
  const data = await loadExecution(projectId);

  // A month being started from the app comes first: it is what needs attention now.
  const { view: request } = await loadRequest(projectId);
  if (request.stage === "needs_approval") return { label: "Month plan needs approval", tone: "bg-amber-500/20 text-amber-900" };
  if (request.active) return { label: "Brain is planning the month", tone: "bg-primary/10 text-primary" };
  if (data.state !== "ok") {
    return request.stage === "failed" ? { label: "Start failed", tone: "bg-destructive/10 text-destructive" } : null;
  }

  const paid = await loadPaidStep(projectId);
  if (paid?.status === "waiting" || paid?.status === "failed") {
    return { label: `${paid.taskId} waiting for you to start`, tone: "bg-amber-500/20 text-amber-900" };
  }

  const waiting = data.view.openDecisions.length;
  if (waiting > 0) {
    return {
      label: `${waiting} decision${waiting === 1 ? "" : "s"} waiting`,
      tone: "bg-amber-500/20 text-amber-900",
    };
  }
  switch (data.view.status) {
    case "done":
      return { label: "Execution finished", tone: "bg-emerald-500/15 text-emerald-800" };
    case "running":
      return { label: "Execution running", tone: "bg-primary/10 text-primary" };
    case "waiting_human":
      return { label: "Waiting for a person", tone: "bg-amber-500/20 text-amber-900" };
    case "stopped":
      return { label: "Execution stopped", tone: "bg-muted text-muted-foreground" };
    default:
      return { label: "Execution being set up", tone: "bg-primary/10 text-primary" };
  }
}

export type ExecutionSummary = {
  status: ExecutionStatus;
  /** The month plan in plain words, e.g. "October 2026". */
  month: string;
  done: number;
  total: number;
  waiting: number;
  /** Headline results, e.g. 181 companies found. */
  results: { label: string; value: number }[];
  /** When the Brain last did anything for this client (ISO), or null. */
  lastActivity: string | null;
};

/** One thing a person has to do on a project in execution, for the overview's "Needs you" list. */
export type AttentionItem = {
  key: string;
  /** What to do, e.g. "Approve the sample emails". */
  title: string;
  /** Which task, e.g. "T14 · Launch email outreach campaigns". */
  detail: string;
  /** When it started waiting (ISO), for "waiting 2 h". */
  since: string | null;
  /** Page inside the project, e.g. "execution". */
  path: string;
};

/**
 * Everything waiting for a person on one project once its plan is ready: a month plan to approve,
 * a step that buys data to start, and the Brain's open questions. Empty when nothing is waiting.
 */
export async function attentionItems(projectId: string): Promise<AttentionItem[]> {
  const [{ view: request }, data, paid] = await Promise.all([
    loadRequest(projectId),
    loadExecution(projectId),
    loadPaidStep(projectId),
  ]);
  const items: AttentionItem[] = [];
  if (request.stage === "needs_approval") {
    items.push({ key: "month-plan", title: "Approve the month plan", detail: "The Brain planned the month", since: null, path: "execution" });
  }
  if (paid?.status === "waiting" || paid?.status === "failed") {
    items.push({
      key: `paid-${paid.taskId}`,
      title: paid.status === "failed" ? `Start ${paid.taskId} again` : `Start ${paid.taskId}`,
      detail: "This step buys data, so it waits for a person",
      since: null,
      path: "execution",
    });
  }
  if (data.state === "ok") {
    for (const decision of data.view.openDecisions) {
      items.push({
        key: `decision-${decision.id}`,
        title: decision.title,
        detail: [decision.taskId, decision.task?.title].filter(Boolean).join(" · "),
        since: decision.askedAt,
        path: "execution",
      });
    }
  }
  return items;
}

const SHORT_LABELS: Record<string, string> = {
  "Companies found": "companies",
  "Contacts found": "contacts",
  "Emails sent": "emails",
  "Calls made": "calls",
};

/** What a project card shows about execution. Null when the Brain has not started for this project. */
export async function executionSummary(projectId: string): Promise<ExecutionSummary | null> {
  const [status, data] = await Promise.all([executionStatus(projectId), loadExecution(projectId)]);
  if (!status || data.state !== "ok") return null;
  return {
    status,
    month: describeMonth(data.view.month).month,
    done: data.view.counts.done,
    total: data.view.counts.total,
    waiting: data.view.openDecisions.length,
    results: data.view.kpis.map((kpi) => ({ label: SHORT_LABELS[kpi.label] ?? kpi.label.toLowerCase(), value: kpi.achieved })),
    lastActivity: lastActivityOf(data.rows),
  };
}
