import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";
import {
  SENDER_CATEGORY,
  SENDER_CODES,
  SENDER_ITEMS,
  SENDER_SECURITY,
  buildSenderView,
  senderAnswers,
  type SenderInput,
  type SenderView,
} from "@/lib/checklist-sender";

// The customer's sender details live in the project's checklist (category "Email sender setup"),
// and the customer's email on the project. This reads and writes both for the Email page's
// Sender account card, which stays editable after the checklist itself is locked: a mailbox
// password changes long after the plan is written.
//
// Service-role client, and only after the caller has checked that the signed-in user owns the
// project (requireProject). The password is written and read by the Brain; it is never returned.

export type SenderState = { available: boolean; view: SenderView };

type Item = { id: string; code: string; position: number; answer: string | null };

async function activeChecklist(projectId: string): Promise<{ id: string; items: Item[] } | null> {
  const admin = createAdminClient();
  const { data: checklist } = await admin
    .from(TABLES.checklists)
    .select("id")
    .eq("project_id", projectId)
    .eq("is_active", true)
    .maybeSingle<{ id: string }>();
  if (!checklist) return null;
  const { data: items } = await admin
    .from(TABLES.checklistItems)
    .select("id, code, position, answer")
    .eq("checklist_id", checklist.id)
    .returns<Item[]>();
  return { id: checklist.id, items: items ?? [] };
}

/**
 * `available` is false until the database update that adds the customer email is applied. Pass
 * `ownerId` to also list the user's other email accounts this project could reuse (the Sender card's
 * "use an existing email account" choice); without it the reuse list is empty.
 */
export const loadSender = cache(async (projectId: string, ownerId?: string): Promise<SenderState> => {
  const admin = createAdminClient();
  const empty = { available: false, view: buildSenderView({}, null) };
  try {
    const { data: project, error } = await admin
      .from(TABLES.projects)
      .select("customer_email")
      .eq("id", projectId)
      .maybeSingle<{ customer_email: string | null }>();
    if (error) return empty;

    // A customer whose email account already exists (its own, or one it reuses) keeps using it.
    const { data: account } = await admin
      .from("revenueos_brain_accounts")
      .select("email_client_reference, email_user_email")
      .eq("project_id", projectId)
      .maybeSingle<{ email_client_reference: string | null; email_user_email: string | null }>();

    const options = ownerId ? await emailAccountOptions(projectId, ownerId) : [];

    const checklist = await activeChecklist(projectId);
    const answers = Object.fromEntries(
      (checklist?.items ?? []).filter((item) => SENDER_CODES.includes(item.code)).map((item) => [item.code, item.answer])
    );
    return {
      available: true,
      view: buildSenderView(answers, project?.customer_email, !!account?.email_client_reference?.trim(), {
        reference: account?.email_client_reference,
        email: account?.email_user_email,
        options,
      }),
    };
  } catch {
    return empty;
  }
});

type OwnerProject = { id: string; customer_name: string; customer_email: string | null; brain_customer_key: string | null };
type AccountRow = {
  project_id: string;
  email_client_reference: string | null;
  email_user_email: string | null;
  email_account_status: string | null;
};

/**
 * The references the email tool has actually provisioned an account under (its own idempotency record,
 * command_receipts). This is the authoritative "does an account exist" list: our email_account_status
 * column is newer than some accounts, so it misses older ones. Returns null if the record cannot be read
 * (then callers fall back to the status/column heuristic). Read-only; the email tool owns this table.
 */
async function provisionedEmailRefs(): Promise<Set<string> | null> {
  try {
    const { data, error } = await createAdminClient()
      .from("command_receipts")
      .select("request_key")
      .eq("operation", "revenueos.user_provision")
      .returns<{ request_key: string | null }[]>();
    if (error) return null;
    return new Set((data ?? []).map((row) => (row.request_key ?? "").trim()).filter(Boolean));
  } catch {
    return null;
  }
}

/**
 * A project's reusable email account, or null when it has none. Its reference is the one stored on the
 * account row, or — because the Brain creates the account under the project's brain key and does not
 * write that back — the project's brain_customer_key. An account "exists" when that reference is in the
 * email tool's provisioned set; when that set is unavailable, we fall back to our own status column.
 */
function emailAccountRef(
  project: OwnerProject,
  account: AccountRow | undefined,
  provisioned: Set<string> | null
): { reference: string; email: string } | null {
  const reference = (account?.email_client_reference ?? "").trim() || (project.brain_customer_key ?? "").trim();
  if (!reference) return null;
  const exists = provisioned
    ? provisioned.has(reference)
    : !!(account?.email_client_reference ?? "").trim() ||
      ["created", "reused"].includes((account?.email_account_status ?? "").trim().toLowerCase());
  if (!exists) return null;
  return { reference, email: (account?.email_user_email ?? "").trim() || (project.customer_email ?? "").trim() };
}

/** The user's OTHER projects that already have an email account, as reuse options for this project. */
async function emailAccountOptions(
  projectId: string,
  ownerId: string
): Promise<{ projectId: string; name: string; reference: string; email: string }[]> {
  const admin = createAdminClient();
  const { data: projects } = await admin
    .from(TABLES.projects)
    .select("id, customer_name, customer_email, brain_customer_key")
    .eq("owner_id", ownerId)
    .returns<OwnerProject[]>();
  const others = (projects ?? []).filter((project) => project.id !== projectId);
  if (!others.length) return [];
  const { data: accounts } = await admin
    .from("revenueos_brain_accounts")
    .select("project_id, email_client_reference, email_user_email, email_account_status")
    .in("project_id", others.map((project) => project.id))
    .returns<AccountRow[]>();
  const byProject = new Map((accounts ?? []).map((account) => [account.project_id, account]));
  const provisioned = await provisionedEmailRefs();
  return others
    .map((project) => {
      const account = emailAccountRef(project, byProject.get(project.id), provisioned);
      return account ? { projectId: project.id, name: project.customer_name, ...account } : null;
    })
    .filter((option): option is { projectId: string; name: string; reference: string; email: string } => option !== null);
}

type SwitchResult = { ok: true; message: string } | { ok: false; error: string };

/**
 * Point this project at another of the user's email accounts: copy that account's reference + login
 * email, so the Brain reuses it (and its already-connected mailbox) instead of creating a new one. The
 * Brain passes no SMTP for a reused account, so no mailbox details are copied.
 */
export async function reuseEmailAccount(
  projectId: string,
  ownerId: string,
  sourceProjectId: string
): Promise<SwitchResult> {
  const admin = createAdminClient();
  const failed = { ok: false as const, error: "That email account could not be used. Try again." };
  // The source must be another project of the same person.
  const { data: source } = await admin
    .from(TABLES.projects)
    .select("id, customer_name, customer_email, brain_customer_key")
    .eq("id", sourceProjectId)
    .eq("owner_id", ownerId)
    .neq("id", projectId)
    .maybeSingle<OwnerProject>();
  if (!source) return { ok: false, error: "That email account is not available." };
  const { data: account } = await admin
    .from("revenueos_brain_accounts")
    .select("project_id, email_client_reference, email_user_email, email_account_status")
    .eq("project_id", source.id)
    .maybeSingle<AccountRow>();
  const resolved = emailAccountRef(source, account ?? undefined, await provisionedEmailRefs());
  if (!resolved) return { ok: false, error: "That project has no email account to use." };
  const { error } = await admin.from("revenueos_brain_accounts").upsert(
    {
      project_id: projectId,
      updated_by: ownerId,
      email_client_reference: resolved.reference,
      email_user_email: resolved.email || null,
    },
    { onConflict: "project_id" }
  );
  if (error) return failed;
  return { ok: true, message: `Emails will go out from ${source.customer_name}'s email account.` };
}

/** Clear the reuse so the Brain opens this project's own email account (from its sender details) again. */
export async function ownEmailAccount(projectId: string, ownerId: string): Promise<SwitchResult> {
  const admin = createAdminClient();
  const { error } = await admin.from("revenueos_brain_accounts").upsert(
    { project_id: projectId, updated_by: ownerId, email_client_reference: null, email_user_email: null },
    { onConflict: "project_id" }
  );
  if (error) return { ok: false, error: "That could not be saved. Try again." };
  return { ok: true, message: "This project will use its own email account again." };
}

export type SenderSmtp = {
  name: string;
  email: string;
  host: string;
  port: number;
  secure: boolean;
  password: string;
};

/**
 * The customer's SMTP credentials for a direct sender test (name + mailbox host/port/security + the
 * login email and password). Server-only; the password never leaves the server. Null when the sender
 * details are incomplete. Reuses the very checklist answers the Brain hands the email tool — so the
 * test checks the exact mailbox that campaigns will send from.
 */
export async function loadSenderSmtp(projectId: string): Promise<SenderSmtp | null> {
  const checklist = await activeChecklist(projectId);
  if (!checklist) return null;
  const get = (code: string) => (checklist.items.find((item) => item.code === code)?.answer ?? "").trim();
  const email = get("SENDER-EMAIL");
  const host = get("SENDER-SMTP-HOST");
  const password = get("SENDER-SMTP-PASSWORD");
  const security = get("SENDER-SMTP-SECURITY");
  if (!email || !host || !password || !(security in SENDER_SECURITY)) return null;
  const { mode, port } = SENDER_SECURITY[security as keyof typeof SENDER_SECURITY];
  return { name: get("SENDER-NAME") || "RevenueOS", email, host, port, secure: mode === "IMPLICIT_TLS", password };
}

/**
 * When the sender's IMAP host was given (null: never). The Brain hands the mailbox to the email tool
 * when it launches a campaign, so a campaign launched before this time cannot read replies.
 */
export async function loadImapAddedAt(projectId: string): Promise<string | null> {
  try {
    const admin = createAdminClient();
    const { data: checklist } = await admin
      .from(TABLES.checklists)
      .select("id")
      .eq("project_id", projectId)
      .eq("is_active", true)
      .maybeSingle<{ id: string }>();
    if (!checklist) return null;
    const { data: item } = await admin
      .from(TABLES.checklistItems)
      .select("answer, answered_at, updated_at")
      .eq("checklist_id", checklist.id)
      .eq("code", "SENDER-IMAP-HOST")
      .maybeSingle<{ answer: string | null; answered_at: string | null; updated_at: string | null }>();
    return item?.answer?.trim() ? (item.answered_at ?? item.updated_at) : null;
  } catch {
    return null;
  }
}

export async function saveSender(
  projectId: string,
  userId: string,
  input: SenderInput
): Promise<{ ok: true } | { ok: false; error: string }> {
  const admin = createAdminClient();
  const failed = { ok: false as const, error: "The sender details could not be saved. Try again." };

  const { error: projectError } = await admin
    .from(TABLES.projects)
    .update({ customer_email: input.loginEmail.trim().toLowerCase() || null })
    .eq("id", projectId);
  if (projectError) return failed;

  const checklist = await activeChecklist(projectId);
  if (!checklist) return { ok: false, error: "This project has no checklist yet. Generate it first." };

  const answers = senderAnswers(input);
  const byCode = new Map(checklist.items.map((item) => [item.code, item]));

  // A blank password is allowed only to KEEP one already saved; on first setup it is required.
  const passwordSaved = !!(byCode.get("SENDER-SMTP-PASSWORD")?.answer ?? "").trim();
  if (!passwordSaved && !input.password.trim()) {
    return { ok: false, error: "Enter the SMTP password." };
  }

  const now = new Date().toISOString();
  let position = Math.max(0, ...checklist.items.map((item) => item.position));

  for (const item of SENDER_ITEMS) {
    const existing = byCode.get(item.code);
    const hasAnswer = item.code in answers;
    if (existing) {
      if (!hasAnswer) continue; // an empty password keeps the saved one
      const { error } = await admin
        .from(TABLES.checklistItems)
        .update({ answer: answers[item.code], answered_by: userId, answered_at: now })
        .eq("id", existing.id);
      if (error) return failed;
    } else {
      // A checklist written before these questions existed: add them, so the answers have a home.
      position += 1;
      const { error } = await admin.from(TABLES.checklistItems).insert({
        checklist_id: checklist.id,
        position,
        code: item.code,
        category: SENDER_CATEGORY,
        question: item.question,
        options: [...item.options],
        answer: hasAnswer ? answers[item.code] : null,
        answered_by: hasAnswer ? userId : null,
        answered_at: hasAnswer ? now : null,
        reason: "Needed to send emails from the customer's own mailbox.",
      });
      if (error) return failed;
    }
  }
  return { ok: true };
}
