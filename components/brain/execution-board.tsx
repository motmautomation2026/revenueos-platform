"use client";

import { useState } from "react";
import { Bot, CalendarDays, Columns3, Link2, UserRound } from "lucide-react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { ColumnKey, ExecutionView, TaskCard, TaskStatus } from "@/lib/brain/view";

const number = new Intl.NumberFormat("en-IN");
// A fixed time zone keeps server and browser rendering identical.
const dateTime = new Intl.DateTimeFormat("en-IN", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Asia/Kolkata",
});

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: "Up next",
  running: "Running",
  waiting_human: "Needs a person",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

const STATUS_TONE: Record<TaskStatus, string> = {
  pending: "bg-muted text-muted-foreground",
  running: "bg-primary/10 text-primary",
  waiting_human: "bg-amber-500/20 text-amber-900",
  done: "bg-emerald-500/15 text-emerald-800",
  failed: "bg-destructive/10 text-destructive",
  stopped: "bg-muted text-muted-foreground",
};

const COLUMN_TONE: Record<ColumnKey, string> = {
  next: "bg-muted/60",
  running: "bg-primary/5 ring-1 ring-primary/30",
  person: "bg-amber-500/10 ring-1 ring-amber-500/40",
  done: "bg-muted/60",
  stopped: "bg-muted/60",
};

function Progress({ card, className }: { card: TaskCard; className?: string }) {
  if (card.achieved == null) return null;
  const testCapped = card.target != null && card.planTarget != null && card.target !== card.planTarget;
  return (
    <div className={cn("grid gap-1", className)}>
      {card.percent != null && (
        <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
          <div
            className={cn("h-full rounded-full", card.percent >= 100 ? "bg-emerald-600" : "bg-primary")}
            style={{ width: `${card.percent}%` }}
          />
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        <span className="font-medium text-foreground">{number.format(card.achieved)}</span>
        {card.target != null && <> of {number.format(card.target)}</>}
        {testCapped && <> · plan target {number.format(card.planTarget!)}</>}
      </p>
    </div>
  );
}

function ActorTag({ card }: { card: TaskCard }) {
  const Icon = card.actor === "tool" ? Bot : UserRound;
  return (
    <span
      className={cn(
        "inline-flex max-w-full items-center gap-1 rounded-md px-1.5 py-0.5 text-xs font-medium",
        card.actor === "tool" ? "bg-primary/10 text-primary" : "bg-muted text-foreground"
      )}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden />
      <span className="truncate">{card.agent}</span>
    </span>
  );
}

function Card({ card, showStatus, onOpen }: { card: TaskCard; showStatus: boolean; onOpen: () => void }) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="grid w-full gap-2 rounded-lg bg-background p-3 text-left shadow-sm ring-1 ring-foreground/10 transition-shadow outline-none hover:shadow-md focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <div className="flex items-center justify-between gap-2">
        <ActorTag card={card} />
        <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
          {card.taskId}
          {!showStatus && card.week != null && <> · Week {card.week}</>}
        </span>
      </div>
      <p className="line-clamp-3 text-sm leading-snug font-medium">{card.title}</p>
      <Progress card={card} />
      {card.column === "next" && card.blockedBy.length > 0 && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Link2 className="size-3" aria-hidden />
          After {card.blockedBy.join(", ")}
        </p>
      )}
      {showStatus && (
        <span className={cn("w-fit rounded-full px-2 py-0.5 text-xs font-medium", STATUS_TONE[card.status])}>
          {STATUS_LABEL[card.status]}
        </span>
      )}
    </button>
  );
}

export function ExecutionBoard({
  columns,
  weeks,
}: {
  columns: ExecutionView["columns"];
  weeks: ExecutionView["weeks"];
}) {
  // With nothing in motion every card sits in "Done" or "Stopped", so the month laid out by week reads better.
  const active = columns.some((c) => ["next", "running", "person"].includes(c.key) && c.cards.length > 0);
  const [mode, setMode] = useState<"status" | "week">(active ? "status" : "week");
  const [open, setOpen] = useState<TaskCard | null>(null);

  // An empty "Stopped" column is noise; the other four always show so the flow stays readable.
  const shownColumns = columns.filter((column) => column.key !== "stopped" || column.cards.length > 0);
  const lanes =
    mode === "status"
      ? shownColumns.map((column) => ({
          key: column.key as string,
          title: column.title,
          hint: column.hint,
          tone: COLUMN_TONE[column.key],
          cards: column.cards,
        }))
      : weeks.map((week) => ({
          key: `week-${week.week}`,
          title: week.week == null ? "No week set" : `Week ${week.week}`,
          hint: "",
          tone: "bg-muted/60",
          cards: week.cards,
        }));

  return (
    <section className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold">Tasks</h3>
        <div className="flex rounded-lg border p-0.5 text-sm" role="group" aria-label="Group tasks by">
          {(
            [
              { key: "status", label: "By status", icon: Columns3 },
              { key: "week", label: "By week", icon: CalendarDays },
            ] as const
          ).map((option) => (
            <button
              key={option.key}
              type="button"
              aria-pressed={mode === option.key}
              onClick={() => setMode(option.key)}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-2.5 py-1 font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                mode === option.key ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              <option.icon className="size-3.5" aria-hidden />
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="overflow-x-auto pb-1">
        <div className="grid auto-cols-[minmax(15rem,1fr)] grid-flow-col items-start gap-3">
          {lanes.map((lane) => (
            <div key={lane.key} className={cn("grid gap-2 rounded-xl p-2", lane.tone)}>
              <div className="flex items-baseline justify-between gap-2 px-1 pt-1">
                <h4 className="text-sm font-semibold" title={lane.hint || undefined}>
                  {lane.title}
                </h4>
                <span className="text-xs font-medium text-muted-foreground tabular-nums">{lane.cards.length}</span>
              </div>
              {lane.cards.length === 0 ? (
                <p className="rounded-lg border border-dashed px-3 py-6 text-center text-xs text-muted-foreground">
                  Nothing here
                </p>
              ) : (
                lane.cards.map((card) => (
                  <Card key={card.rowId} card={card} showStatus={mode === "week"} onOpen={() => setOpen(card)} />
                ))
              )}
            </div>
          ))}
        </div>
      </div>

      <Dialog open={open !== null} onOpenChange={(next) => !next && setOpen(null)}>
        {open && (
          <DialogContent className="max-h-[88svh] overflow-y-auto sm:max-w-2xl">
            <DialogHeader>
              <div className="flex flex-wrap items-center gap-2">
                <ActorTag card={open} />
                <span className={cn("rounded-full px-2 py-0.5 text-xs font-medium", STATUS_TONE[open.status])}>
                  {STATUS_LABEL[open.status]}
                </span>
                <span className="text-xs text-muted-foreground">
                  {open.taskId}
                  {open.week != null && <> · Week {open.week}</>}
                  {open.tier && <> · {open.tier}</>}
                </span>
              </div>
              <DialogTitle className="text-lg leading-snug">{open.title}</DialogTitle>
              {open.targetText && <DialogDescription>Target: {open.targetText}</DialogDescription>}
            </DialogHeader>

            <div className="grid gap-5 text-sm">
              {open.lastError && (
                <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-destructive">
                  {open.lastError}
                </p>
              )}

              {(open.achieved != null || open.note) && (
                <div className="grid gap-2 rounded-lg bg-muted/50 p-3">
                  <Progress card={open} />
                  {open.note && <p className="text-muted-foreground">{open.note}</p>}
                </div>
              )}

              {open.stats.length > 0 && (
                <Section title="Result">
                  <dl className="grid grid-cols-2 gap-2 sm:grid-cols-3">
                    {open.stats.map((stat) => (
                      <div key={stat.label} className="rounded-lg border p-2">
                        <dt className="text-xs text-muted-foreground">{stat.label}</dt>
                        <dd className="font-semibold break-words">{stat.value}</dd>
                      </div>
                    ))}
                  </dl>
                </Section>
              )}

              {open.instructions.length > 0 && (
                <Section title="What the task involves">
                  <ol className="grid list-decimal gap-1.5 pl-5 leading-relaxed">
                    {open.instructions.map((line, i) => (
                      <li key={i}>{line}</li>
                    ))}
                  </ol>
                </Section>
              )}

              {open.doneWhen && <Section title="Done when">{open.doneWhen}</Section>}
              {open.escalateIf && <Section title="Escalate if">{open.escalateIf}</Section>}
              {open.revenueLink && <Section title="Why it matters">{open.revenueLink}</Section>}
              {open.geography && open.geography !== "All" && <Section title="Geography">{open.geography}</Section>}

              {open.calls.length > 0 && (
                <Section title="Activity log">
                  <ul className="divide-y rounded-lg border">
                    {open.calls.map((call) => (
                      <li key={call.id} className="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2">
                        <span className="font-medium">
                          {call.tool} · {call.step}
                        </span>
                        <span className={cn("text-xs", call.status === "done" ? "text-muted-foreground" : "font-medium text-destructive")}>
                          {call.status}
                        </span>
                        <span className="ml-auto text-xs text-muted-foreground">
                          {dateTime.format(new Date(call.startedAt))}
                        </span>
                        {call.error && <p className="w-full text-xs text-destructive">{call.error}</p>}
                      </li>
                    ))}
                  </ul>
                </Section>
              )}
            </div>
          </DialogContent>
        )}
      </Dialog>
    </section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <h5 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h5>
      <div className="leading-relaxed">{children}</div>
    </div>
  );
}
