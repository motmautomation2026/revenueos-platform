import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import type { RunRows } from "./tools";
import {
  buildExecutionView,
  monthOfRunKey,
  type BrainDecisionRow,
  type BrainRunRow,
  type BrainTaskRow,
  type BrainToolCallRow,
  type ExecutionView,
  type RunStatus,
} from "./view";

// READ-ONLY. The brain_* and quickicp_revenueos_client_* tables belong to the Brain (brain/, a
// separate Python codebase). This file must only ever SELECT. The app's single write, answering
// an open decision, lives in write.ts.
//
// They have row level security with no policies, so the signed-in user's client cannot read
// them. We use the service-role client, and only after the caller has checked that the user
// owns the project (requireProject).

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const CUSTOMER_KEY_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/;

/**
 * Fallback pairing from settings, used only where the database has no key for a project
 * (before supabase/migrations/0003_project_brain_key.sql is applied):
 *   BRAIN_PROJECTS=<project id>=<customer key>,<project id>=<customer key>
 * The single pair REVENUEOS_PROJECT_ID + CUSTOMER_KEY is also honoured.
 */
function settingsPairs(): Map<string, string> {
  const pairs = new Map<string, string>();
  const add = (projectId?: string, customer?: string) => {
    const id = projectId?.trim().toLowerCase();
    const key = customer?.trim();
    if (id && key && UUID_RE.test(id) && CUSTOMER_KEY_RE.test(key)) pairs.set(id, key);
  };
  for (const pair of (process.env.BRAIN_PROJECTS ?? "").split(",")) {
    const [projectId, customer] = pair.split("=");
    add(projectId, customer);
  }
  add(process.env.REVENUEOS_PROJECT_ID, process.env.CUSTOMER_KEY);
  return pairs;
}

export function settingsCustomerFor(projectId: string): string | null {
  return settingsPairs().get(projectId.toLowerCase()) ?? null;
}

/**
 * The Brain's customer key for this project, or null if it has none.
 *
 * Every project carries its own key in the database (revenueos_projects.brain_customer_key),
 * set automatically when the project is created and never changed. The Brain files a customer's
 * runs and data under that key, so this is all that is needed to find a project's execution.
 */
export const brainCustomerFor = cache(async (projectId: string): Promise<string | null> => {
  if (!UUID_RE.test(projectId)) return null;
  try {
    const { data, error } = await createAdminClient()
      .from("revenueos_projects")
      .select("brain_customer_key")
      .eq("id", projectId)
      .maybeSingle<{ brain_customer_key: string | null }>();
    const key = data?.brain_customer_key?.trim();
    if (!error && key && CUSTOMER_KEY_RE.test(key)) return key;
  } catch {
    // The column may not exist yet; fall through to the settings pairing.
  }
  return settingsCustomerFor(projectId);
});

export type RunOption = { runKey: string; month: string; status: RunStatus };

export type ExecutionData =
  | { state: "not_configured" }
  | { state: "other_project" }
  | { state: "no_runs" }
  | { state: "error"; message: string }
  | { state: "ok"; customer: string; runs: RunOption[]; view: ExecutionView; rows: RunRows };

/** One run with its tasks, decisions and tool calls. Cached per request, so tabs and layout share it. */
export const loadExecution = cache(async (projectId: string, runKey?: string): Promise<ExecutionData> => {
  const customer = await brainCustomerFor(projectId);
  if (!customer) return { state: "not_configured" };
  const config = { customer };

  try {
    const admin = createAdminClient();

    const { data: runs, error: runsError } = await admin
      .from("brain_customer_runs")
      .select("id, run_key, customer, status, test_mode, stop_reason, plan, created_at, updated_at")
      .eq("customer", config.customer)
      .order("id", { ascending: false })
      .returns<BrainRunRow[]>();
    if (runsError) return { state: "error", message: "The Brain's tables could not be read." };
    if (!runs?.length) return { state: "no_runs" };

    const run = runs.find((r) => r.run_key === runKey) ?? runs[0];

    const [tasks, decisions] = await Promise.all([
      admin
        .from("brain_plan_tasks")
        .select(
          "id, run_id, task_id, agent, output_type, depends_on, week, work_order, target_number, achieved_number, status, waiting_for, result, last_error, started_at, finished_at"
        )
        .eq("run_id", run.id)
        .order("id", { ascending: true })
        .returns<BrainTaskRow[]>(),
      admin
        .from("brain_decisions")
        .select("id, run_id, task_row_id, kind, status, question, answer, decided_by, reason, created_at, answered_at")
        .eq("run_id", run.id)
        .order("id", { ascending: true })
        .returns<BrainDecisionRow[]>(),
    ]);
    if (tasks.error || decisions.error) {
      return { state: "error", message: "The Brain's tasks could not be read." };
    }

    // The request/response/output columns are deliberately not selected: they can hold full
    // contact lists and message bodies. What the pages need is on the task and decision rows.
    const taskRowIds = (tasks.data ?? []).map((t) => t.id);
    const { data: toolCalls } = taskRowIds.length
      ? await admin
          .from("brain_tool_calls")
          .select("id, task_row_id, step, tool, paid, status, error, started_at, finished_at")
          .in("task_row_id", taskRowIds)
          .order("id", { ascending: true })
          .returns<BrainToolCallRow[]>()
      : { data: [] as BrainToolCallRow[] };

    const rows: RunRows = {
      tasks: tasks.data ?? [],
      decisions: decisions.data ?? [],
      toolCalls: toolCalls ?? [],
    };
    return {
      state: "ok",
      customer: config.customer,
      runs: runs.map((r) => ({
        runKey: r.run_key,
        month: monthOfRunKey(r.run_key, r.customer),
        status: r.status,
      })),
      view: buildExecutionView({ run, ...rows }),
      rows,
    };
  } catch {
    return { state: "error", message: "The Brain's data could not be loaded." };
  }
});

// ---------------------------------------------------------------------------
// QuickICP records (the companies and contacts found for this customer)
// ---------------------------------------------------------------------------

export type CompanyRow = {
  id: number;
  company_name: string | null;
  website: string | null;
  location: string | null;
  industry: string | null;
  headcount: number | null;
  linkedin: string | null;
  created_at: string;
};

export type PersonRow = {
  id: number;
  full_name: string | null;
  job_position: string | null;
  location: string | null;
  company_id: number | null;
  email: string | null;
  phone_number: string | null;
  user_social: string | null;
  created_at: string;
};

export const COMPANY_PAGE_SIZE = 25;
const PEOPLE_LIMIT = 500;

export type ResearchRecords = {
  companies: CompanyRow[];
  companyTotal: number;
  page: number;
  pageCount: number;
  people: (PersonRow & { company_name: string | null })[];
  peopleTotal: number;
};

export async function loadResearchRecords(
  customer: string,
  options: { search?: string; page?: number }
): Promise<ResearchRecords | null> {
  try {
    const admin = createAdminClient();
    // Keep only characters that are safe inside a PostgREST pattern.
    const search = (options.search ?? "").replace(/[^\p{L}\p{N} .&'-]/gu, "").trim().slice(0, 60);
    const page = Math.max(1, Math.floor(options.page ?? 1));
    const from = (page - 1) * COMPANY_PAGE_SIZE;

    let companyQuery = admin
      .from("quickicp_revenueos_client_company_data")
      .select("id, company_name, website, location, industry, headcount, linkedin, created_at", { count: "exact" })
      .eq("customer", customer);
    if (search) companyQuery = companyQuery.ilike("company_name", `%${search}%`);

    const [companies, people] = await Promise.all([
      companyQuery
        .order("company_name", { ascending: true })
        .range(from, from + COMPANY_PAGE_SIZE - 1)
        .returns<CompanyRow[]>(),
      admin
        .from("quickicp_revenueos_client_people_data")
        .select("id, full_name, job_position, location, company_id, email, phone_number, user_social, created_at", {
          count: "exact",
        })
        .eq("customer", customer)
        .order("id", { ascending: true })
        .limit(PEOPLE_LIMIT)
        .returns<PersonRow[]>(),
    ]);
    if (companies.error || people.error) return null;

    const companyIds = [...new Set((people.data ?? []).map((p) => p.company_id).filter((id): id is number => id != null))];
    const { data: employers } = companyIds.length
      ? await admin
          .from("quickicp_revenueos_client_company_data")
          .select("id, company_name")
          .in("id", companyIds)
          .returns<{ id: number; company_name: string | null }[]>()
      : { data: [] as { id: number; company_name: string | null }[] };
    const nameOf = new Map((employers ?? []).map((c) => [c.id, c.company_name]));

    const companyTotal = companies.count ?? companies.data?.length ?? 0;
    return {
      companies: companies.data ?? [],
      companyTotal,
      page,
      pageCount: Math.max(1, Math.ceil(companyTotal / COMPANY_PAGE_SIZE)),
      people: (people.data ?? []).map((p) => ({
        ...p,
        company_name: p.company_id != null ? (nameOf.get(p.company_id) ?? null) : null,
      })),
      peopleTotal: people.count ?? people.data?.length ?? 0,
    };
  } catch {
    return null;
  }
}


/** Industry, address and size of every company on file, for the "who is on the list" analysis. */
export async function loadCompanyFacts(
  customer: string
): Promise<{ industry: string | null; location: string | null; headcount: number | null }[]> {
  try {
    const { data } = await createAdminClient()
      .from("quickicp_revenueos_client_company_data")
      .select("industry, location, headcount")
      .eq("customer", customer)
      .limit(5000)
      .returns<{ industry: string | null; location: string | null; headcount: number | null }[]>();
    return data ?? [];
  } catch {
    return [];
  }
}

// ---------------------------------------------------------------------------
// Quickhowl's own records (the calling tool's tables in the same database)
// ---------------------------------------------------------------------------
//
// The Brain stores a summary of each call. The calling tool stores the full record: when the
// phone rang and was answered, the transcript, and what the prospect asked for. The link is the
// campaign reference the Brain generated:  brain reference -> revenueos_launches.reference ->
// campaign_id -> voice_campaigns / campaign_contacts / calls / call_transcripts.
// Phone numbers and recordings are deliberately not selected.

export type CallRecord = {
  id: string;
  contactName: string;
  company: string;
  role: string;
  status: string;
  outcome: string;
  startedAt: string | null;
  /** Seconds the phone rang before it was answered. */
  ringSeconds: number | null;
  durationSeconds: number | null;
  costInr: number | null;
  summary: string;
  error: string;
  /** What the prospect said they need, and anything else the tool pulled out of the call. */
  extracted: { label: string; value: string }[];
  transcript: { speaker: "agent" | "prospect"; text: string }[];
};

export type CallCampaignRecord = {
  reference: string;
  name: string;
  status: string;
  totalContacts: number | null;
  completedCalls: number | null;
  interested: number | null;
  failed: number | null;
  startedAt: string | null;
  completedAt: string | null;
  maxRetries: number | null;
  calls: CallRecord[];
};

const text = (value: unknown) => (typeof value === "string" ? value.trim() : value == null ? "" : String(value));
const label = (key: string) => {
  const words = key.replace(/_/g, " ");
  return words ? words[0].toUpperCase() + words.slice(1) : "";
};

/** The calling tool's records for the Brain's campaign references. Empty when nothing matches. */
export async function loadCallRecords(references: string[]): Promise<Map<string, CallCampaignRecord>> {
  const result = new Map<string, CallCampaignRecord>();
  const wanted = [...new Set(references.filter(Boolean))];
  if (!wanted.length) return result;

  try {
    const admin = createAdminClient();
    const { data: launches } = await admin
      .from("revenueos_launches")
      .select("reference, campaign_id")
      .in("reference", wanted)
      .returns<{ reference: string; campaign_id: string }[]>();
    const campaignIds = (launches ?? []).map((l) => l.campaign_id).filter(Boolean);
    if (!campaignIds.length) return result;

    const [campaigns, calls, contacts] = await Promise.all([
      admin
        .from("voice_campaigns")
        .select("id, name, status, total_contacts, completed_calls, interested_count, failed_count, started_at, completed_at, max_retries")
        .in("id", campaignIds)
        .returns<Record<string, unknown>[]>(),
      admin
        .from("calls")
        .select("id, campaign_id, contact_id, status, outcome, duration_seconds, cost_inr, extracted_data, summary, started_at, answered_at, error_message")
        .in("campaign_id", campaignIds)
        .order("started_at", { ascending: true })
        .returns<Record<string, unknown>[]>(),
      admin
        .from("campaign_contacts")
        .select("id, name, company, custom_fields")
        .in("campaign_id", campaignIds)
        .returns<{ id: string; name: string | null; company: string | null; custom_fields: Record<string, unknown> | null }[]>(),
    ]);

    const callIds = (calls.data ?? []).map((c) => text(c.id));
    const { data: transcripts } = callIds.length
      ? await admin
          .from("call_transcripts")
          .select("call_id, segments")
          .in("call_id", callIds)
          .returns<{ call_id: string; segments: unknown }[]>()
      : { data: [] as { call_id: string; segments: unknown }[] };

    const contactOf = new Map((contacts.data ?? []).map((c) => [c.id, c]));
    const transcriptOf = new Map((transcripts ?? []).map((t) => [t.call_id, t.segments]));
    const num = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
    const seconds = (from: unknown, to: unknown) => {
      const a = Date.parse(text(from));
      const b = Date.parse(text(to));
      return Number.isFinite(a) && Number.isFinite(b) && b >= a ? Math.round((b - a) / 1000) : null;
    };

    for (const launch of launches ?? []) {
      const campaign = (campaigns.data ?? []).find((c) => c.id === launch.campaign_id);
      if (!campaign) continue;
      result.set(launch.reference, {
        reference: launch.reference,
        name: text(campaign.name),
        status: label(text(campaign.status)),
        totalContacts: num(campaign.total_contacts),
        completedCalls: num(campaign.completed_calls),
        interested: num(campaign.interested_count),
        failed: num(campaign.failed_count),
        startedAt: text(campaign.started_at) || null,
        completedAt: text(campaign.completed_at) || null,
        maxRetries: num(campaign.max_retries),
        calls: (calls.data ?? [])
          .filter((call) => call.campaign_id === launch.campaign_id)
          .map((call): CallRecord => {
            const contact = contactOf.get(text(call.contact_id));
            const extracted = call.extracted_data;
            const segments = transcriptOf.get(text(call.id));
            return {
              id: text(call.id),
              contactName: text(contact?.name),
              company: text(contact?.company),
              role: text(contact?.custom_fields?.designation),
              status: label(text(call.status)),
              outcome: label(text(call.outcome)),
              startedAt: text(call.started_at) || null,
              ringSeconds: seconds(call.started_at, call.answered_at),
              durationSeconds: num(call.duration_seconds),
              costInr: num(call.cost_inr) ?? (typeof call.cost_inr === "string" && call.cost_inr ? Number(call.cost_inr) : null),
              summary: text(call.summary),
              error: text(call.error_message),
              extracted:
                extracted && typeof extracted === "object" && !Array.isArray(extracted)
                  ? Object.entries(extracted)
                      .filter(([, value]) => typeof value === "string" || typeof value === "number")
                      .map(([key, value]) => ({ label: label(key), value: text(value) }))
                      .filter((entry) => entry.value)
                  : [],
              transcript: Array.isArray(segments)
                ? segments
                    .filter((s): s is { speaker?: unknown; text?: unknown } => !!s && typeof s === "object")
                    .map((s) => ({
                      speaker: s.speaker === "agent" ? ("agent" as const) : ("prospect" as const),
                      text: text(s.text),
                    }))
                    .filter((s) => s.text)
                : [],
            };
          }),
      });
    }
  } catch {
    // The calling tool's tables are optional extra detail: without them the page shows the Brain's summary.
  }
  return result;
}


// ---------------------------------------------------------------------------
// The email tool's own records, as counts only
// ---------------------------------------------------------------------------
//
// The email tool keeps its tables closed to this app on purpose. What the app may call is one
// database function, revenueos_email_campaign_stats (supabase/migrations/0002), which returns
// numbers for the campaigns asked for and nothing else: no addresses, subjects or message text.

export type EmailStats = {
  campaignId: string;
  status: string;
  recipients: number;
  messages: number;
  /** Handed to the mail provider and accepted. */
  sent: number;
  /** Still to go out: later emails in the sequence. */
  waiting: number;
  failed: number;
  firstSentAt: string | null;
  lastSentAt: string | null;
  nextDueAt: string | null;
  failureReasons: { label: string; count: number }[];
  /** Opens, clicks, bounces and so on, by the email tool's own names for them. */
  events: { label: string; count: number }[];
  outcomes: { label: string; count: number }[];
  replies: number;
};

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function countsOf(value: unknown): { label: string; count: number }[] {
  if (!value || typeof value !== "object" || Array.isArray(value)) return [];
  return Object.entries(value as Record<string, unknown>)
    .filter((entry): entry is [string, number] => typeof entry[1] === "number")
    .map(([key, count]) => ({ label: label(key.toLowerCase().replace(/[:]/g, ": ")), count }))
    .sort((a, b) => b.count - a.count);
}

/** Counts for the given email campaigns, keyed by campaign id. Empty if the function is unavailable. */
export async function loadEmailStats(campaignIds: string[]): Promise<Map<string, EmailStats>> {
  const result = new Map<string, EmailStats>();
  const ids = [...new Set(campaignIds.filter((id) => UUID.test(id)))];
  if (!ids.length) return result;

  try {
    const { data, error } = await createAdminClient().rpc("revenueos_email_campaign_stats", { p_campaign_ids: ids });
    if (error || !Array.isArray(data)) return result;

    for (const row of data as Record<string, unknown>[]) {
      const int = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : 0);
      const byStatus = (row.messages_by_status ?? {}) as Record<string, unknown>;
      const failureReasons = countsOf(row.failure_reasons);
      const sent = int(row.accepted);
      const failed = Math.max(int(byStatus.FAILED), failureReasons.reduce((sum, r) => sum + r.count, 0));
      result.set(text(row.campaign_id), {
        campaignId: text(row.campaign_id),
        status: label(text(row.campaign_status).toLowerCase()),
        recipients: int(row.recipients),
        messages: int(row.messages),
        sent,
        waiting: Math.max(0, int(row.messages) - sent - failed),
        failed,
        firstSentAt: text(row.first_accepted_at) || null,
        lastSentAt: text(row.last_accepted_at) || null,
        nextDueAt: text(row.next_due_at) || null,
        failureReasons,
        events: countsOf(row.events_by_kind),
        outcomes: countsOf(row.outcomes_by_kind),
        replies: int(row.replies),
      });
    }
  } catch {
    // Optional extra detail: without it the page shows what the Brain recorded.
  }
  return result;
}
