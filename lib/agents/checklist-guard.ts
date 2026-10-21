// Checks on the Checklist agent's answer, in code, before it is saved. No AI calls.
//
// The agent sometimes quotes text that is not in any document, prefills "No" where the documents
// say nothing, writes "As per proposal" instead of the number, and answers the customer's price
// position with MOTM's own fee. Each of these would reach the plan and the Brain as a confirmed
// fact, so they are removed here, and the counts are kept with the run for the CAM to see.

type Fact = { source_quote?: unknown } & Record<string, unknown>;
type Item = { question: string; prefilled_answer: string | null } & Record<string, unknown>;

export type GuardReport = {
  /** Facts dropped because their quote is in no document. */
  unquotedFacts: number;
  /** Prefills cleared because they were not an answer ("No", "As per proposal", MOTM's fee). */
  clearedPrefills: { question: string; was: string; why: string }[];
};

/** Letters and digits only, lower case: a quote still matches across line breaks, dashes and spacing. */
const letters = (text: string) => text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");

// A prefill that only says the documents were silent, or points somewhere else.
const SILENT = /^(no|none|nil|n\/?a|not (stated|mentioned|given|provided|specified|available)|unknown|tbd|tba|to be (decided|confirmed))\.?$/i;
const POINTER = /^(as per|refer to|see|according to|mentioned in)\b/i;
// MOTM's own commercials, as the BD proposal states them.
const MOTM_FEE = /\bretainer\b|\badd-?on\b.*\/\s*month|\bMOTM\b.*\b(fee|charges?)\b/i;
const PRICE_QUESTION = /\b(price|pricing)\b/i;

export function guardChecklist<F extends Fact, I extends Item>(
  output: { known_facts: F[]; checklist_items: I[] },
  documents: string[]
): { known_facts: F[]; checklist_items: I[]; report: GuardReport } {
  const corpus = documents.map(letters).join("|");
  const known_facts = output.known_facts.filter((fact) => {
    const quote = typeof fact.source_quote === "string" ? letters(fact.source_quote) : "";
    // Very short quotes ("Yes", a number) cannot be checked reliably; keep them.
    return quote.length < 12 || corpus.includes(quote);
  });

  const clearedPrefills: GuardReport["clearedPrefills"] = [];
  const checklist_items = output.checklist_items.map((item) => {
    const was = item.prefilled_answer?.trim() ?? "";
    if (!was) return item;
    const why = SILENT.test(was)
      ? "the documents say nothing, so the customer must answer"
      : POINTER.test(was)
        ? "it points to another document instead of giving the answer"
        : PRICE_QUESTION.test(item.question) && MOTM_FEE.test(was)
          ? "it is MOTM's fee from the proposal, not the customer's price"
          : null;
    if (!why) return item;
    clearedPrefills.push({ question: item.question, was, why });
    return { ...item, prefilled_answer: null };
  });

  return {
    known_facts,
    checklist_items,
    report: { unquotedFacts: output.known_facts.length - known_facts.length, clearedPrefills },
  };
}
