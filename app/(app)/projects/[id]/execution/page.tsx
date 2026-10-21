import type { Metadata } from "next";
import {
  ArrowDown,
  CheckCircle2,
  Hand,
  Lightbulb,
} from "lucide-react";
import { DecisionList } from "@/components/brain/decision-list";
import { ExecutionBoard } from "@/components/brain/execution-board";
import {
  ExecutionEmpty,
  ExecutionHeader,
  Pill,
  SectionTitle,
  StatTile,
  TASK_STATUS,
  dateTime,
  number,
} from "@/components/brain/parts";
import { PaidStepCard } from "@/components/brain/paid-step-card";
import { RequestPanel } from "@/components/brain/request-panel";
import { RunModeCard } from "@/components/brain/run-mode-card";
import { loadSetupGaps } from "@/lib/brain/setup";
import { readStaging } from "@/lib/brain/staging";
import { loadPaidStep } from "@/lib/brain/paid-steps";
import { loadCallRecords } from "@/lib/brain/read";
import { loadRequest } from "@/lib/brain/request-store";
import {
  buildCallsReport,
  buildFunnel,
  toolsInRun,
} from "@/lib/brain/tools";
import type { ExecutionView } from "@/lib/brain/view";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "./load";

export const metadata: Metadata = { title: "Dashboard" };

const ACTION_STATE = {
  open: {
    label: "Waiting for a decision",
    tone: "bg-amber-500/20 text-amber-900",
  },
  accepted: { label: "Accepted", tone: "bg-emerald-500/15 text-emerald-800" },
  rejected: { label: "Rejected", tone: "bg-muted text-muted-foreground" },
  suggested: { label: "Suggested", tone: "bg-muted text-muted-foreground" },
} as const;

export default async function ExecutionPage(props: ExecutionPageProps) {
  const { project, data, base } = await loadExecutionPage(props);
  // Starting execution is asked for here and done by the Brain's worker; see lib/brain/requests.ts.
  const [request, gaps] = await Promise.all([
    loadRequest(project.id),
    loadSetupGaps(project.id),
  ]);
  // Email first: it is the one a new customer always has to fill in.
  const accountsHref = gaps.email
    ? `${base}/email`
    : gaps.calls
      ? `${base}/calls`
      : undefined;
  const saved = readStaging(project);
  const staging = saved.available ? saved : undefined;
  if (data.state !== "ok") {
    if (data.state === "no_runs" && request.available) {
      return (
        <RequestPanel
          projectId={project.id}
          request={request.view}
          canStart
          accountsHref={accountsHref}
          staging={staging}
        />
      );
    }
    return <ExecutionEmpty data={data} />;
  }

  const { view, runs, rows } = data;
  // A new month can be planned only when the latest run is over.
  const latestOver =
    runs[0]?.status === "done" || runs[0]?.status === "stopped";

  // The Brain never starts a task that buys data on its own; it waits here for a person.
  const paidStep = latestOver ? null : await loadPaidStep(project.id);
  const paidTask = paidStep
    ? rows.tasks.find((task) => task.task_id === paidStep.taskId)
    : undefined;
  const paidOrder = (paidTask?.work_order ?? {}) as {
    task?: unknown;
    target?: unknown;
  };
  // A step waiting to be started counts as something waiting for the person, like a decision.
  const startNeeded =
    paidStep?.status === "waiting" || paidStep?.status === "failed";
  const waiting = view.openDecisions.length + (startNeeded ? 1 : 0);

  const tools = toolsInRun(rows.tasks);
  const calls = buildCallsReport(rows);
  const funnel = buildFunnel(rows.tasks);

  // "Interested" is not a Brain task: it comes from the calling tool's own count for these campaigns.
  const callRecords = await loadCallRecords(
    calls.campaigns.map((c) => c.reference),
  );
  const callsStage = funnel.findIndex((stage) => stage.key === "calls");
  // While calls are still being made the Brain has no total yet: use the calling tool's own count.
  if (callsStage !== -1 && funnel[callsStage].achieved == null && callRecords.size > 0) {
    funnel[callsStage].achieved = [...callRecords.values()].reduce((sum, record) => sum + record.calls.length, 0);
  }
  if (callRecords.size > 0 && callsStage !== -1) {
    const interested = [...callRecords.values()].reduce(
      (sum, record) => sum + (record.interested ?? 0),
      0,
    );
    funnel.splice(callsStage + 1, 0, {
      ...funnel[callsStage],
      key: "interested",
      label: "Interested after calls",
      achieved: interested,
      target: null,
      planTarget: null,
    });
  }
  const percentDone = view.counts.total
    ? Math.round((view.counts.done / view.counts.total) * 100)
    : 0;

  return (
    <div className="grid gap-8">
      <div className="grid gap-2">
        <ExecutionHeader
          title="Dashboard"
          view={view}
          runs={runs}
          basePath={base}
        />
        <p className="text-sm text-muted-foreground">
          {view.approval && (
            <>
              Month plan{" "}
              {view.approval[0].toLowerCase() + view.approval.slice(1)} ·{" "}
            </>
          )}
          Started {dateTime.format(new Date(view.startedAt))}
        </p>
        {view.stopReason && (
          <p className="text-sm text-muted-foreground">
            Stopped because: {view.stopReason}
          </p>
        )}
      </div>

      {request.available && (request.view.active || latestOver) && (
        <RequestPanel
          projectId={project.id}
          request={request.view}
          canStart={latestOver}
          again
          accountsHref={accountsHref}
          staging={staging}
        />
      )}

      {staging && !latestOver && (
        <RunModeCard projectId={project.id} saved={staging} />
      )}

      {paidStep && (
        <div id="paid-step" className="scroll-mt-20">
          <PaidStepCard
            projectId={project.id}
            step={paidStep}
            task={
              paidTask
                ? {
                    agent: paidTask.agent,
                    what:
                      typeof paidOrder.task === "string" ? paidOrder.task : "",
                    target:
                      typeof paidOrder.target === "string"
                        ? paidOrder.target
                        : "",
                  }
                : null
            }
          />
        </div>
      )}

      {/* Progress and what needs you. Each tool's result is in the pipeline below (not repeated here). */}
      <section aria-label="At a glance" className="grid grid-cols-2 gap-3 lg:max-w-2xl">
        <StatTile
          label="Tasks done"
          value={number.format(view.counts.done)}
          of={number.format(view.counts.total)}
          percent={percentDone}
          note={`${percentDone}% of this month plan`}
        />
        {waiting > 0 ? (
          <a
            href={startNeeded ? "#paid-step" : "#decisions"}
            className="grid content-start gap-2 rounded-xl border border-amber-500/60 bg-amber-500/10 p-4 outline-none hover:bg-amber-500/15 focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <p className="text-sm text-amber-900">Waiting for you</p>
            <p className="text-3xl leading-none font-semibold tabular-nums">
              {number.format(waiting)}
            </p>
            <p className="flex items-center gap-1 text-xs font-medium text-amber-900">
              {startNeeded
                ? `Start ${paidStep?.taskId}`
                : `Review decision${waiting === 1 ? "" : "s"}`}
              <ArrowDown className="size-3" aria-hidden />
            </p>
          </a>
        ) : (
          <div className="grid content-start gap-2 rounded-xl border p-4">
            <p className="text-sm text-muted-foreground">Waiting for you</p>
            <p className="flex items-center gap-2 text-3xl leading-none font-semibold tabular-nums">
              0
              <CheckCircle2 className="size-6 text-emerald-700" aria-hidden />
            </p>
            <p className="text-xs text-muted-foreground">
              {view.status === "done"
                ? "Every task is finished"
                : "The Brain will ask when it needs you"}
            </p>
          </div>
        )}
      </section>

      <div className="grid gap-3">
        {funnel.length > 0 && (
          <section className="grid content-start gap-3">
            <SectionTitle
              aside={
                funnel.some((stage) => stage.planTarget != null && stage.target != null && stage.planTarget !== stage.target)
                  ? "Targets capped for testing (Staging): plan targets in brackets"
                  : undefined
              }
            >
              Pipeline this month
            </SectionTitle>
            <ol className="grid divide-y rounded-xl border px-4">
              {funnel.map((stage) => {
                const status = TASK_STATUS[stage.status];
                const started = stage.achieved != null;
                // "Interested" has no target of its own: it is a share of the calls made.
                const callsMade = callsStage !== -1 ? funnel[callsStage].achieved : null;
                const interested = stage.key === "interested";
                const base = interested ? callsMade : stage.target;
                // Each bar is that stage's own progress to its target, so every row reads the same way.
                const percent = started && base ? Math.round(((stage.achieved ?? 0) / base) * 100) : null;
                const width = percent != null ? Math.min(100, Math.max(percent, stage.achieved ? 2 : 0)) : 0;
                const short =
                  stage.status === "done" && stage.target != null && started ? stage.target - (stage.achieved ?? 0) : 0;
                const capped = stage.planTarget != null && stage.target != null && stage.planTarget !== stage.target;
                return (
                  <li
                    key={stage.key}
                    className="grid items-center gap-x-5 gap-y-1.5 py-3.5 sm:grid-cols-[11rem_minmax(0,1fr)_8.5rem_6.5rem]"
                  >
                    <div className="min-w-0">
                      <p className="text-sm font-medium">{stage.label}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {stage.agent} · {stage.taskId}
                      </p>
                    </div>

                    <div className="grid gap-1">
                      <div className="h-2.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                        <div
                          className={cn("h-full rounded-full", percent != null && percent >= 100 ? "bg-emerald-600" : "bg-primary")}
                          style={{ width: `${width}%` }}
                        />
                      </div>
                      {short > 0 && (
                        <p className="text-xs text-muted-foreground">{number.format(short)} short of the target</p>
                      )}
                    </div>

                    <p className="text-sm tabular-nums sm:text-right">
                      {started ? (
                        <>
                          <span className="text-lg leading-none font-semibold">{number.format(stage.achieved ?? 0)}</span>
                          {interested ? (
                            callsMade != null && <span className="text-muted-foreground"> of {number.format(callsMade)} calls</span>
                          ) : (
                            stage.target != null && (
                              <span className="text-muted-foreground">
                                {" "}/ {number.format(stage.target)}
                                {capped && ` (${number.format(stage.planTarget!)})`}
                              </span>
                            )
                          )}
                          {percent != null && (
                            <span className="block text-xs text-muted-foreground">
                              {percent}% {interested ? "interested" : "of target"}
                            </span>
                          )}
                        </>
                      ) : (
                        <span className="text-muted-foreground">
                          {stage.target != null ? `Target ${number.format(stage.target)}` : "Not started"}
                        </span>
                      )}
                    </p>

                    <div className="sm:justify-self-end">
                      <Pill className={status.tone}>{status.label}</Pill>
                    </div>
                  </li>
                );
              })}
            </ol>
          </section>
        )}
        {tools.unconnected.length > 0 && (
          <p className="text-sm text-muted-foreground">
            In the plan but not connected to the Brain yet, so there is nothing to show:{" "}
            {tools.unconnected.join(", ")}.
          </p>
        )}
      </div>

      {view.openDecisions.length > 0 && (
        <section id="decisions" className="grid scroll-mt-20 gap-3">
          <div className="flex items-center gap-3 rounded-xl bg-primary px-4 py-3 text-primary-foreground">
            <Hand className="size-5 shrink-0" aria-hidden />
            <div>
              <p className="text-xs font-semibold tracking-wide uppercase opacity-85">
                Your next step
              </p>
              <p className="font-semibold">
                {view.openDecisions.length} decision
                {view.openDecisions.length === 1 ? " is" : "s are"} waiting for
                a person
              </p>
            </div>
          </div>
          <DecisionList projectId={project.id} decisions={view.openDecisions} />
        </section>
      )}

      <ExecutionBoard columns={view.columns} weeks={view.weeks} />

      {view.review && <Review review={view.review} />}

      <MonthPlan plan={view.plan} />

      <p className="text-xs text-muted-foreground">
        These pages show what the Brain has recorded. Answers given here are
        saved for the Brain, which applies them the next time it runs; the app
        itself never sends an email, makes a call or buys data.
      </p>
    </div>
  );
}

function Review({ review }: { review: NonNullable<ExecutionView["review"]> }) {
  return (
    <section className="grid gap-3">
      <SectionTitle aside={dateTime.format(new Date(review.reviewedAt))}>
        The Brain&apos;s review
      </SectionTitle>

      <div className="grid gap-3 md:grid-cols-2">
        <ReviewList title="What worked" items={review.worked} />
        <ReviewList title="What is short" items={review.short} />
      </div>

      {review.actions.length > 0 && (
        <div className="grid gap-2">
          <h4 className="flex items-center gap-1.5 text-sm font-semibold">
            <Lightbulb className="size-4 text-muted-foreground" aria-hidden />
            Suggested actions
          </h4>
          <ul className="grid gap-2">
            {review.actions.map((action, i) => {
              const state = ACTION_STATE[action.state];
              return (
                <li
                  key={action.id ?? i}
                  className="grid gap-1 rounded-xl border p-3 text-sm"
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="min-w-0 flex-1 font-medium">{action.text}</p>
                    <Pill className={cn("shrink-0", state.tone)}>
                      {state.label}
                    </Pill>
                  </div>
                  {action.why && (
                    <p className="leading-relaxed text-muted-foreground">
                      {action.why}
                    </p>
                  )}
                  {action.taskId && (
                    <p className="text-xs text-muted-foreground">
                      About task {action.taskId}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </section>
  );
}

function ReviewList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="rounded-xl border p-4">
      <h4 className="mb-2 text-sm font-semibold">{title}</h4>
      <ul className="grid list-disc gap-1.5 pl-4 text-sm leading-relaxed">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}

function MonthPlan({ plan }: { plan: ExecutionView["plan"] }) {
  if (!plan.objectives.length && !plan.revenue.length && !plan.blocked.length)
    return null;
  return (
    <details className="group rounded-xl border">
      <summary className="cursor-pointer list-none px-4 py-3 font-semibold [&::-webkit-details-marker]:hidden">
        The month plan behind these tasks
        <span className="ml-2 text-sm font-normal text-muted-foreground group-open:hidden">
          Show
        </span>
        <span className="ml-2 hidden text-sm font-normal text-muted-foreground group-open:inline">
          Hide
        </span>
      </summary>
      <div className="grid gap-5 border-t px-4 py-4 text-sm">
        {plan.revenue.length > 0 && (
          <div className="grid gap-2">
            <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
              Revenue picture
            </h4>
            <dl className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {plan.revenue.map((stat) => (
                <div key={stat.label} className="rounded-lg border p-3">
                  <dt className="text-xs text-muted-foreground">
                    {stat.label}
                  </dt>
                  <dd className="font-semibold">{stat.value}</dd>
                </div>
              ))}
            </dl>
          </div>
        )}
        <PlanList title="Objectives" items={plan.objectives} />
        <PlanList title="Still missing or blocked" items={plan.blocked} />
        <PlanList title="Measures being tracked" items={plan.kpisToTrack} />
      </div>
    </details>
  );
}

function PlanList({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="grid gap-2">
      <h4 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {title}
      </h4>
      <ul className="grid list-disc gap-1 pl-4 leading-relaxed">
        {items.map((item, i) => (
          <li key={i}>{item}</li>
        ))}
      </ul>
    </div>
  );
}
