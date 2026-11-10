import type { Metadata } from "next";
import Link from "next/link";
import {
  BrainCircuit,
  CircleHelp,
  Database,
  Loader,
  Mail,
  Phone,
  Search,
  TriangleAlert,
  UserCheck,
  Zap,
  type LucideIcon,
} from "lucide-react";
import {
  EmptyTool,
  ExecutionEmpty,
  ExecutionHeader,
  Pill,
  StatTile,
  number,
  timeOnly,
} from "@/components/brain/parts";
import { ToolHealthPanel } from "@/components/brain/tool-health-panel";
import {
  buildActivity,
  filterActivity,
  type ActivityEvent,
  type ActivityFilter,
  type ActivitySource,
} from "@/lib/brain/tools";
import { loadWorkerStatus } from "@/lib/brain/worker-status";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "../load";
import { checkToolHealth } from "../diagnostics/actions";

export const metadata: Metadata = { title: "Activity log" };

const dayLabel = new Intl.DateTimeFormat("en-IN", { dateStyle: "full", timeZone: "Asia/Kolkata" });
const dayKey = new Intl.DateTimeFormat("en-CA", { dateStyle: "short", timeZone: "Asia/Kolkata" });
const when = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" });
const fmt = (iso: string | null) => (iso ? when.format(new Date(iso)) : "—");

const SOURCE_ICON: Record<ActivitySource, LucideIcon> = {
  QuickICP: Search,
  Quickhowl: Phone,
  "Email tool": Mail,
  "Saved data": Database,
  Person: UserCheck,
  Brain: BrainCircuit,
  Automatic: Zap,
};

const FILTERS: { key: ActivityFilter; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "tools", label: "Tool steps" },
  { key: "decisions", label: "Decisions" },
  { key: "problems", label: "Needs attention" },
];

// Green = healthy, amber = waiting/running, red = failed, grey = not run yet.
type Tone = "ok" | "attention" | "problem" | "idle";
const DOT: Record<Tone, string> = {
  ok: "bg-emerald-500",
  attention: "bg-amber-500",
  problem: "bg-destructive",
  idle: "bg-muted-foreground/40",
};
const CARD: Record<Tone, string> = {
  ok: "border-emerald-500/30",
  attention: "border-amber-500/40 bg-amber-500/5",
  problem: "border-destructive/30 bg-destructive/5",
  idle: "",
};

const TOOLS: { key: string; label: string; icon: LucideIcon; match: (tool: string) => boolean }[] = [
  { key: "data", label: "Data (QuickICP)", icon: Search, match: (t) => t.startsWith("quickicp") },
  { key: "email", label: "Email (Outly)", icon: Mail, match: (t) => t === "email_tool" },
  { key: "calls", label: "Calls (Quickhowl)", icon: Phone, match: (t) => t === "quickhowl" },
];

const PROBLEM_STATUS = new Set(["failed", "error"]);
const ATTENTION_STATUS = new Set(["blocked", "pending", "running"]);

export default async function ActivityPage(props: ExecutionPageProps) {
  const [{ data, base, query }, worker] = await Promise.all([loadExecutionPage(props), loadWorkerStatus()]);
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;
  const { view, runs, rows } = data;

  // ---------------------------------------------------------------------------
  // Live head — "Right now" (folded in from the old Diagnostics tab)
  // ---------------------------------------------------------------------------
  const taskIdOf = new Map(rows.tasks.map((t) => [t.id, t.task_id]));
  const taskById = new Map(rows.tasks.map((t) => [t.id, t]));
  const cardsIn = (key: string) => view.columns.find((c) => c.key === key)?.cards ?? [];

  // What the Brain is working on this moment — from the run's task state, so it shows even before
  // the first tool-call of a task is recorded (the old log only showed a step once it existed).
  const processing = cardsIn("running");

  // The latest tool call in each external tool's bucket, and how healthy it looks.
  const tools = TOOLS.map((tool) => {
    const calls = rows.toolCalls.filter((c) => tool.match(c.tool));
    const last = calls.length ? calls.reduce((a, b) => (b.id > a.id ? b : a)) : null;
    const status = last?.status ?? null;
    const task = last ? (taskById.get(last.task_row_id) ?? null) : null;
    const target = task?.target_number ?? null;
    const achieved = task?.achieved_number ?? null;
    // "done" but nothing found is a silent blocker: show it as a warning, not a green tick.
    const emptyDone = !!last && status === "done" && (target ?? 0) > 0 && (achieved ?? 0) === 0;
    const tone: Tone = !last
      ? "idle"
      : last.error || (status != null && PROBLEM_STATUS.has(status))
        ? "problem"
        : emptyDone
          ? "attention"
          : status === "done"
            ? "ok"
            : status != null && ATTENTION_STATUS.has(status)
              ? "attention"
              : "attention";
    return { ...tool, last, status, tone, target, achieved, emptyDone };
  });

  const failedTasks = rows.tasks.filter((t) => t.status === "failed" || t.last_error);
  const waiting = view.openDecisions;

  const workerTone: Tone =
    worker.state === "online" ? "ok" : worker.state === "several" ? "attention" : worker.state === "offline" ? "problem" : "idle";
  const workerText =
    worker.state === "online"
      ? `Online (seen ${fmt(worker.lastSeen)})`
      : worker.state === "several"
        ? `${worker.workers.length} workers running — steps could run twice`
        : worker.state === "offline"
          ? worker.lastSeen
            ? `Offline (last seen ${fmt(worker.lastSeen)})`
            : "Offline"
          : "Unknown";

  const runTone: Tone =
    view.status === "stopped" ? "problem" : view.status === "waiting_human" || view.status === "planning" ? "attention" : "ok";
  // Open decisions are answered on the Dashboard (#decisions).
  const decisionsHref = `${base}${query.run ? `?run=${encodeURIComponent(query.run)}` : ""}#decisions`;

  // ---------------------------------------------------------------------------
  // History — the step-by-step timeline (unchanged)
  // ---------------------------------------------------------------------------
  const all = buildActivity(rows);
  const filter = FILTERS.some((f) => f.key === query.show) ? (query.show as ActivityFilter) : "all";
  const events = filterActivity(all, filter);

  const days: { key: string; label: string; events: ActivityEvent[] }[] = [];
  for (const event of events) {
    const at = new Date(event.at);
    const key = dayKey.format(at);
    const day = days.at(-1)?.key === key ? days.at(-1)! : days[days.push({ key, label: dayLabel.format(at), events: [] }) - 1];
    day.events.push(event);
  }

  const steps = all.filter((e) => e.kind === "tool").reduce((sum, e) => sum + e.times, 0);
  const answered = all.filter((e) => e.kind === "answer").length;
  const waitingAnswers = all.filter((e) => e.kind === "question").length;
  const problems = all.filter((e) => e.tone === "problem").length;

  const href = (show: ActivityFilter) => {
    const params = new URLSearchParams();
    if (query.run) params.set("run", query.run);
    if (show !== "all") params.set("show", show);
    const text = params.toString();
    return `${base}/activity${text ? `?${text}` : ""}`;
  };

  return (
    <div className="grid gap-8">
      <ExecutionHeader title="Activity log" view={view} runs={runs} basePath={`${base}/activity`} />

      {/* ===================== Right now (live) ===================== */}
      <div className="grid gap-4">
        <div>
          <h2 className="text-sm font-semibold">Right now</h2>
          <p className="text-sm text-muted-foreground">
            Is the Brain running, what it is working on this moment, and anything stuck or waiting.
          </p>
        </div>

        {/* System */}
        <section aria-label="System" className="grid gap-3 sm:grid-cols-2">
          <HealthCard icon={BrainCircuit} tone={workerTone} label="AI Brain worker" value={workerText} note={null} />
          <HealthCard
            icon={BrainCircuit}
            tone={runTone}
            label="This run"
            value={view.testMode ? `${view.status} · TEST MODE` : view.status}
            note={`Month ${view.month} · ${view.counts.done}/${view.counts.total} tasks done`}
          />
        </section>

        {/* Processing now */}
        <section aria-label="Processing now" className="grid gap-2">
          <h3 className="text-sm font-semibold">Processing now</h3>
          {processing.length === 0 ? (
            <EmptyTool>Nothing is processing right now.</EmptyTool>
          ) : (
            <ul className="grid gap-2">
              {processing.map((card) => {
                const startedAt = taskById.get(card.rowId)?.started_at ?? null;
                const lastStep = card.calls.at(-1)?.step ?? null;
                return (
                  <li
                    key={`run-${card.rowId}`}
                    className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-sm", CARD.attention)}
                  >
                    <Loader className="size-4 shrink-0 animate-spin text-amber-700" aria-hidden />
                    <span className="font-medium">
                      {card.taskId} — {card.title}
                    </span>
                    <Pill className="bg-amber-500/15 text-amber-800">running</Pill>
                    {startedAt && (
                      <span className="ml-auto text-xs text-muted-foreground tabular-nums">started {fmt(startedAt)}</span>
                    )}
                    <p className="w-full text-muted-foreground">
                      {lastStep ? `Current step: ${lastStep.replace(/_/g, " ")}` : "Getting started…"}
                    </p>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        {/* Stuck / waiting / failed */}
        <section aria-label="Stuck, waiting or failed" className="grid gap-2">
          <h3 className="text-sm font-semibold">Stuck, waiting or failed</h3>
          {waiting.length === 0 && failedTasks.length === 0 ? (
            <EmptyTool>Nothing is waiting for a person and nothing has failed.</EmptyTool>
          ) : (
            <ul className="grid gap-2">
              {waiting.map((d) => (
                <li
                  key={`d-${d.id}`}
                  className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-sm", CARD.attention)}
                >
                  <span className={cn("size-2.5 shrink-0 rounded-full", DOT.attention)} aria-hidden />
                  <span className="font-medium">
                    {d.taskId ? `${d.taskId} — ` : ""}
                    {d.title}
                  </span>
                  <Pill className="bg-amber-500/15 text-amber-800">{d.kind.replace(/_/g, " ")}</Pill>
                  <span className="ml-auto text-xs text-muted-foreground tabular-nums">waiting since {fmt(d.askedAt)}</span>
                  <Link href={decisionsHref} className="w-full text-xs font-medium text-primary hover:underline">
                    Answer on the Dashboard →
                  </Link>
                </li>
              ))}
              {failedTasks.map((t) => (
                <li
                  key={`t-${t.id}`}
                  className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-sm", CARD.problem)}
                >
                  <TriangleAlert className="size-4 shrink-0 text-destructive" aria-hidden />
                  <span className="font-medium">
                    {t.task_id} — {t.agent} ({t.output_type})
                  </span>
                  <Pill className="bg-destructive/15 text-destructive">{t.status}</Pill>
                  {t.last_error ? <p className="w-full text-muted-foreground">{t.last_error}</p> : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Tool health */}
        <section aria-label="Tool health" className="grid gap-2">
          <h3 className="text-sm font-semibold">Tool health</h3>
          <ul className="grid gap-2">
            {tools.map((tool) => {
              const Icon = tool.icon;
              const taskId = tool.last ? taskIdOf.get(tool.last.task_row_id) : null;
              return (
                <li
                  key={tool.key}
                  className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-sm", CARD[tool.tone])}
                >
                  <span className={cn("size-2.5 shrink-0 rounded-full", DOT[tool.tone])} aria-hidden />
                  <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="font-medium">{tool.label}</span>
                  {tool.target != null ? (
                    <Pill className={cn(tool.emptyDone ? "bg-amber-500/15 text-amber-800" : "bg-muted text-muted-foreground")}>
                      {tool.achieved ?? 0}/{tool.target}
                    </Pill>
                  ) : null}
                  <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                    {tool.last ? fmt(tool.last.finished_at ?? tool.last.started_at) : ""}
                  </span>
                  <p className="w-full text-muted-foreground">
                    {tool.last ? (
                      <>
                        last: {taskId ? `${taskId} ` : ""}
                        {tool.last.step} · {tool.last.status}
                        {tool.emptyDone ? <span className="text-amber-800"> — finished but found nothing</span> : null}
                        {tool.last.error ? <span className="text-destructive"> — {tool.last.error}</span> : null}
                      </>
                    ) : (
                      "Has not run yet."
                    )}
                  </p>
                </li>
              );
            })}
          </ul>
        </section>

        {/* Connectivity — on-demand reachability of the tool hosts */}
        <section aria-label="Connectivity">
          <ToolHealthPanel check={checkToolHealth} />
        </section>
      </div>

      {/* ===================== History (timeline) ===================== */}
      <div className="grid gap-4">
        <div>
          <h2 className="text-sm font-semibold">History</h2>
          <p className="text-sm text-muted-foreground">Every step the Brain took and every decision, newest first.</p>
        </div>

        {all.length === 0 ? (
          <EmptyTool>Nothing has happened in this month plan yet.</EmptyTool>
        ) : (
          <>
            <div className="grid gap-3 sm:grid-cols-3">
              <StatTile label="Steps the Brain took" value={number.format(steps)} />
              <StatTile
                label="Decisions"
                value={number.format(answered + waitingAnswers)}
                note={waitingAnswers ? `${number.format(waitingAnswers)} still waiting for a person` : "All answered"}
                attention={waitingAnswers > 0}
              />
              <StatTile
                label="Steps that failed"
                value={number.format(problems)}
                note={problems ? undefined : "Nothing failed"}
                attention={problems > 0}
              />
            </div>

            <nav aria-label="Show" className="flex flex-wrap gap-1.5">
              {FILTERS.map((option) => {
                const active = option.key === filter;
                const count = filterActivity(all, option.key).length;
                return (
                  <Link
                    key={option.key}
                    href={href(option.key)}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                      active ? "border-primary bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted hover:text-foreground"
                    )}
                  >
                    {option.label}
                    <span className={cn("text-xs tabular-nums", active ? "opacity-80" : "")}>{number.format(count)}</span>
                  </Link>
                );
              })}
            </nav>

            {events.length === 0 && <EmptyTool>Nothing matches this filter.</EmptyTool>}

            {days.map((day) => (
              <section key={day.key} className="grid gap-2">
                <h3 className="sticky top-14 z-10 -mx-1 flex items-baseline justify-between gap-3 bg-background/95 px-1 py-1.5 backdrop-blur">
                  <span className="text-sm font-semibold">{day.label}</span>
                  <span className="text-xs text-muted-foreground">
                    {number.format(day.events.length)} entr{day.events.length === 1 ? "y" : "ies"}
                  </span>
                </h3>
                <ol className="grid">
                  {day.events.map((event, index) => (
                    <EventRow key={event.key} event={event} last={index === day.events.length - 1} />
                  ))}
                </ol>
              </section>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

function HealthCard({
  icon: Icon,
  tone,
  label,
  value,
  note,
}: {
  icon: LucideIcon;
  tone: Tone;
  label: string;
  value: string;
  note: string | null;
}) {
  return (
    <div className={cn("flex items-start gap-3 rounded-xl border p-4", CARD[tone])}>
      <span className={cn("mt-1 size-2.5 shrink-0 rounded-full", DOT[tone])} aria-hidden />
      <Icon className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0">
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="font-medium">{value}</p>
        {note ? <p className="mt-0.5 text-xs text-muted-foreground">{note}</p> : null}
      </div>
    </div>
  );
}

function EventRow({ event, last }: { event: ActivityEvent; last: boolean }) {
  const Icon = event.kind === "question" ? CircleHelp : SOURCE_ICON[event.source];
  return (
    <li className="relative flex gap-3 pb-3 last:pb-0">
      {!last && <span aria-hidden className="absolute top-9 bottom-0 left-[15px] w-px bg-border" />}
      <span
        className={cn(
          "relative flex size-8 shrink-0 items-center justify-center rounded-full",
          event.tone === "problem"
            ? "bg-destructive/10 text-destructive"
            : event.tone === "attention"
              ? "bg-amber-500/20 text-amber-800"
              : event.kind === "answer"
                ? "bg-emerald-500/15 text-emerald-800"
                : "bg-primary/10 text-primary"
        )}
      >
        <Icon className="size-4" aria-hidden />
      </span>

      <div
        className={cn(
          "min-w-0 flex-1 rounded-xl border px-3 py-2 text-sm",
          event.tone === "problem" && "border-destructive/30 bg-destructive/5",
          event.tone === "attention" && "border-amber-500/40 bg-amber-500/5"
        )}
      >
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="font-medium">{event.title}</p>
          {event.times > 1 && <span className="text-xs text-muted-foreground">× {event.times}</span>}
          {event.answer && <Pill className="bg-emerald-500/15 text-emerald-800">{event.answer}</Pill>}
          <span className="ml-auto text-xs text-muted-foreground tabular-nums">{timeOnly.format(new Date(event.at))}</span>
        </div>
        <p className="mt-0.5 text-xs text-muted-foreground">
          {event.kind === "answer" ? `Answered by: ${event.source === "Person" ? "a person" : event.source === "Brain" ? "the Brain" : "automatic rule"}` : event.source}
          {event.taskId && <> · task {event.taskId}</>}
        </p>
        {event.detail && <p className="mt-1 leading-relaxed break-words text-muted-foreground">{event.detail}</p>}
      </div>
    </li>
  );
}
