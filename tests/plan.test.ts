import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import Ajv from "ajv";
import { buildFilledChecklistBlock, buildDocumentBlock } from "../lib/agents/build-inputs";
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
      "=== BD HANDOVER FORM (h.pdf) ===\nHandover text\n\n" +
      "=== BD PROPOSAL (p.pdf) ===\nProposal text\n\n" +
      "=== OTHER DOCUMENT (misc.txt) ===\nMisc"
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
