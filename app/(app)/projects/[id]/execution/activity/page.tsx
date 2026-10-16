import type { Metadata } from "next";
import Link from "next/link";
import {
  BrainCircuit,
  CircleHelp,
  Database,
  Mail,
  Phone,
  Search,
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
import {
  buildActivity,
  filterActivity,
  type ActivityEvent,
  type ActivityFilter,
  type ActivitySource,
} from "@/lib/brain/tools";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "Activity log" };

const dayLabel = new Intl.DateTimeFormat("en-IN", { dateStyle: "full", timeZone: "Asia/Kolkata" });
const dayKey = new Intl.DateTimeFormat("en-CA", { dateStyle: "short", timeZone: "Asia/Kolkata" });

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

export default async function ActivityPage(props: ExecutionPageProps) {
  const { data, base, query } = await loadExecutionPage(props);
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;

  const { view, runs, rows } = data;
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
  const waiting = all.filter((e) => e.kind === "question").length;
  const problems = all.filter((e) => e.tone === "problem").length;

  const href = (show: ActivityFilter) => {
    const params = new URLSearchParams();
    if (query.run) params.set("run", query.run);
    if (show !== "all") params.set("show", show);
    const text = params.toString();
    return `${base}/activity${text ? `?${text}` : ""}`;
  };

  return (
    <div className="grid gap-6">
      <ExecutionHeader title="Activity log" view={view} runs={runs} basePath={`${base}/activity`} />

      {all.length === 0 ? (
        <EmptyTool>Nothing has happened in this month plan yet.</EmptyTool>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile label="Steps the Brain took" value={number.format(steps)} />
            <StatTile
              label="Decisions"
              value={number.format(answered + waiting)}
              note={waiting ? `${number.format(waiting)} still waiting for a person` : "All answered"}
              attention={waiting > 0}
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
