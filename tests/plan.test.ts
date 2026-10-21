import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import Ajv from "ajv";
import { SOURCE_RULES, buildFilledChecklistBlock, buildDocumentBlock, laterStepFiles } from "../lib/agents/build-inputs";
import { DOC_TYPES, TRUST, guessDocType, trustOf } from "../lib/files/rules";
import { normalize } from "../lib/agents/normalize";
import { buildPlanDocument, formatRange } from "../lib/plan/document";
import { renderPlanHtml } from "../lib/plan/render-html";

const schema = (agent: string) => JSON.parse(readFileSync(`schemas/${agent}.schema.json`, "utf8"));
const ajv = new Ajv({ allErrors: true, strict: false });

for (const agent of ["diagnosis", "strategy", "execution"]) {
  test(`${agent} schema compiles and an empty answer is repaired into a valid one`, () => {
    const validate = ajv.compile(schema(agent));
    const { data, repairs } = normalize("{}", schema(agent));
    assert.ok(validate(data), JSON.stringify(validate.errors));
    assert.ok(repairs.length > 5);
  });
}

test("diagnosis gate field is a real boolean after repair", () => {
  const { data } = normalize(
    JSON.stringify({ readiness: { can_proceed_to_strategy: "false", blockers: [{ blocker: "b" }] } }),
    schema("diagnosis")
  );
  const readiness = (data as { readiness: { can_proceed_to_strategy: boolean; blockers: object[] } }).readiness;
  assert.equal(readiness.can_proceed_to_strategy, false);
  assert.deepEqual(readiness.blockers, [{ blocker: "b", question_to_resolve: "" }]);
});

test("numbers use Indian grouping and the rupee sign", () => {
  assert.equal(formatRange(100000, 500000, "₹"), "₹1,00,000 – ₹5,00,000");
  assert.equal(formatRange(100000, null, "INR"), "₹1,00,000");
  assert.equal(formatRange(20, 30, "%"), "20 – 30 %");
  assert.equal(formatRange(45, 45, "days"), "45 days");
  assert.equal(formatRange(null, null, "days"), "");
});

const sample = {
  customerName: "Example <b>Co</b>",
  diagnosis: {
    business_understanding: { summary: 'They fix pumps. <script>alert("x")</script>' },
    key_numbers: [{ label: "Order value", value: "₹4 lakh", source_ref: "checklist:Q-30" }],
    data_gaps: [{ item: "Margin", why_it_matters: "Needed for targets", source_ref: "checklist:Q-31" }],
  },
  strategy: { title: "GTM plan", priorities: [{ tier: "P1", segment: "Refineries", rationale: "Best margin", evidence: [] }] },
  execution: {
    roadmap: [
      {
        phase: "Foundation",
        months: "Months 1–3",
        activities: ["Build account list"],
        milestones: [{ milestone: "Valid enquiries", metric: "enquiries", value_low: 8, value_high: 12, basis: "motm_proposal" }],
      },
    ],
  },
};
const internal = { camNotes: ["Confirm in kickoff: lead definition"], flags: [], overrideReason: "CAM confirmed scope by phone", repairs: [] };

test("the document keeps the part order and drops empty sections", () => {
  const doc = buildPlanDocument({ ...sample, internal });
  assert.deepEqual(doc.parts.map((p) => p.id), ["part-1", "part-2", "part-3", "internal"]);
  assert.deepEqual(doc.parts[0].sections.map((s) => s.id), ["business-understanding", "key-numbers"]);
  assert.deepEqual(doc.parts[3].sections.map((s) => s.id), ["cam-notes", "override", "data-gaps"]);
});

test("the customer-facing document has no internal part", () => {
  const html = renderPlanHtml(buildPlanDocument(sample), "footer");
  assert.ok(!html.includes("Internal (MOTM only)"));
  assert.ok(!html.includes("CAM confirmed scope"));
  assert.ok(!html.includes("Needed for targets"));
});

test("HTML output escapes agent text and is self-contained", () => {
  const html = renderPlanHtml(buildPlanDocument({ ...sample, internal }), "Generated on 1 January 2026");
  assert.ok(!html.includes("<script"));
  assert.ok(html.includes("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"));
  assert.ok(html.includes("Example &lt;b&gt;Co&lt;/b&gt;"));
  assert.ok(html.includes("8 – 12 enquiries"));
  assert.ok(html.includes("CAM confirmed scope by phone"));
  assert.ok(!/<link|src=|@import/.test(html));
});

test("agent inputs follow the document and checklist block formats", () => {
  const block = buildDocumentBlock({ customer_name: "Example Co", engagement_type: "Full funnel" }, [
    { doc_type: "other", file_name: "misc.txt", extracted_text: "Misc" },
    { doc_type: "bd_proposal", file_name: "p.pdf", extracted_text: "Proposal text" },
    { doc_type: "bd_handover", file_name: "h.pdf", extracted_text: "Handover text" },
    { doc_type: "signed_scope", file_name: "s.pdf", extracted_text: null },
  ]);
  assert.equal(
    block,
    "CUSTOMER: Example Co\nENGAGEMENT TYPE: Full funnel\n\n" +
      SOURCE_RULES +
      "\n\n" +
      `=== BD HANDOVER FORM (h.pdf) ===\n[How to use: ${TRUST.agreed.rule}]\nHandover text\n\n` +
      `=== BD PROPOSAL (p.pdf) ===\n[How to use: ${TRUST.agreed.rule}]\nProposal text\n\n` +
      `=== OTHER DOCUMENT (misc.txt) ===\n[How to use: ${TRUST.reference.rule}]\nMisc`
  );
  assert.equal(
    buildFilledChecklistBlock([
      { code: "Q-1", category: "Business basics", question: "Legal name?", answer: "Example Pvt Ltd" },
      { code: "Q-2", category: "Buyers", question: "Who decides?", answer: " " },
    ]),
    "=== FILLED CHECKLIST ===\nQ-1 [Business basics] Legal name?\nAnswer: Example Pvt Ltd\n" +
      "Q-2 [Buyers] Who decides?\nAnswer: (not answered)"
  );
});

test("a password answer is never sent to the AI", () => {
  const block = buildFilledChecklistBlock([
    { code: "Q-41", category: "Email sender setup", question: "What is the SMTP host?", answer: "smtp.example.com" },
    { code: "Q-42", category: "Email sender setup", question: "What is the SMTP password (or app password)?", answer: "s3cret-value" },
    { code: "Q-43", category: "Email sender setup", question: "What is the SMTP password for the backup mailbox?", answer: "" },
  ]);
  assert.ok(block.includes("Answer: smtp.example.com"));
  assert.ok(!block.includes("s3cret-value"));
  assert.ok(block.includes("Q-42 [Email sender setup] What is the SMTP password (or app password)?\nAnswer: (provided; kept hidden)"));
  assert.ok(block.endsWith("Answer: (not answered)"));
});

test("each kind of document tells the agents how far to trust it", () => {
  const block = buildDocumentBlock({ customer_name: "Example Co", engagement_type: "Full funnel" }, [
    { doc_type: "email_templates", file_name: "old-emails.docx", extracted_text: "Dear sir, 50% off this week" },
    { doc_type: "brochure", file_name: "brochure.pdf", extracted_text: "The best welding machines in India" },
    { doc_type: "bd_proposal", file_name: "p.pdf", extracted_text: "Proposal text" },
  ]);
  // Agreed documents first, the customer's own material next; style examples are not given to planning.
  assert.ok(block.indexOf("BD PROPOSAL") < block.indexOf("BROCHURE"));
  assert.match(block, /=== BROCHURE \(brochure\.pdf\) ===\n\[How to use: The customer's own material.*not commitments/);
  assert.ok(!block.includes("EXISTING EMAIL TEMPLATES"));
  assert.match(TRUST.style.rule, /Never take facts/);
  assert.match(block, /write 'not stated'/);
  // With no documents there is nothing to explain.
  assert.ok(!buildDocumentBlock({ customer_name: "X", engagement_type: "Full funnel" }, []).includes(SOURCE_RULES));
});

test("every document type has a trust level, and the database update adds the new ones", () => {
  for (const type of DOC_TYPES) assert.ok(TRUST[trustOf(type.value)], type.value);
  assert.equal(trustOf("bd_handover"), "agreed");
  assert.equal(trustOf("product_catalogue"), "customer");
  assert.equal(trustOf("email_templates"), "style");
  const sql = readFileSync("supabase/migrations/0001_init.sql", "utf8") + readFileSync("supabase/migrations/0010_more_document_types.sql", "utf8");
  for (const type of DOC_TYPES) assert.ok(sql.includes(`'${type.value}'`), type.value);
});

test("a document's type is guessed from its file name", () => {
  assert.equal(guessDocType("Durel Product Catalogue 2026.pdf"), "product_catalogue");
  assert.equal(guessDocType("company-profile.pdf"), "company_profile");
  assert.equal(guessDocType("Brochure_HDPE.pdf"), "brochure");
  assert.equal(guessDocType("Case Study - Water Utility.docx"), "case_study");
  assert.equal(guessDocType("cold email templates.docx"), "email_templates");
  assert.equal(guessDocType("Sales deck.pptx"), "presentation");
  assert.equal(guessDocType("BD Handover.pdf"), "bd_handover");
  assert.equal(guessDocType("notes.txt"), "other");
});

test("planning agents read a large customer document as its fact sheet, and never the email templates", () => {
  const long = "x".repeat(25_000);
  const block = buildDocumentBlock({ customer_name: "Example Co", engagement_type: "Full funnel" }, [
    { doc_type: "bd_proposal", file_name: "p.pdf", extracted_text: "Proposal text" },
    { doc_type: "product_catalogue", file_name: "catalogue.pdf", extracted_text: long, ai_summary: "Products and services\nCNC router R-630 (page 4)" },
    { doc_type: "email_templates", file_name: "old-emails.docx", extracted_text: "Dear sir, 50% off" },
  ]);
  assert.match(block, /=== PRODUCT CATALOGUE \(catalogue\.pdf\): FACT SHEET ===\n\[How to use: .*fact sheet of a 25,000-character document/);
  assert.ok(block.includes("CNC router R-630 (page 4)"));
  assert.ok(!block.includes(long), "the full catalogue is not repeated");
  assert.ok(!block.includes("50% off") && !block.includes("EXISTING EMAIL TEMPLATES"), "email templates are kept for the email writer only");
  assert.ok(block.includes("Proposal text"), "agreed documents are always read in full");
});

test("the agents' instructions name the customer's own material as a source", () => {
  const checklist = readFileSync("prompts/checklist.md", "utf8");
  const diagnosis = readFileSync("prompts/diagnose.md", "utf8");
  assert.match(checklist, /customer's own material \(company profile, product catalogue, brochure, presentation, case study\)/);
  assert.match(diagnosis, /"catalogue", "brochure", "presentation:<slide>", "case_study"/);
  assert.match(diagnosis, /marketing claims are not facts/);
});

test("strategy and execution do not read the customer's material again", () => {
  const files = [
    { doc_type: "bd_proposal" as const, file_name: "p.pdf", extracted_text: "Proposal text" },
    { doc_type: "cam_notes" as const, file_name: "cam.txt", extracted_text: "CAM text" },
    { doc_type: "product_catalogue" as const, file_name: "c.pdf", extracted_text: "Catalogue text" },
    { doc_type: "brochure" as const, file_name: "b.pdf", extracted_text: "Brochure text" },
    { doc_type: "other" as const, file_name: "o.txt", extracted_text: "Other text" },
  ];
  assert.deepEqual(laterStepFiles(files).map((f) => f.file_name), ["p.pdf", "cam.txt", "o.txt"]);
  const pipeline = readFileSync("lib/agents/pipeline.ts", "utf8");
  // \s+ rather than \n: a Windows checkout has \r\n line endings.
  assert.match(pipeline, /case "strategy":\s+return `\$\{inputs\.later\}/);
  assert.match(pipeline, /case "execution":\s+return `\$\{inputs\.later\}/);
  assert.match(pipeline, /case "diagnosis":\s+return `\$\{inputs\.diagnosis\}/);
});
