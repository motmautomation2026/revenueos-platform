import type { Metadata } from "next";
import Link from "next/link";
import {
  BrainCircuit,
  Mail,
  Phone,
  Search,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import { EmptyTool, ExecutionEmpty, ExecutionHeader, Pill } from "@/components/brain/parts";
import { loadWorkerStatus } from "@/lib/brain/worker-status";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "Diagnostics" };

const when = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" });
const fmt = (iso: string | null) => (iso ? when.format(new Date(iso)) : "—");

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

export default async function DiagnosticsPage(props: ExecutionPageProps) {
  const [{ data, base, query }, worker] = await Promise.all([loadExecutionPage(props), loadWorkerStatus()]);
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;
  const { view, runs, rows } = data;

  const taskIdOf = new Map(rows.tasks.map((t) => [t.id, t.task_id]));

  // The latest tool call in each tool's bucket, and how healthy it looks.
  const tools = TOOLS.map((tool) => {
    const calls = rows.toolCalls.filter((c) => tool.match(c.tool));
    const last = calls.length ? calls.reduce((a, b) => (b.id > a.id ? b : a)) : null;
    const status = last?.status ?? null;
    const tone: Tone = !last
      ? "idle"
      : last.error || (status != null && PROBLEM_STATUS.has(status))
        ? "problem"
        : status === "done"
          ? "ok"
          : status != null && ATTENTION_STATUS.has(status)
            ? "attention"
            : "attention";
    return { ...tool, last, status, tone };
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

  return (
    <div className="grid gap-6">
      <ExecutionHeader title="Diagnostics" view={view} runs={runs} basePath={`${base}/diagnostics`} />

      <p className="text-sm text-muted-foreground">
        A read-only health view: is the Brain running, how far the run has got, and which tool (if any) is stuck.
      </p>

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

      {/* Tools */}
      <section aria-label="Tools" className="grid gap-2">
        <h3 className="text-sm font-semibold">Tools</h3>
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
                <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                  {tool.last ? fmt(tool.last.finished_at ?? tool.last.started_at) : ""}
                </span>
                <p className="w-full text-muted-foreground">
                  {tool.last ? (
                    <>
                      last: {taskId ? `${taskId} ` : ""}
                      {tool.last.step} · {tool.last.status}
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

      {/* Waiting / failed */}
      <section aria-label="Waiting or failed" className="grid gap-2">
        <h3 className="text-sm font-semibold">Waiting or failed</h3>
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
                <span className="font-medium">{d.taskId ? `${d.taskId} — ` : ""}{d.title}</span>
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
