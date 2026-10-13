import { redirect } from "next/navigation";
import { LiveIndicator } from "@/components/brain/live-indicator";
import { requireProject } from "@/lib/auth";

export default async function ExecutionLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { project } = await requireProject(id);
  // Execution opens only once the plan is ready.
  if (project.status !== "plan_ready") redirect(`/projects/${project.id}`);

  return (
    <div className="grid gap-4">
      <LiveIndicator />
      {children}
    </div>
  );
}
