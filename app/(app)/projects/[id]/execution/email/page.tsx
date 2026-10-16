import type { Metadata } from "next";
import Link from "next/link";
import {
  ArrowRight,
  CalendarClock,
  FileText,
  Info,
  ListOrdered,
  MailCheck,
  MousePointerClick,
  Send,
  TriangleAlert,
  Users,
  type LucideIcon,
} from "lucide-react";
import { loadEmailPlans, loadEmailStats, type EmailStats } from "@/lib/brain/read";
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
import { buildEmailReport, type EmailCampaign, type EmailPlan, type EmailSample } from "@/lib/brain/tools";
import { cn } from "@/lib/utils";
import { loadExecutionPage, type ExecutionPageProps } from "../load";

export const metadata: Metadata = { title: "Email" };

const TILE_ICONS: Record<string, LucideIcon> = { recipients: Send, accepted: MailCheck, samples: FileText };
const WEEKDAYS = ["", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export default async function EmailPage(props: ExecutionPageProps) {
  const { project, data, base, query } = await loadExecutionPage(props);
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
  // What the email tool itself recorded after the Brain handed the campaign over (counts only),
  // and the campaign as the Brain handed it over (sequence, schedule, recipients).
  const [stats, plans] = await Promise.all([
    loadEmailStats(report.campaigns.map((c) => c.campaignId)),
    loadEmailPlans(report.campaigns.map((c) => c.taskRowId)),
  ]);
  const dashboard = query.run ? `${base}?run=${encodeURIComponent(query.run)}` : base;

  return (
    <div className="grid gap-8">
      <ExecutionHeader title="Email" view={view} runs={runs} basePath={`${base}/email`} />
      {senderCard}

      {report.campaigns.length === 0 && <EmptyTool>This month plan has no email task.</EmptyTool>}

      {report.campaigns.map((campaign, index) => {
        const status = TASK_STATUS[campaign.status];
        const sending = stats.get(campaign.campaignId);
        const plan = plans.get(campaign.taskRowId) ?? null;
        return (
          <section key={campaign.taskId} className="grid gap-6">
            <div className="grid gap-1.5">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h3 className="text-lg font-semibold">
                  {plan?.name || `Email campaign${report.campaigns.length > 1 ? ` ${index + 1}` : ""}`}
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
                  : "Each recipient gets their own version, rewritten by AI for them."}
                {campaign.typeNote && <span className="text-foreground"> {campaign.typeNote}</span>}
              </p>
            </div>

            <NextStep campaign={campaign} dashboard={dashboard} />

            {campaign.lastError && (
              <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
                {campaign.lastError}
              </p>
            )}

            {plan && <AtAGlance plan={plan} recipients={campaign.recipients} />}

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

            <Samples campaign={campaign} plan={plan} />

            {plan && plan.recipients.length > 0 && <Recipients plan={plan} />}

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

      {report.campaigns.some((c) => c.campaignId && !stats.has(c.campaignId)) && (
        <div className="flex items-start gap-3 rounded-xl border bg-muted/40 p-4 text-sm">
          <Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
          <p className="leading-relaxed text-muted-foreground">
            The email tool has no sending record for this campaign yet, so what happened after sending cannot be shown.
          </p>
        </div>
      )}
    </div>
  );
}

/** What a person has to do now, if anything: the answer itself is given on the Dashboard. */
function NextStep({ campaign, dashboard }: { campaign: EmailCampaign; dashboard: string }) {
  if (!campaign.approval?.open) return null;
  const written = campaign.samples.filter((s) => s.written).length;
  const incomplete = campaign.samplesStatus === "failed" || written < campaign.samples.length;
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-4 gap-y-2 rounded-xl border p-4 text-sm",
        incomplete ? "border-destructive/30 bg-destructive/5" : "border-amber-500/40 bg-amber-500/10"
      )}
    >
      <TriangleAlert className={cn("size-5 shrink-0", incomplete ? "text-destructive" : "text-amber-700")} aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="font-semibold">
          {incomplete
            ? `${number.format(written)} of ${number.format(campaign.samples.length)} sample emails were written`
            : "The sample emails are waiting for your approval"}
        </p>
        <p className="text-muted-foreground">
          {incomplete
            ? "They cannot be approved until every one is written. Ask for changes on the Dashboard to have them written again."
            : "Nothing is sent until you approve them on the Dashboard."}
          {campaign.samplesVersion ? ` Version ${campaign.samplesVersion}.` : ""}
        </p>
      </div>
      <Link
        href={dashboard}
        className="inline-flex items-center gap-1 rounded-lg bg-primary px-3 py-1.5 font-medium text-primary-foreground hover:bg-primary/90"
      >
        Go to the Dashboard <ArrowRight className="size-4" aria-hidden />
      </Link>
    </div>
  );
}

function weekdaysLabel(days: number[]) {
  const sorted = [...new Set(days)].filter((d) => d >= 1 && d <= 7).sort((a, b) => a - b);
  if (!sorted.length) return "";
  const consecutive = sorted.every((d, i) => i === 0 || d === sorted[i - 1] + 1);
  return consecutive && sorted.length > 2
    ? `${WEEKDAYS[sorted[0]]}–${WEEKDAYS[sorted[sorted.length - 1]]}`
    : sorted.map((d) => WEEKDAYS[d]).join(", ");
}

/** Day each email goes out, counted from the first one (each waits from the one before). */
const sendDays = (plan: EmailPlan) =>
  plan.emails.reduce<number[]>((days, email, i) => [...days, (i ? days[i - 1] : 0) + (i ? email.waitDays : 0)], []);

function AtAGlance({ plan, recipients }: { plan: EmailPlan; recipients: number | null }) {
  const days = sendDays(plan);
  const people = recipients ?? plan.recipients.length;
  const facts: { icon: LucideIcon; label: string; value: string; note?: string }[] = [
    { icon: Users, label: "Recipients", value: number.format(people), note: people === 1 ? "person" : "people" },
    {
      icon: ListOrdered,
      label: "Sequence",
      value: `${plan.emails.length} email${plan.emails.length === 1 ? "" : "s"}`,
      note: days.length > 1 ? `over ${days[days.length - 1]} days` : undefined,
    },
    {
      icon: CalendarClock,
      label: "Sending",
      value: [weekdaysLabel(plan.weekdays), plan.windowStart && plan.windowEnd ? `${plan.windowStart}–${plan.windowEnd}` : ""]
        .filter(Boolean)
        .join(" · ") || "Not given",
      note: [plan.dailyLimit ? `up to ${number.format(plan.dailyLimit)} a day` : "", plan.timezone].filter(Boolean).join(" · "),
    },
    { icon: MousePointerClick, label: "Asks the reader", value: plan.cta || "Not given", note: plan.tone || undefined },
  ];
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {facts.map((fact) => (
        <div key={fact.label} className="grid content-start gap-1 rounded-xl border p-4">
          <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
            <fact.icon className="size-3.5" aria-hidden />
            {fact.label}
          </p>
          <p className="leading-snug font-semibold">{fact.value}</p>
          {fact.note && <p className="text-xs text-muted-foreground">{fact.note}</p>}
        </div>
      ))}
    </div>
  );
}

function EmailFrame({
  badge,
  subject,
  preheader,
  children,
  muted,
}: {
  badge: string;
  subject: React.ReactNode;
  preheader?: string;
  children: React.ReactNode;
  muted?: boolean;
}) {
  return (
    <li className={cn("flex min-w-0 flex-col overflow-hidden rounded-xl border text-sm", muted && "border-dashed")}>
      <div className="grid gap-1 border-b bg-muted/50 px-4 py-2.5">
        <p className="text-xs font-medium text-muted-foreground">{badge}</p>
        <p className="font-semibold break-words">{subject}</p>
        {preheader && <p className="text-xs break-words text-muted-foreground italic">{preheader}</p>}
      </div>
      <div className="px-4 py-3 leading-relaxed break-words whitespace-pre-line">{children}</div>
    </li>
  );
}

const dayLabel = (days: number[], i: number) =>
  i === 0 ? "Sent first" : `Day ${days[i]} · ${days[i] - days[i - 1]} day${days[i] - days[i - 1] === 1 ? "" : "s"} after email ${i}`;

/** Samples grouped per recipient, in sequence order; an email the tool could not write is shown in its place. */
function Samples({ campaign, plan }: { campaign: EmailCampaign; plan: EmailPlan | null }) {
  const written = campaign.samples.filter((s) => s.written).length;
  const groups = new Map<string, EmailSample[]>();
  for (const sample of campaign.samples) groups.set(sample.to, [...(groups.get(sample.to) ?? []), sample]);
  const days = plan ? sendDays(plan) : [];

  return (
    <div className="grid gap-3">
      <SectionTitle
        aside={
          campaign.samples.length
            ? `${number.format(written)} of ${number.format(campaign.samples.length)} written${campaign.samplesVersion ? ` · version ${campaign.samplesVersion}` : ""}`
            : undefined
        }
      >
        {campaign.type === "standard" ? "The emails, as the first recipient reads them" : "Sample emails, per recipient"}
      </SectionTitle>
      {campaign.samples.length === 0 ? (
        <p className="text-sm text-muted-foreground">No emails have been written yet.</p>
      ) : (
        <div className="grid gap-3">
          {[...groups.entries()].map(([to, samples], g) => {
            const missing = samples.filter((s) => !s.written).length;
            return (
              <details key={to || g} className="group rounded-xl border" open={g === 0 || missing > 0}>
                <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
                  <span className="font-medium">{to || "Recipient"}</span>
                  <span className={cn("text-xs", missing ? "font-semibold text-destructive" : "text-muted-foreground")}>
                    {missing
                      ? `${missing} of ${samples.length} not written`
                      : `${samples.length} email${samples.length === 1 ? "" : "s"}`}
                  </span>
                </summary>
                <ol className="grid gap-3 border-t p-3 xl:grid-cols-3">
                  {[...samples]
                    .sort((a, b) => (a.email ?? 0) - (b.email ?? 0))
                    .map((sample, i) => {
                      const n = sample.email ?? i + 1;
                      const when = days.length >= n ? ` · ${dayLabel(days, n - 1)}` : "";
                      return sample.written ? (
                        <EmailFrame key={i} badge={`Email ${n}${when}`} subject={sample.subject || "(no subject)"} preheader={sample.preheader}>
                          {sample.text}
                        </EmailFrame>
                      ) : (
                        <EmailFrame key={i} badge={`Email ${n}${when}`} subject="Not written" muted>
                          <span className="text-muted-foreground">
                            The email tool could not write this one. Ask for changes on the Dashboard to have it written again.
                          </span>
                        </EmailFrame>
                      );
                    })}
                </ol>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}

function Recipients({ plan }: { plan: EmailPlan }) {
  return (
    <details className="rounded-xl border text-sm">
      <summary className="cursor-pointer px-4 py-3 font-medium">
        Who it goes to ({number.format(plan.recipients.length)})
      </summary>
      <div className="overflow-x-auto border-t">
        <table className="w-full text-left">
          <thead className="bg-muted/50 text-xs text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Name</th>
              <th className="px-4 py-2 font-medium">Role</th>
              <th className="px-4 py-2 font-medium">Company</th>
              <th className="px-4 py-2 font-medium">Email</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {plan.recipients.map((r, i) => (
              <tr key={i}>
                <td className="px-4 py-2 font-medium">{r.name || "—"}</td>
                <td className="px-4 py-2 text-muted-foreground">{r.title || "—"}</td>
                <td className="px-4 py-2">{r.company || "—"}</td>
                <td className="px-4 py-2 break-all text-muted-foreground">{r.email || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
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
