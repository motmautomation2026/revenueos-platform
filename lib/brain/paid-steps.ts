import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";

// public.revenueos_brain_paid_steps (supabase/migrations/0007_brain_paid_steps.sql).
//
// The Brain's worker never starts a task that buys data on its own. It records the step here
// and waits; a person starts it from the Dashboard. This file is the app's side: read the open
// step, and record the person's go-ahead. Nothing is run or bought from here.
//
// Service-role client, and only after the caller has checked that the signed-in user owns the
// project (requireProject).

const TABLE = "revenueos_brain_paid_steps";

export type PaidStepStatus = "waiting" | "approved" | "running" | "failed";
export type PaidStep = {
  id: string;
  taskId: string;
  month: string;
  status: PaidStepStatus;
  approvedBy: string | null;
  error: string | null;
};

type Row = {
  id: string;
  task_id: string;
  month: string;
  status: string;
  approved_by: string | null;
  error: string | null;
};

/**
 * The step that buys data and is waiting for a person, being run, or just failed. Null when
 * there is none, or before the database update that adds the table is applied.
 */
export const loadPaidStep = cache(async (projectId: string): Promise<PaidStep | null> => {
  try {
    const { data, error } = await createAdminClient()
      .from(TABLE)
      .select("id, task_id, month, status, approved_by, error")
      .eq("project_id", projectId)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle<Row>();
    if (error || !data) return null;
    if (!["waiting", "approved", "running", "failed"].includes(data.status)) return null;
    return {
      id: data.id,
      taskId: data.task_id,
      month: data.month,
      status: data.status as PaidStepStatus,
      approvedBy: data.approved_by,
      error: data.error,
    };
  } catch {
    return null;
  }
});

/** Records the person's go-ahead. A failed step is handed back to the Brain the same way. */
export async function approvePaidStep(input: {
  projectId: string;
  stepId: string;
  approvedBy: string;
}): Promise<{ ok: true; message: string } | { ok: false; error: string }> {
  const { data, error } = await createAdminClient()
    .from(TABLE)
    .update({ status: "approved", approved_by: input.approvedBy.slice(0, 80), approved_at: new Date().toISOString(), error: null })
    .eq("id", input.stepId)
    .eq("project_id", input.projectId) // never another project's step
    .in("status", ["waiting", "failed"])
    .select("id");
  // 23505: the task already has an open step (someone else started it).
  if (error?.code === "23505") return { ok: false, error: "This step was already started." };
  if (error) return { ok: false, error: "That could not be saved. Try again." };
  if (!data?.length) return { ok: false, error: "This step is no longer waiting. Refresh the page." };
  return { ok: true, message: "Started. The Brain picks it up within about half a minute." };
}
