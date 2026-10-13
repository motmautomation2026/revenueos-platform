"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { requireProject, requireUser } from "@/lib/auth";
import { loadExecution } from "@/lib/brain/read";
import { loadRequest } from "@/lib/brain/request-store";
import { ENGAGEMENT_TYPE } from "@/lib/projects";
import { newProjectSchema } from "@/lib/validation";
import { TABLES } from "@/lib/supabase/tables";

export async function createProject(input: unknown): Promise<{ error: string }> {
  const { supabase, user } = await requireUser();

  const parsed = newProjectSchema.safeParse(input);
  if (!parsed.success) return { error: "Please check the form and try again." };
  const { customerName, customerEmail, description } = parsed.data;

  const { data, error } = await supabase
    .from(TABLES.projects)
    .insert({
      owner_id: user.id,
      customer_name: customerName,
      customer_email: customerEmail,
      engagement_type: ENGAGEMENT_TYPE,
      description: description || null,
    })
    .select("id")
    .single();
  if (error?.message?.includes("customer_email")) {
    return { error: "The database update that adds the customer email has not been applied (0005_project_customer_email.sql)." };
  }
  if (error || !data) return { error: "Could not create the project. Please try again." };

  redirect(`/projects/${data.id}`);
}

type ArchiveResult = { ok: true } | { ok: false; error: string };

const NOT_APPLIED =
  "The database update that adds archiving has not been applied (supabase/migrations/0006_project_archive.sql).";

/**
 * Moves a project out of the main list. Nothing is deleted. Refused while the Brain is working on
 * it: an archived project would keep running out of sight.
 */
export async function archiveProject(projectId: unknown): Promise<ArchiveResult> {
  if (typeof projectId !== "string") return { ok: false, error: "Invalid request." };
  const { supabase, project } = await requireProject(projectId);

  if (project.status === "plan_ready") {
    const [execution, request] = await Promise.all([loadExecution(project.id), loadRequest(project.id)]);
    const running = execution.state === "ok" && ["planning", "running", "waiting_human"].includes(execution.runs[0]?.status ?? "");
    if (running || request.view.active) {
      return { ok: false, error: "Execution is in progress for this customer. It must finish or be closed before archiving." };
    }
  }

  const { error } = await supabase
    .from(TABLES.projects)
    .update({ archived_at: new Date().toISOString() })
    .eq("id", project.id);
  if (error) return { ok: false, error: error.message.includes("archived_at") ? NOT_APPLIED : "Could not archive the project. Try again." };

  revalidatePath("/projects");
  revalidatePath(`/projects/${project.id}`, "layout");
  return { ok: true };
}

export async function restoreProject(projectId: unknown): Promise<ArchiveResult> {
  if (typeof projectId !== "string") return { ok: false, error: "Invalid request." };
  const { supabase, project } = await requireProject(projectId);

  const { error } = await supabase.from(TABLES.projects).update({ archived_at: null }).eq("id", project.id);
  if (error) return { ok: false, error: error.message.includes("archived_at") ? NOT_APPLIED : "Could not restore the project. Try again." };

  revalidatePath("/projects");
  revalidatePath(`/projects/${project.id}`, "layout");
  return { ok: true };
}
