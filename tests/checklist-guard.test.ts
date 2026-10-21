import assert from "node:assert/strict";
import { test } from "node:test";
import { guardChecklist } from "../lib/agents/checklist-guard";

// Anvixa's documents and answers (October 2026) are the examples.
const documents = [
  "Presented for: Alan Scott – Anvixa AI\nDomestic Retainer — ₹60,000/month\nRoadmap: 10–12 qualified leads",
  "Client Visit Required? No\nInternal Risk Flags (BD Input): Need accurate follow ups.",
];
const item = (question: string, prefilled_answer: string | null) => ({ id: "Q", category: "c", question, options: [], prefilled_answer, reason: "r" });

test("a fact whose quote is in no document is removed; real quotes stay, across dashes and line breaks", () => {
  const { known_facts, report } = guardChecklist(
    {
      known_facts: [
        { topic: "Legal name", source_quote: "Presented for: Alan Scott - Anvixa AI" },
        { topic: "Brands", source_quote: "No mention of brands or OEM partnerships" },
        { topic: "Follow-ups", source_quote: "Internal Risk Flags (BD Input):\nNeed accurate follow ups." },
      ],
      checklist_items: [],
    },
    documents
  );
  assert.deepEqual(known_facts.map((f) => f.topic), ["Legal name", "Follow-ups"]);
  assert.equal(report.unquotedFacts, 1);
});

test("a prefill that only says the documents were silent, or points elsewhere, is cleared", () => {
  const { checklist_items, report } = guardChecklist(
    {
      known_facts: [],
      checklist_items: [
        item("Do you have any brands or OEM partnerships?", "No"),
        item("What is your monthly target for leads?", "As per roadmap mentioned in the proposal"),
        item("Is a site visit needed to close deals?", "No"),
        item("What is your company's full legal name?", "Anvixa AI"),
      ],
    },
    documents
  );
  assert.deepEqual(checklist_items.map((i) => i.prefilled_answer), [null, null, null, "Anvixa AI"]);
  assert.equal(report.clearedPrefills.length, 3);
});

test("MOTM's own fee is never the customer's price position", () => {
  const { checklist_items, report } = guardChecklist(
    {
      known_facts: [],
      checklist_items: [
        item("What is your price position in the market?", "Domestic retainer ₹60,000/month; International add-on ₹15,000/month"),
        item("What is your typical price range?", "₹30 lakh – ₹2 crore per project"),
      ],
    },
    documents
  );
  assert.deepEqual(checklist_items.map((i) => i.prefilled_answer), [null, "₹30 lakh – ₹2 crore per project"]);
  assert.match(report.clearedPrefills[0].why, /MOTM's fee/);
});
