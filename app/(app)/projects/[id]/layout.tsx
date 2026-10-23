import { Suspense } from "react";
import { cookies } from "next/headers";
import Link from "next/link";
import { ChevronRight, Users } from "lucide-react";
import { ProjectArchiveButton } from "@/components/project-archive-button";
import { ProjectSidebar, type SidebarBadges } from "@/components/project-sidebar";
import { buttonVariants } from "@/components/ui/button";
import { requireProject } from "@/lib/auth";
import { loadPaidStep } from "@/lib/brain/paid-steps";
import { loadCallRecords, loadExecution } from "@/lib/brain/read";
import { executionStatus } from "@/lib/brain/status";
import { buildCallsReport, buildEmailReport, buildResearchReport } from "@/lib/brain/tools";
import { reachedStep, statusLabel, statusTone } from "@/lib/projects";
import { SIDEBAR_COOKIE } from "@/lib/sidebar";
import { cn } from "@/lib/utils";

const number = new Intl.NumberFormat("en-IN");
const updated = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: "Asia/Kolkata" });

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

const count = (value: number, one: string, many: string) => `${number.format(value)} ${value === 1 ? one : many}`;

/** What the left panel says under each Execution entry, in words, from the latest month plan. */
async function sidebarBadges(projectId: string): Promise<SidebarBadges | null> {
  const data = await loadExecution(projectId);
  if (data.state !== "ok") return null;

  const research = buildResearchReport(data.rows);
  const email = buildEmailReport(data.rows);
  const calls = buildCallsReport(data.rows);
  const recipients = email.campaigns.reduce((sum, c) => sum + (c.recipients ?? 0), 0);
  // The Brain writes its own count only when the task is finished; the calling tool's records
  // are there while calls are still being made.
  const records = await loadCallRecords(calls.campaigns.map((c) => c.reference));
  const callsMade = calls.campaigns.reduce(
    (sum, c) => sum + Math.max(c.outcomes.length, records.get(c.reference)?.calls.length ?? 0),
    0
  );
  const paid = await loadPaidStep(projectId);
  const startNeeded = paid?.status === "waiting" || paid?.status === "failed" ? 1 : 0;
  const companies = research.accounts?.achieved;
  const contacts = research.contacts?.achieved;

  return {
    waiting: data.view.openDecisions.length + startNeeded,
    waitingPeople: data.view.openDecisions.filter((d) => d.kind === "human_task" || d.kind === "human_gate_task").length,
    running: data.view.status === "running",
    research:
      companies != null
        ? [count(companies, "company", "companies"), contacts != null && count(contacts, "contact", "contacts")]
            .filter(Boolean)
            .join(" · ")
        : null,
    email: email.campaigns.length ? (recipients ? count(recipients, "recipient", "recipients") : "Being prepared") : null,
    calls: calls.campaigns.length ? (callsMade ? count(callsMade, "call made", "calls made") : "No calls yet") : null,
  };
}

export default async function ProjectLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const { project, user } = await requireProject(id);
  const isOwner = project.owner_id === user.id;
  const planReady = project.status === "plan_ready";
  const sidebarCollapsed = (await cookies()).get(SIDEBAR_COOKIE)?.value === "collapsed";
  const badges = planReady ? await sidebarBadges(project.id) : null;
  // Planning status until the plan is ready; after that, where execution stands.
  const status = (planReady && (await executionStatus(project.id))) || {
    label: statusLabel(project.status),
    tone: statusTone(project.status),
  };

  return (
    <div className="grid gap-5">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-3 rounded-xl border bg-background px-4 py-4 sm:px-6 print:hidden">
        <span
          aria-hidden
          className="flex size-12 shrink-0 items-center justify-center rounded-xl bg-primary text-lg font-semibold text-primary-foreground"
        >
          {initials(project.customer_name)}
        </span>

        <div className="min-w-0 flex-1">
          <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-xs text-muted-foreground">
            <Link href="/projects" className="hover:text-foreground hover:underline">
              Projects
            </Link>
            <ChevronRight className="size-3" aria-hidden />
            <span className="truncate">{project.customer_name}</span>
          </nav>
          <h1 className="truncate text-xl leading-tight font-semibold tracking-tight sm:text-2xl">
            {project.customer_name}
          </h1>
          {project.description && (
            <p className="mt-0.5 line-clamp-2 max-w-3xl text-sm text-muted-foreground">{project.description}</p>
          )}
        </div>

        <dl className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
          <div>
            <dt className="text-xs text-muted-foreground">Last updated</dt>
            <dd className="font-medium">{updated.format(new Date(project.updated_at))}</dd>
          </div>
          <div>
            <dt className="sr-only">Status</dt>
            <dd>
              <span className={cn("inline-flex rounded-full px-3 py-1 text-sm font-semibold", status.tone)}>
                {status.label}
              </span>
            </dd>
          </div>
          {isOwner && (
            <Link href={`/projects/${project.id}/access`} className={buttonVariants({ variant: "outline", size: "sm" })}>
              <Users aria-hidden />
              Manage access
            </Link>
          )}
          <ProjectArchiveButton projectId={project.id} customerName={project.customer_name} archived={!!project.archived_at} />
        </dl>
      </header>

      {project.archived_at && (
        <p className="rounded-xl border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-sm text-amber-900 print:hidden">
          This project was archived on {updated.format(new Date(project.archived_at))}. Everything is kept and can
          be read; restore it to start execution again.
        </p>
      )}

      {/* The panel sets its own width (open or collapsed to icons); the page takes the rest. */}
      <div className="grid items-start gap-5 lg:grid-cols-[auto_minmax(0,1fr)] xl:gap-6 print:block">
        <Suspense
          fallback={
            <div
              className={cn(
                "h-12 rounded-xl border bg-background lg:h-96",
                sidebarCollapsed ? "lg:w-[3.75rem]" : "lg:w-[15.5rem] xl:w-[17rem]"
              )}
            />
          }
        >
          <ProjectSidebar
            projectId={project.id}
            reached={reachedStep(project.status)}
            planReady={planReady}
            badges={badges}
            initiallyCollapsed={sidebarCollapsed}
          />
        </Suspense>
        <div className="min-w-0 rounded-xl border bg-background p-4 sm:p-6 xl:p-8 print:border-0 print:p-0">
          {children}
        </div>
      </div>
    </div>
  );
}
