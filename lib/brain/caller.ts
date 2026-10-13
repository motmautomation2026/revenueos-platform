import { z } from "zod";

// The calling account a customer's AI calls go out from (supabase/migrations/0009_caller_line.sql).
// Two ways to have one:
//   own       the customer's own account, opened with the customer email, with its own caller
//             line: three values from the telephony provider (Vobiz)
//   existing  another project's calling account, which already has a line connected
// The Brain's worker reads the result from revenueos_brain_accounts (brain/run_for.py).

/** Column -> the setting the Brain's calling step reads it under (run_for.py CALLER_LINE_SETTINGS). */
export const CALLER_LINE_SETTINGS = {
  calls_vobiz_auth_id: "QUICKHOWL_VOBIZ_AUTH_ID",
  calls_vobiz_auth_token: "QUICKHOWL_VOBIZ_AUTH_TOKEN",
  calls_did: "QUICKHOWL_DID",
} as const;

/** The columns that say WHICH calling account is used. Empty: the customer's own, opened with its email. */
export const CALLS_ACCOUNT_COLUMNS = [
  "calls_client_reference",
  "calls_user_email",
  "calls_user_password",
  "calls_user_full_name",
  "calls_user_company",
] as const;

/** A phone number in international form, e.g. +918065382389. Null if it is not one. */
export function normaliseDid(value: string): string | null {
  const digits = value.replace(/[\s\-().]/g, "");
  const match = digits.match(/^\+?(\d{8,15})$/);
  return match ? `+${match[1]}` : null;
}

export const callerSchema = z
  .object({
    mode: z.enum(["own", "existing"]),
    authId: z.string().trim().max(200),
    /** Empty keeps the saved token. */
    authToken: z.string().max(500),
    did: z.string().trim().max(30),
    /** The project whose calling account is used, in "existing" mode. */
    sourceProjectId: z.string(),
    /** True when a token is already saved, so an empty one is allowed. */
    tokenSaved: z.boolean().default(false),
  })
  .superRefine((value, context) => {
    if (value.mode === "existing") {
      if (!z.uuid().safeParse(value.sourceProjectId).success) {
        context.addIssue({ code: "custom", path: ["sourceProjectId"], message: "Choose which calling account to use." });
      }
      return;
    }
    if (!value.authId) context.addIssue({ code: "custom", path: ["authId"], message: "Enter the Vobiz account ID." });
    if (!value.authToken && !value.tokenSaved) {
      context.addIssue({ code: "custom", path: ["authToken"], message: "Enter the Vobiz auth token." });
    }
    if (!normaliseDid(value.did)) {
      context.addIssue({ code: "custom", path: ["did"], message: "Enter the caller number with its country code, e.g. +918065382389." });
    }
  });
export type CallerInput = z.infer<typeof callerSchema>;

export type CallerView = {
  /** own: its own account and line. existing: another account. none: nothing usable yet. */
  mode: "own" | "existing" | "none";
  authId: string;
  did: string;
  tokenSaved: boolean;
  /** In "existing" mode: the account's reference, and the project it was taken from when known. */
  existingReference: string;
  sourceProjectId: string;
  /** Other projects of this user whose calling account can be used. */
  options: { projectId: string; name: string }[];
  /** True when calls can go out: an existing account, or an own account with a complete line. */
  ready: boolean;
};

type Row = Partial<Record<string, string | null>>;

/** What the Calls page shows. The auth token never leaves the server. */
export function buildCallerView(
  row: Row | null,
  hasCustomerEmail: boolean,
  options: { projectId: string; name: string; reference: string }[] = []
): CallerView {
  const text = (column: string) => (row?.[column] ?? "").trim();
  const reference = text("calls_client_reference");
  const authId = text("calls_vobiz_auth_id");
  const did = text("calls_did");
  const tokenSaved = !!text("calls_vobiz_auth_token");
  const lineComplete = !!(authId && did && tokenSaved);
  const mode = reference ? "existing" : lineComplete ? "own" : "none";
  return {
    mode,
    authId,
    did,
    tokenSaved,
    existingReference: reference,
    sourceProjectId: options.find((option) => option.reference === reference)?.projectId ?? "",
    options: options.map(({ projectId, name }) => ({ projectId, name })),
    ready: mode === "existing" || (mode === "own" && hasCustomerEmail),
  };
}
