import type { Metadata } from "next";
import Link from "next/link";
import { Archive, ArrowRight, FolderPlus, Plus } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { requireUser } from "@/lib/auth";
import { executionSummary } from "@/lib/brain/status";
import { isArchived, reachedStep, statusLabel, statusTone, type Project } from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Projects" };

const number = new Intl.NumberFormat("en-IN");
const dateFormat = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: "Asia/Kolkata" });

const PLANNING = ["Inputs", "Checklist", "GTM plan"];

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

export default async function ProjectsPage({ searchParams }: { searchParams: Promise<{ show?: string }> }) {
  const showArchived = (await searchParams).show === "archived";
  const { supabase, user } = await requireUser();
  const { data, error } = await supabase
    .from(TABLES.projects)
    .select("*")
    .eq("owner_id", user.id)
    .order("updated_at", { ascending: false })
    .returns<Project[]>();
  const all = data ?? [];
  const archivedCount = all.filter(isArchived).length;
  // Archived projects live in their own list and are left out of every number above it.
  const projects = all.filter((project) => isArchived(project) === showArchived);

  // For projects whose plan is ready: where execution stands, from the Brain's latest month plan.
  const execution = new Map(
    await Promise.all(
      projects
        .filter((project) => project.status === "plan_ready" && !showArchived)
        .map(async (project) => [project.id, await executionSummary(project.id)] as const)
    )
  );

  const inExecution = projects.filter((project) => execution.get(project.id)).length;
  const waiting = [...execution.values()].reduce((sum, summary) => sum + (summary?.waiting ?? 0), 0);
  const tiles = [
    { label: "Customers", value: projects.length, note: "" },
    { label: "In planning", value: projects.length - inExecution, note: "Inputs, checklist or plan" },
    { label: "In execution", value: inExecution, note: "The Brain has a month plan" },
    { label: "Waiting for you", value: waiting, note: waiting ? "Decisions to answer" : "Nothing to answer", attention: waiting > 0 },
  ];

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
          <p className="text-sm text-muted-foreground">One project for each customer, most recently updated first.</p>
          {archivedCount > 0 && (
            <nav aria-label="Project lists" className="mt-3 flex gap-1 text-sm">
              {[
                { href: "/projects", label: `Active (${number.format(all.length - archivedCount)})`, current: !showArchived },
                { href: "/projects?show=archived", label: `Archived (${number.format(archivedCount)})`, current: showArchived },
              ].map((tab) => (
                <Link
                  key={tab.href}
                  href={tab.href}
                  aria-current={tab.current ? "page" : undefined}
                  className={cn(
                    "rounded-lg px-3 py-1.5 font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                    tab.current ? "bg-primary/10 text-primary" : "text-muted-foreground hover:bg-muted"
                  )}
                >
                  {tab.label}
                </Link>
              ))}
            </nav>
          )}
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
      ) : projects.length === 0 && all.length > 0 ? (
        <p className="rounded-xl border border-dashed bg-background px-6 py-12 text-center text-sm text-muted-foreground">
          {showArchived ? "No archived projects." : "Every project is archived. Create a new one, or restore one from Archived."}
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
        <>
          <div className={cn("grid grid-cols-2 gap-3 lg:grid-cols-4", showArchived && "hidden")}>
            {tiles.map((tile) => (
              <div
                key={tile.label}
                className={cn(
                  "grid content-start gap-1 rounded-xl border bg-background p-4",
                  tile.attention && "border-amber-500/60 bg-amber-500/10"
                )}
              >
                <p className="text-sm text-muted-foreground">{tile.label}</p>
                <p className="text-3xl leading-none font-semibold tabular-nums">{number.format(tile.value)}</p>
                {tile.note && <p className="text-xs text-muted-foreground">{tile.note}</p>}
              </div>
            ))}
          </div>

          <ul className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
            {projects.map((project) => {
              const summary = execution.get(project.id) ?? null;
              const reached = reachedStep(project.status);
              const planReady = project.status === "plan_ready";
              const status = showArchived
                ? { label: "Archived", tone: "bg-muted text-muted-foreground" }
                : (summary?.status ?? { label: statusLabel(project.status), tone: statusTone(project.status) });
              // Inputs, checklist and plan: how many of the three are finished.
              const planningDone = planReady ? 3 : Math.min(2, reached - 1);
              const tasksPercent = summary && summary.total ? Math.round((summary.done / summary.total) * 100) : 0;

              return (
                <li key={project.id}>
                  <Link
                    href={`/projects/${project.id}`}
                    className="group flex h-full flex-col gap-4 rounded-xl border bg-background p-5 transition-shadow outline-none hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <div className="flex items-center gap-3">
                      <span
                        aria-hidden
                        className={cn(
                          "flex size-11 shrink-0 items-center justify-center rounded-xl font-semibold",
                          showArchived ? "bg-muted text-muted-foreground" : "bg-primary text-primary-foreground"
                        )}
                      >
                        {showArchived ? <Archive className="size-5" /> : initials(project.customer_name)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-lg leading-tight font-semibold" title={project.customer_name}>
                          {project.customer_name}
                        </p>
                        <p className="truncate text-xs text-muted-foreground">
                          {summary ? `${summary.month} plan` : `Updated ${dateFormat.format(new Date(project.updated_at))}`}
                        </p>
                      </div>
                      <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold", status.tone)}>
                        {status.label}
                      </span>
                    </div>

                    <div className="grid gap-3">
                      <div className="grid gap-1.5">
                        <div className="flex items-baseline justify-between gap-2 text-sm">
                          <span className="font-medium">Planning</span>
                          <span className="text-xs text-muted-foreground">
                            {planReady ? "Complete" : `Now: ${PLANNING[Math.min(2, reached - 1)]}`}
                          </span>
                        </div>
                        <div className="flex gap-1" aria-hidden>
                          {PLANNING.map((step, index) => (
                            <span
                              key={step}
                              className={cn(
                                "h-1.5 flex-1 rounded-full",
                                index < planningDone ? "bg-primary" : index === planningDone ? "bg-primary/40" : "bg-muted"
                              )}
                            />
                          ))}
                        </div>
                      </div>

                      <div className="grid gap-1.5">
                        <div className="flex items-baseline justify-between gap-2 text-sm">
                          <span className="font-medium">Execution</span>
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {summary
                              ? `${number.format(summary.done)} of ${number.format(summary.total)} tasks`
                              : planReady
                                ? "Not started"
                                : "After the plan"}
                          </span>
                        </div>
                        <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                          {summary && (
                            <div
                              className={cn("h-full rounded-full", tasksPercent >= 100 ? "bg-emerald-600" : "bg-primary")}
                              style={{ width: `${tasksPercent}%` }}
                            />
                          )}
                        </div>
                      </div>
                    </div>

                    {summary && summary.results.length > 0 && (
                      <dl className="grid grid-cols-2 gap-2 sm:grid-cols-4">
                        {summary.results.map((result) => (
                          <div key={result.label} className="rounded-lg bg-muted/50 px-3 py-2">
                            <dd className="text-lg leading-tight font-semibold tabular-nums">{number.format(result.value)}</dd>
                            <dt className="text-xs text-muted-foreground">{result.label}</dt>
                          </div>
                        ))}
                      </dl>
                    )}

                    <div className="mt-auto flex items-center justify-between gap-3 border-t pt-3 text-sm">
                      <span className="text-xs text-muted-foreground">
                        {summary?.waiting
                          ? `${number.format(summary.waiting)} decision${summary.waiting === 1 ? "" : "s"} waiting for you`
                          : summary
                            ? "Nothing waiting for you"
                            : project.archived_at
                              ? `Archived ${dateFormat.format(new Date(project.archived_at))}`
                              : `Created ${dateFormat.format(new Date(project.created_at))}`}
                      </span>
                      <span className="flex items-center gap-1 font-medium text-primary">
                        Open
                        <ArrowRight className="size-4 transition-transform group-hover:translate-x-0.5" aria-hidden />
                      </span>
                    </div>
                  </Link>
                </li>
              );
            })}
          </ul>
        </>
      )}
    </div>
  );
}
