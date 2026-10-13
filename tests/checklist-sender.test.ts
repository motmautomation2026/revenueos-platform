import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  SENDER_CATEGORY,
  SENDER_CODES,
  SENDER_ITEMS,
  SENDER_SECURITY,
  buildSenderView,
  senderAnswers,
  senderSchema,
  withFixedSenderQuestions,
} from "../lib/checklist-sender";
import { isSecretQuestion } from "../lib/checklist-secrets";

const question = (category: string, text: string) => ({ category, question: text, options: [] as string[], prefilled_answer: null, reason: null });

test("the app and the Brain's worker read the same sender codes and security choices", () => {
  const worker = readFileSync("brain/run_for.py", "utf8");
  const codes = worker.match(/^SENDER_CODES = \{([\s\S]*?)^\}/m)?.[1] ?? "";
  assert.deepEqual([...codes.matchAll(/"(SENDER-[A-Z-]+)":/g)].map((m) => m[1]), SENDER_CODES);

  const security = worker.match(/^SENDER_SECURITY = \{([\s\S]*?)^\}/m)?.[1] ?? "";
  const brain = Object.fromEntries([...security.matchAll(/"([^"]+)":\s*\("(\w+)",\s*"(\d+)"\)/g)].map((m) => [m[1], { mode: m[2], port: Number(m[3]) }]));
  assert.deepEqual(brain, SENDER_SECURITY);
});

test("only values the email tool accepts are offered", () => {
  // Read from the hosted tool's own rules as recorded in its reference: two modes, two ports.
  assert.deepEqual(Object.values(SENDER_SECURITY).map((s) => [s.mode, s.port]), [["STARTTLS", 587], ["IMPLICIT_TLS", 465]]);
  const prompt = readFileSync("prompts/checklist.md", "utf8");
  assert.match(prompt, /STARTTLS \(port 587\) \/ SSL\/TLS \(port 465\)/);
  assert.doesNotMatch(prompt, /SSL \/ TLS \/ STARTTLS \/ None/);
});

test("the agent decides whether to ask; the questions themselves are fixed", () => {
  const plain = withFixedSenderQuestions([question("Business basics", "Website?"), question("Targets", "Goal?")]);
  assert.deepEqual(plain.map((q) => q.code), ["Q-1", "Q-2"]);

  const asked = withFixedSenderQuestions([
    question("Business basics", "Website?"),
    question("Email sender setup", "What SMTP port?"),
    question("Email Sender Setup", "Reply-to address?"),
    question("Targets", "Goal?"),
  ]);
  assert.deepEqual(asked.map((q) => q.code), ["Q-1", "Q-2", ...SENDER_CODES]);
  assert.ok(asked.slice(2).every((q) => q.category === SENDER_CATEGORY));
  assert.ok(!asked.some((q) => /port\?|reply-to/i.test(q.question)), "the agent's own sender wording is dropped");
});

test("the password question is treated as a secret everywhere", () => {
  const password = SENDER_ITEMS.find((item) => item.key === "password")!;
  assert.ok(isSecretQuestion(password.question));
  assert.ok(SENDER_ITEMS.filter((item) => item.key !== "password").every((item) => !isSecretQuestion(item.question)));
});

test("the page is told what is missing, and never the password", () => {
  const empty = buildSenderView({}, null);
  assert.deepEqual(empty.missing, ["Customer email", "Sender name", "Sender email", "SMTP host", "SMTP security", "SMTP password"]);

  const answers = {
    "SENDER-NAME": "Durel Sales",
    "SENDER-EMAIL": "sales@durel.com",
    "SENDER-SMTP-HOST": "smtp.durel.com",
    "SENDER-SMTP-SECURITY": "STARTTLS (port 587)",
    "SENDER-SMTP-PASSWORD": "mail-secret",
  };
  const ready = buildSenderView(answers, "owner@durel.com");
  assert.deepEqual(ready.missing, [], "the IMAP host is optional");
  assert.equal(ready.values.password, "");
  assert.equal(ready.passwordSaved, true);
  assert.ok(!JSON.stringify(ready).includes("mail-secret"));

  const old = buildSenderView({ ...answers, "SENDER-SMTP-SECURITY": "TLS" }, "owner@durel.com");
  assert.deepEqual(old.missing, ["SMTP security"], "an old answer the tool would refuse must be chosen again");

  assert.deepEqual(buildSenderView({}, null, true).missing, [], "an existing email account needs no sender details");
});

test("saving keeps the stored password when the field is left empty", () => {
  const input = senderSchema.parse({ loginEmail: "owner@durel.com", name: " Durel Sales ", email: "sales@durel.com", host: "smtp.durel.com", security: "SSL/TLS (port 465)", password: "", imapHost: "", style: "Same email for everyone" });
  const answers = senderAnswers(input);
  assert.ok(!("SENDER-SMTP-PASSWORD" in answers));
  assert.equal(answers["SENDER-NAME"], "Durel Sales");
  assert.equal(answers["SENDER-IMAP-HOST"], null);
  assert.equal(answers["SENDER-STYLE"], "Same email for everyone");
  assert.equal(senderAnswers({ ...input, password: "new-secret" })["SENDER-SMTP-PASSWORD"], "new-secret");
});

test("bad hosts, emails and security choices are refused", () => {
  const base = { loginEmail: "", name: "", email: "", host: "", security: "", password: "", imapHost: "", style: "" };
  assert.ok(senderSchema.safeParse(base).success, "everything may be left empty");
  assert.ok(!senderSchema.safeParse({ ...base, host: "http://smtp.x.com/path" }).success);
  assert.ok(!senderSchema.safeParse({ ...base, host: "127.0.0.1" }).success);
  assert.ok(!senderSchema.safeParse({ ...base, email: "not-an-email" }).success);
  assert.ok(!senderSchema.safeParse({ ...base, security: "None" }).success);
  assert.ok(!senderSchema.safeParse({ ...base, style: "Fancy" }).success);
  assert.equal(buildSenderView({}, null).values.style, "Personalised for each recipient", "personalised unless chosen otherwise");
});

test("the email style names match what the Brain's worker reads", () => {
  const worker = readFileSync("brain/run_for.py", "utf8").match(/^SENDER_STYLE = \{([\s\S]*?)^\}/m)?.[1] ?? "";
  const brain = Object.fromEntries([...worker.matchAll(/"([^"]+)":\s*"(\w+)"/g)].map((m) => [m[1], m[2]]));
  assert.deepEqual(brain, { "Personalised for each recipient": "HYPER_PERSONALIZED", "Same email for everyone": "STANDARD" });
});
