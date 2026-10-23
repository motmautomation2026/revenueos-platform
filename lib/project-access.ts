import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";

// Who may work on a project besides its owner: the salespeople the admin has given access to
// (revenueos_project_members, migration 0014). A member has the same full access as the owner.
// Reads and writes use the service-role client, AFTER the caller has checked the signed-in user is
// the project's owner (the admin). revenueos_profiles is own-row under RLS, so looking up a person
// by email also needs the service-role client.

export type ProjectMember = { userId: string; name: string; email: string; addedAt: string };

type Result = { ok: true } | { ok: false; error: string };

const NOT_APPLIED =
  "The database update that adds project members has not been applied (supabase/migrations/0014_project_members.sql).";

/** The project's members (not the owner), oldest first, with their name and email. */
export async function loadProjectMembers(projectId: string): Promise<ProjectMember[]> {
  const admin = createAdminClient();
  const { data: rows } = await admin
    .from(TABLES.projectMembers)
    .select("user_id, added_at")
    .eq("project_id", projectId)
    .order("added_at", { ascending: true })
    .returns<{ user_id: string; added_at: string }[]>();
  if (!rows?.length) return [];

  const { data: profiles } = await admin
    .from(TABLES.profiles)
    .select("id, full_name, email")
    .in(
      "id",
      rows.map((r) => r.user_id)
    )
    .returns<{ id: string; full_name: string | null; email: string | null }[]>();
  const byId = new Map((profiles ?? []).map((p) => [p.id, p]));

  return rows.map((r) => {
    const profile = byId.get(r.user_id);
    const email = (profile?.email ?? "").trim();
    return { userId: r.user_id, name: (profile?.full_name ?? "").trim() || email, email, addedAt: r.added_at };
  });
}

/** Gives an existing RevenueOS account access to the project by email. */
export async function addProjectMemberByEmail(projectId: string, ownerId: string, email: string): Promise<Result> {
  const clean = email.trim().toLowerCase();
  if (!clean) return { ok: false, error: "Enter an email address." };

  const admin = createAdminClient();
  const { data: profile } = await admin
    .from(TABLES.profiles)
    .select("id")
    .ilike("email", clean)
    .maybeSingle<{ id: string }>();
  if (!profile) return { ok: false, error: "No RevenueOS account has that email. The person must sign up first." };
  if (profile.id === ownerId) return { ok: false, error: "That is the project owner, who already has access." };

  const { error } = await admin
    .from(TABLES.projectMembers)
    .upsert({ project_id: projectId, user_id: profile.id, added_by: ownerId }, { onConflict: "project_id,user_id" });
  if (error) return { ok: false, error: error.message.includes(TABLES.projectMembers) ? NOT_APPLIED : "Could not give access. Try again." };
  return { ok: true };
}

/** Removes one member's access to the project. */
export async function removeProjectMember(projectId: string, userId: string): Promise<Result> {
  const { error } = await createAdminClient()
    .from(TABLES.projectMembers)
    .delete()
    .eq("project_id", projectId)
    .eq("user_id", userId);
  if (error) return { ok: false, error: "Could not remove access. Try again." };
  return { ok: true };
}
