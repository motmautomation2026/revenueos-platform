// User messages are always built from database rows. No customer-specific text lives in code.

import { answerForSharing } from "@/lib/checklist-secrets";
import { TRUST, trustOf, type DocType } from "@/lib/files/rules";

// Agreed documents first, then MOTM's notes, the customer's own material, style examples, the rest.
const DOC_HEADINGS: Record<DocType, string> = {
  bd_handover: "BD HANDOVER FORM",
  bd_proposal: "BD PROPOSAL",
  signed_scope: "SIGNED SCOPE",
  filled_checklist: "FILLED CHECKLIST FILE",
  cam_notes: "CAM NOTES",
  company_profile: "COMPANY PROFILE",
  product_catalogue: "PRODUCT CATALOGUE",
  brochure: "BROCHURE",
  presentation: "PRESENTATION",
  case_study: "CASE STUDY",
  email_templates: "EXISTING EMAIL TEMPLATES",
  other: "OTHER DOCUMENT",
};

/** Said once above the documents: what to do when they disagree or say nothing. */
export const SOURCE_RULES =
  "HOW TO USE THESE DOCUMENTS: each document says how far to trust it. If documents disagree, an agreed document " +
  "wins. If something is not in any document, write 'not stated'; never fill a gap with a guess.";
const DOC_ORDER = Object.keys(DOC_HEADINGS) as DocType[];

export type InputFile = {
  doc_type: DocType;
  file_name: string;
  extracted_text: string | null;
  /** A large customer document's fact sheet (lib/agents/fact-sheet.ts): read in place of the full text. */
  ai_summary?: string | null;
};
export type InputProject = { customer_name: string; engagement_type: string };
export type InputChecklistItem = {
  code: string;
  category: string;
  question: string;
  answer: string | null;
};

export function buildDocumentBlock(project: InputProject, files: InputFile[]): string {
  const sorted = [...files]
    .filter((f) => f.extracted_text?.trim())
    // Style examples (existing email templates) say nothing a plan needs: they are kept for the
    // email writer, not given to the planning agents.
    .filter((f) => trustOf(f.doc_type) !== "style")
    .sort((a, b) => DOC_ORDER.indexOf(a.doc_type) - DOC_ORDER.indexOf(b.doc_type));

  const sections = sorted.map(
    (f) =>
      f.ai_summary?.trim()
        ? `=== ${DOC_HEADINGS[f.doc_type] ?? DOC_HEADINGS.other} (${f.file_name}): FACT SHEET ===\n` +
          `[How to use: ${TRUST[trustOf(f.doc_type)].rule} This is a fact sheet of a ${f.extracted_text!.length.toLocaleString("en-IN")}-character document, ` +
          `written from it without adding anything.]\n${f.ai_summary.trim()}`
        : `=== ${DOC_HEADINGS[f.doc_type] ?? DOC_HEADINGS.other} (${f.file_name}) ===\n` +
          `[How to use: ${TRUST[trustOf(f.doc_type)].rule}]\n${f.extracted_text!.trim()}`
  );
  return [
    `CUSTOMER: ${project.customer_name}\nENGAGEMENT TYPE: ${project.engagement_type}`,
    ...(sections.length ? [SOURCE_RULES] : []),
    ...sections,
  ].join("\n\n");
}

/**
 * The documents for the Strategy and Execution agents: everything except the customer's own
 * material. Those agents take customer facts only from the Diagnosis (their instructions say so),
 * and the Diagnosis has already read that material, so sending it again would only add text.
 */
export function laterStepFiles<T extends InputFile>(files: T[]): T[] {
  return files.filter((file) => trustOf(file.doc_type) !== "customer");
}

export function buildFilledChecklistBlock(items: InputChecklistItem[]): string {
  const lines = items.map(
    (item) =>
      `${item.code} [${item.category}] ${item.question}\nAnswer: ${answerForSharing(item.question, item.answer)?.trim() || "(not answered)"}`
  );
  return ["=== FILLED CHECKLIST ===", ...lines].join("\n");
}

export function buildChecklistMessage(project: InputProject, files: InputFile[]) {
  return `${buildDocumentBlock(project, files)}\n\nWrite the checklist now.`;
}
