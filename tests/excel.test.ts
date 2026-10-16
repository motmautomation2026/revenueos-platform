import assert from "node:assert/strict";
import { test } from "node:test";
import ExcelJS from "exceljs";
import { SECRET_PROVIDED, answerForSharing, isSecretQuestion } from "../lib/checklist-secrets";
import { buildChecklistWorkbook, parseChecklistWorkbook, questionNumber } from "../lib/files/excel";

const items = [
  { code: "Q-1", category: "Business basics", question: "Legal name?", options: [], answer: "Example Pvt Ltd" },
  { code: "Q-2", category: "Offer and value", question: "Price position?", options: ["Premium", "Mid", "Budget"], answer: null },
];

test("export has exactly the four customer-facing columns", async () => {
  const workbook = new ExcelJS.Workbook();
  await workbook.xlsx.load((await buildChecklistWorkbook(items)) as unknown as ExcelJS.Buffer);
  assert.equal(workbook.worksheets.length, 1);
  const sheet = workbook.worksheets[0];
  const row = (n: number) => (sheet.getRow(n).values as unknown[]).slice(1);
  assert.deepEqual(row(1), ["#", "Category", "Question", "Answer"]);
  assert.deepEqual(row(2), ["Q-1", "Business basics", "Legal name?", "Example Pvt Ltd"]);
  assert.deepEqual(row(3), ["Q-2", "Offer and value", "Price position? (Options: Premium / Mid / Budget)", ""]);
  assert.equal(sheet.actualColumnCount, 4);
});

test("a downloaded file can be read back", async () => {
  const rows = await parseChecklistWorkbook(await buildChecklistWorkbook(items));
  assert.deepEqual(rows, [
    { number: 1, answer: "Example Pvt Ltd" },
    { number: 2, answer: "" },
  ]);
});

test("import finds columns by header name, in any order and case", async () => {
  const workbook = new ExcelJS.Workbook();
  workbook.addWorksheet("Sheet1").addRows([
    ["Customer checklist"],
    ["YOUR ANSWER", "Question", "ID"],
    ["Mid", "Price position?", 2],
    ["Something", "Unknown row", "abc"],
  ]);
  const rows = await parseChecklistWorkbook(Buffer.from(await workbook.xlsx.writeBuffer()));
  assert.deepEqual(rows, [
    { number: 2, answer: "Mid" },
    { number: null, answer: "Something" },
  ]);
});

test("question numbers are read from several spellings", () => {
  assert.equal(questionNumber("Q-12"), 12);
  assert.equal(questionNumber("q 7"), 7);
  assert.equal(questionNumber("3"), 3);
  assert.equal(questionNumber("Question"), null);
});

test("secret questions are recognised by their wording, and their answers are masked for sharing", () => {
  for (const q of ["What is the SMTP password (or app password)?", "Mailbox Password", "API key for the CRM?", "Authentication token"]) {
    assert.ok(isSecretQuestion(q), q);
  }
  for (const q of ["What is the SMTP host?", "What is the SMTP username?", "Who passes leads to sales?", "Sender email address"]) {
    assert.ok(!isSecretQuestion(q), q);
  }
  assert.equal(answerForSharing("SMTP password?", "abc"), SECRET_PROVIDED);
  assert.equal(answerForSharing("SMTP password?", "  "), null);
  assert.equal(answerForSharing("SMTP host?", "smtp.example.com"), "smtp.example.com");
});
