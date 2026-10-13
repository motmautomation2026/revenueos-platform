"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BrainCircuit, Check, CircleX, FlaskConical, Hand, Loader2, Rocket, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { RunModeFields } from "@/components/brain/run-mode-fields";
import { stagingSchema, type StagingSettings } from "@/lib/brain/staging";
import {
  MAX_ANSWER_TEXT,
  REQUEST_STEPS,
  failedWhile,
  planAnswerProblem,
  requestStepIndex,
  type MonthPlanView,
  type PlanAnswer,
  type RequestView,
} from "@/lib/brain/requests";
import { cn } from "@/lib/utils";
import {
  answerMonthPlan,
  cancelExecutionRequest,
  requestExecution,
  retryExecutionRequest,
} from "@/app/(app)/projects/[id]/execution/actions";

type Result = { ok: true; message: string } | { ok: false; error: string };

/**
 * Starting execution without anyone typing a command: ask the Brain for a month plan, watch it
 * being written, approve it here. The Brain's worker does the work; this only records requests
 * and answers.
 */
export function RequestPanel({
  projectId,
  request,
  canStart,
  again = false,
  accountsHref,
  staging,
}: {
  projectId: string;
  request: RequestView;
  /** No run is in progress for this customer, so a new month can be planned. */
  canStart: boolean;
  /** A month was already run for this customer; this would plan another one. */
  again?: boolean;
  /** Set when this customer's sender details or calling account are not filled in yet: where to do that. */
  accountsHref?: string;
  /** The project's saved Staging / Production choice. Left out until the database has it: production only. */
  staging?: StagingSettings;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmStart, setConfirmStart] = useState(false);
  const [runMode, setRunMode] = useState<StagingSettings | null>(staging ?? null);
  const [modeError, setModeError] = useState<string | null>(null);
  const inStaging = runMode?.mode === "staging";
  const setMode = (change: Partial<StagingSettings>) => {
    setModeError(null);
    setRunMode((current) => (current ? { ...current, ...change } : current));
  };

  const run = async (name: string, action: () => Promise<Result>) => {
    setBusy(name);
    try {
      const result = await action();
      if (result.ok) toast.success(result.message);
      else toast.error(result.error);
      router.refresh();
    } catch {
      toast.error("That could not be sent. Check your connection and try again.");
    } finally {
      setBusy(null);
    }
  };

  const ids = { projectId, requestId: request.id ?? "" };
  const askToStart = () => {
    // Check the staging fields before the confirmation, so a mistake is fixed where it was made.
    const problem = runMode ? stagingSchema.safeParse(runMode).error?.issues[0]?.message : undefined;
    setModeError(problem ?? null);
    if (!problem) setConfirmStart(true);
  };
  const start = () => {
    setConfirmStart(false);
    void run("start", () => requestExecution({ projectId, ...(runMode ? { run: runMode } : {}) }));
  };
  const cancel = (
    <Button variant="outline" disabled={busy !== null} onClick={() => void run("cancel", () => cancelExecutionRequest(ids))}>
      {busy === "cancel" && <Loader2 className="animate-spin" aria-hidden />}
      Cancel
    </Button>
  );

  const idle = request.stage === "none" || request.stage === "cancelled" || request.stage === "loaded";
  if (idle && !canStart) return null;

  return (
    <section aria-label="Start execution" className="grid gap-4 rounded-xl border p-5">
      {idle && (
        <div className="flex flex-wrap items-center gap-4">
          <span className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Rocket className="size-5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <p className="font-semibold">{again ? "Plan another month" : "Start execution"}</p>
            <p className="text-sm text-muted-foreground">
              The Brain reads the GTM plan and writes this month&apos;s tasks. You approve them here before anything
              runs.
              {request.stage === "cancelled" && " The last request was cancelled."}
            </p>
          </div>
          {accountsHref ? (
            <Link
              href={accountsHref}
              className="rounded-lg border border-amber-500/60 bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-900 outline-none hover:bg-amber-500/15 focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              Finish this customer&apos;s setup first
            </Link>
          ) : (
            <Button disabled={busy !== null} onClick={askToStart}>
              {busy === "start" && <Loader2 className="animate-spin" aria-hidden />}
              {again ? "Plan another month" : "Start execution"}
              {inStaging && " in staging"}
            </Button>
          )}
        </div>
      )}

      {idle && runMode && !accountsHref && (
        <div className="grid gap-4 border-t pt-4">
          <RunModeFields value={runMode} onChange={setMode} disabled={busy !== null} />
          {modeError && (
            <p role="alert" className="text-sm text-destructive">
              {modeError}
            </p>
          )}
        </div>
      )}

      {!idle && request.stage !== "failed" && <Progress request={request} />}

      {(request.stage === "waiting_for_brain" || request.stage === "worker_missing") && (
        <Waiting
          title="Waiting for the Brain to pick this up"
          note={
            request.stage === "worker_missing"
              ? "Nothing has picked this up for a few minutes. The Brain's worker is probably not running on its machine; the request stays here until it is."
              : "This usually takes less than a minute."
          }
          warn={request.stage === "worker_missing"}
          action={cancel}
        />
      )}

      {request.stage === "planning" && (
        <Waiting
          title="The Brain is writing the month plan"
          note={request.brainMessage ?? "This takes a few minutes. You can leave this page; the plan will wait here for your approval."}
          action={cancel}
        />
      )}

      {request.stage === "needs_approval" && request.plan && (
        <Approval
          plan={request.plan}
          errors={request.planErrors}
          message={request.brainMessage}
          busy={busy}
          cancel={cancel}
          onAnswer={(answer, note) => void run(answer, () => answerMonthPlan({ ...ids, answer, note }))}
        />
      )}
      {request.stage === "needs_approval" && !request.plan && (
        <Waiting title="The month plan could not be shown" note="The Brain published a plan this page cannot read." warn action={cancel} />
      )}

      {request.stage === "answer_sent" && (
        <Waiting title="Your answer is on its way to the Brain" note="It reads answers every few seconds." />
      )}

      {request.stage === "finishing" && (
        <Waiting
          title={`Approved${request.approvedBy ? ` by ${request.approvedBy}` : ""}. Preparing the run`}
          note="The Brain is writing the work orders, checking its setup and loading the run. Execution starts by itself."
        />
      )}

      {request.stage === "failed" && (
        <div className="flex flex-wrap items-start gap-4">
          <CircleX className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
          <div className="grid min-w-0 flex-1 gap-1">
            <p className="font-semibold">{failedWhile(request.failedStep)}</p>
            {request.error && <p className="text-sm leading-relaxed break-words text-muted-foreground">{request.error}</p>}
            <p className="text-sm text-muted-foreground">
              {request.approvedBy && (request.failedStep === "check" || request.failedStep === "load")
                ? "The approved month plan is kept: trying again continues from here."
                : "Nothing was approved or started."}
            </p>
          </div>
          <Button disabled={busy !== null} onClick={() => void run("retry", () => retryExecutionRequest(ids))}>
            {busy === "retry" && <Loader2 className="animate-spin" aria-hidden />}
            Try again
          </Button>
        </div>
      )}

      <AlertDialog open={confirmStart} onOpenChange={setConfirmStart}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {inStaging ? "Start a staging run?" : "Ask the Brain to plan this month?"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {inStaging
                ? `Staging: no more than ${runMode?.companies} companies and ${runMode?.people} profiles are fetched. `
                : "Production: the plan's full targets. "}
              The Brain will read the GTM plan and write the month&apos;s tasks, which uses AI. No email is sent, no
              call is made and no data is bought until you approve the month plan, and later each campaign.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not now</AlertDialogCancel>
            <Button onClick={start}>Start</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}

function Progress({ request }: { request: RequestView }) {
  const at = requestStepIndex(request.stage);
  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <BrainCircuit className="size-5 text-primary" aria-hidden />
        <p className="font-semibold">Starting execution</p>
        {request.month && <span className="text-sm text-muted-foreground">Run {request.month}</span>}
        {request.testMode && (
          <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/50 px-2 py-0.5 text-xs font-medium text-amber-900">
            <FlaskConical className="size-3" aria-hidden />
            Staging
          </span>
        )}
        {request.requestedBy && <span className="text-sm text-muted-foreground">asked by {request.requestedBy}</span>}
      </div>
      <ol className="grid gap-2 sm:grid-cols-5">
        {REQUEST_STEPS.map((step, index) => (
          <li
            key={step.key}
            aria-current={index === at ? "step" : undefined}
            className={cn(
              "flex items-center gap-2 rounded-lg border px-3 py-2 text-sm",
              index < at && "border-emerald-600/30 bg-emerald-500/10 text-emerald-900",
              index === at && "border-primary bg-primary/5 font-medium",
              index > at && "text-muted-foreground"
            )}
          >
            {index < at ? (
              <Check className="size-4 shrink-0" aria-hidden />
            ) : (
              <span className="flex size-5 shrink-0 items-center justify-center rounded-full border text-xs tabular-nums">
                {index + 1}
              </span>
            )}
            {step.label}
          </li>
        ))}
      </ol>
    </div>
  );
}

function Waiting({
  title,
  note,
  warn = false,
  action,
}: {
  title: string;
  note: string;
  warn?: boolean;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-4">
      {warn ? (
        <TriangleAlert className="size-5 shrink-0 text-amber-700" aria-hidden />
      ) : (
        <Loader2 className="size-5 shrink-0 animate-spin text-primary" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground">{note}</p>
      </div>
      {action}
    </div>
  );
}

function Approval({
  plan,
  errors,
  message,
  busy,
  cancel,
  onAnswer,
}: {
  plan: MonthPlanView;
  errors: string[];
  message: string | null;
  busy: string | null;
  cancel: React.ReactNode;
  onAnswer: (answer: PlanAnswer, note: string) => void;
}) {
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const broken = errors.length;
  const weeks = [...new Set(plan.tasks.map((task) => task.week ?? 0))].sort((a, b) => a - b);

  const answer = (choice: PlanAnswer) => {
    const found = planAnswerProblem(choice, note, broken);
    setProblem(found);
    if (found) {
      if (choice !== "yes") document.getElementById("plan-answer-note")?.focus();
      return;
    }
    onAnswer(choice, note);
  };

  return (
    <div className="grid gap-5">
      <div className="flex items-center gap-3 rounded-xl bg-primary px-4 py-3 text-primary-foreground">
        <Hand className="size-5 shrink-0" aria-hidden />
        <div>
          <p className="text-xs font-semibold tracking-wide uppercase opacity-85">Your next step</p>
          <p className="font-semibold">
            Approve the month plan{plan.month ? ` for ${plan.month}` : ""}: {plan.tasks.length} task
            {plan.tasks.length === 1 ? "" : "s"}
          </p>
        </div>
      </div>

      {message && (
        <p role="status" className="rounded-lg border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm text-amber-900">
          {message}
        </p>
      )}

      {broken > 0 && (
        <div className="grid gap-2 rounded-lg border border-amber-500/50 bg-amber-500/10 p-3 text-sm">
          <p className="flex items-center gap-2 font-semibold text-amber-900">
            <TriangleAlert className="size-4" aria-hidden />
            This plan still breaks {broken} rule{broken === 1 ? "" : "s"}
          </p>
          <ul className="grid list-disc gap-1 pl-5 leading-relaxed">
            {errors.map((error, i) => (
              <li key={i}>{error}</li>
            ))}
          </ul>
          <p className="text-amber-900">Ask for changes that fix them, or approve anyway with a reason.</p>
        </div>
      )}

      {plan.objectives.length > 0 && <PlanList title="Objectives" items={plan.objectives} />}

      <div className="grid gap-3">
        <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Tasks</h4>
        {weeks.map((week) => (
          <div key={week} className="overflow-hidden rounded-lg border">
            <p className="border-b bg-muted/50 px-3 py-1.5 text-xs font-semibold">{week ? `Week ${week}` : "No week set"}</p>
            <ul className="divide-y">
              {plan.tasks
                .filter((task) => (task.week ?? 0) === week)
                .map((task) => (
                  <li key={task.id} className="grid gap-x-4 gap-y-1 px-3 py-2 text-sm md:grid-cols-[3rem_11rem_minmax(0,1fr)]">
                    <span className="font-semibold tabular-nums">{task.id}</span>
                    <span className="text-muted-foreground">{task.agent}</span>
                    <div className="grid min-w-0 gap-0.5">
                      <p className="leading-relaxed">{task.task}</p>
                      {task.target && <p className="text-muted-foreground">Target: {task.target}</p>}
                      {task.dependsOn.length > 0 && (
                        <p className="text-xs text-muted-foreground">After {task.dependsOn.join(", ")}</p>
                      )}
                    </div>
                  </li>
                ))}
            </ul>
          </div>
        ))}
      </div>

      {plan.revenue.length > 0 && (
        <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {plan.revenue.map((fact) => (
            <div key={fact.label} className="rounded-lg border p-3 text-sm">
              <dt className="text-xs text-muted-foreground">{fact.label}</dt>
              <dd className="font-medium">{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <PlanList title="Still missing or blocked" items={plan.blocked} />
        <PlanList title="Measures being tracked" items={plan.kpis} />
      </div>

      <div className="grid gap-2 border-t pt-4">
        <label htmlFor="plan-answer-note" className="text-sm font-medium">
          {broken > 0 ? "Changes you want, or your reason for approving anyway" : "Changes you want (leave empty to approve)"}
        </label>
        <Textarea
          id="plan-answer-note"
          rows={3}
          maxLength={MAX_ANSWER_TEXT}
          value={note}
          disabled={busy !== null}
          aria-invalid={problem !== null}
          placeholder="e.g. Fewer calls in week 1; start with Pune only."
          onChange={(e) => {
            setNote(e.target.value);
            setProblem(null);
          }}
        />
        {problem && (
          <p role="alert" className="text-sm text-destructive">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          {broken === 0 ? (
            <Button disabled={busy !== null || note.trim() !== ""} onClick={() => answer("yes")}>
              {busy === "yes" && <Loader2 className="animate-spin" aria-hidden />}
              Approve month plan
            </Button>
          ) : (
            <Button variant="destructive" disabled={busy !== null} onClick={() => answer("override")}>
              {busy === "override" && <Loader2 className="animate-spin" aria-hidden />}
              Approve anyway
            </Button>
          )}
          <Button variant="outline" disabled={busy !== null} onClick={() => answer("changes")}>
            {busy === "changes" && <Loader2 className="animate-spin" aria-hidden />}
            Request changes
          </Button>
          <span className="ml-auto">{cancel}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          Approving lets the Brain start the tasks. Every email campaign and every call campaign still asks for its
          own approval before anything is sent.
        </p>
      </div>
    </div>
  );
}

function PlanList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="grid content-start gap-2 text-sm">
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{title}</h4>
      <ul className="grid list-disc gap-1 pl-4 leading-relaxed">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
