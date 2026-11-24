import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowRight,
  ChevronDown,
  Clock,
  PhoneCall,
  PhoneOff,
  Quote,
  ThumbsUp,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import {
  EmptyTool,
  ExecutionEmpty,
  ExecutionHeader,
  InsightTileCard,
  Pill,
  SectionTitle,
  StageTrack,
  StepsTimeline,
  TASK_STATUS,
  dateTime,
  number,
} from "@/components/brain/parts";
import { CallerCard } from "@/components/brain/caller-card";
import { AccountStatusCard } from "@/components/brain/account-status-card";
import { CallScriptCard } from "@/components/brain/call-script";
import { TestCallCard } from "@/components/brain/test-call-card";
import { explainCallProblem } from "@/lib/brain/call-errors";
import { loadAccountStatus } from "@/lib/brain/account-status";
import { loadCallSyncHealth } from "@/lib/brain/call-sync";
import { loadCaller } from "@/lib/brain/caller-store";
import { loadVoice } from "@/lib/brain/voice-store";
import { loadScript } from "@/lib/brain/script-store";
import {
  loadCallProgress,
  loadCallRecords,
  loadCallScripts,
  type CallProgress,
  type CallRecord,
} from "@/lib/brain/read";
import {
  buildCallTiles,
  buildCallsReport,
  callHealth,
  liveCallLabel,
  summariseLiveCalls,
  type CallOutcome,
  type LiveCallSummary,
} from "@/lib/brain/tools";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "Calls" };

const TILE_ICONS: Record<string, LucideIcon> = {
  called: PhoneCall,
  interested: ThumbsUp,
  notCalled: PhoneOff,
};

function duration(seconds: number | null) {
  if (seconds == null) return "—";
  return seconds < 60
    ? `${seconds}s`
    : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

function outcomeTone(outcome: string) {
  const value = outcome.toLowerCase();
  if (value === "interested") return "bg-emerald-500/15 text-emerald-800";
  if (value.includes("fail") || value.includes("dropped"))
    return "bg-destructive/10 text-destructive";
  return "bg-muted text-muted-foreground";
}

function initials(name: string) {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return (
    (
      (words[0]?.[0] ?? "") +
      (words.length > 1 ? words[words.length - 1][0] : "")
    ).toUpperCase() || "?"
  );
}

export default async function CallsPage(props: ExecutionPageProps) {
  const { project, data, base } = await loadExecutionPage(props);
  const caller = await loadCaller(project.id, project.owner_id);
  const callerCard = caller.available ? (
    <CallerCard projectId={project.id} view={caller.view} />
  ) : null;
  const accountStatus = await loadAccountStatus(project.id);
  const callsStatusCard = accountStatus.available ? (
    <AccountStatusCard projectId={project.id} status={accountStatus} only="calls" />
  ) : null;
  const testCallCard = caller.available ? <TestCallCard projectId={project.id} /> : null;
  const callSync = await loadCallSyncHealth(project.id);
  const syncWarning =
    callSync.state === "stale" ? (
      <div
        role="alert"
        className="flex flex-wrap items-start gap-3 rounded-xl border border-amber-500/50 bg-amber-500/10 p-4 text-sm"
      >
        <TriangleAlert className="mt-0.5 size-5 shrink-0 text-amber-900" aria-hidden />
        <div className="grid min-w-0 gap-0.5">
          <p className="font-semibold text-amber-900">Call records may not be syncing</p>
          <p className="text-amber-900/90">
            A call campaign ran {dateTime.format(new Date(callSync.lastCampaignAt))}, but the most recent saved call is
            {callSync.lastCallAt ? ` from ${dateTime.format(new Date(callSync.lastCallAt))}` : " missing"}. Recent calls may be
            missing here and in the CRM — the calling platform may have stopped writing call records to the database.
          </p>
        </div>
      </div>
    ) : null;

  // Once a calling line is connected, tuck the account config + status into a collapsible so the page
  // leads with call results; it stays open while a line still needs setting up. The Test call tool and
  // any alerts stay outside it, always visible.
  const callerReady = caller.view.ready;
  const setupSection = callerCard ? (
    <details className="group grid gap-4" open={!callerReady}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded-xl border bg-background px-4 py-3 text-sm font-semibold outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <span className="flex items-center gap-2">
          <PhoneCall className="size-4 text-muted-foreground" aria-hidden />
          Calling account
        </span>
        <span className="flex items-center gap-2 font-normal text-muted-foreground">
          {callerReady ? "Connected · view or change" : "Needs setup"}
          <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
        </span>
      </summary>
      <div className="grid gap-4">
        {callerCard}
        {callsStatusCard}
      </div>
    </details>
  ) : null;

  // A plain-language orientation so a first-time viewer knows what the Calls tab is and the order of things.
  const intro = (
    <p className="max-w-3xl text-sm text-muted-foreground">
      This is your AI phone calling. First connect a{" "}
      <span className="font-medium text-foreground">calling account</span> — the number calls go out from — then send
      yourself a <span className="font-medium text-foreground">test call</span> to hear the agent. Each run, the AI calls
      this customer&apos;s leads, and every campaign&apos;s progress, outcomes, and what each prospect said appear below.
      You only need to step in where something is marked{" "}
      <span className="font-medium text-amber-700">waiting</span> or{" "}
      <span className="font-medium text-destructive">failed</span>.
    </p>
  );

  // Before the first run there is still something to do here: say where the calls come from.
  if (data.state === "no_runs" && callerCard) {
    return (
      <div className="grid gap-6">
        <div className="grid gap-2">
          <h2 className="text-xl font-semibold tracking-tight">Calls</h2>
          {intro}
        </div>
        {callerCard}
        {callsStatusCard}
        {testCallCard}
        {syncWarning}
      </div>
    );
  }
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;

  const { view, runs, rows } = data;
  const report = buildCallsReport(rows);
  // The calling tool's own records: full timings, transcripts and what the prospect asked for.
  const records = await loadCallRecords(
    report.campaigns.map((c) => c.reference),
  );
  // The agent's script per campaign, so the person can confirm how the agent talks (like the email samples).
  const scripts = await loadCallScripts(report.campaigns.map((c) => c.rowId));
  // Live per-call progress from the Brain's latest poll (the only live source while a campaign dials;
  // the voice platform's own tables stopped syncing to this database).
  const progress = await loadCallProgress(report.campaigns.map((c) => c.rowId));
  // The project's AI calling voice (default pre-selected), so the person can change it on the script card.
  const voice = await loadVoice(project.id);
  // The project's edited call script (if any), so the person can edit the opening line + instructions.
  const scriptEdit = await loadScript(project.id);

  return (
    <div className="grid gap-8">
      <ExecutionHeader
        title="Calls"
        view={view}
        runs={runs}
        basePath={`${base}/calls`}
      />
      {intro}
      {setupSection}
      {testCallCard}
      {syncWarning}

      {report.campaigns.length === 0 && (
        <EmptyTool>This month plan has no calling task.</EmptyTool>
      )}

      {report.campaigns.map((campaign, index) => {
        const status = TASK_STATUS[campaign.status];
        const record = records.get(campaign.reference) ?? null;
        const calls = record?.calls ?? [];
        const tiles = buildCallTiles(
          campaign,
          record && {
            interested: record.interested,
            completedCalls: record.completedCalls,
            talkSeconds: calls.reduce(
              (sum, call) => sum + (call.durationSeconds ?? 0),
              0,
            ),
            calls: calls.length,
          },
        );
        const callCount = record ? calls.length : campaign.outcomes.length;
        // The problem in plain words; the tool's own message stays under Technical details.
        // Only while the campaign is actually stopped on it: the Brain keeps the last message even
        // after a retry has gone through.
        const stoppedNow = campaign.steps.at(-1)?.status === "failed";
        const problem = stoppedNow ? explainCallProblem(campaign.lastError) : null;
        const stages = campaign.stages.map((stage) =>
          stage.state === "refused"
            ? { ...stage, note: problem ? "Stopped here" : stage.note }
            : stage,
        );
        const waitingOnPerson = campaign.status === "waiting_human";
        // While the campaign is still dialing, show the Brain's latest poll snapshot (live), since the
        // final outcomes are only written once every call is done.
        const live = campaign.status === "running" ? progress.get(campaign.rowId) ?? null : null;
        const liveSummary = live ? summariseLiveCalls(live.calls) : null;
        // Calls placed but none connected: the campaign "finished" yet every call failed to reach anyone.
        const health = callHealth(campaign.outcomes);

        // Outcome counts from the calling tool when it has them, otherwise from the Brain's summary.
        const outcomes = record
          ? [
              ...calls.reduce(
                (map, call) =>
                  map.set(
                    call.outcome || "Unknown",
                    (map.get(call.outcome || "Unknown") ?? 0) + 1,
                  ),
                new Map<string, number>(),
              ),
            ]
              .map(([label, count]) => ({ label, count }))
              .sort((a, b) => b.count - a.count)
          : campaign.outcomeCounts;

        return (
          <section key={campaign.taskId} className="grid gap-6">
            <div className="grid gap-1">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h3 className="text-lg font-semibold">
                  Call campaign
                  {report.campaigns.length > 1 ? ` ${index + 1}` : ""}
                </h3>
                <Pill className={status.tone}>{status.label}</Pill>
                <span className="text-sm text-muted-foreground">
                  Task {campaign.taskId}
                </span>
              </div>
              {record?.name && (
                <p className="text-sm text-muted-foreground">{record.name}</p>
              )}
            </div>

            {scripts.get(campaign.rowId) && (
              <CallScriptCard
                script={scripts.get(campaign.rowId)!}
                projectId={project.id}
                voice={voice}
                scriptEdit={scriptEdit}
              />
            )}

            {health.noneConnected && !problem && (
              <div
                role="alert"
                className="flex flex-wrap items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
              >
                <TriangleAlert className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
                <div className="grid min-w-0 flex-1 gap-0.5 text-sm">
                  <p className="font-semibold text-destructive">
                    None of these {health.attempted} call{health.attempted === 1 ? "" : "s"} connected
                  </p>
                  <p className="text-muted-foreground">
                    Every call was placed but failed to reach anyone ({health.failed} failed, 0 answered). A whole campaign
                    failing to connect usually means the calling platform could not place the calls — not a problem with
                    your leads or this app. Check the caller line; if it keeps happening across campaigns, the voice provider
                    needs to look at it.
                  </p>
                </div>
              </div>
            )}

            {problem ? (
              <div
                role="alert"
                className="flex flex-wrap items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4"
              >
                <TriangleAlert
                  className="mt-0.5 size-5 shrink-0 text-destructive"
                  aria-hidden
                />
                <div className="grid min-w-0 flex-1 gap-0.5 text-sm">
                  <p className="font-semibold">{problem.what}</p>
                  <p className="text-muted-foreground">{problem.next}</p>
                </div>
                <Link
                  href={`${base}#decisions`}
                  className="inline-flex items-center gap-1 rounded-lg border bg-background px-3 py-1.5 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  Go to Retry
                  <ArrowRight className="size-4" aria-hidden />
                </Link>
              </div>
            ) : (
              waitingOnPerson && (
                <div className="flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/50 bg-amber-500/10 p-4 text-sm">
                  <p className="min-w-0 flex-1 font-medium text-amber-900">
                    This campaign is waiting for your approval before anyone is
                    called.
                  </p>
                  <Link
                    href={`${base}#decisions`}
                    className="inline-flex items-center gap-1 rounded-lg border bg-background px-3 py-1.5 font-medium outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    Review and approve
                    <ArrowRight className="size-4" aria-hidden />
                  </Link>
                </div>
              )
            )}

            <StageTrack stages={stages} />

            {live && liveSummary && <LiveProgress live={live} summary={liveSummary} />}

            {callCount > 0 && (
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {tiles.map((tile) => (
                  <InsightTileCard
                    key={tile.key}
                    tile={tile}
                    icon={TILE_ICONS[tile.key] ?? PhoneCall}
                  />
                ))}
              </div>
            )}

            {callCount > 0 && (
              <div className="grid gap-3">
                <SectionTitle
                  aside={
                    outcomes.length > 0 && (
                      <span className="flex flex-wrap justify-end gap-1.5">
                        {outcomes.map((outcome) => (
                          <Pill
                            key={outcome.label}
                            className={outcomeTone(outcome.label)}
                          >
                            {number.format(outcome.count)}{" "}
                            {outcome.label.toLowerCase()}
                          </Pill>
                        ))}
                      </span>
                    )
                  }
                >
                  Every call
                </SectionTitle>
                {record ? (
                  <ul
                    className={cn(
                      "grid gap-3",
                      calls.length > 1 && "2xl:grid-cols-2",
                    )}
                  >
                    {calls.map((call) => (
                      <CallCard key={call.id} call={call} />
                    ))}
                  </ul>
                ) : (
                  <SummaryTable outcomes={campaign.outcomes} />
                )}
              </div>
            )}

            <details className="rounded-xl border text-sm">
              <summary className="cursor-pointer px-4 py-3 font-medium">
                Technical details
              </summary>
              <div className="grid gap-4 border-t px-4 py-4">
                <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
                  {campaign.lastError && (
                    <>
                      <dt className="text-muted-foreground">
                        Last message from the tool
                      </dt>
                      <dd className="font-mono text-xs break-words">
                        {campaign.lastError}
                      </dd>
                    </>
                  )}
                  {campaign.reference && (
                    <>
                      <dt className="text-muted-foreground">
                        Campaign reference
                      </dt>
                      <dd className="font-mono text-xs break-all">
                        {campaign.reference}
                      </dd>
                    </>
                  )}
                  {record?.status && (
                    <>
                      <dt className="text-muted-foreground">
                        Status in Quickhowl
                      </dt>
                      <dd>{record.status.toLowerCase()}</dd>
                    </>
                  )}
                  {campaign.contactsApproved > 0 && (
                    <>
                      <dt className="text-muted-foreground">
                        Contacts in the approved list
                      </dt>
                      <dd>{number.format(campaign.contactsApproved)}</dd>
                    </>
                  )}
                  {campaign.approval?.reason && (
                    <>
                      <dt className="text-muted-foreground">
                        Approver&apos;s note
                      </dt>
                      <dd className="break-words">
                        {campaign.approval.reason}
                      </dd>
                    </>
                  )}
                  {campaign.dnd && (
                    <>
                      <dt className="text-muted-foreground">
                        Do-not-call check
                      </dt>
                      <dd className="break-words">{campaign.dnd}</dd>
                    </>
                  )}
                </dl>
                <div className="grid gap-2">
                  <p className="font-medium">Every step the Brain took</p>
                  <StepsTimeline steps={campaign.steps} />
                </div>
              </div>
            </details>
          </section>
        );
      })}

      {report.objections.quotes.length > 0 && (
        <section className="grid gap-3">
          <SectionTitle>What prospects said</SectionTitle>
          <ul className="grid gap-3 md:grid-cols-2 2xl:grid-cols-3">
            {report.objections.quotes.map((quote, i) => (
              <li
                key={i}
                className="grid content-start gap-2 rounded-xl border p-4 text-sm"
              >
                <Quote className="size-4 text-muted-foreground" aria-hidden />
                <p className="leading-relaxed">{quote.quote}</p>
                <p className="text-xs text-muted-foreground">
                  {[quote.name, quote.label].filter(Boolean).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** One call from the calling tool's own record, with its transcript. */
function CallCard({ call }: { call: CallRecord }) {
  return (
    <li className="flex min-w-0 flex-col overflow-hidden rounded-xl border text-sm">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b bg-muted/40 px-4 py-3">
        <span
          aria-hidden
          className="flex size-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary"
        >
          {initials(call.contactName)}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-semibold">
            {call.contactName || "Unknown contact"}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {[call.role, call.company].filter(Boolean).join(" · ") || "—"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {call.status && call.status.toLowerCase() !== "completed" && (
            <Pill className="bg-muted text-muted-foreground">
              {call.status}
            </Pill>
          )}
          {call.outcome && (
            <Pill className={outcomeTone(call.outcome)}>{call.outcome}</Pill>
          )}
        </div>
      </div>

      <div className="grid gap-3 px-4 py-3">
        <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
          {call.startedAt && (
            <div className="flex items-center gap-1.5">
              <Clock className="size-3.5" aria-hidden />
              <dt className="sr-only">Called</dt>
              <dd>{dateTime.format(new Date(call.startedAt))}</dd>
            </div>
          )}
          {call.ringSeconds != null && (
            <div className="flex gap-1">
              <dt>Rang</dt>
              <dd className="font-medium text-foreground tabular-nums">
                {duration(call.ringSeconds)}
              </dd>
            </div>
          )}
          <div className="flex gap-1">
            <dt>Talked</dt>
            <dd className="font-medium text-foreground tabular-nums">
              {duration(call.durationSeconds)}
            </dd>
          </div>
        </dl>

        {call.error && (
          <p role="alert" className="text-destructive">
            {call.error}
          </p>
        )}
        {call.summary && <p className="leading-relaxed">{call.summary}</p>}

        {call.extracted.length > 0 && (
          <dl className="grid gap-2 rounded-lg bg-emerald-500/10 p-3">
            {call.extracted.map((entry) => (
              <div key={entry.label}>
                <dt className="text-xs font-semibold text-emerald-900">
                  {entry.label}
                </dt>
                <dd className="leading-relaxed break-words">{entry.value}</dd>
              </div>
            ))}
          </dl>
        )}

        {call.transcript.length > 0 && (
          <details className="rounded-lg border">
            <summary className="cursor-pointer px-3 py-2 font-medium">
              Read the conversation · {number.format(call.transcript.length)}{" "}
              turn
              {call.transcript.length === 1 ? "" : "s"}
            </summary>
            <ol className="grid max-h-96 gap-2 overflow-y-auto border-t p-3">
              {call.transcript.map((turn, i) => (
                <li
                  key={i}
                  className={cn(
                    "max-w-[85%] rounded-lg px-3 py-2",
                    turn.speaker === "agent"
                      ? "bg-muted"
                      : "justify-self-end bg-primary/10",
                  )}
                >
                  <p className="text-xs font-semibold text-muted-foreground">
                    {turn.speaker === "agent" ? "AI caller" : "Prospect"}
                  </p>
                  <p className="leading-relaxed break-words">{turn.text}</p>
                </li>
              ))}
            </ol>
          </details>
        )}
      </div>
    </li>
  );
}

/**
 * Live progress while a campaign is still dialing, from the Brain's latest poll snapshot. Server-rendered,
 * so it shows the state as of this page load — refresh to update.
 */
function LiveProgress({ live, summary }: { live: CallProgress; summary: LiveCallSummary }) {
  const recent = live.calls.slice(-8).reverse();
  return (
    <div className="grid gap-3 rounded-xl border border-primary/30 bg-primary/5 p-4">
      <SectionTitle
        aside={<span className="text-xs font-normal text-muted-foreground">as of now · refresh to update</span>}
      >
        Live progress
      </SectionTitle>
      <div className="flex flex-wrap gap-1.5 text-sm">
        <Pill className="bg-primary/10 text-primary">{number.format(summary.total)} dialed</Pill>
        <Pill className="bg-emerald-500/15 text-emerald-800">{number.format(summary.completed)} finished</Pill>
        {summary.inProgress > 0 && (
          <Pill className="bg-amber-500/20 text-amber-900">{number.format(summary.inProgress)} in progress</Pill>
        )}
        {summary.outcomes.map((outcome) => (
          <Pill key={outcome.label} className={outcomeTone(outcome.label)}>
            {number.format(outcome.count)} {outcome.label.toLowerCase()}
          </Pill>
        ))}
      </div>
      {recent.length > 0 && (
        <ul className="grid gap-1.5">
          {recent.map((call) => (
            <li
              key={call.id}
              className="flex items-center justify-between gap-3 rounded-lg bg-background px-3 py-2 text-sm"
            >
              <span className="min-w-0 truncate">
                <span className="font-medium">{call.name || "Unnamed contact"}</span>
                {call.company && <span className="text-muted-foreground"> · {call.company}</span>}
              </span>
              <Pill className={outcomeTone(liveCallLabel(call))}>
                {liveCallLabel(call)}
              </Pill>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Fallback when the calling tool's own record cannot be found: the Brain's summary of each call. */
function SummaryTable({ outcomes }: { outcomes: CallOutcome[] }) {
  return (
    <div className="overflow-x-auto rounded-xl border">
      <table className="w-full min-w-[44rem] text-left text-sm">
        <thead className="bg-muted/60 text-xs text-muted-foreground">
          <tr>
            <th className="px-4 py-2.5 font-semibold">Contact</th>
            <th className="px-4 py-2.5 font-semibold">Outcome</th>
            <th className="px-4 py-2.5 text-right font-semibold">Length</th>
            <th className="px-4 py-2.5 font-semibold">Summary</th>
          </tr>
        </thead>
        <tbody>
          {outcomes.map((call, i) => (
            <tr key={i} className="border-t align-top">
              <td className="px-4 py-2.5">
                <p className="font-medium">{call.name || "—"}</p>
                {call.company && (
                  <p className="text-xs text-muted-foreground">
                    {call.company}
                  </p>
                )}
              </td>
              <td className="px-4 py-2.5">
                {call.outcome ? (
                  <Pill className={outcomeTone(call.outcome)}>
                    {call.outcome}
                  </Pill>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-4 py-2.5 text-right whitespace-nowrap tabular-nums">
                {duration(call.durationSeconds)}
              </td>
              <td className="px-4 py-2.5 leading-relaxed text-muted-foreground">
                {call.summary || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
