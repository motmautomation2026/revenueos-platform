import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import {
  ACCOUNT_FIELDS,
  accountsUpdate,
  buildAccountsView,
  type AccountColumn,
  type AccountsInput,
  type AccountsView,
} from "./accounts";

// public.revenueos_brain_accounts: one row per project with that customer's tool accounts.
// Row level security with no policies, so it is reached only with the service-role client, and
// only after the caller has checked that the signed-in user owns the project (requireProject).
// The password is written here and read by the Brain; it is never returned to the browser.

const TABLE = "revenueos_brain_accounts";
const COLUMNS = ACCOUNT_FIELDS.map((field) => field.column).join(", ");

export type AccountsState = { available: boolean; view: AccountsView };

/** `available` is false until the database update that adds the table is applied. */
export const loadAccounts = cache(async (projectId: string): Promise<AccountsState> => {
  try {
    const { data, error } = await createAdminClient()
      .from(TABLE)
      .select(COLUMNS)
      .eq("project_id", projectId)
      .maybeSingle<Record<AccountColumn, string | null>>();
    if (error) return { available: false, view: buildAccountsView(null) };
    return { available: true, view: buildAccountsView(data ?? null) };
  } catch {
    return { available: false, view: buildAccountsView(null) };
  }
});

export async function saveAccounts(
  projectId: string,
  userId: string,
  input: AccountsInput
): Promise<{ ok: true } | { ok: false; error: string }> {
  const { error } = await createAdminClient()
    .from(TABLE)
    .upsert({ project_id: projectId, updated_by: userId, ...accountsUpdate(input) }, { onConflict: "project_id" });
  if (error) return { ok: false, error: "The accounts could not be saved. Try again." };
  return { ok: true };
}
