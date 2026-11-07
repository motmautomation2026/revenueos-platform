import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";

/**
 * Standalone tool testing — a separate pipeline from the Brain's run/orchestration. The platform calls
 * the tool integration APIs directly (QUICKHOWL_URL/KEY), using an existing project's configured line.
 * No run, no task, no approval. Credentials for the tools come from the platform env; the per-project
 * sender/line comes from revenueos_brain_accounts (read with the service-role key, owner-checked above).
 *
 * The call test is live. The email test needs the sender/SMTP assembly and is not wired yet.
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

/** Email test: not wired yet (needs the sender/SMTP assembly + the email API's immediate-send path). */
export async function sendTestEmail(projectId: string, target: string): Promise<TestResult> {
  if (!projectId || !target) return { ok: false, detail: "Invalid request." };
  return {
    ok: false,
    pending: true,
    detail: "Email testing is coming next — it needs the sender/SMTP wiring and the email tool's immediate-send path.",
  };
}
