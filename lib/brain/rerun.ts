import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";

/**
 * Re-run a step and everything downstream — the platform side of the Brain's rerun_tasks
 * (brain/state_store.py), done directly against the shared tables. A re-run is just a state reset: the
 * target task and all its transitive dependents go back to 'pending', their steps and decisions are
 * deleted, and the run resumes. The Brain worker then re-runs the pending tasks in dependency order.
 *
 * Deleting the decisions is the safety boundary: an email/call task re-opens a FRESH approval and waits
 * again, so nothing is re-sent without a new 'yes'. (Mirrors brain/state_store.rerun_tasks.)
 *
 * A data task (accounts/contacts) in the reset set also clears its saved rows (QuickICP companies/people)
 * for this customer, so a re-run truly re-fetches instead of reusing stale data — and the Data research
 * page then shows only the current run's results.
 */

type TaskRow = { id: number; task_id: string; depends_on: string[] | null; status: string; output_type: string | null };

/** `taskId` plus every task that transitively depends on it. Pure. */
export function dependentsClosure(tasks: TaskRow[], taskId: string): Set<string> {
  const ids = new Set(tasks.map((t) => t.task_id));
  if (!ids.has(taskId)) return new Set();
  const closure = new Set([taskId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const t of tasks) {
      if (closure.has(t.task_id)) continue;
      if ((t.depends_on ?? []).some((d) => closure.has(d))) {
        closure.add(t.task_id);
        changed = true;
      }
    }
  }
  return closure;
}

export type RerunResult = { ok: true; reset: string[]; paid: string[] } | { ok: false; error: string };

export async function rerunFrom(projectId: string, taskId: string): Promise<RerunResult> {
  const admin = createAdminClient();

  const { data: req } = await admin
    .from("revenueos_brain_requests")
    .select("customer_key")
    .eq("project_id", projectId)
    .order("created_at", { ascending: false })
    .limit(1)
    .maybeSingle<{ customer_key: string }>();
  if (!req?.customer_key) return { ok: false, error: "No execution run found for this project." };

  const { data: run } = await admin
    .from("brain_customer_runs")
    .select("id")
    .eq("customer", req.customer_key)
    .order("id", { ascending: false })
    .limit(1)
    .maybeSingle<{ id: number }>();
  if (!run) return { ok: false, error: "No run to re-run." };

  const { data: tasks } = await admin
    .from("brain_plan_tasks")
    .select("id, task_id, depends_on, status, output_type")
    .eq("run_id", run.id)
    .returns<TaskRow[]>();
  if (!tasks?.length) return { ok: false, error: "This run has no tasks yet." };
  if (!tasks.some((t) => t.task_id === taskId)) return { ok: false, error: `No task ${taskId} in this run.` };

  const closure = dependentsClosure(tasks, taskId);
  const closureTasks = tasks.filter((t) => closure.has(t.task_id));
  if (closureTasks.some((t) => t.status === "running")) {
    return { ok: false, error: "A step is running right now — try again in a moment." };
  }

  const ids = closureTasks.map((t) => t.id);
  await admin.from("brain_tool_calls").delete().in("task_row_id", ids);
  await admin.from("brain_decisions").delete().eq("run_id", run.id).in("task_row_id", ids);
  await admin
    .from("brain_plan_tasks")
    .update({
      status: "pending",
      waiting_for: null,
      result: null,
      last_error: null,
      achieved_number: null,
      attempts: 0,
      started_at: null,
      finished_at: null,
    })
    .in("id", ids);
  await admin.from("brain_customer_runs").update({ status: "running" }).eq("id", run.id);

  // Clear a reset data task's saved rows so it re-fetches (and Data research shows only current results).
  // Accounts feed contacts, so re-running accounts clears both companies and people.
  if (closureTasks.some((t) => t.output_type === "accounts")) {
    await admin.from("quickicp_revenueos_client_company_data").delete().eq("customer", req.customer_key);
    await admin.from("quickicp_revenueos_client_people_data").delete().eq("customer", req.customer_key);
  } else if (closureTasks.some((t) => t.output_type === "contacts")) {
    await admin.from("quickicp_revenueos_client_people_data").delete().eq("customer", req.customer_key);
  }

  return {
    ok: true,
    reset: closureTasks.map((t) => t.task_id),
    paid: closureTasks.filter((t) => t.output_type === "accounts" || t.output_type === "contacts").map((t) => t.task_id),
  };
}
