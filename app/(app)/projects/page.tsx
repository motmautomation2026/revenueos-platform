import type { Metadata } from "next";
import Link from "next/link";
import { Archive, ArrowRight, CircleCheck, FolderPlus, Plus, Search, TriangleAlert } from "lucide-react";
import { LiveIndicator } from "@/components/brain/live-indicator";
import { buttonVariants } from "@/components/ui/button";
import { requireUser } from "@/lib/auth";
import { attentionItems, executionSummary, type AttentionItem, type ExecutionSummary } from "@/lib/brain/status";
import type { WorkerState } from "@/lib/brain/worker-state";
import { loadWorkerStatus } from "@/lib/brain/worker-status";
import {
  isArchived,
  planningNextStep,
  reachedStep,
  statusLabel,
  statusTone,
  type Project,
} from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Projects" };

const number = new Intl.NumberFormat("en-IN");
const dateFormat = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeZone: "Asia/Kolkata" });
const todayFormat = new Intl.DateTimeFormat("en-IN", { weekday: "long", day: "numeric", month: "long", timeZone: "Asia/Kolkata" });
const hourFormat = new Intl.DateTimeFormat("en-IN", { hour: "numeric", hourCycle: "h23", timeZone: "Asia/Kolkata" });

const PLANNING = ["Inputs", "Checklist", "GTM plan"];
const FILTERS = [
  { key: "all", label: "All" },
  { key: "needs", label: "Needs you" },
  { key: "planning", label: "Planning" },
  { key: "execution", label: "Execution" },
] as const;
type FilterKey = (typeof FILTERS)[number]["key"];

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? "")).toUpperCase() || "?";
}

function greeting() {
  const hour = Number(hourFormat.format(new Date()));
  return hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
}

/** "5 min", "3 h", "2 days": how long ago something happened, or how long it has been waiting. */
function waitingFor(since: string | null) {
  if (!since) return "";
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(since)) / 60000));
  if (!Number.isFinite(minutes)) return "";
  if (minutes < 1) return "under 1 min";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  return `${Math.round(hours / 24)} days`;
}

type Row = {
  project: Project;
  summary: ExecutionSummary | null;
  /** What a person has to do on this project now. */
  needs: (AttentionItem & { projectId: string; projectName: string })[];
};

export default async function ProjectsPage({
  searchParams,
}: {
  searchParams: Promise<{ show?: string; filter?: string; q?: string }>;
}) {
  const query = await searchParams;
  const showArchived = query.show === "archived";
  const filter: FilterKey = FILTERS.some((f) => f.key === query.filter) ? (query.filter as FilterKey) : "all";
  const search = (query.q ?? "").trim().slice(0, 80);

  const { supabase, user } = await requireUser();
  const workerPromise = loadWorkerStatus();
  // No owner filter: RLS returns the projects this user owns AND those they are a member of
  // (revenueos_project_members). Assigned ones get the same full access and are marked "Shared".
  const { data, error } = await supabase
    .from(TABLES.projects)
    .select("*")
    .order("updated_at", { ascending: false })
    .returns<Project[]>();
  const all = data ?? [];
  const archivedCount = all.filter(isArchived).length;
  // Archived projects live in their own list and are left out of every number above it.
  const listed = all.filter((project) => isArchived(project) === showArchived);

  // Per project: where execution stands (plan ready) and what is waiting for a person.
  const rows: Row[] = await Promise.all(
    listed.map(async (project): Promise<Row> => {
      const base = { projectId: project.id, projectName: project.customer_name };
      if (showArchived) return { project, summary: null, needs: [] };
      if (project.status === "plan_ready") {
        const [summary, items] = await Promise.all([executionSummary(project.id), attentionItems(project.id)]);
        return { project, summary, needs: items.map((item) => ({ ...item, ...base })) };
      }
      const next = planningNextStep(project.status);
      return {
        project,
        summary: null,
        needs: next
          ? [{ key: `planning-${project.status}`, title: next.label, detail: statusLabel(project.status), since: project.updated_at, path: next.path, ...base }]
          : [],
      };
    })
  );

  // Clients that need you first (longest waiting first), then the most recently active.
  const activity = (row: Row) => Date.parse(row.summary?.lastActivity ?? row.project.updated_at) || 0;
  const oldestNeed = (row: Row) => Math.min(...row.needs.map((n) => (n.since ? Date.parse(n.since) : 0)));
  rows.sort((a, b) =>
    a.needs.length && b.needs.length ? oldestNeed(a) - oldestNeed(b)
    : a.needs.length ? -1
    : b.needs.length ? 1
    : activity(b) - activity(a)
  );
  const worker = await workerPromise;

  const inExecution = rows.filter((row) => row.summary).length;
  const needsYou = rows.flatMap((row) => row.needs);
  // Oldest first: what has waited longest is at the top. Items without a time go first too.
  needsYou.sort((a, b) => (a.since ? Date.parse(a.since) : 0) - (b.since ? Date.parse(b.since) : 0));

  // Each project's own results (contacts, emails, calls) stay on its card: they are not added up here.
  const tiles = [
    { label: "Clients", value: rows.length, note: `${number.format(rows.length - inExecution)} planning · ${number.format(inExecution)} in execution` },
  ];

  const inFilter = (row: Row) =>
    filter === "needs" ? row.needs.length > 0 : filter === "planning" ? !row.summary : filter === "execution" ? !!row.summary : true;
  const counts: Record<FilterKey, number> = {
    all: rows.length,
    needs: rows.filter((row) => row.needs.length > 0).length,
    planning: rows.filter((row) => !row.summary).length,
    execution: inExecution,
  };
  const shown = rows.filter(inFilter).filter((row) => !search || row.project.customer_name.toLowerCase().includes(search.toLowerCase()));

  const href = (changes: Record<string, string | undefined>) => {
    const params = new URLSearchParams();
    const next = { show: showArchived ? "archived" : undefined, filter: filter === "all" ? undefined : filter, q: search || undefined, ...changes };
    for (const [key, value] of Object.entries(next)) if (value) params.set(key, value);
    const text = params.toString();
    return text ? `/projects?${text}` : "/projects";
  };
  const firstName = String(user.user_metadata?.full_name ?? user.user_metadata?.name ?? "").trim().split(/\s+/)[0];

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">
            {greeting()}
            {firstName ? `, ${firstName}` : ""} · {todayFormat.format(new Date())}
          </p>
          <h1 className="text-2xl font-semibold tracking-tight">Projects</h1>
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
        <div className="flex flex-wrap items-center gap-3">
          <LiveIndicator intervalMs={60_000} />
          <Link href="/projects/new" className={buttonVariants({ size: "lg" })}>
            <Plus aria-hidden />
            New project
          </Link>
        </div>
      </div>

      {!showArchived && <WorkerLine worker={worker} />}

      {error ? (
        <p role="alert" className="rounded-xl border border-destructive/30 bg-background p-4 text-sm text-destructive">
          Could not load your projects. Refresh the page to try again.
        </p>
      ) : rows.length === 0 && all.length > 0 ? (
        <p className="rounded-xl border border-dashed bg-background px-6 py-12 text-center text-sm text-muted-foreground">
          {showArchived ? "No archived projects." : "Every project is archived. Create a new one, or restore one from Archived."}
        </p>
      ) : rows.length === 0 ? (
        <div className="flex flex-col items-center gap-4 rounded-xl border border-dashed bg-background px-6 py-16 text-center">
          <span className="flex size-12 items-center justify-center rounded-full bg-primary/10">
            <FolderPlus className="size-6 text-primary" aria-hidden />
          </span>
          <div className="max-w-sm">
            <p className="font-medium">Start with your first client</p>
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
          {!showArchived && (
            <>
              <div className="grid gap-3 sm:grid-cols-3">
                <WaitingTile items={needsYou} />
                {tiles.map((tile) => (
                  <div key={tile.label} className="grid content-start gap-1 rounded-xl border bg-background p-4">
                    <p className="text-sm text-muted-foreground">{tile.label}</p>
                    <p className="text-3xl leading-none font-semibold tabular-nums">{number.format(tile.value)}</p>
                    <p className="text-xs text-muted-foreground">{tile.note}</p>
                  </div>
                ))}
              </div>
            </>
          )}

          <div className="flex flex-wrap items-center justify-between gap-3">
            {!showArchived ? (
              <nav aria-label="Filter projects" className="flex flex-wrap gap-1 text-sm">
                {FILTERS.map((f) => (
                  <Link
                    key={f.key}
                    href={href({ filter: f.key === "all" ? undefined : f.key })}
                    aria-current={filter === f.key ? "page" : undefined}
                    className={cn(
                      "rounded-full border px-3 py-1 font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                      filter === f.key ? "border-primary bg-primary text-primary-foreground" : "bg-background text-muted-foreground hover:bg-muted"
                    )}
                  >
                    {f.label} <span className="tabular-nums opacity-80">{number.format(counts[f.key])}</span>
                  </Link>
                ))}
              </nav>
            ) : (
              <span />
            )}
            <form action="/projects" className="relative w-full sm:w-64">
              {showArchived && <input type="hidden" name="show" value="archived" />}
              {filter !== "all" && <input type="hidden" name="filter" value={filter} />}
              <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
              <input
                type="search"
                name="q"
                defaultValue={search}
                placeholder="Search clients"
                aria-label="Search clients"
                className="h-9 w-full rounded-lg border bg-background pr-3 pl-9 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              />
            </form>
          </div>

          {shown.length === 0 ? (
            <p className="rounded-xl border border-dashed bg-background px-6 py-10 text-center text-sm text-muted-foreground">
              No projects match.{" "}
              <Link href={href({ filter: undefined, q: undefined })} className="font-medium text-primary hover:underline">
                Show all
              </Link>
            </p>
          ) : (
            <ul className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
              {shown.map((row) => (
                <ProjectCard key={row.project.id} row={row} archived={showArchived} myUserId={user.id} />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/** The worker's note ("1 step(s) running, 0 planning") in plain words: "idle", "1 step running", "planning 1 month". */
function workerActivity(note: string) {
  const match = note.match(/^(\d+) step\(s\) running, (\d+) planning$/);
  if (!match) return note === "started" || !note ? "idle" : note;
  const [steps, planning] = [Number(match[1]), Number(match[2])];
  const parts = [
    steps ? `${steps} step${steps === 1 ? "" : "s"} running` : "",
    planning ? `planning ${planning} month${planning === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(", ") : "idle";
}

/** Whether the Brain worker is running. Nothing moves for any client while it is not. */
function WorkerLine({ worker }: { worker: WorkerState }) {
  if (worker.state === "unknown") return null;
  if (worker.state === "online") {
    return (
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span aria-hidden className="size-2 rounded-full bg-emerald-600" />
        <span className="font-medium text-foreground">Brain worker online</span>
        <span>
          {worker.worker} · checked {waitingFor(worker.lastSeen)} ago · {workerActivity(worker.note)}
        </span>
      </p>
    );
  }
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-destructive/40 bg-destructive/5 p-4 text-sm">
      <TriangleAlert className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
      {worker.state === "several" ? (
        <p>
          <span className="font-semibold text-destructive">{worker.workers.length} Brain workers are running: {worker.workers.join(", ")}.</span>{" "}
          <span className="text-muted-foreground">Only one may run, or a client&apos;s steps can run twice. Stop all but one.</span>
        </p>
      ) : (
        <p>
          <span className="font-semibold text-destructive">The Brain worker is not running.</span>{" "}
          <span className="text-muted-foreground">
            {worker.lastSeen
              ? `${worker.worker} was last seen ${waitingFor(worker.lastSeen)} ago. `
              : "No worker has reported in yet. "}
            Nothing moves for any client until it is started again (on the VPS: docker compose up -d worker).
          </span>
        </p>
      )}
    </div>
  );
}

/**
 * The first tile: how many things wait for a person, and the oldest of them, each linking straight
 * to where it is answered. The rest are one click away (the "Needs you" filter).
 */
function WaitingTile({ items }: { items: Row["needs"] }) {
  const SHOWN = 2;
  return (
    <div
      className={cn(
        "grid min-w-0 content-start gap-2 overflow-hidden rounded-xl border bg-background p-4 sm:col-span-2",
        items.length > 0 && "border-amber-500/60 bg-amber-500/10"
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-sm text-muted-foreground">Waiting for you</p>
        {items.length > SHOWN && (
          <Link href="/projects?filter=needs" className="text-xs font-medium text-primary hover:underline">
            +{number.format(items.length - SHOWN)} more
          </Link>
        )}
      </div>
      <p className="text-3xl leading-none font-semibold tabular-nums">{number.format(items.length)}</p>
      {items.length === 0 ? (
        <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <CircleCheck className="size-3.5 text-emerald-600" aria-hidden />
          Nothing to do
        </p>
      ) : (
        <ul className="grid min-w-0 gap-1">
          {items.slice(0, SHOWN).map((item) => (
            <li key={`${item.projectId}-${item.key}`} className="min-w-0">
              <Link
                href={`/projects/${item.projectId}/${item.path}`}
                title={[item.title, item.detail].filter(Boolean).join(" · ")}
                className="group flex min-w-0 items-center gap-2 rounded-lg bg-background/70 px-2 py-1.5 outline-none hover:bg-background focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <span className="min-w-0 flex-1">
                  {/* The task says more than a generic "Task for your team". */}
                  <span className="block truncate text-xs font-medium">{item.detail || item.title}</span>
                  <span className="block truncate text-[0.6875rem] text-muted-foreground">
                    {item.projectName}
                    {item.detail ? ` · ${item.title}` : ""}
                    {item.since ? ` · ${waitingFor(item.since)}` : ""}
                  </span>
                </span>
                <ArrowRight className="size-3.5 shrink-0 text-primary transition-transform group-hover:translate-x-0.5" aria-hidden />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * "Brain active 5 min ago", or in amber "quiet for 2 days" when a month has not moved for a day
 * although nothing waits for a person: a stall worth a look. A month that is finished, or that is
 * waiting for you, is quiet on purpose.
 */
function ActivityNote({ at, expected }: { at: string; expected: boolean }) {
  const quiet = !expected && Date.now() - Date.parse(at) > 24 * 60 * 60 * 1000;
  return (
    <span className={cn(quiet && "font-medium text-amber-700")} title={dateFormat.format(new Date(at))}>
      · {quiet ? `quiet for ${waitingFor(at)}` : `Brain active ${waitingFor(at)} ago`}
    </span>
  );
}

/** The four results every card shows, always in this order (the Brain's short labels). */
const RESULTS = ["companies", "contacts", "emails", "calls"] as const;

function ProjectCard({ row, archived, myUserId }: { row: Row; archived: boolean; myUserId: string }) {
  const { project, summary, needs } = row;
  const shared = project.owner_id !== myUserId; // assigned to this user (full access), not created by them
  const reached = reachedStep(project.status);
  const planReady = project.status === "plan_ready";
  const status = archived
    ? { label: "Archived", tone: "bg-muted text-muted-foreground" }
    : (summary?.status ?? { label: statusLabel(project.status), tone: statusTone(project.status) });
  // Inputs, checklist and plan: how many of the three are finished.
  const planningDone = planReady ? 3 : Math.min(2, reached - 1);
  const tasksPercent = summary && summary.total ? Math.round((summary.done / summary.total) * 100) : 0;
  const results = new Map((summary?.results ?? []).map((r) => [r.label, r.value]));
  const next = needs[0];

  return (
    <li>
      <Link
        href={`/projects/${project.id}`}
        className={cn(
          "group flex h-full flex-col gap-4 rounded-xl border bg-background p-5 transition-shadow outline-none hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring/50",
          needs.length > 0 && "border-amber-500/50"
        )}
      >
        <div className="flex items-start gap-3">
          <span
            aria-hidden
            className={cn(
              "flex size-10 shrink-0 items-center justify-center rounded-xl text-sm font-semibold",
              archived ? "bg-muted text-muted-foreground" : "bg-primary text-primary-foreground"
            )}
          >
            {archived ? <Archive className="size-5" /> : initials(project.customer_name)}
          </span>
          <div className="min-w-0 flex-1">
            <p className="truncate text-base leading-tight font-semibold" title={project.customer_name}>
              {project.customer_name}
            </p>
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>{summary ? `${summary.month} plan` : `Updated ${dateFormat.format(new Date(project.updated_at))}`}</span>
              {summary?.lastActivity && (
                <ActivityNote at={summary.lastActivity} expected={summary.done >= summary.total || needs.length > 0} />
              )}
              {project.run_mode === "staging" && (
                <span className="rounded bg-violet-500/10 px-1.5 py-px text-[0.6875rem] font-semibold text-violet-700">Staging</span>
              )}
              {shared && (
                <span className="rounded bg-sky-500/10 px-1.5 py-px text-[0.6875rem] font-semibold text-sky-700">Shared</span>
              )}
            </p>
          </div>
          <span className={cn("shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold", status.tone)}>{status.label}</span>
        </div>

        {/* One bar: the plan while it is being built, then the month's tasks. */}
        {summary || planReady ? (
          <div className="grid gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">Execution</span>
              <span className="text-xs text-muted-foreground tabular-nums">
                {summary ? `${number.format(summary.done)} of ${number.format(summary.total)} tasks · ${tasksPercent}%` : "Not started"}
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
        ) : (
          <div className="grid gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="font-medium">Planning</span>
              <span className="text-xs text-muted-foreground">Step {Math.min(3, reached)} of 3 · {PLANNING[Math.min(2, reached - 1)]}</span>
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
        )}

        {summary && (
          <dl className="grid grid-cols-4 divide-x rounded-lg bg-muted/50 py-2">
            {RESULTS.map((label) => (
              <div key={label} className="grid gap-0.5 px-3 text-center">
                <dd className="text-base leading-tight font-semibold tabular-nums">
                  {results.has(label) ? number.format(results.get(label)!) : "–"}
                </dd>
                <dt className="text-[0.6875rem] text-muted-foreground">{label}</dt>
              </div>
            ))}
          </dl>
        )}

        <div className="mt-auto flex items-center gap-3 border-t pt-3 text-xs">
          {next ? (
            <span className="min-w-0 flex-1 truncate" title={[next.title, next.detail].filter(Boolean).join(" · ")}>
              <span className="font-semibold text-amber-800">Next: </span>
              {/* The task says more than a generic "Task for your team". */}
              <span className="text-foreground">{next.detail || next.title}</span>
              {needs.length > 1 && <span className="text-muted-foreground"> · +{needs.length - 1} more</span>}
            </span>
          ) : (
            <span className="min-w-0 flex-1 truncate text-muted-foreground">
              {summary
                ? "Nothing waiting for you"
                : project.archived_at
                  ? `Archived ${dateFormat.format(new Date(project.archived_at))}`
                  : `Created ${dateFormat.format(new Date(project.created_at))}`}
            </span>
          )}
          <ArrowRight className="size-4 shrink-0 text-primary transition-transform group-hover:translate-x-0.5" aria-hidden />
        </div>
      </Link>
    </li>
  );
}
