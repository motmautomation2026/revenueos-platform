import "server-only";
import { requireProject } from "@/lib/auth";
import { loadExecution } from "@/lib/brain/read";

export type ExecutionPageProps = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ run?: string; q?: string; page?: string; list?: string; show?: string }>;
};

/** Session + ownership check, then the selected run. Shared by every Execution tab. */
export async function loadExecutionPage({ params, searchParams }: ExecutionPageProps) {
  const { id } = await params;
  const query = await searchParams;
  const { project } = await requireProject(id);
  const data = await loadExecution(project.id, query.run);
  return { project, data, query, base: `/projects/${project.id}/execution` };
}
