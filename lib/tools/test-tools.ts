import "server-only";
import nodemailer from "nodemailer";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";
import { loadSenderSmtp } from "@/lib/brain/sender-store";

/**
 * Standalone tool testing — a separate pipeline from the Brain's run/orchestration. The platform calls
 * the tool integration APIs directly (QUICKHOWL_URL/KEY), using an existing project's configured line.
 * No run, no task, no approval. Credentials for the tools come from the platform env; the per-project
 * sender/line comes from revenueos_brain_accounts (read with the service-role key, owner-checked above).
 *
 * The call test uses QUICKHOWL_URL/KEY + the project's calling account. The email test sends directly
 * over SMTP using the project's configured sender mailbox (loadSenderSmtp) — no email-tool API needed.
 */

export type TestProject = {
  id: string;
  name: string;
  emailSender: string | null; // what emails would be sent as
  callLine: string | null; // a human label for the line/account
  callLineReady: boolean; // a real own line (DID) or a borrowed account reference is set
};

type AccountRow = {
  project_id: string;
  email_client_reference: string | null;
  calls_client_reference: string | null;
  calls_user_email: string | null;
  calls_user_password: string | null;
  calls_user_full_name: string | null;
  calls_user_company: string | null;
  calls_vobiz_auth_id: string | null;
  calls_vobiz_auth_token: string | null;
  calls_did: string | null;
};

/** The signed-in user's own projects, each with a short summary of its sender and call line. */
export async function loadTestProjects(ownerId: string): Promise<TestProject[]> {
  const admin = createAdminClient();
  const { data: projects } = await admin
    .from(TABLES.projects)
    .select("id, customer_name, customer_email")
    .eq("owner_id", ownerId)
    .is("archived_at", null)
    .order("customer_name")
    .returns<{ id: string; customer_name: string; customer_email: string | null }[]>();
  if (!projects?.length) return [];

  const { data: accounts } = await admin
    .from("revenueos_brain_accounts")
    .select("project_id, email_client_reference, calls_client_reference, calls_did")
    .in("project_id", projects.map((p) => p.id))
    .returns<Pick<AccountRow, "project_id" | "email_client_reference" | "calls_client_reference" | "calls_did">[]>();
  const byProject = new Map((accounts ?? []).map((a) => [a.project_id, a]));

  return projects.map((p) => {
    const a = byProject.get(p.id);
    const did = a?.calls_did?.trim();
    const borrowed = a?.calls_client_reference?.trim();
    return {
      id: p.id,
      name: p.customer_name,
      emailSender: p.customer_email?.trim() || a?.email_client_reference?.trim() || null,
      callLine: did ? `Own line · ${did}` : borrowed ? `Account · ${borrowed}` : null,
      callLineReady: !!(did || borrowed),
    };
  });
}

export type TestResult = { ok: boolean; detail: string; pending?: boolean };

const E164 = /^\+[1-9]\d{7,14}$/;

async function loadAccount(projectId: string): Promise<AccountRow | null> {
  const { data } = await createAdminClient()
    .from("revenueos_brain_accounts")
    .select(
      "project_id, email_client_reference, calls_client_reference, calls_user_email, calls_user_password, calls_user_full_name, calls_user_company, calls_vobiz_auth_id, calls_vobiz_auth_token, calls_did"
    )
    .eq("project_id", projectId)
    .maybeSingle<AccountRow>();
  return data ?? null;
}

/**
 * Place ONE real test call to the user's own number, using the project's configured line/account, by
 * calling the Quickhowl v2 integration directly. Returns the launch outcome (started / blocked + reason).
 */
export async function placeTestCall(projectId: string, target: string): Promise<TestResult> {
  const base = process.env.QUICKHOWL_URL?.replace(/\/$/, "");
  const key = process.env.QUICKHOWL_KEY;
  if (!base || !key) {
    return { ok: false, detail: "Call testing isn't enabled: set QUICKHOWL_URL and QUICKHOWL_KEY in the platform env." };
  }
  if (!E164.test(target)) return { ok: false, detail: "Enter your number in full international form, e.g. +9198XXXXXXXX." };

  const account = await loadAccount(projectId);
  const clientRef = account?.calls_client_reference?.trim();
  const email = account?.calls_user_email?.trim();
  if (!clientRef || !email) {
    return { ok: false, detail: "This project has no calling account set. Set it on the Calls page first." };
  }

  const did = account?.calls_did?.trim();
  const authId = account?.calls_vobiz_auth_id?.trim();
  const authToken = account?.calls_vobiz_auth_token?.trim();

  // v2 launch: one contact (the user's own number), auto_start. phone_number only when the project has
  // its own line; a borrowed account reuses its line. The agent is a short, fixed line-test script.
  const body: Record<string, unknown> = {
    client_reference: clientRef,
    reference: `motm-linetest-${projectId.slice(0, 8)}-${Date.now()}`,
    user: {
      email,
      password: account?.calls_user_password ?? "",
      full_name: account?.calls_user_full_name ?? "RevenueOS",
      company_name: account?.calls_user_company ?? "RevenueOS",
    },
    agent: {
      name: "Line test",
      language: "hinglish",
      welcome_message: "{time_greeting}. This is a test call from RevenueOS to check the line. If you can hear this, the line works.",
      system_prompt:
        "This is a line test. Greet briefly, say it is a test call to check the calling line, confirm the person can hear you, then thank them and end. Keep it under 20 seconds.",
    },
    campaign: { name: "Line test" },
    contacts: [{ name: "Line test", phone: target }],
    auto_start: true,
  };
  if (did && authId && authToken) body.phone_number = { auth_id: authId, auth_token: authToken, did };

  try {
    const res = await fetch(`${base}/launch`, {
      method: "POST",
      headers: { "X-RevenueOS-Key": key, "Content-Type": "application/json" },
      body: JSON.stringify(body),
      cache: "no-store",
    });
    const data = (await res.json().catch(() => ({}))) as Record<string, unknown>;
    if (!res.ok) {
      const detail = typeof data?.detail === "string" ? data.detail : `HTTP ${res.status}`;
      return { ok: false, detail: `The call tool refused the test: ${detail}` };
    }
    const startStatus = String(data?.start_status ?? "");
    const reason = typeof data?.start_reason === "string" ? data.start_reason : null;
    if (startStatus === "started" || startStatus === "already_started") {
      return { ok: true, detail: "Calling your number now — pick up to check the line and voice quality." };
    }
    if (startStatus === "blocked") {
      return { ok: false, detail: `Could not start: ${reason ?? "the line is not ready"}.` };
    }
    return { ok: false, detail: `The call did not start (status: ${startStatus || "unknown"}).` };
  } catch {
    return { ok: false, detail: "Could not reach the call tool. Check QUICKHOWL_URL and connectivity." };
  }
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Send ONE test email from the customer's own mailbox, over SMTP, using the sender details already
 * configured for this project (host, security, login email and password — loadSenderSmtp). This checks
 * the exact mailbox campaigns send from; no email-tool API is involved. The recipient is the person's
 * own inbox, so nothing reaches a prospect.
 */
export async function sendTestEmail(projectId: string, target: string): Promise<TestResult> {
  if (!EMAIL.test(target)) return { ok: false, detail: "Enter a valid email address to send the test to." };

  const smtp = await loadSenderSmtp(projectId);
  if (!smtp) {
    return { ok: false, detail: "Add this customer's sender details first (mailbox host, security and password)." };
  }

  try {
    const transport = nodemailer.createTransport({
      host: smtp.host,
      port: smtp.port,
      secure: smtp.secure, // true on 465 (SSL/TLS), false on 587 (STARTTLS)
      requireTLS: !smtp.secure,
      auth: { user: smtp.email, pass: smtp.password },
      connectionTimeout: 15000,
      greetingTimeout: 10000,
      socketTimeout: 20000,
    });
    await transport.sendMail({
      from: `"${smtp.name}" <${smtp.email}>`,
      to: target,
      subject: "RevenueOS — sender test",
      text:
        "This is a test email from RevenueOS to check your sending setup.\n\n" +
        "If you received it, your mailbox is configured correctly and campaigns can go out from this address.",
    });
    return {
      ok: true,
      detail: `Sent from ${smtp.email}. Check ${target} (and the spam folder) — if it arrived, your sender is set up correctly.`,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : "unknown error";
    return { ok: false, detail: `Could not send through ${smtp.host}: ${message}` };
  }
}
