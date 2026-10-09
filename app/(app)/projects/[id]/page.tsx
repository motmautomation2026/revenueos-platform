import { redirect } from "next/navigation";
import { requireProject } from "@/lib/auth";
import { currentStep } from "@/lib/projects";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { project } = await requireProject(id);
  redirect(`/projects/${project.id}/${currentStep(project.status).path}`);
}
