"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  Check,
  CircleDashed,
  Loader2,
  Play,
  RotateCw,
  ShieldAlert,
  Sparkles,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import {
  RUNNING_STATUSES,
  STEP_LABELS,
  type PlanProgress,
  type StepProgress,
} from "@/lib/plan/types";

const POLL_MS = 3000;
const MIN_OVERRIDE_REASON = 10;

function formatDuration(ms: number) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

type Action =
  | { action: "start" }
  | { action: "resume" }
  | { action: "answer_blockers"; answers: { question: string; answer: string }[] }
  | { action: "override"; reason: string };

export function PlanRunner({
  projectId,
  initial,
}: {
  projectId: string;
  /** The latest plan's progress, or null when no plan has been started. */
  initial: PlanProgress | null;
}) {
  const router = useRouter();
  const [progress, setProgress] = useState(initial);
  const [sending, setSending] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  // Server clock minus browser clock, so elapsed time is right even if the PC clock is off.
  const clockOffset = useRef(0);

  const running = !!progress && RUNNING_STATUSES.includes(progress.status) && !progress.stale;
  const planId = progress?.id;

  const load = useCallback(
    async (id: string) => {
      try {
        const response = await fetch(`/api/plans/${id}`, { cache: "no-store" });
        if (!response.ok) return;
        const next = (await response.json()) as PlanProgress;
        clockOffset.current = Date.parse(next.serverTime) - Date.now();
        setProgress(next);
        if (next.status === "ready") router.refresh();
      } catch {
        // A dropped poll is fine: the pipeline runs on the server and the next poll catches up.
      }
    },
    [router]
  );

  useEffect(() => {
    if (!running || !planId) return;
    const poll = setInterval(() => void load(planId), POLL_MS);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [running, planId, load]);

  const send = async (action: Action) => {
    setSending(true);
    try {
      const response = await fetch(`/api/projects/${projectId}/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(action),
      });
      const body = (await response.json().catch(() => null)) as { planId?: string; error?: string } | null;
      if (!response.ok || !body?.planId) {
        toast.error(body?.error ?? "Could not start the plan.");
        return;
      }
      await load(body.planId);
      router.refresh(); // updates the stepper and project status in the header
    } catch {
      toast.error("Could not reach the server. Check your connection and try again.");
    } finally {
      setSending(false);
    }
  };

  if (!progress) {
    return (
      <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed px-6 py-14 text-center">
        <Sparkles className="size-8 text-muted-foreground" aria-hidden />
        <div className="max-w-md">
          <p className="font-medium">Ready to write the GTM plan</p>
          <p className="text-sm text-muted-foreground">
            Three AI agents run in order: Diagnosis, Strategy, then Execution. This takes several
            minutes and keeps running if you close this page.
          </p>
        </div>
        <Button disabled={sending} onClick={() => send({ action: "start" })}>
          {sending ? <Loader2 className="animate-spin" aria-hidden /> : <Play aria-hidden />}
          Generate GTM plan
        </Button>
      </div>
    );
  }

  return (
    <div className="grid max-w-3xl gap-6">
      <div>
        <h2 className="text-lg font-semibold">GTM plan · version {progress.version}</h2>
        <p className="text-sm text-muted-foreground">
          {running
            ? "The plan is being written on the server. You can close this page and come back."
            : progress.status === "blocked"
              ? "The Diagnosis Agent needs more information before the strategy can be written."
              : progress.status === "failed" || progress.stale
                ? "The run stopped before finishing. Finished steps are saved."
                : "Finishing up…"}
        </p>
      </div>

      <ol className="grid gap-2">
        {progress.steps.map((step, index) => (
          <StepRow
            key={step.step}
            step={step}
            number={index + 1}
            now={now + clockOffset.current}
            stalled={progress.stale}
          />
        ))}
      </ol>

      {(progress.status === "failed" || progress.stale) && (
        <div className="grid gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
          <p className="flex items-start gap-2 text-sm">
            <AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
            <span>
              {progress.stale
                ? "This run has made no progress for 20 minutes, so it has probably stopped."
                : (progress.error ?? "The run failed.")}
            </span>
          </p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={sending} onClick={() => send({ action: "resume" })}>
              {sending ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCw aria-hidden />}
              Resume from the failed step
            </Button>
            <Button variant="outline" disabled={sending} onClick={() => send({ action: "start" })}>
              Start a new version
            </Button>
          </div>
        </div>
      )}

      {progress.status === "blocked" && (
        <BlockedPanel progress={progress} sending={sending} send={send} />
      )}
    </div>
  );
}

function StepRow({
  step,
  number,
  now,
  stalled,
}: {
  step: StepProgress;
  number: number;
  now: number;
  stalled: boolean;
}) {
  const state = stalled && step.state === "running" ? "failed" : step.state;
  const elapsed =
    state === "running" && step.runningSince
      ? formatDuration(now - Date.parse(step.runningSince))
      : step.durationMs
        ? formatDuration(step.durationMs)
        : null;

  return (
    <li
      className={cn(
        "flex items-center gap-3 rounded-xl border px-4 py-3",
        state === "running" && "border-primary/40 bg-muted",
        state === "pending" && "text-muted-foreground"
      )}
    >
      <span className="flex size-6 shrink-0 items-center justify-center">
        {state === "done" && <Check className="size-5 text-emerald-600" aria-hidden />}
        {state === "running" && <Loader2 className="size-5 animate-spin" aria-hidden />}
        {state === "failed" && <XCircle className="size-5 text-destructive" aria-hidden />}
        {state === "pending" && <CircleDashed className="size-5" aria-hidden />}
      </span>
      <div className="flex-1">
        <p className="font-medium">
          {number}. {STEP_LABELS[step.step]}
        </p>
        <p className="text-sm text-muted-foreground">
          {state === "done" && "Done"}
          {state === "running" && "Running…"}
          {state === "failed" && "Failed"}
          {state === "pending" && "Pending"}
        </p>
      </div>
      {elapsed && <span className="text-sm tabular-nums text-muted-foreground">{elapsed}</span>}
    </li>
  );
}

function BlockedPanel({
  progress,
  sending,
  send,
}: {
  progress: PlanProgress;
  sending: boolean;
  send: (action: Action) => Promise<void>;
}) {
  const [answers, setAnswers] = useState<string[]>(() => progress.blockers.map(() => ""));
  const [overriding, setOverriding] = useState(false);
  const [reason, setReason] = useState("");

  const hasAnswer = answers.some((answer) => answer.trim());
  const reasonOk = reason.trim().length >= MIN_OVERRIDE_REASON;

  return (
    <div className="grid gap-5 rounded-xl border border-amber-500/40 bg-amber-500/5 p-4">
      <p className="flex items-start gap-2 font-medium">
        <ShieldAlert className="mt-0.5 size-5 shrink-0 text-amber-600" aria-hidden />
        {progress.blockers.length} blocker{progress.blockers.length === 1 ? "" : "s"} to resolve
      </p>

      {progress.blockers.length === 0 && (
        <p className="text-sm text-muted-foreground">
          The agent said it cannot proceed but listed no specific blocker. You can override with a
          reason, or start a new version after updating the checklist.
        </p>
      )}

      <ol className="grid gap-4">
        {progress.blockers.map((blocker, i) => (
          <li key={i} className="grid gap-1.5">
            <p className="text-sm text-muted-foreground">{blocker.blocker}</p>
            <label htmlFor={`blocker-${i}`} className="text-sm font-medium">
              {blocker.question_to_resolve}
            </label>
            <Textarea
              id={`blocker-${i}`}
              rows={2}
              value={answers[i] ?? ""}
              disabled={sending}
              onChange={(e) =>
                setAnswers((current) => current.map((a, j) => (j === i ? e.target.value : a)))
              }
            />
          </li>
        ))}
      </ol>

      <div className="flex flex-wrap gap-2">
        {progress.blockers.length > 0 && (
          <Button
            disabled={sending || !hasAnswer}
            onClick={() =>
              send({
                action: "answer_blockers",
                answers: progress.blockers.map((blocker, i) => ({
                  question: blocker.question_to_resolve,
                  answer: answers[i] ?? "",
                })),
              })
            }
          >
            {sending ? <Loader2 className="animate-spin" aria-hidden /> : <RotateCw aria-hidden />}
            Save answers and re-run diagnosis
          </Button>
        )}
        {!overriding && (
          <Button variant="outline" disabled={sending} onClick={() => setOverriding(true)}>
            Override and continue
          </Button>
        )}
      </div>

      {overriding && (
        <div className="grid gap-2 border-t pt-4">
          <label htmlFor="override-reason" className="text-sm font-medium">
            Why is it safe to continue without resolving these?
          </label>
          <Textarea
            id="override-reason"
            rows={2}
            value={reason}
            disabled={sending}
            onChange={(e) => setReason(e.target.value)}
          />
          <p className="text-sm text-muted-foreground">
            Required. The reason is saved and shown in the plan&apos;s internal section.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button
              disabled={sending || !reasonOk}
              onClick={() => send({ action: "override", reason: reason.trim() })}
            >
              Override and continue
            </Button>
            <Button variant="ghost" disabled={sending} onClick={() => setOverriding(false)}>
              Cancel
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
