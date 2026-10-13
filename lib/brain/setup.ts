import "server-only";
import { loadAccounts } from "./account-store";
import { createAdminClient } from "@/lib/supabase/admin";
import { loadSender } from "./sender-store";

/**
 * What a customer still needs before the Brain can run it. The Brain's own setup check demands
 * an email account and a calling account for every customer.
 *   email   ready when the sender details are complete (a new account is created with the first
 *           campaign), or when an existing account is given under Tool accounts
 *   calls   ready when an existing calling account is used, or the project has a customer email
 *           (its own account is opened with it) AND its own caller line (the number calls are made
 *           from), set on the Calls page
 */
export async function loadSetupGaps(projectId: string): Promise<{ email: boolean; calls: boolean; known: boolean }> {
  const [accounts, sender] = await Promise.all([loadAccounts(projectId), loadSender(projectId)]);
  if (!accounts.available) return { email: false, calls: false, known: false };
  const existingEmailAccount = !accounts.view.missing.some((label) => label.startsWith("Email:"));
  const senderReady = sender.available && sender.view.missing.length === 0;
  return {
    email: !(existingEmailAccount || senderReady),
    calls:
      accounts.view.missing.some((label) => label.startsWith("Calls:")) &&
      !(sender.available && sender.view.loginEmail && (await hasCallerLine(projectId))),
    known: true,
  };
}

/** True when the project's own caller line is complete. Also true before 0009_caller_line.sql is applied, so nothing is blocked by it. */
async function hasCallerLine(projectId: string): Promise<boolean> {
  const { data, error } = await createAdminClient()
    .from("revenueos_brain_accounts")
    .select("calls_vobiz_auth_id, calls_vobiz_auth_token, calls_did")
    .eq("project_id", projectId)
    .maybeSingle<Record<string, string | null>>();
  if (error) return true;
  return !!(data?.calls_vobiz_auth_id?.trim() && data.calls_vobiz_auth_token?.trim() && data.calls_did?.trim());
}
