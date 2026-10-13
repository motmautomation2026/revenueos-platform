import Link from "next/link";
import { BrainCircuit, Check, ChevronDown, FlaskConical, TriangleAlert, X, type LucideIcon } from "lucide-react";
import { StepPlaceholder } from "@/components/step-placeholder";
import type { ExecutionData, RunOption } from "@/lib/brain/read";
import type { Approval, InsightTile, Stage } from "@/lib/brain/tools";
import { describeMonth, type ExecutionView, type RunStatus, type TaskStatus, type ToolCallView } from "@/lib/brain/view";
import { cn } from "@/lib/utils";

// Shared pieces of the Execution pages. No hooks here, so they render on the server.

export const number = new Intl.NumberFormat("en-IN");
// A fixed time zone keeps server and browser rendering identical.
export const dateTime = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Kolkata",
});
export const timeOnly = new Intl.DateTimeFormat("en-IN", {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  timeZone: "Asia/Kolkata",
});

export const RUN_STATUS: Record<RunStatus, { label: string; tone: string }> = {
  planning: { label: "Being set up", tone: "bg-muted text-muted-foreground" },
  running: { label: "Running", tone: "bg-primary/10 text-primary" },
  waiting_human: { label: "Waiting for a person", tone: "bg-amber-500/20 text-amber-900" },
  done: { label: "Finished", tone: "bg-emerald-500/15 text-emerald-800" },
  stopped: { label: "Stopped", tone: "bg-muted text-muted-foreground" },
};

export const TASK_STATUS: Record<TaskStatus, { label: string; tone: string }> = {
  pending: { label: "Up next", tone: "bg-muted text-muted-foreground" },
  running: { label: "Running", tone: "bg-primary/10 text-primary" },
  waiting_human: { label: "Needs a person", tone: "bg-amber-500/20 text-amber-900" },
  done: { label: "Done", tone: "bg-emerald-500/15 text-emerald-800" },
  failed: { label: "Failed", tone: "bg-destructive/10 text-destructive" },
  stopped: { label: "Stopped", tone: "bg-muted text-muted-foreground" },
};

export function Pill({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <span className={cn("inline-flex w-fit items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold", className)}>
      {children}
    </span>
  );
}

/** What to show when there is no run to display. */
export function ExecutionEmpty({ data }: { data: Exclude<ExecutionData, { state: "ok" }> }) {
  const message = {
    not_configured:
      "This project has no Brain key yet, so its execution cannot be found. The database update that adds it has not been applied.",
    other_project:
      "The Brain is currently set up for a different project. Execution for this customer has not been started.",
    no_runs:
      "The plan is ready, but the Brain has not planned a month for this customer yet. Starting it from here needs the database update in supabase/migrations/0003_project_brain_key.sql.",
    error: data.state === "error" ? data.message : "",
  }[data.state];
  return (
    <StepPlaceholder
      icon={data.state === "error" ? TriangleAlert : BrainCircuit}
      title={data.state === "error" ? "Execution could not be loaded" : "Execution has not started"}
    >
      {message}
    </StepPlaceholder>
  );
}

/** Month, status, test-mode badge and the switcher between month plans. Same on every tab. */
export function ExecutionHeader({
  title,
  view,
  runs,
  basePath,
}: {
  title: string;
  view: ExecutionView;
  runs: RunOption[];
  /** The current tab's path, so switching month keeps you on the same tab. */
  basePath: string;
}) {
  const status = RUN_STATUS[view.status];
  const current = describeMonth(view.month);
  return (
    <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
      <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
      <span className="text-sm text-muted-foreground">{current.month}</span>
      <Pill className={status.tone}>{status.label}</Pill>
      {view.testMode && (
        <span
          className="inline-flex items-center gap-1 rounded-full border border-amber-500/50 px-2 py-0.5 text-xs font-medium text-amber-900"
          title="Staging: small volumes; emails and calls go only to your test recipients."
        >
          <FlaskConical className="size-3" aria-hidden />
          Staging
        </span>
      )}

      {/* Earlier plans are one click away, but out of the way: most of the time only the latest matters. */}
      {runs.length > 1 && (
        <div className="group relative ml-auto">
          <button
            type="button"
            aria-haspopup="menu"
            className="flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {current.plan != null ? `Plan ${current.plan} of ${runs.length}` : view.month}
            <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
          </button>
          <div
            role="menu"
            aria-label="Plans written for this customer"
            className="invisible absolute top-full right-0 z-30 w-60 pt-1.5 opacity-0 transition-opacity group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
          >
            <div className="rounded-xl border bg-popover p-1 text-sm text-popover-foreground shadow-lg">
              {runs.map((run) => {
                const active = run.runKey === view.runKey;
                const label = describeMonth(run.month);
                return (
                  <Link
                    key={run.runKey}
                    role="menuitem"
                    href={`${basePath}?run=${encodeURIComponent(run.runKey)}`}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "flex items-center justify-between gap-3 rounded-md px-2.5 py-2 outline-none hover:bg-muted focus-visible:bg-muted",
                      active && "bg-primary/10 font-medium text-primary"
                    )}
                  >
                    <span>
                      {label.plan != null ? `Plan ${label.plan}` : run.month}
                      {label.month !== current.month && <span className="text-muted-foreground"> · {label.month}</span>}
                    </span>
                    <span className="text-xs text-muted-foreground">{RUN_STATUS[run.status].label}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </header>
  );
}

export function StatTile({
  label,
  value,
  of,
  percent,
  note,
  attention,
}: {
  label: string;
  value: string;
  of?: string;
  percent?: number | null;
  note?: string;
  attention?: boolean;
}) {
  return (
    <div className={cn("grid content-start gap-2 rounded-xl border p-4", attention && "border-amber-500/60 bg-amber-500/5")}>
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="text-3xl leading-none font-semibold tabular-nums">
        {value}
        {of && <span className="text-base font-normal text-muted-foreground"> / {of}</span>}
      </p>
      {percent != null && (
        <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
          <div
            className={cn("h-full rounded-full", percent >= 100 ? "bg-emerald-600" : "bg-primary")}
            style={{ width: `${Math.min(100, percent)}%` }}
          />
        </div>
      )}
      {note && <p className="text-xs leading-relaxed text-muted-foreground">{note}</p>}
    </div>
  );
}

/** The note that explains a capped run, so a full bar is not read as hitting the real target. */
export function capNote(target: number | null, planTarget: number | null) {
  return planTarget != null && target != null && planTarget !== target
    ? `Plan target ${number.format(planTarget)}; this run was capped for testing`
    : undefined;
}

export function percentOf(achieved: number | null, target: number | null) {
  return achieved != null && target ? Math.round((achieved / target) * 100) : null;
}

export function SectionTitle({ children, aside }: { children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h3 className="text-base font-semibold">{children}</h3>
      {aside && <div className="text-sm text-muted-foreground">{aside}</div>}
    </div>
  );
}

/** The steps the Brain took for one task, oldest first, as recorded in its ledger. */
export function StepsTimeline({ steps }: { steps: ToolCallView[] }) {
  if (!steps.length) return <p className="text-sm text-muted-foreground">No steps recorded yet.</p>;

  // A step the Brain repeats (polling for results, say) is shown once with a count.
  const merged: (ToolCallView & { times: number })[] = [];
  for (const step of steps) {
    const last = merged.at(-1);
    if (last && last.tool === step.tool && last.step === step.step && last.status === step.status && !step.error) {
      last.times += 1;
    } else {
      merged.push({ ...step, times: 1 });
    }
  }

  return (
    <ol className="grid">
      {merged.map((step, i) => {
        const problem = step.status === "failed" || step.status === "unknown";
        const running = step.status === "running";
        return (
          <li key={step.id} className="relative flex gap-3 pb-4 last:pb-0">
            {i < merged.length - 1 && <span aria-hidden className="absolute top-3 left-[5px] h-full w-px bg-border" />}
            <span
              aria-hidden
              className={cn(
                "relative mt-1.5 size-[11px] shrink-0 rounded-full",
                problem ? "bg-destructive" : running ? "animate-pulse bg-primary" : "bg-emerald-600"
              )}
            />
            <div className="min-w-0 flex-1 text-sm">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                <span className="font-medium capitalize">{step.step.replace(/_/g, " ")}</span>
                {step.times > 1 && <span className="text-xs text-muted-foreground">× {step.times}</span>}
                <span className="text-xs text-muted-foreground">{step.tool.replace(/_/g, " ")}</span>
                {(problem || running) && (
                  <span className={cn("text-xs font-medium", problem ? "text-destructive" : "text-primary")}>
                    {running ? "In progress" : step.status}
                  </span>
                )}
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                  {dateTime.format(new Date(step.startedAt))}
                </span>
              </div>
              {step.error && <p className="text-xs text-destructive">{step.error}</p>}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

export function ApprovalLine({ approval, what }: { approval: Approval | null; what: string }) {
  if (!approval) return <p className="text-sm text-muted-foreground">{what}: not asked yet.</p>;
  if (approval.open) {
    return (
      <p className="text-sm">
        <Pill className="mr-2 bg-amber-500/20 text-amber-900">Waiting for approval</Pill>
        {what} has not been approved yet.
      </p>
    );
  }
  const yes = approval.answer === "yes";
  return (
    <p className="text-sm">
      <Pill className={cn("mr-2", yes ? "bg-emerald-500/15 text-emerald-800" : "bg-muted text-muted-foreground")}>
        {yes ? "Approved" : `Answered: ${approval.answer}`}
      </Pill>
      {approval.at && <span className="text-muted-foreground">{dateTime.format(new Date(approval.at))}</span>}
      {approval.reason && <span className="text-muted-foreground"> · “{approval.reason}”</span>}
    </p>
  );
}

export function EmptyTool({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-xl border border-dashed px-6 py-12 text-center text-sm text-muted-foreground">{children}</p>
  );
}

const VERDICT_TONE = {
  good: "bg-emerald-500/15 text-emerald-800",
  attention: "bg-amber-500/20 text-amber-900",
  neutral: "bg-muted text-muted-foreground",
} as const;

/** A result tile. What it says is decided in lib/brain/tools.ts; this only lays it out. */
export function InsightTileCard({ tile, icon: Icon }: { tile: InsightTile; icon: LucideIcon }) {
  return (
    // min-w-0 and flex-col keep long content inside the card instead of pushing past its edge.
    <div className="flex min-w-0 flex-col gap-3 rounded-xl border p-4">
      <div className="flex min-w-0 items-center gap-2">
        <span className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon className="size-4" aria-hidden />
        </span>
        <p className="min-w-0 flex-1 text-sm leading-tight font-medium">{tile.label}</p>
      </div>

      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="text-3xl leading-none font-semibold tabular-nums">
          {tile.value != null ? number.format(tile.value) : "—"}
        </span>
        {tile.of != null && (
          <span className="text-sm text-muted-foreground tabular-nums">
            of {number.format(tile.of)} {tile.ofLabel}
          </span>
        )}
        {tile.verdict && <Pill className={cn("ml-auto", VERDICT_TONE[tile.verdict.tone])}>{tile.verdict.text}</Pill>}
      </div>

      {tile.percent != null && (
        <div className="grid gap-1">
          <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
            <div
              className={cn("h-full rounded-full", tile.percent >= 100 ? "bg-emerald-600" : "bg-primary")}
              style={{ width: `${Math.max(tile.percent, tile.value ? 2 : 0)}%` }}
            />
          </div>
          <p className="text-xs text-muted-foreground tabular-nums">{tile.percent}%</p>
        </div>
      )}

      {tile.lines.length > 0 && (
        <ul className="grid gap-1 border-t pt-3 text-sm leading-snug text-muted-foreground">
          {tile.lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** A campaign's milestones in order, so it is obvious how far it got and where it is waiting. */
export function StageTrack({ stages }: { stages: Stage[] }) {
  return (
    <ol className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      {stages.map((stage, index) => (
        <li
          key={stage.label}
          className={cn(
            "flex min-w-0 gap-3 rounded-xl border p-3",
            stage.state === "waiting" && "border-amber-500/60 bg-amber-500/5",
            stage.state === "refused" && "border-destructive/40 bg-destructive/5",
            stage.state === "todo" && "border-dashed"
          )}
        >
          <span
            className={cn(
              "flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold",
              stage.state === "done" && "bg-emerald-600 text-white",
              stage.state === "waiting" && "bg-amber-500 text-white",
              stage.state === "refused" && "bg-destructive text-white",
              stage.state === "todo" && "bg-muted text-muted-foreground"
            )}
          >
            {stage.state === "done" ? (
              <Check className="size-3.5" aria-hidden />
            ) : stage.state === "refused" ? (
              <X className="size-3.5" aria-hidden />
            ) : (
              index + 1
            )}
          </span>
          <div className="min-w-0">
            <p className={cn("text-sm leading-tight font-medium", stage.state === "todo" && "text-muted-foreground")}>
              {stage.label}
            </p>
            <p className="text-xs text-muted-foreground">
              {stage.at ? dateTime.format(new Date(stage.at)) : stage.state === "todo" ? "Not yet" : ""}
            </p>
            {stage.note && <p className="mt-1 text-xs leading-snug break-words text-muted-foreground">{stage.note}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}
