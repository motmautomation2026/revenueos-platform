import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";
import {
  CALLER_LINE_SETTINGS,
  CALLS_ACCOUNT_COLUMNS,
  buildCallerView,
  normaliseDid,
  type CallerInput,
  type CallerView,
} from "./caller";

// The app's side of a customer's calling account and caller line (revenueos_brain_accounts).
// Service-role client, and only after the caller has checked that the signed-in user owns the
// project (requireProject). The Vobiz auth token and an existing account's password are written
// here and read by the Brain; they are never returned to the browser.

const TABLE = "revenueos_brain_accounts";
const LINE_COLUMNS = Object.keys(CALLER_LINE_SETTINGS);
const COLUMNS = [...CALLS_ACCOUNT_COLUMNS, ...LINE_COLUMNS].join(", ");

export type CallerState = { available: boolean; view: CallerView };

/** `available` is false until supabase/migrations/0009_caller_line.sql is applied. */
export const loadCaller = cache(async (projectId: string, ownerId: string): Promise<CallerState> => {
  const admin = createAdminClient();
  const empty = { available: false, view: buildCallerView(null, false) };
  try {
    const { data: row, error } = await admin.from(TABLE).select(COLUMNS).eq("project_id", projectId).maybeSingle();
    if (error) return empty;

    // Other projects of the same person that already have a calling account.
    const { data: projects } = await admin
      .from(TABLES.projects)
      .select("id, customer_name, customer_email")
      .eq("owner_id", ownerId)
      .returns<{ id: string; customer_name: string; customer_email: string | null }[]>();
    const others = (projects ?? []).filter((project) => project.id !== projectId);
    const { data: accounts } = others.length
      ? await admin
          .from(TABLE)
          .select("project_id, calls_client_reference")
          .in("project_id", others.map((project) => project.id))
          .returns<{ project_id: string; calls_client_reference: string | null }[]>()
      : { data: [] };
    const options = (accounts ?? [])
      .filter((account) => account.calls_client_reference?.trim())
      .map((account) => ({
        projectId: account.project_id,
        name: others.find((project) => project.id === account.project_id)?.customer_name ?? "Another project",
        reference: account.calls_client_reference!.trim(),
      }));

    const own = (projects ?? []).find((project) => project.id === projectId);
    return {
      available: true,
      view: buildCallerView(row as Record<string, string | null> | null, !!own?.customer_email?.trim(), options),
    };
  } catch {
    return empty;
  }
});

export async function saveCaller(
  projectId: string,
  ownerId: string,
  input: CallerInput
): Promise<{ ok: true; message: string } | { ok: false; error: string }> {
  const admin = createAdminClient();
  const failed = { ok: false as const, error: "The calling account could not be saved. Try again." };

  if (input.mode === "existing") {
    // The source must be another project of the same person.
    const { data: source } = await admin
      .from(TABLES.projects)
      .select("id, customer_name")
      .eq("id", input.sourceProjectId)
      .eq("owner_id", ownerId)
      .neq("id", projectId)
      .maybeSingle<{ id: string; customer_name: string }>();
    if (!source) return { ok: false, error: "That calling account is not available." };
    const { data: account } = await admin
      .from(TABLE)
      .select(CALLS_ACCOUNT_COLUMNS.join(", "))
      .eq("project_id", source.id)
      .maybeSingle<Record<string, string | null>>();
    if (!account?.calls_client_reference?.trim()) return { ok: false, error: "That project has no calling account to use." };

    const copied = Object.fromEntries(CALLS_ACCOUNT_COLUMNS.map((column) => [column, account[column] ?? null]));
    const cleared = Object.fromEntries(LINE_COLUMNS.map((column) => [column, null]));
    const { error } = await admin
      .from(TABLE)
      .upsert({ project_id: projectId, updated_by: ownerId, ...copied, ...cleared }, { onConflict: "project_id" });
    if (error) return failed;
    return { ok: true, message: `Calls will go out from ${source.customer_name}'s calling account.` };
  }

  // Own account: opened with the customer email, so no existing account is named.
  const cleared = Object.fromEntries(CALLS_ACCOUNT_COLUMNS.map((column) => [column, null]));
  const line: Record<string, string | null> = {
    calls_vobiz_auth_id: input.authId.trim(),
    calls_did: normaliseDid(input.did),
  };
  if (input.authToken) line.calls_vobiz_auth_token = input.authToken; // empty keeps the saved one
  const { error } = await admin
    .from(TABLE)
    .upsert({ project_id: projectId, updated_by: ownerId, ...cleared, ...line }, { onConflict: "project_id" });
  if (error) return failed;
  return { ok: true, message: "Caller line saved. It is connected with this customer's next call campaign." };
}
