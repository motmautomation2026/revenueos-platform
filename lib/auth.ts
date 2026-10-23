import "server-only";
import { notFound, redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";
import type { Project } from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

/** Session check for pages and server actions. Redirects to /login when signed out. */
export async function requireUser() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();
  if (!user) redirect("/login");
  return { supabase, user };
}

/**
 * Loads a project the signed-in user can access, or 404s. Access = the owner OR a member
 * (revenueos_project_members); a member has the same full access as the owner. RLS is the second
 * layer: the row returns only when the user owns the project or is a member of it.
 */
export async function requireProject(projectId: string) {
  const { supabase, user } = await requireUser();
  if (!UUID_RE.test(projectId)) notFound();

  const { data: project } = await supabase
    .from(TABLES.projects)
    .select("*")
    .eq("id", projectId)
    .maybeSingle<Project>();
  if (!project) notFound();

  return { supabase, user, project };
}
