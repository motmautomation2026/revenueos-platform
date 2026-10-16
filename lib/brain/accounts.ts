import { z } from "zod";

// Each customer's own tool accounts (public.revenueos_brain_accounts,
// supabase/migrations/0004_brain_accounts.sql). Pure definitions; reads and writes are in
// account-store.ts.
//
// `setting` is the name the Brain's tools read the value under. The Brain's worker
// (brain/run_for.py, ACCOUNT_SETTINGS) maps the same columns to the same names; a test keeps
// the two in step.

export type AccountField = {
  column: string;
  setting: string;
  tool: "email" | "calls";
  label: string;
  hint: string;
  secret?: boolean;
  type?: "email";
};

export const ACCOUNT_FIELDS = [
  {
    column: "email_client_reference",
    setting: "CLIENT_REFERENCE",
    tool: "email",
    label: "Account reference",
    hint: "The email tool's reference for this customer's sending account.",
  },
  {
    column: "email_user_email",
    setting: "EMAIL_TOOL_USER_EMAIL",
    tool: "email",
    label: "Admin user email",
    hint: "The user campaigns are created under in the email tool.",
    type: "email",
  },
  {
    column: "calls_client_reference",
    setting: "QUICKHOWL_CLIENT_REFERENCE",
    tool: "calls",
    label: "Account reference",
    hint: "Quickhowl's reference for this customer's calling account.",
  },
  {
    column: "calls_user_email",
    setting: "QUICKHOWL_USER_EMAIL",
    tool: "calls",
    label: "User email",
    hint: "The Quickhowl user campaigns are created under.",
    type: "email",
  },
  {
    column: "calls_user_password",
    setting: "QUICKHOWL_USER_PASSWORD",
    tool: "calls",
    label: "User password",
    hint: "At least 6 characters. Kept on the server and never shown again.",
    secret: true,
  },
  {
    column: "calls_user_full_name",
    setting: "QUICKHOWL_USER_FULL_NAME",
    tool: "calls",
    label: "User full name",
    hint: "The name on the Quickhowl user.",
  },
  {
    column: "calls_user_company",
    setting: "QUICKHOWL_USER_COMPANY",
    tool: "calls",
    label: "Company name",
    hint: "The company name on the Quickhowl account.",
  },
] as const satisfies readonly AccountField[];

export type AccountColumn = (typeof ACCOUNT_FIELDS)[number]["column"];
export type AccountValues = Record<AccountColumn, string>;

export const SECRET_COLUMNS: readonly AccountColumn[] = ACCOUNT_FIELDS.filter((f) => "secret" in f && f.secret).map(
  (f) => f.column
);

const line = (max: number) => z.string().trim().max(max, `Keep it under ${max} characters.`);
const optionalEmail = line(200).refine((value) => !value || z.email().safeParse(value).success, "Enter a valid email.");

/** What the form sends. Every field may be left empty; an empty password keeps the saved one. */
export const accountsSchema = z.object({
  email_client_reference: line(200),
  email_user_email: optionalEmail,
  calls_client_reference: line(200),
  calls_user_email: optionalEmail,
  calls_user_password: z
    .string()
    .max(200, "Keep it under 200 characters.")
    .refine((value) => !value || value.length >= 6, "At least 6 characters."),
  calls_user_full_name: line(120),
  calls_user_company: line(120),
});
export type AccountsInput = z.infer<typeof accountsSchema>;

/** What the browser is given: every value except secrets, plus whether each secret is saved. */
export type AccountsView = {
  values: AccountValues;
  saved: Record<string, boolean>;
  /** Labels of fields the Brain needs that are still empty, e.g. "Email: Account reference". */
  missing: string[];
};

const TOOL_NAME = { email: "Email", calls: "Calls" } as const;

/** Fields the Brain needs before it can run this customer, by label. */
export function missingAccountFields(row: Partial<Record<AccountColumn, string | null>> | null): string[] {
  return ACCOUNT_FIELDS.filter((field) => !(row?.[field.column] ?? "").trim()).map(
    (field) => `${TOOL_NAME[field.tool]}: ${field.label}`
  );
}

export function buildAccountsView(row: Partial<Record<AccountColumn, string | null>> | null): AccountsView {
  const values = {} as AccountValues;
  const saved: Record<string, boolean> = {};
  for (const field of ACCOUNT_FIELDS) {
    const value = (row?.[field.column] ?? "").trim();
    const secret = SECRET_COLUMNS.includes(field.column);
    values[field.column] = secret ? "" : value; // a secret never leaves the server
    saved[field.column] = value !== "";
  }
  return { values, saved, missing: missingAccountFields(row) };
}

/** The columns to write for a submitted form: an empty secret is left out, so the saved one is kept. */
export function accountsUpdate(input: AccountsInput): Partial<Record<AccountColumn, string | null>> {
  const update: Partial<Record<AccountColumn, string | null>> = {};
  for (const field of ACCOUNT_FIELDS) {
    const value = input[field.column];
    if (SECRET_COLUMNS.includes(field.column)) {
      if (value) update[field.column] = value;
    } else {
      update[field.column] = value.trim() || null;
    }
  }
  return update;
}
