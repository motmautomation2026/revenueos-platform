import type { Metadata } from "next";
import { FileText, Info, MailCheck, Send, type LucideIcon } from "lucide-react";
import { loadEmailStats, type EmailStats } from "@/lib/brain/read";
import {
  EmptyTool,
  ExecutionEmpty,
  ExecutionHeader,
  InsightTileCard,
  Pill,
  SectionTitle,
  StageTrack,
  StatTile,
  StepsTimeline,
  TASK_STATUS,
  dateTime,
  number,
} from "@/components/brain/parts";
import { SenderCard } from "@/components/brain/sender-card";
import { loadSender } from "@/lib/brain/sender-store";
import { buildEmailReport } from "@/lib/brain/tools";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "Email" };

const TILE_ICONS: Record<string, LucideIcon> = { recipients: Send, accepted: MailCheck, samples: FileText };

export default async function EmailPage(props: ExecutionPageProps) {
  const { project, data, base } = await loadExecutionPage(props);
  const sender = await loadSender(project.id);
  const senderCard = sender.available ? <SenderCard projectId={project.id} view={sender.view} /> : null;

  // Before the first run there is still something to do here: say who the emails come from.
  if (data.state === "no_runs" && senderCard) {
    return (
      <div className="grid gap-6">
        <h2 className="text-xl font-semibold tracking-tight">Email</h2>
        {senderCard}
        <EmptyTool>No email campaign yet. It appears here once execution starts and the Brain reaches its email task.</EmptyTool>
      </div>
    );
  }
  if (data.state !== "ok") return <ExecutionEmpty data={data} />;

  const { view, runs, rows } = data;
  const report = buildEmailReport(rows);
  // What the email tool itself recorded after the Brain handed the campaign over (counts only).
  const stats = await loadEmailStats(report.campaigns.map((c) => c.campaignId));

  return (
    <div className="grid gap-8">
      <ExecutionHeader title="Email" view={view} runs={runs} basePath={`${base}/email`} />
      {senderCard}

      {report.campaigns.length === 0 && <EmptyTool>This month plan has no email task.</EmptyTool>}

      {report.campaigns.map((campaign, index) => {
        const status = TASK_STATUS[campaign.status];
        const sending = stats.get(campaign.campaignId);
        return (
          <section key={campaign.taskId} className="grid gap-6">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h3 className="text-lg font-semibold">
                Email campaign{report.campaigns.length > 1 ? ` ${index + 1}` : ""}
              </h3>
              <Pill className={status.tone}>{status.label}</Pill>
              <Pill className="bg-primary/10 text-primary">
                {campaign.type === "standard" ? "Standard" : "Personalised"}
              </Pill>
              <span className="text-sm text-muted-foreground">Task {campaign.taskId}</span>
            </div>
            <p className="text-sm text-muted-foreground">
              {campaign.type === "standard"
                ? "Every recipient gets the same emails; only their name and company change."
                : "Each recipient gets their own version, rewritten by AI from the emails below."}
              {campaign.typeNote && <span className="text-foreground"> {campaign.typeNote}</span>}
            </p>
            {campaign.lastError && (
              <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                {campaign.lastError}
              </p>
            )}

            <div className="grid gap-3">
              <SectionTitle>How far it got</SectionTitle>
              <StageTrack stages={campaign.stages} />
            </div>

            <div className="grid gap-3">
              <SectionTitle>Numbers</SectionTitle>
              <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
                {campaign.tiles.map((tile) => (
                  <InsightTileCard key={tile.key} tile={tile} icon={TILE_ICONS[tile.key] ?? Send} />
                ))}
              </div>
            </div>

            {sending && <Sending stats={sending} />}

            <div className="grid gap-3">
              <SectionTitle aside={campaign.samples.length ? `${number.format(campaign.samples.length)} written` : undefined}>
                {campaign.type === "standard" ? "The emails, as the first recipient reads them" : "Sample emails"}
              </SectionTitle>
              {campaign.samples.length === 0 ? (
                <p className="text-sm text-muted-foreground">No emails have been written yet.</p>
              ) : (
                <ul className="grid gap-3 xl:grid-cols-2 2xl:grid-cols-3">
                  {campaign.samples.map((sample, i) => (
                    <li key={i} className="flex min-w-0 flex-col overflow-hidden rounded-xl border text-sm">
                      <div className="grid gap-0.5 border-b bg-muted/50 px-4 py-2.5">
                        {sample.to && <p className="text-xs text-muted-foreground">Written for {sample.to}</p>}
                        <p className="font-semibold break-words">{sample.subject || "(no subject)"}</p>
                      </div>
                      <p className="px-4 py-3 leading-relaxed break-words whitespace-pre-line">{sample.text}</p>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <details className="rounded-xl border text-sm">
              <summary className="cursor-pointer px-4 py-3 font-medium">Technical details</summary>
              <div className="grid gap-4 border-t px-4 py-4">
                <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-[max-content_1fr]">
                  {campaign.reference && (
                    <>
                      <dt className="text-muted-foreground">Campaign reference</dt>
                      <dd className="font-mono text-xs break-all">{campaign.reference}</dd>
                    </>
                  )}
                  {campaign.campaignStatus && (
                    <>
                      <dt className="text-muted-foreground">Status reported by the email tool</dt>
                      <dd>
                        {campaign.campaignStatus.toLowerCase()}{" "}
                        <span className="text-muted-foreground">(as recorded when the Brain last checked)</span>
                      </dd>
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

      {report.campaigns.some((c) => !stats.has(c.campaignId)) && (
        <div className="flex items-start gap-3 rounded-xl border bg-muted/40 p-4 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <p className="leading-relaxed text-muted-foreground">
            The email tool has no record for this campaign yet, so what happened after sending cannot be shown.
          </p>
        </div>
      )}
    </div>
  );
}

/** What the email tool recorded once the campaign was in its hands. Counts only. */
function Sending({ stats }: { stats: EmailStats }) {
  const percentSent = stats.messages ? Math.round((stats.sent / stats.messages) * 100) : null;
  const tracked = stats.events.length > 0 || stats.outcomes.length > 0;
  return (
    <div className="grid gap-3">
      <SectionTitle aside={stats.status ? `Email tool: ${stats.status.toLowerCase()}` : undefined}>
        What happened after sending started
      </SectionTitle>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Emails sent"
          value={number.format(stats.sent)}
          of={number.format(stats.messages)}
          percent={percentSent}
          note={
            stats.lastSentAt
              ? `Across ${number.format(stats.recipients)} recipient${stats.recipients === 1 ? "" : "s"} · last sent ${dateTime.format(new Date(stats.lastSentAt))}`
              : "Nothing has gone out yet"
          }
        />
        <StatTile
          label="Still to send"
          value={number.format(stats.waiting)}
          note={
            stats.waiting > 0
              ? stats.nextDueAt
                ? `Later emails in the sequence · next due ${dateTime.format(new Date(stats.nextDueAt))}`
                : "Later emails in the sequence"
              : "Nothing is waiting"
          }
        />
        <StatTile
          label="Failed to send"
          value={number.format(stats.failed)}
          attention={stats.failed > 0}
          note={stats.failureReasons.map((r) => `${r.label}: ${number.format(r.count)}`).join(" · ") || "No failures"}
        />
        <StatTile
          label="Replies received"
          value={number.format(stats.replies)}
          note={stats.replies > 0 ? "Read them in the email tool" : "No replies recorded"}
        />
      </div>

      {tracked ? (
        <ul className="flex flex-wrap gap-2">
          {[...stats.events, ...stats.outcomes].map((entry) => (
            <li key={entry.label} className="rounded-lg border px-3 py-2 text-sm">
              <span className="font-semibold tabular-nums">{number.format(entry.count)}</span>{" "}
              <span className="text-muted-foreground">{entry.label.toLowerCase()}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          No opens, clicks or bounces have been recorded for this campaign.
        </p>
      )}
    </div>
  );
}
