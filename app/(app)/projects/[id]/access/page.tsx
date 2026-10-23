import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { ProjectAccess } from "@/components/project-access";
import { requireProject } from "@/lib/auth";
import { loadProjectMembers } from "@/lib/project-access";

export const metadata: Metadata = { title: "Access" };

export default async function AccessPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { user, project } = await requireProject(id);
  // Owner-only: a member has full access to the project but does not manage who else is on it.
  if (project.owner_id !== user.id) notFound();

  const members = await loadProjectMembers(project.id);
  return <ProjectAccess projectId={project.id} members={members} />;
}
