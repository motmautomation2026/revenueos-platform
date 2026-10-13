// Moves the tool accounts typed into brain/.env onto ONE project in the database, then removes
// them from brain/.env. Run once, for the customer those accounts belong to:
//
//   node scripts/import-brain-accounts.mjs <project-id>
//
// Needs supabase/migrations/0004_brain_accounts.sql applied. Reads the server key from
// .env.local. Prints setting names only, never values. brain/.env is changed only after the
// database has confirmed the save, and a project that already has accounts is never overwritten.

import { readFileSync, writeFileSync } from "node:fs";
import { createClient } from "@supabase/supabase-js";

// The same pairs as lib/brain/accounts.ts and brain/run_for.py (ACCOUNT_SETTINGS).
const SETTINGS = {
  CLIENT_REFERENCE: "email_client_reference",
  EMAIL_TOOL_USER_EMAIL: "email_user_email",
  QUICKHOWL_CLIENT_REFERENCE: "calls_client_reference",
  QUICKHOWL_USER_EMAIL: "calls_user_email",
  QUICKHOWL_USER_PASSWORD: "calls_user_password",
  QUICKHOWL_USER_FULL_NAME: "calls_user_full_name",
  QUICKHOWL_USER_COMPANY: "calls_user_company",
};
const BRAIN_ENV = "brain/.env";
const LINE = /^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/;

const stop = (message) => {
  console.error(`STOPPED: ${message}`);
  process.exit(1);
};

const unquote = (value) => value.trim().replace(/^(['"])(.*)\1$/, "$2");
const readEnv = (path) => {
  const values = {};
  for (const line of readFileSync(path, "utf8").split(/\r?\n/)) {
    const match = line.match(LINE);
    if (match) values[match[1]] = unquote(match[2]);
  }
  return values;
};

const projectId = (process.argv[2] ?? "").toLowerCase();
if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/.test(projectId)) {
  stop("give the project id: node scripts/import-brain-accounts.mjs <project-id>");
}

const app = readEnv(".env.local");
const brain = readEnv(BRAIN_ENV);
if (!app.NEXT_PUBLIC_SUPABASE_URL || !app.SUPABASE_SERVICE_ROLE_KEY) stop(".env.local has no Supabase URL or server key.");

const row = { project_id: projectId };
const found = [];
for (const [setting, column] of Object.entries(SETTINGS)) {
  if (brain[setting]) {
    row[column] = brain[setting];
    found.push(setting);
  }
}
if (!found.length) stop(`${BRAIN_ENV} holds no tool account settings. Nothing to move.`);

const db = createClient(app.NEXT_PUBLIC_SUPABASE_URL, app.SUPABASE_SERVICE_ROLE_KEY, { auth: { persistSession: false } });

const { data: project, error: projectError } = await db
  .from("revenueos_projects")
  .select("customer_name, brain_customer_key")
  .eq("id", projectId)
  .maybeSingle();
if (projectError || !project) stop("no project with that id.");

const { data: existing, error: readError } = await db
  .from("revenueos_brain_accounts")
  .select("project_id")
  .eq("project_id", projectId)
  .maybeSingle();
if (readError) stop("the accounts table could not be read. Apply supabase/migrations/0004_brain_accounts.sql first.");
if (existing) stop(`${project.customer_name} already has tool accounts. Nothing was changed; edit them in the app.`);

const { data: saved, error: saveError } = await db.from("revenueos_brain_accounts").insert(row).select("project_id");
if (saveError || !saved?.length) stop("the accounts could not be saved. brain/.env was not changed.");

const kept = readFileSync(BRAIN_ENV, "utf8")
  .split(/\r?\n/)
  .filter((line) => !(line.match(LINE)?.[1] in SETTINGS));
writeFileSync(BRAIN_ENV, kept.join("\n").replace(/\n*$/, "\n"));

console.log(`Saved for ${project.customer_name} (${project.brain_customer_key}): ${found.join(", ")}`);
console.log(`Removed from ${BRAIN_ENV}: ${found.join(", ")}`);
