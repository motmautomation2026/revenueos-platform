// Per-tool reports built from the Brain's rows: data research (QuickICP), email, calls
// (Quickhowl) and the activity log. Pure functions over real rows; nothing is invented, and
// a value the Brain has not recorded is simply left out.

import {
  decisionTitle,
  isToolAgent,
  type BrainDecisionRow,
  type BrainTaskRow,
  type BrainToolCallRow,
  type TaskStatus,
  type ToolCallView,
} from "./view";

type Json = Record<string, unknown>;
const obj = (v: unknown): Json => (v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {});
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const str = (v: unknown): string => (v == null ? "" : typeof v === "string" ? v.trim() : String(v));
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const ids = (v: unknown): number[] => list(v).filter((n): n is number => typeof n === "number");
const words = (key: string) => {
  const text = key.replace(/_/g, " ");
  return text ? text[0].toUpperCase() + text.slice(1) : "";
};

export type RunRows = {
  tasks: BrainTaskRow[];
  decisions: BrainDecisionRow[];
  toolCalls: BrainToolCallRow[];
};

export type Approval = { answer: string; by: string; reason: string; at: string | null; open: boolean };

function stepsOf(task: BrainTaskRow, toolCalls: BrainToolCallRow[]): ToolCallView[] {
  return toolCalls
    .filter((c) => c.task_row_id === task.id)
    .map((c) => ({ id: c.id, tool: c.tool, step: c.step, paid: c.paid, status: c.status, error: c.error, startedAt: c.started_at }));
}

function approvalOf(task: BrainTaskRow, kind: string, decisions: BrainDecisionRow[]): { row: BrainDecisionRow; approval: Approval } | null {
  const row = [...decisions].reverse().find((d) => d.task_row_id === task.id && d.kind === kind);
  if (!row) return null;
  return {
    row,
    approval: {
      answer: row.answer ?? "",
      by: row.decided_by ?? "",
      reason: row.reason ?? "",
      at: row.answered_at,
      open: row.status === "open",
    },
  };
}

/** The numbers behind the Brain's latest review of this run, if it has reviewed it. */
function reviewNumbers(decisions: BrainDecisionRow[]): Json {
  const row = [...decisions].reverse().find((d) => d.kind === "run_review");
  return obj(obj(row?.question).numbers);
}

// ---------------------------------------------------------------------------
// Data research (QuickICP)
// ---------------------------------------------------------------------------

export type ResearchTask = {
  taskId: string;
  status: TaskStatus;
  achieved: number | null;
  target: number | null;
  planTarget: number | null;
  reused: number | null;
  bought: number | null;
  searches: number | null;
  shortfall: number | null;
  note: string;
};

export type QualitySignal = { label: string; valuePct: number; thresholdPct: number | null; flagged: boolean };

export type ResearchReport = {
  accounts: ResearchTask | null;
  contacts: (ResearchTask & { withEmail: number | null; mobile: number | null; landline: number | null }) | null;
  judged: { companies: number | null; people: number | null };
  quality: QualitySignal[];
  /** Rows this month plan actually used, by id in the QuickICP tables. */
  usedCompanyIds: number[];
  usedPeopleIds: number[];
  phoneTypes: Record<string, string>;
};

const QUALITY_LABELS: Record<string, string> = {
  companies_failed_judge_pct: "Companies that failed the fit check",
  people_failed_judge_pct: "Contacts that failed the fit check",
  without_email_pct: "Contacts without an email",
  landline_pct: "Contacts with only a landline",
};

function researchTask(task: BrainTaskRow): ResearchTask {
  const r = obj(task.result);
  return {
    taskId: task.task_id,
    status: task.status,
    achieved: task.achieved_number ?? num(r.achieved),
    target: num(r.target) ?? task.target_number,
    planTarget: task.target_number,
    reused: num(r.reused),
    bought: num(r.bought),
    searches: num(r.searches) ?? num(r.people_searches),
    shortfall: num(r.shortfall),
    note: str(r.note),
  };
}

export function buildResearchReport({ tasks, decisions }: RunRows): ResearchReport {
  const accountsTask = tasks.find((t) => t.output_type === "accounts");
  const contactsTask = tasks.find((t) => t.output_type === "contacts");
  const contactsResult = obj(contactsTask?.result);

  const quality = obj(reviewNumbers(decisions).data_quality);
  const thresholds = obj(quality.thresholds_pct);
  const flagged = new Set(list(quality.flagged).map((f) => str(obj(f).signal)));

  return {
    accounts: accountsTask ? researchTask(accountsTask) : null,
    contacts: contactsTask
      ? {
          ...researchTask(contactsTask),
          withEmail: num(contactsResult.with_email),
          mobile: num(contactsResult.mobile),
          landline: num(contactsResult.landline),
        }
      : null,
    judged: { companies: num(quality.companies_judged), people: num(quality.people_judged) },
    quality: Object.entries(QUALITY_LABELS)
      .filter(([key]) => num(quality[key]) != null)
      .map(([key, label]) => ({
        label,
        valuePct: num(quality[key])!,
        thresholdPct: num(thresholds[key]),
        flagged: flagged.has(key),
      })),
    usedCompanyIds: ids(obj(accountsTask?.result).company_ids),
    usedPeopleIds: ids(contactsResult.people_ids),
    phoneTypes: Object.fromEntries(
      Object.entries(obj(contactsResult.phone_types)).map(([id, type]) => [id, str(type)])
    ),
  };
}

// ---------------------------------------------------------------------------
// Email
// ---------------------------------------------------------------------------

/** One milestone in a campaign's life, in order. */
export type Stage = { label: string; state: "done" | "waiting" | "refused" | "todo"; at: string | null; note: string };

/** A number with what it is measured against, a one-word verdict and plain sentences explaining it. */
export type InsightTile = {
  key: string;
  label: string;
  value: number | null;
  of: number | null;
  ofLabel: string;
  percent: number | null;
  verdict: { text: string; tone: "good" | "attention" | "neutral" } | null;
  lines: string[];
};

const plain = new Intl.NumberFormat("en-IN");
const share = (value: number | null, of: number | null) =>
  value != null && of ? Math.min(100, Math.round((value / of) * 100)) : null;

/**
 * The email tool works in this order: the Brain prepares the campaign, stores it as a draft,
 * the tool writes sample emails, a person approves them, and only then does sending start.
 */
function emailStages(steps: ToolCallView[], approval: Approval | null): Stage[] {
  const last = (name: string) => [...steps].reverse().find((step) => step.step === name && step.status === "done");
  const failed = (name: string) => steps.find((step) => step.step === name && step.status !== "done" && step.status !== "running");
  const stage = (label: string, name: string): Stage => {
    const done = last(name);
    const problem = failed(name);
    return {
      label,
      state: done ? "done" : problem ? "refused" : "todo",
      at: done?.startedAt ?? problem?.startedAt ?? null,
      note: !done && problem ? (problem.error ?? "This step did not finish.") : "",
    };
  };

  const approved: Stage = !approval
    ? { label: "Approved by a person", state: "todo", at: null, note: "" }
    : approval.open
      ? { label: "Approved by a person", state: "waiting", at: null, note: "Waiting for someone to approve the samples." }
      : approval.answer === "yes"
        ? { label: "Approved by a person", state: "done", at: approval.at, note: approval.reason }
        : { label: "Not approved", state: "refused", at: approval.at, note: approval.reason };

  return [
    stage("Campaign prepared", "request"),
    stage("Saved in the email tool as a draft", "launch"),
    stage("Sample emails written", "samples"),
    approved,
    stage("Sending started", "approve"),
  ];
}

function emailTiles(c: {
  recipients: number | null;
  rejected: number | null;
  target: number | null;
  planTarget: number | null;
  testMode: boolean;
  samples: number;
  approval: Approval | null;
}): InsightTile[] {
  const goal = c.planTarget ?? c.target;
  const capped = c.planTarget != null && c.target != null && c.target < c.planTarget;
  const recipientLines: string[] = [];
  if (capped) recipientLines.push(`Test run: capped at ${plain.format(c.target!)} recipient${c.target === 1 ? "" : "s"}.`);
  if (c.testMode) recipientLines.push("Sent only to MOTM team inboxes, not to prospects.");

  const tiles: InsightTile[] = [
    {
      key: "recipients",
      label: "Recipients",
      value: c.recipients,
      of: goal,
      ofLabel: "planned",
      percent: share(c.recipients, goal),
      verdict:
        c.recipients == null || goal == null
          ? null
          : c.recipients >= goal
            ? { text: "Target met", tone: "good" }
            : capped && c.recipients >= c.target!
              ? { text: "Test cap reached", tone: "neutral" }
              : { text: `${plain.format(goal - c.recipients)} short`, tone: "attention" },
      lines: recipientLines,
    },
  ];

  if (c.recipients != null && c.rejected != null) {
    const total = c.recipients + c.rejected;
    tiles.push({
      key: "accepted",
      label: "Accepted by the email tool",
      value: c.recipients,
      of: total,
      ofLabel: "submitted",
      percent: share(c.recipients, total),
      verdict: c.rejected > 0 ? { text: `${plain.format(c.rejected)} rejected`, tone: "attention" } : { text: "All accepted", tone: "good" },
      lines: [
        c.rejected > 0
          ? `${plain.format(c.rejected)} address${c.rejected === 1 ? " was" : "es were"} rejected before sending.`
          : "No address was rejected.",
      ],
    });
  }

  const approved = !!c.approval && !c.approval.open && c.approval.answer === "yes";
  tiles.push({
    key: "samples",
    label: "Sample emails",
    value: c.samples,
    of: null,
    ofLabel: "",
    percent: null,
    verdict: !c.samples
      ? null
      : approved
        ? { text: "Approved", tone: "good" }
        : c.approval?.open
          ? { text: "Waiting for approval", tone: "attention" }
          : c.approval
            ? { text: "Not approved", tone: "attention" }
            : null,
    lines: [c.samples ? "Written by the email tool and shown to a person before anything is sent." : "No samples have been written yet."],
  });
  return tiles;
}

/** One sample email; `written` is false when the tool returned that email empty (it failed). */
export type EmailSample = {
  to: string;
  /** 1, 2, 3...: its place in the sequence, when the Brain recorded it. */
  email: number | null;
  subject: string;
  preheader: string;
  text: string;
  written: boolean;
};

/** The campaign as the Brain handed it to the email tool (its 'request' step): no addresses of the sender, no secrets. */
export type EmailPlan = {
  name: string;
  description: string;
  timezone: string;
  /** ISO weekdays, 1 = Monday. */
  weekdays: number[];
  dailyLimit: number | null;
  windowStart: string;
  windowEnd: string;
  cta: string;
  tone: string;
  /** The base sequence the samples are written from; waitDays counts from the email before. */
  emails: { subject: string; preheader: string; text: string; waitDays: number }[];
  recipients: { name: string; title: string; company: string; email: string }[];
};

/** Paragraphs and line breaks kept, every other tag dropped, the few entities the Brain writes decoded. */
export function htmlToText(html: string): string {
  return html
    .replace(/<br\s*\/?>/gi, "\n")
    .replace(/<\/p>\s*<p[^>]*>/gi, "\n\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&zwnj;/g, "")
    .replace(/&amp;/g, "&")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&#39;|&apos;/g, "'")
    .replace(/&quot;/g, '"')
    .trim();
}

/** The plan out of a stored 'request' tool call (its output column). Only the fields named here are read. */
export function emailPlanOf(output: unknown): EmailPlan | null {
  const body = obj(obj(output).body);
  if (!Object.keys(body).length) return null;
  const campaign = obj(body.campaign);
  const schedule = obj(body.schedule);
  const objective = obj(body.objective);
  const time = (v: unknown) => str(v).slice(0, 5);
  return {
    name: str(campaign.name),
    description: str(campaign.description),
    timezone: str(schedule.timezone),
    weekdays: ids(schedule.weekdays),
    dailyLimit: num(schedule.daily_limit),
    windowStart: time(schedule.window_start_local),
    windowEnd: time(schedule.window_end_local),
    cta: str(objective.cta),
    tone: str(objective.tone),
    emails: list(body.emails)
      .map(obj)
      .map((e) => ({
        subject: str(e.subject),
        preheader: str(e.preheader),
        text: htmlToText(str(e.body_html) || str(e.body_text)),
        waitDays: num(e.wait_days_before) ?? 0,
      })),
    recipients: list(body.recipients)
      .map(obj)
      .map((r) => ({
        name: [str(r.first_name), str(r.last_name)].filter(Boolean).join(" ") || str(r.name),
        title: str(r.title),
        company: str(r.company),
        email: str(r.email),
      })),
  };
}

export type EmailCampaign = {
  taskId: string;
  /** The Brain's row id of the task, to find its plan (loadEmailPlans). */
  taskRowId: number;
  /** The campaign's id in the email tool, when the Brain recorded one. */
  campaignId: string;
  status: TaskStatus;
  reference: string;
  campaignStatus: string;
  /** Personalised: AI rewrites each email for its recipient. Standard: everyone gets the same email. */
  type: "personalised" | "standard";
  /** Why the Brain chose that type, when there is something to say. */
  typeNote: string;
  recipients: number | null;
  rejected: number | null;
  achieved: number | null;
  target: number | null;
  planTarget: number | null;
  testMode: boolean;
  note: string;
  lastError: string | null;
  samples: EmailSample[];
  /** The email tool's state of the latest samples: ready, failed (some not written), stale... */
  samplesStatus: string;
  /** Which version of the samples (1 = first, +1 per round of changes). */
  samplesVersion: number | null;
  approval: Approval | null;
  steps: ToolCallView[];
  stages: Stage[];
  tiles: InsightTile[];
};

export type EmailReport = { campaigns: EmailCampaign[]; resultsNote: string };

export function buildEmailReport({ tasks, decisions, toolCalls }: RunRows): EmailReport {
  const campaigns = tasks
    .filter((t) => t.output_type === "emails")
    .map((task): EmailCampaign => {
      const r = obj(task.result);
      const approved = approvalOf(task, "email_approve", decisions);
      const steps = stepsOf(task, toolCalls);
      const approval = approved?.approval ?? null;
      const question = obj(approved?.row.question);
      const samples = list(question.samples)
        .map(obj)
        .map(
          (s): EmailSample => ({
            to: str(s.for) || (typeof s.email === "string" ? str(s.email) : ""),
            email: num(s.email),
            subject: str(s.subject),
            preheader: str(s.preheader),
            text: str(s.text),
            written: !!(str(s.subject) || str(s.text)),
          })
        );
      const written = samples.filter((s) => s.written).length;
      const facts = {
        recipients: num(r.recipients),
        rejected: num(r.rejected),
        target: num(r.target) ?? task.target_number,
        planTarget: task.target_number,
        testMode: r.test_mode === true,
      };
      return {
        taskId: task.task_id,
        taskRowId: task.id,
        status: task.status,
        campaignId: str(r.campaign_id),
        reference: str(r.reference) || str(obj(approved?.row.question).reference),
        campaignStatus: str(r.campaign_status),
        // Recorded by the Brain with the approval question, and again with the result.
        type: (str(r.campaign_type) || str(obj(approved?.row.question).campaign_type)) === "STANDARD" ? "standard" : "personalised",
        typeNote: str(r.type_note) || str(obj(approved?.row.question).type_note),
        ...facts,
        achieved: task.achieved_number ?? num(r.achieved),
        note: str(r.note),
        lastError: task.last_error,
        samples,
        samplesStatus: str(question.samples_status),
        samplesVersion: num(question.round),
        approval,
        steps,
        stages: emailStages(steps, approval),
        tiles: emailTiles({ ...facts, samples: written, approval }),
      };
    });

  const note = reviewNumbers(decisions).email_results;
  return { campaigns, resultsNote: typeof note === "string" ? note : "" };
}

// ---------------------------------------------------------------------------
// Calls (Quickhowl)
// ---------------------------------------------------------------------------

export type CallOutcome = {
  name: string;
  company: string;
  outcome: string;
  status: string;
  durationSeconds: number | null;
  summary: string;
};

export type CallCampaign = {
  taskId: string;
  status: TaskStatus;
  reference: string;
  achieved: number | null;
  target: number | null;
  planTarget: number | null;
  testMode: boolean;
  note: string;
  lastError: string | null;
  outcomes: CallOutcome[];
  outcomeCounts: { label: string; count: number }[];
  skippedLandlines: { name: string; phoneType: string }[];
  rejectedDoNotCall: number | null;
  rejectedByTool: number | null;
  dnd: string;
  approval: Approval | null;
  /** How many contacts the person was asked to approve calling. */
  contactsApproved: number;
  steps: ToolCallView[];
  stages: Stage[];
};

export type CallsReport = {
  campaigns: CallCampaign[];
  objections: { counts: { label: string; count: number }[]; quotes: { name: string; label: string; quote: string; taskId: string }[] };
};

const counts = (value: unknown) =>
  Object.entries(obj(value))
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .map(([key, count]) => ({ label: words(key), count }))
    .sort((a, b) => b.count - a.count);

export function buildCallsReport({ tasks, decisions, toolCalls }: RunRows): CallsReport {
  const campaigns = tasks
    .filter((t) => t.output_type === "calls")
    .map((task): CallCampaign => {
      const r = obj(task.result);
      const approved = approvalOf(task, "call_start", decisions);
      return {
        taskId: task.task_id,
        status: task.status,
        reference: str(r.reference) || str(obj(approved?.row.question).reference),
        achieved: task.achieved_number ?? num(r.achieved),
        target: num(r.target) ?? task.target_number,
        planTarget: task.target_number,
        testMode: r.test_mode === true,
        note: str(r.note),
        lastError: task.last_error,
        outcomes: list(r.outcomes)
          .map(obj)
          .map((o) => ({
            name: str(o.name),
            company: str(o.company),
            outcome: words(str(o.outcome)),
            status: words(str(o.status)),
            durationSeconds: num(o.duration_seconds),
            summary: str(o.summary),
          })),
        outcomeCounts: counts(r.outcome_counts),
        skippedLandlines: list(r.skipped_landlines)
          .map(obj)
          .map((s) => ({ name: str(s.name), phoneType: str(s.phone_type) })),
        rejectedDoNotCall: num(r.rejected_do_not_call),
        rejectedByTool: num(r.rejected_by_quickhowl),
        dnd: str(r.dnd_confirmation),
        approval: approved?.approval ?? null,
        contactsApproved: list(obj(approved?.row.question).contacts).length,
        steps: stepsOf(task, toolCalls),
        stages: callStages(stepsOf(task, toolCalls), approved?.approval ?? null, task),
      };
    });

  const objections = obj(reviewNumbers(decisions).objections);
  return {
    campaigns,
    objections: {
      counts: counts(objections.counts),
      quotes: list(objections.quotes)
        .map(obj)
        .map((q) => ({ name: str(q.name), label: words(str(q.label)), quote: str(q.quote), taskId: str(q.task_id) }))
        .filter((q) => q.quote),
    },
  };
}

// ---------------------------------------------------------------------------
// Calls: stages and result tiles
// ---------------------------------------------------------------------------

/**
 * A calling campaign goes: the Brain picks the contacts, saves the campaign in Quickhowl,
 * a person approves the list, calling starts, and the Brain polls until the calls are finished.
 */
function callStages(steps: ToolCallView[], approval: Approval | null, task: BrainTaskRow): Stage[] {
  const last = (name: string) => [...steps].reverse().find((step) => step.step === name && step.status === "done");
  const failed = (name: string) => steps.find((step) => step.step === name && step.status !== "done" && step.status !== "running");
  const stage = (label: string, name: string): Stage => {
    const done = last(name);
    const problem = failed(name);
    return {
      label,
      state: done ? "done" : problem ? "refused" : "todo",
      at: done?.startedAt ?? problem?.startedAt ?? null,
      note: !done && problem ? (problem.error ?? "This step did not finish.") : "",
    };
  };

  const approved: Stage = !approval
    ? { label: "Approved by a person", state: "todo", at: null, note: "" }
    : approval.open
      ? { label: "Approved by a person", state: "waiting", at: null, note: "Waiting for someone to approve the list." }
      : approval.answer === "yes"
        ? { label: "Approved by a person", state: "done", at: approval.at, note: "" }
        : { label: "Not approved", state: "refused", at: approval.at, note: approval.reason };

  const finished: Stage =
    task.status === "done"
      ? { label: "Calls finished", state: "done", at: task.finished_at ?? last("poll")?.startedAt ?? null, note: "" }
      : task.status === "failed" || task.status === "stopped"
        ? { label: "Stopped before finishing", state: "refused", at: task.finished_at, note: task.last_error ?? "" }
        : { label: "Calls finished", state: "todo", at: null, note: "" };

  return [
    stage("Contact list chosen", "contacts"),
    stage("Campaign saved in Quickhowl", "store"),
    approved,
    stage("Calling started", "start"),
    finished,
  ];
}

const minutes = (seconds: number) =>
  seconds < 60 ? `${seconds} seconds` : `${Math.floor(seconds / 60)} min ${seconds % 60} sec`;

/**
 * The tiles at the top of a calling campaign. `tool` is the calling tool's own record of the
 * campaign when it can be found; without it, interest cannot be counted and that tile says so.
 */
export function buildCallTiles(
  campaign: CallCampaign,
  tool: { interested: number | null; completedCalls: number | null; talkSeconds: number; calls: number } | null
): InsightTile[] {
  const goal = campaign.planTarget ?? campaign.target;
  const capped = campaign.planTarget != null && campaign.target != null && campaign.target < campaign.planTarget;
  const reached = campaign.achieved;

  const calledLines: string[] = [];
  if (capped) calledLines.push(`Test run: capped at ${plain.format(campaign.target!)} call${campaign.target === 1 ? "" : "s"}.`);
  if (campaign.testMode) calledLines.push("Calls went only to MOTM test numbers, not to prospects.");

  const tiles: InsightTile[] = [
    {
      key: "called",
      label: "Contacts called",
      value: reached,
      of: goal,
      ofLabel: "planned",
      percent: share(reached, goal),
      verdict:
        reached == null || goal == null
          ? null
          : reached >= goal
            ? { text: "Target met", tone: "good" }
            : capped && reached >= campaign.target!
              ? { text: "Test cap reached", tone: "neutral" }
              : { text: `${plain.format(goal - reached)} short`, tone: "attention" },
      lines: calledLines,
    },
  ];

  const answered = tool?.completedCalls ?? null;
  const interested = tool?.interested ?? null;
  const interestLines: string[] = [];
  if (!tool) interestLines.push("The calling tool has no record of this campaign, so interest cannot be counted.");
  else {
    if (interested != null && answered != null && answered > 0) {
      interestLines.push(
        interested === 0
          ? "Nobody showed interest on these calls."
          : `${plain.format(interested)} of ${plain.format(answered)} completed call${answered === 1 ? "" : "s"} showed interest.`
      );
    }
    if (tool.calls > 0) interestLines.push(`Total talk time ${minutes(tool.talkSeconds)}.`);
  }
  tiles.push({
    key: "interested",
    label: "Showed interest",
    value: interested,
    of: answered,
    ofLabel: "completed calls",
    percent: share(interested, answered),
    verdict:
      interested == null || !answered
        ? null
        : interested > 0
          ? { text: `${share(interested, answered)}% interested`, tone: "good" }
          : { text: "None yet", tone: "neutral" },
    lines: interestLines,
  });

  const landlines = campaign.skippedLandlines.length;
  const doNotCall = campaign.rejectedDoNotCall ?? 0;
  const byTool = campaign.rejectedByTool ?? 0;
  const notCalled = landlines + doNotCall + byTool;
  const reasons: string[] = [];
  if (landlines) reasons.push(`${plain.format(landlines)} ${landlines === 1 ? "has" : "have"} only a landline.`);
  if (doNotCall) reasons.push(`${plain.format(doNotCall)} ${doNotCall === 1 ? "is" : "are"} on the do-not-call list.`);
  if (byTool) reasons.push(`${plain.format(byTool)} ${byTool === 1 ? "was" : "were"} rejected by Quickhowl.`);
  tiles.push({
    key: "notCalled",
    label: "Left out before calling",
    value: notCalled,
    of: null,
    ofLabel: "",
    percent: null,
    verdict: notCalled > 0 ? { text: "Check the numbers", tone: "attention" } : { text: "None", tone: "good" },
    lines: reasons.length ? reasons : ["Everyone on the list could be called."],
  });
  return tiles;
}

// ---------------------------------------------------------------------------
// Activity log
// ---------------------------------------------------------------------------

export type ActivitySource = "QuickICP" | "Quickhowl" | "Email tool" | "Saved data" | "Person" | "Brain" | "Automatic";

export type ActivityEvent = {
  key: string;
  at: string;
  kind: "tool" | "question" | "answer";
  /** What happened, in plain words. */
  title: string;
  detail: string;
  taskId: string | null;
  tone: "normal" | "attention" | "problem";
  paid: boolean;
  /** Which tool did it, or who answered. */
  source: ActivitySource;
  /** For an answered question: what was answered. */
  answer: string | null;
  /** How many identical steps in a row this line stands for (the Brain polls for results). */
  times: number;
};

const TOOL_NAMES: Record<string, ActivitySource> = {
  quickicp: "QuickICP",
  quickhowl: "Quickhowl",
  email_tool: "Email tool",
  supabase: "Saved data",
};

/** The Brain's step names, said the way a person would. Unknown steps fall back to the raw name. */
const STEP_TITLES: Record<string, string> = {
  "quickicp:payload": "Prepared the company search",
  "quickicp:companies": "Searched for companies",
  "quickicp:titles": "Chose which job titles to look for",
  "quickicp:people": "Searched for decision-makers",
  "quickicp:email_plan": "Planned which emails to look up",
  "quickicp:emails": "Looked up email addresses",
  "quickicp:phone_plan": "Planned which phone numbers to look up",
  "quickicp:phones": "Looked up phone numbers",
  "supabase:reuse": "Reused data already held",
  "supabase:save": "Saved the results",
  "supabase:companies": "Loaded the companies to search in",
  "supabase:contacts": "Chose the contacts for outreach",
  "email_tool:request": "Prepared the email campaign",
  "email_tool:launch": "Saved the campaign as a draft",
  "email_tool:samples": "Checked for sample emails",
  "email_tool:approve": "Started sending",
  "quickhowl:request": "Prepared the call campaign",
  "quickhowl:store": "Saved the call campaign",
  "quickhowl:start": "Started calling",
  "quickhowl:poll": "Checked for call results",
};

const ANSWER_WORDS: Record<string, string> = {
  yes: "Approved",
  no: "Not approved",
  done: "Marked done",
  skip: "Skipped",
  stop: "Stopped",
  accept: "Accepted",
  reject: "Rejected",
  continue: "Continue as is",
  more: "Try for more",
  rerun: "Run again",
  retry: "Retry",
  saved: "Saved",
  ask_human: "Sent to a person",
};

/** Everything the Brain did and every question it asked or had answered, newest first. */
export function buildActivity({ tasks, decisions, toolCalls }: RunRows): ActivityEvent[] {
  const taskIdOf = new Map(tasks.map((t) => [t.id, t.task_id]));
  const events: ActivityEvent[] = [];

  for (const call of toolCalls) {
    const failed = call.status === "failed" || call.status === "unknown";
    events.push({
      key: `call-${call.id}`,
      at: call.started_at,
      kind: "tool",
      title: STEP_TITLES[`${call.tool}:${call.step}`] ?? words(call.step),
      detail:
        call.error ??
        (call.status === "running"
          ? "In progress"
          : call.status === "unknown"
            ? "It is not known whether this went through."
            : failed
              ? "This step failed."
              : ""),
      taskId: taskIdOf.get(call.task_row_id) ?? null,
      tone: failed ? "problem" : "normal",
      paid: call.paid,
      source: TOOL_NAMES[call.tool] ?? "Brain",
      answer: null,
      times: 1,
    });
  }

  for (const decision of decisions) {
    const taskId =
      (decision.task_row_id != null && taskIdOf.get(decision.task_row_id)) || str(obj(decision.question).task_id) || null;
    const title = decisionTitle(decision.kind);
    if (decision.status === "open") {
      events.push({
        key: `ask-${decision.id}`,
        at: decision.created_at,
        kind: "question",
        title,
        detail: "Waiting for a person to answer.",
        taskId,
        tone: "attention",
        paid: false,
        source: "Brain",
        answer: null,
        times: 1,
      });
    } else {
      const source: ActivitySource = decision.decided_by === "brain" ? "Brain" : decision.decided_by === "auto" ? "Automatic" : "Person";
      events.push({
        key: `answer-${decision.id}`,
        at: decision.answered_at ?? decision.created_at,
        kind: "answer",
        title,
        detail: decision.reason ?? "",
        taskId,
        tone: "normal",
        paid: false,
        source,
        answer: decision.answer ? (ANSWER_WORDS[decision.answer] ?? words(decision.answer)) : null,
        times: 1,
      });
    }
  }

  events.sort((a, b) => Date.parse(b.at) - Date.parse(a.at));

  // The Brain repeats some steps while it waits (polling). Show a run of them as one line.
  const merged: ActivityEvent[] = [];
  for (const event of events) {
    const previous = merged.at(-1);
    if (
      previous &&
      event.kind === "tool" &&
      previous.kind === "tool" &&
      event.tone === "normal" &&
      previous.tone === "normal" &&
      previous.title === event.title &&
      previous.source === event.source &&
      previous.taskId === event.taskId &&
      previous.paid === event.paid
    ) {
      previous.times += 1;
    } else {
      merged.push({ ...event });
    }
  }
  return merged;
}

export type ActivityFilter = "all" | "tools" | "decisions" | "problems";

export function filterActivity(events: ActivityEvent[], filter: ActivityFilter): ActivityEvent[] {
  switch (filter) {
    case "tools":
      return events.filter((e) => e.kind === "tool");
    case "decisions":
      return events.filter((e) => e.kind !== "tool");
    case "problems":
      return events.filter((e) => e.tone !== "normal");
    default:
      return events;
  }
}

// ---------------------------------------------------------------------------
// Pipeline (dashboard)
// ---------------------------------------------------------------------------

export type FunnelStage = {
  key: string;
  label: string;
  taskId: string;
  agent: string;
  status: TaskStatus;
  /** Null when the Brain has not recorded a number for this stage. */
  achieved: number | null;
  target: number | null;
  planTarget: number | null;
};

const FUNNEL: [string, string][] = [
  ["accounts", "Companies found"],
  ["contacts", "Contacts found"],
  ["emails", "Emails sent"],
  ["calls", "Calls made"],
  ["qualified_leads", "Qualified leads"],
  ["opportunities", "Opportunities"],
];

/** The month's pipeline from top to bottom, using only the stages this plan contains. */
export function buildFunnel(tasks: BrainTaskRow[]): FunnelStage[] {
  return FUNNEL.flatMap(([key, label]) => {
    const task = tasks.find((t) => t.output_type === key);
    if (!task) return [];
    const result = obj(task.result);
    return [
      {
        key,
        label,
        taskId: task.task_id,
        agent: task.agent,
        status: task.status,
        achieved: task.achieved_number ?? num(result.achieved),
        target: num(result.target) ?? task.target_number,
        planTarget: task.target_number,
      },
    ];
  });
}

/** Which tool tabs have something to show for this run. */
export function toolsInRun(tasks: BrainTaskRow[]) {
  const outputs = new Set(tasks.map((t) => t.output_type));
  return {
    research: outputs.has("accounts") || outputs.has("contacts"),
    email: outputs.has("emails"),
    calls: outputs.has("calls"),
    /** Tool agents the plan names that have no screen here because the Brain stores no data for them yet. */
    unconnected: [...new Set(tasks.filter((t) => isToolAgent(t.agent) && !["accounts", "contacts", "emails", "calls"].includes(t.output_type)).map((t) => t.agent))],
  };
}

// ---------------------------------------------------------------------------
// Data research result tiles
// ---------------------------------------------------------------------------

export type ResultTile = InsightTile & { key: "companies" | "contacts" | "email" | "mobile" };

const count = new Intl.NumberFormat("en-IN");
const pct = (value: number | null, of: number | null) =>
  value != null && of ? Math.min(100, Math.round((value / of) * 100)) : null;

function foundTile(key: "companies" | "contacts", label: string, task: ResearchTask): ResultTile {
  const { achieved, target, planTarget, reused, bought, status } = task;
  // Measure against the plan's real target. A test run is held to a smaller cap, which is
  // reported as a fact rather than used as the yardstick: "181 of 40" reads as nonsense.
  const goal = planTarget ?? target;
  const capped = planTarget != null && target != null && target < planTarget;
  const lines: string[] = [];

  if (capped) {
    lines.push(
      achieved != null && achieved >= target!
        ? `Test run: capped at ${count.format(target!)}, and the cap was reached.`
        : `Test run: capped at ${count.format(target!)}.`
    );
  }
  if (reused != null || bought != null) {
    const r = reused ?? 0;
    const b = bought ?? 0;
    if (r > 0 && b === 0) lines.push("All reused from data already held; nothing newly bought.");
    else if (r === 0 && b > 0) lines.push(`All ${count.format(b)} newly bought.`);
    else if (r > 0 && b > 0) lines.push(`${count.format(r)} reused from data already held, ${count.format(b)} newly bought.`);
  }

  let verdict: ResultTile["verdict"] = null;
  if (status !== "done") {
    verdict = { text: status === "running" ? "In progress" : status === "pending" ? "Not started" : "Needs attention", tone: status === "running" || status === "pending" ? "neutral" : "attention" };
  } else if (achieved != null && goal != null) {
    if (achieved >= goal) verdict = { text: "Target met", tone: "good" };
    else if (capped && achieved >= target!) verdict = { text: "Test cap reached", tone: "neutral" };
    else verdict = { text: `${count.format(goal - achieved)} short`, tone: "attention" };
  }

  return { key, label, value: achieved, of: goal, ofLabel: "planned", percent: pct(achieved, goal), verdict, lines };
}

/** The tiles at the top of the Data research page, with the reasoning for what each one says. */
export function buildResultTiles(report: ResearchReport): ResultTile[] {
  const tiles: ResultTile[] = [];
  if (report.accounts) tiles.push(foundTile("companies", "Companies found", report.accounts));

  const contacts = report.contacts;
  if (!contacts) return tiles;
  tiles.push(foundTile("contacts", "Contacts found", contacts));
  const total = contacts.achieved;

  if (contacts.withEmail != null) {
    const missing = total != null ? total - contacts.withEmail : null;
    tiles.push({
      key: "email",
      label: "Can be emailed",
      value: contacts.withEmail,
      of: total,
      ofLabel: "contacts",
      percent: pct(contacts.withEmail, total),
      verdict:
        missing == null ? null : missing <= 0 ? { text: "All of them", tone: "good" } : { text: `${count.format(missing)} missing`, tone: "attention" },
      lines: [
        missing == null
          ? "Have a verified email address."
          : missing <= 0
            ? "Every contact has an email address."
            : `${count.format(missing)} contact${missing === 1 ? " has" : "s have"} no email address.`,
      ],
    });
  }

  if (contacts.mobile != null) {
    const landline = contacts.landline ?? 0;
    const noPhone = total != null ? Math.max(0, total - contacts.mobile - landline) : 0;
    const lines: string[] = [];
    if (landline > 0) lines.push(`${count.format(landline)} ${landline === 1 ? "has" : "have"} only a landline and ${landline === 1 ? "is" : "are"} not called.`);
    if (noPhone > 0) lines.push(`${count.format(noPhone)} ${noPhone === 1 ? "has" : "have"} no phone number.`);
    if (!lines.length) lines.push("Every contact has a mobile number.");
    tiles.push({
      key: "mobile",
      label: "Can be called",
      value: contacts.mobile,
      of: total,
      ofLabel: "contacts",
      percent: pct(contacts.mobile, total),
      verdict:
        total == null ? null : contacts.mobile >= total ? { text: "All of them", tone: "good" } : { text: `${count.format(total - contacts.mobile)} cannot`, tone: "attention" },
      lines,
    });
  }
  return tiles;
}

// ---------------------------------------------------------------------------
// Who is on the company list (data research analysis)
// ---------------------------------------------------------------------------

export type Share = { label: string; count: number; percent: number };

export type CompanyBreakdown = {
  total: number;
  industries: Share[];
  places: Share[];
  sizes: Share[];
};

/**
 * "City, State" from a stored address such as
 * "Ashford Centre, ..., Lower Parel, Mumbai, Maharashtra 400013, IN".
 * Addresses are free text, so this reads from the end and gives up quietly when unsure.
 */
export function placeOf(location: string | null): { city: string; region: string } {
  const parts = (location ?? "")
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
  if (parts.length > 1 && /^[A-Z]{2}$/.test(parts[parts.length - 1])) parts.pop(); // country code
  const clean = (value: string | undefined) => (value ?? "").replace(/[0-9]/g, "").replace(/\s+/g, " ").trim();
  let region = clean(parts.pop());
  if (!region) region = clean(parts.pop()); // the last part was only a PIN code
  const city = clean(parts.pop());
  return { city, region };
}

const SIZE_BANDS: [string, number][] = [
  ["Under 1,000 employees", 1000],
  ["1,000 to 4,999", 5000],
  ["5,000 to 9,999", 10000],
  ["10,000 and above", Infinity],
];

function shares(counts: Map<string, number>, total: number, keep: number): Share[] {
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1]);
  const top = sorted.slice(0, keep);
  const rest = sorted.slice(keep).reduce((sum, [, count]) => sum + count, 0);
  if (rest > 0) top.push(["Other", rest]);
  return top.map(([label, count]) => ({ label, count, percent: total ? Math.round((count / total) * 100) : 0 }));
}

export function buildCompanyBreakdown(
  rows: { industry: string | null; location: string | null; headcount: number | null }[]
): CompanyBreakdown {
  const industries = new Map<string, number>();
  const places = new Map<string, number>();
  const sizes = new Map<string, number>(SIZE_BANDS.map(([label]) => [label, 0]));
  const bump = (map: Map<string, number>, key: string) => map.set(key, (map.get(key) ?? 0) + 1);

  for (const row of rows) {
    bump(industries, row.industry?.trim() || "Not recorded");
    bump(places, placeOf(row.location).region || "Not recorded");
    if (typeof row.headcount === "number") {
      bump(sizes, SIZE_BANDS.find(([, below]) => row.headcount! < below)![0]);
    }
  }

  return {
    total: rows.length,
    industries: shares(industries, rows.length, 6),
    places: shares(places, rows.length, 6),
    // Size bands stay in their natural order rather than by count.
    sizes: SIZE_BANDS.map(([label]) => ({
      label,
      count: sizes.get(label) ?? 0,
      percent: rows.length ? Math.round(((sizes.get(label) ?? 0) / rows.length) * 100) : 0,
    })).filter((band) => band.count > 0),
  };
}
