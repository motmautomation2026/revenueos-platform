import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  contactColumns,
  contactSchema,
  normalisePhone,
  readStaging,
  stagingColumns,
  stagingSchema,
} from "../lib/brain/staging";

const staging = { mode: "staging" as const, companies: 5, people: 10 };

test("staging is two numbers, and the app, the database and the Brain's worker agree on them", () => {
  const columns = Object.keys(stagingColumns(stagingSchema.parse(staging)));
  assert.deepEqual(columns, ["run_mode", "staging_companies", "staging_people"]);

  const sql = readFileSync("supabase/migrations/0008_project_run_mode.sql", "utf8");
  for (const column of columns) assert.match(sql, new RegExp(`add column if not exists ${column} `), column);

  const worker = readFileSync("brain/run_for.py", "utf8");
  const selected = worker.match(/\.select\("(run_mode[^"]*)"\)/)?.[1].split(/,\s*/) ?? [];
  assert.deepEqual(selected.sort(), [...columns].sort());
});

test("the Brain caps a tool task at the staging limit in real mode too", () => {
  const kit = readFileSync("brain/runner_kit.py", "utf8");
  assert.match(kit, /STAGING_LIMITS = \{"accounts": "STAGING_COMPANIES", "contacts": "STAGING_PEOPLE"\}/);
  assert.match(kit, /return min\(target, limit\) if limit else target/);
  // Staging does not switch the Brain into its test mode: who is contacted is what is on each profile.
  const worker = readFileSync("brain/run_for.py", "utf8");
  assert.match(worker, /values\["BRAIN_TEST_MODE"\] = "true" if test_mode else "false"/);
});

test("the two limits are checked; production needs nothing more", () => {
  assert.ok(stagingSchema.safeParse(staging).success);
  assert.ok(!stagingSchema.safeParse({ ...staging, companies: 0 }).success);
  assert.ok(!stagingSchema.safeParse({ ...staging, people: 500 }).success);
  assert.ok(stagingSchema.safeParse({ mode: "production", companies: 5, people: 10 }).success);
});

test("the saved choice is read back for the form, and production is the default", () => {
  assert.deepEqual(readStaging({}), { available: false, mode: "production", companies: 5, people: 10 });
  assert.deepEqual(readStaging({ run_mode: "staging", staging_companies: 8, staging_people: 12 }), {
    available: true,
    mode: "staging",
    companies: 8,
    people: 12,
  });
});

test("an edited profile is saved the way the Brain's calling and email steps read it", () => {
  assert.equal(normalisePhone("9800000001"), "+919800000001");
  assert.equal(normalisePhone("+91 98000-00002"), "+919800000002");
  for (const bad of ["12345", "5800000001", "+1 415 555 0100"]) assert.equal(normalisePhone(bad), null, bad);

  assert.deepEqual(contactColumns(contactSchema.parse({ email: " Me@MOTM.tech ", phone: "098000 00003" })), {
    email: "me@motm.tech",
    phone_number: "+919800000003",
  });
  assert.deepEqual(contactColumns(contactSchema.parse({ email: "", phone: "" })), { email: null, phone_number: null });
  assert.ok(!contactSchema.safeParse({ email: "not-an-email", phone: "" }).success);
  assert.ok(!contactSchema.safeParse({ email: "", phone: "12345" }).success);

  // The columns are the people table's own.
  const store = readFileSync("brain/supabase_store.py", "utf8");
  assert.match(store, /phone_number/);
});

test("both calling-account options are checked, and the app and the worker agree on the caller line", async () => {
  const { CALLER_LINE_SETTINGS, buildCallerView, callerSchema, normaliseDid } = await import("../lib/brain/caller");
  const worker = readFileSync("brain/run_for.py", "utf8").match(/^CALLER_LINE_SETTINGS = \{([\s\S]*?)^\}/m)?.[1] ?? "";
  assert.deepEqual(Object.fromEntries([...worker.matchAll(/"(\w+)":\s*"(\w+)"/g)].map((m) => [m[1], m[2]])), CALLER_LINE_SETTINGS);
  const sql = readFileSync("supabase/migrations/0009_caller_line.sql", "utf8");
  for (const column of Object.keys(CALLER_LINE_SETTINGS)) assert.match(sql, new RegExp(`add column if not exists ${column} text`), column);
  // The Brain's calling step sends the line under these same names.
  const calling = readFileSync("brain/quickhowl.py", "utf8");
  for (const setting of Object.values(CALLER_LINE_SETTINGS)) assert.ok(calling.includes(`"${setting}"`), setting);

  assert.equal(normaliseDid("+91 80653-82389"), "+918065382389");
  assert.equal(normaliseDid("abc"), null);

  const own = { mode: "own" as const, authId: "MA_1", authToken: "tok", did: "+918065382389", sourceProjectId: "", tokenSaved: false };
  assert.ok(callerSchema.safeParse(own).success);
  assert.match(callerSchema.safeParse({ ...own, authToken: "" }).error!.issues[0].message, /auth token/);
  assert.ok(callerSchema.safeParse({ ...own, authToken: "", tokenSaved: true }).success, "an empty token keeps the saved one");
  assert.match(callerSchema.safeParse({ ...own, did: "12" }).error!.issues[0].message, /country code/);
  assert.match(callerSchema.safeParse({ ...own, mode: "existing" }).error!.issues[0].message, /Choose which calling account/);
  assert.ok(callerSchema.safeParse({ ...own, mode: "existing", sourceProjectId: "22222222-2222-4222-8222-222222222222" }).success);

  const options = [{ projectId: "p-1", name: "Anvika Ai", reference: "motm-tech" }];
  assert.deepEqual(
    (({ mode, ready }) => ({ mode, ready }))(buildCallerView(null, true, options)),
    { mode: "none", ready: false },
    "a new customer has no number to call from"
  );
  const line = { calls_vobiz_auth_id: "MA_1", calls_vobiz_auth_token: "tok-secret", calls_did: "+918065382389" };
  const ownView = buildCallerView(line, true, options);
  assert.equal(ownView.mode, "own");
  assert.equal(ownView.ready, true);
  assert.ok(!JSON.stringify(ownView).includes("tok-secret"), "the token never leaves the server");
  assert.equal(buildCallerView(line, false).ready, false, "its own account also needs the customer email");
  const existing = buildCallerView({ calls_client_reference: "motm-tech", calls_user_password: "pw-secret" }, false, options);
  assert.deepEqual([existing.mode, existing.ready, existing.sourceProjectId], ["existing", true, "p-1"]);
  assert.ok(!JSON.stringify(existing).includes("pw-secret"));
});

test("a stopped call campaign is explained in plain words", async () => {
  const { explainCallProblem } = await import("../lib/brain/call-errors");
  assert.equal(explainCallProblem(""), null);
  assert.match(
    explainCallProblem("Quickhowl store blocked: No usable phone number: This client has no connected phone number.")!.what,
    /no phone number to call from/
  );
  assert.match(
    explainCallProblem("HTTP 409. Conflict: reference reused with different content, or a different user email for this client. Detail: {'detail': 'This client_reference already belongs to an account with a different user.email.'}")!.what,
    /belongs to a different user/
  );
  assert.match(explainCallProblem("Call profile contains internal words {'key_points': ['segments']}.")!.next, /written again/);
  const unknown = explainCallProblem("Something nobody has seen before")!;
  assert.match(unknown.next, /Technical details/);
  // Nothing technical leaks into the plain version.
  for (const raw of ["HTTP 409 Conflict client_reference", "No usable phone number auth_id did"]) {
    const problem = explainCallProblem(raw)!;
    assert.doesNotMatch(problem.what + problem.next, /HTTP|client_reference|auth_id|\bdid\b/);
  }
});
