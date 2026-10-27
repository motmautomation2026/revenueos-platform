"use server";

import { revalidatePath } from "next/cache";
import { requireProject } from "@/lib/auth";
import { addProjectMemberByEmail, removeProjectMember, type ProjectRole } from "@/lib/project-access";

/** Only the project's owner (the admin who created it) may give or remove access. */
async function requireOwner(projectId: unknown) {
  if (typeof projectId !== "string") return null;
  const { user, project } = await requireProject(projectId);
  return project.owner_id === user.id ? { user, project } : null;
}

export async function giveAccess(projectId: unknown, email: unknown, role: unknown): Promise<{ error?: string }> {
  const ctx = await requireOwner(projectId);
  if (!ctx) return { error: "Only the project owner can manage access." };
  if (typeof email !== "string") return { error: "Enter an email address." };
  const memberRole: ProjectRole = role === "client" ? "client" : "salesperson";

  const result = await addProjectMemberByEmail(ctx.project.id, ctx.user.id, email, memberRole);
  if (!result.ok) return { error: result.error };
  revalidatePath(`/projects/${ctx.project.id}/access`);
  return {};
}

export async function revokeAccess(projectId: unknown, userId: unknown): Promise<{ error?: string }> {
  const ctx = await requireOwner(projectId);
  if (!ctx) return { error: "Only the project owner can manage access." };
  if (typeof userId !== "string") return { error: "Invalid request." };

  const result = await removeProjectMember(ctx.project.id, userId);
  if (!result.ok) return { error: result.error };
  revalidatePath(`/projects/${ctx.project.id}/access`);
  return {};
}
