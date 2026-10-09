import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, FolderPlus, Plus } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { requireUser } from "@/lib/auth";
import { STEPS, currentStep, reachedStep, statusLabel, type Project, type ProjectStatus } from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Projects" };

const dateFormat = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" });

const BUSY: ProjectStatus[] = ["checklist_generating", "plan_running"];
const NEEDS_ATTENTION: ProjectStatus[] = ["plan_blocked", "failed"];

function statusTone(status: ProjectStatus) {
  if (NEEDS_ATTENTION.includes(status)) return "bg-amber-500/15 text-amber-800";
  if (status === "plan_ready") return "bg-emerald-500/15 text-emerald-800";
  if (BUSY.includes(status)) return "bg-primary/10 text-primary";
  return "bg-muted text-muted-foreground";
}

export default async function ProjectsPage() {
  const { supabase, user } = await requireUser();
  const { data, error } = await supabase
    .from(TABLES.projects)
    .select("*")
    .eq("owner_id", user.id)
    .order("updated_at", { ascending: false })
    .returns<Project[]>();
  const projects = data ?? [];

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-muted-foreground">
            {projects.length === 0
              ? "One project per customer."
              : `${projects.length} customer${projects.length === 1 ? "" : "s"} · most recently updated first`}
          </p>
        </div>
        <Link href="/projects/new" className={buttonVariants({ size: "lg" })}>
          <Plus aria-hidden />
          New project
        </Link>
      </div>

      {error ? (
        <p role="alert" className="rounded-xl border border-destructive/30 bg-background p-4 text-sm text-destructive">
          Could not load your projects. Refresh the page to try again.
        </p>
      ) : projects.length === 0 ? (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed bg-background px-6 py-16 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-primary/10">
            <FolderPlus className="size-6 text-primary" aria-hidden />
          </span>
          <div className="max-w-sm">
            <p className="font-medium">Start with your first customer</p>
            <p className="text-sm text-muted-foreground">
              Create a project, upload the handover and proposal, and RevenueOS writes the
              discovery checklist and then the go-to-market plan.
            </p>
          </div>
          <Link href="/projects/new" className={buttonVariants({ size: "lg" })}>
            <Plus aria-hidden />
            New project
          </Link>
        </div>
      ) : (
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map((project) => {
            const step = currentStep(project.status);
            const reached = reachedStep(project.status);
            return (
              <li key={project.id}>
                <Link
                  href={`/projects/${project.id}`}
                  className="group flex h-full flex-col gap-4 rounded-xl border bg-background p-4 transition-shadow outline-none hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="truncate font-semibold" title={project.customer_name}>
                        {project.customer_name}
                      </p>
                      <p className="truncate text-sm text-muted-foreground">{project.engagement_type}</p>
                    </div>
                    <span
                      className={cn(
                        "shrink-0 rounded-full px-2 py-0.5 text-xs font-medium",
                        statusTone(project.status)
                      )}
                    >
                      {statusLabel(project.status)}
                    </span>
                  </div>

                  <div className="mt-auto grid gap-1.5">
                    <div className="flex gap-1" aria-hidden>
                      {STEPS.map((s) => (
                        <span
                          key={s.key}
                          className={cn(
                            "h-1.5 flex-1 rounded-full",
                            s.number < reached || project.status === "plan_ready"
                              ? "bg-primary"
                              : s.number === reached
                                ? "bg-primary/40"
                                : "bg-muted"
                          )}
                        />
                      ))}
                    </div>
                    <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                      <span>
                        Step {step.number} of {STEPS.length} · {step.label}
                      </span>
                      <span>{dateFormat.format(new Date(project.updated_at))}</span>
                    </div>
                  </div>

                  <span className="flex items-center gap-1 text-sm font-medium text-primary">
                    Open
                    <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
