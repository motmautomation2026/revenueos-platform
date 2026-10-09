import Link from "next/link";
import { ChevronRight } from "lucide-react";
import { ProjectEditDialog } from "@/components/project-edit-dialog";
import { ProjectStepper } from "@/components/project-stepper";
import { requireProject } from "@/lib/auth";
import { reachedStep, statusLabel } from "@/lib/projects";

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { project } = await requireProject(id);

  return (
    <div className="grid gap-5">
      <div className="grid gap-4 print:hidden">
        <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-sm text-muted-foreground">
          <Link href="/projects" className="hover:text-foreground">
            Projects
          </Link>
          <ChevronRight className="size-3.5" aria-hidden />
          <span className="truncate text-foreground">{project.customer_name}</span>
        </nav>

        <div className="grid gap-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">{project.customer_name}</h1>
            <ProjectEditDialog
              projectId={project.id}
              values={{
                customerName: project.customer_name,
                engagementType: project.engagement_type,
                description: project.description ?? "",
              }}
            />
          </div>
          <p className="text-sm text-muted-foreground">
            {project.engagement_type} · {statusLabel(project.status)}
          </p>
          {project.description && (
            <p className="max-w-3xl text-sm text-muted-foreground">{project.description}</p>
          )}
        </div>

        <div className="rounded-xl border bg-background px-2 py-3 sm:px-6">
          <ProjectStepper
            projectId={project.id}
            reached={reachedStep(project.status)}
            complete={project.status === "plan_ready"}
          />
        </div>
      </div>

      <div className="rounded-xl border bg-background p-4 sm:p-6 print:border-0 print:p-0">
        {children}
      </div>
    </div>
  );
}
