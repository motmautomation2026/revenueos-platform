import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { brainCustomerFor } from "./read";

// Account-making acknowledgement. Reads whether each of a customer's tool accounts (email, calls)
// was actually created, reused, or failed — first from the stored columns on revenueos_brain_accounts
// (supabase/migrations/0016_account_status.sql), otherwise derived live from the Brain's ledger
// (brain_tool_calls). READ-ONLY of the Brain's tables; the only write is the status columns we own.

const TABLE = "revenueos_brain_accounts";

export type AccountStatusValue = "created" | "reused" | "error" | "unknown";

export type ToolAccountStatus = {
  status: AccountStatusValue;
  /** Plain-language line for the UI (e.g. the account email, or the error). */
  detail: string;
  /** The account email / reference the tool used, when known. */
  account: string;
  checkedAt: string | null;
  source: "stored" | "ledger" | "none";
};

export type AccountStatus = {
  /** False before the table/columns exist. */
  available: boolean;
  email: ToolAccountStatus;
  calls: ToolAccountStatus;
};

const none = (): ToolAccountStatus => ({ status: "unknown", detail: "", account: "", checkedAt: null, source: "none" });

type StoredRow = {
  email_user_email: string | null;
  calls_user_email: string | null;
  calls_client_reference: string | null;
  email_account_status: string | null;
  email_account_detail: string | null;
  email_account_checked_at: string | null;
  calls_account_status: string | null;
  calls_account_detail: string | null;
  calls_account_checked_at: string | null;
};

const STORED_COLUMNS =
  "email_user_email, calls_user_email, calls_client_reference, " +
  "email_account_status, email_account_detail, email_account_checked_at, " +
  "calls_account_status, calls_account_detail, calls_account_checked_at";

const VALUES: readonly AccountStatusValue[] = ["created", "reused", "error", "unknown"];
const asValue = (v: string | null | undefined): AccountStatusValue | null =>
  v && (VALUES as readonly string[]).includes(v) ? (v as AccountStatusValue) : null;

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

type Ledger = { email: ToolAccountStatus; calls: ToolAccountStatus };

/** Parse one email-tool `launch` response into a status. Pure; exported for tests. */
export function emailStatusFrom(response: Record<string, unknown>, failed: boolean, error: string): ToolAccountStatus {
  const account = text(response.email);
  const mailboxError = text(response.mailbox_error);
  const mailboxStatus = text(response.mailbox_status).toLowerCase();
  if (failed || error || mailboxError) {
    return { status: "error", detail: mailboxError || error || "The email account could not be set up.", account, checkedAt: null, source: "ledger" };
  }
  if (response.user_created === true) {
    return { status: "created", detail: account ? `New email account created for ${account}.` : "New email account created.", account, checkedAt: null, source: "ledger" };
  }
  // user_created false but no error: the account already existed and the mailbox connected.
  const detail = mailboxStatus === "connected" ? "Existing email account used; mailbox connected." : "Existing email account used.";
  return { status: "reused", detail, account, checkedAt: null, source: "ledger" };
}

/** Parse one QuickHowl `store`/`start` response into a status. Pure; exported for tests. */
export function callsStatusFrom(response: Record<string, unknown>, failed: boolean, error: string): ToolAccountStatus {
  const account = response.account && typeof response.account === "object" ? (response.account as Record<string, unknown>) : {};
  const login = text(account.login_email);
  const accountStatus = text(account.status).toLowerCase();
  if (failed || error) {
    return { status: "error", detail: error || "The calling account could not be set up.", account: login, checkedAt: null, source: "ledger" };
  }
  if (accountStatus === "created") {
    return { status: "created", detail: login ? `New calling account created for ${login}.` : "New calling account created.", account: login, checkedAt: null, source: "ledger" };
  }
  if (accountStatus === "reused") {
    return { status: "reused", detail: login ? `Existing calling account used (${login}).` : "Existing calling account used.", account: login, checkedAt: null, source: "ledger" };
  }
  return none();
}

/**
 * Derive the latest account outcome for both tools from the Brain's ledger: the most recent
 * email-tool `launch` step and QuickHowl `store`/`start` step across this customer's recent runs.
 */
async function deriveFromLedger(customer: string): Promise<Ledger> {
  const result: Ledger = { email: none(), calls: none() };
  const admin = createAdminClient();

  const { data: runs } = await admin
    .from("brain_customer_runs")
    .select("id")
    .eq("customer", customer)
    .order("id", { ascending: false })
    .limit(10)
    .returns<{ id: number }[]>();
  if (!runs?.length) return result;

  const { data: tasks } = await admin
    .from("brain_plan_tasks")
    .select("id")
    .in("run_id", runs.map((r) => r.id))
    .returns<{ id: number }[]>();
  const taskIds = (tasks ?? []).map((t) => t.id);
  if (!taskIds.length) return result;

  // Newest first; the launch/store/start responses are small (unlike samples), so selecting them is fine.
  const { data: calls } = await admin
    .from("brain_tool_calls")
    .select("tool, step, status, error, response, finished_at")
    .in("task_row_id", taskIds)
    .in("tool", ["email_tool", "quickhowl"])
    .in("step", ["launch", "store", "start"])
    .order("id", { ascending: false })
    .returns<{ tool: string; step: string; status: string | null; error: string | null; response: Record<string, unknown> | null; finished_at: string | null }[]>();

  for (const row of calls ?? []) {
    const failed = (row.status ?? "").toLowerCase() === "failed";
    const error = text(row.error);
    const response = row.response ?? {};
    if (row.tool === "email_tool" && row.step === "launch" && result.email.source === "none") {
      result.email = { ...emailStatusFrom(response, failed, error), checkedAt: row.finished_at };
    }
    if (row.tool === "quickhowl" && (row.step === "store" || row.step === "start") && result.calls.source === "none") {
      const parsed = callsStatusFrom(response, failed, error);
      if (parsed.source !== "none") result.calls = { ...parsed, checkedAt: row.finished_at };
    }
    if (result.email.source !== "none" && result.calls.source !== "none") break;
  }
  return result;
}

/** Read a stored status column triple into a ToolAccountStatus, or null if not stored yet. */
function storedStatus(
  status: string | null,
  detail: string | null,
  checkedAt: string | null,
  account: string
): ToolAccountStatus | null {
  const value = asValue(status);
  if (!value) return null;
  return { status: value, detail: detail ?? "", account, checkedAt, source: "stored" };
}

/**
 * The account-making status for both tools. Prefers the stored columns (written by refreshAccountStatus);
 * falls back to a live read of the ledger. `available` is false before 0016 is applied.
 */
export async function loadAccountStatus(projectId: string): Promise<AccountStatus> {
  const customer = await brainCustomerFor(projectId);
  try {
    const admin = createAdminClient();
    const { data: row, error } = await admin
      .from(TABLE)
      .select(STORED_COLUMNS)
      .eq("project_id", projectId)
      .maybeSingle<StoredRow>();
    if (error) return { available: false, email: none(), calls: none() };

    const emailAccount = text(row?.email_user_email);
    const callsAccount = text(row?.calls_user_email) || text(row?.calls_client_reference);

    const emailStored = storedStatus(row?.email_account_status ?? null, row?.email_account_detail ?? null, row?.email_account_checked_at ?? null, emailAccount);
    const callsStored = storedStatus(row?.calls_account_status ?? null, row?.calls_account_detail ?? null, row?.calls_account_checked_at ?? null, callsAccount);

    // Derive from the ledger only for the tools without a stored status, to keep the read light.
    const needLedger = !emailStored || !callsStored;
    const ledger = needLedger && customer ? await deriveFromLedger(customer) : { email: none(), calls: none() };

    const fill = (stored: ToolAccountStatus | null, derived: ToolAccountStatus, account: string): ToolAccountStatus => {
      if (stored) return stored;
      return { ...derived, account: derived.account || account };
    };

    return {
      available: true,
      email: fill(emailStored, ledger.email, emailAccount),
      calls: fill(callsStored, ledger.calls, callsAccount),
    };
  } catch {
    return { available: false, email: none(), calls: none() };
  }
}

/**
 * Re-read the outcome from the ledger and persist it to the status columns, so the acknowledgement
 * survives and is shown without re-querying the ledger. Returns the fresh status. No external tool is
 * called — this only reads the Brain's ledger and writes our own columns.
 */
export async function refreshAccountStatus(
  projectId: string,
  userId: string
): Promise<{ ok: true; status: AccountStatus } | { ok: false; error: string }> {
  const customer = await brainCustomerFor(projectId);
  if (!customer) return { ok: false, error: "This project has no Brain key yet, so there is no account activity to read." };

  const ledger = await deriveFromLedger(customer);
  const now = new Date().toISOString();
  const update: Record<string, string | null> = { project_id: projectId, updated_by: userId };
  if (ledger.email.source !== "none") {
    update.email_account_status = ledger.email.status;
    update.email_account_detail = ledger.email.detail;
    update.email_account_checked_at = now;
  }
  if (ledger.calls.source !== "none") {
    update.calls_account_status = ledger.calls.status;
    update.calls_account_detail = ledger.calls.detail;
    update.calls_account_checked_at = now;
  }

  try {
    const { error } = await createAdminClient().from(TABLE).upsert(update, { onConflict: "project_id" });
    if (error) return { ok: false, error: "The status could not be saved. Try again." };
  } catch {
    return { ok: false, error: "The status could not be saved. Try again." };
  }
  return { ok: true, status: await loadAccountStatus(projectId) };
}
