"use server";

import { redirect } from "next/navigation";
import { revalidatePath } from "next/cache";
import { requireProject, requireUser } from "@/lib/auth";
import { newProjectSchema } from "@/lib/validation";
import { TABLES } from "@/lib/supabase/tables";

export async function createProject(input: unknown): Promise<{ error: string }> {
  const { supabase, user } = await requireUser();

  const parsed = newProjectSchema.safeParse(input);
  if (!parsed.success) return { error: "Please check the form and try again." };
  const { customerName, engagementType, description } = parsed.data;

  const { data, error } = await supabase
    .from(TABLES.projects)
    .insert({
      owner_id: user.id,
      customer_name: customerName,
      engagement_type: engagementType,
      description: description || null,
    })
    .select("id")
    .single();
  if (error || !data) return { error: "Could not create the project. Please try again." };

  redirect(`/projects/${data.id}`);
}

export async function updateProject(
  projectId: unknown,
  input: unknown
): Promise<{ ok: true } | { ok: false; error: string }> {
  if (typeof projectId !== "string") return { ok: false, error: "Invalid request." };
  const { supabase, project } = await requireProject(projectId);

  const parsed = newProjectSchema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Please check the form and try again." };
  const { customerName, engagementType, description } = parsed.data;

  const { error } = await supabase
    .from(TABLES.projects)
    .update({
      customer_name: customerName,
      engagement_type: engagementType,
      description: description || null,
    })
    .eq("id", project.id);
  if (error) return { ok: false, error: "Could not save the changes. Please try again." };

  revalidatePath("/projects");
  return { ok: true };
}
