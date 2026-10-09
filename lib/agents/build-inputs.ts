// User messages are always built from database rows. No customer-specific text lives in code.

import type { DocType } from "@/lib/files/rules";

const DOC_HEADINGS: Record<DocType, string> = {
  bd_handover: "BD HANDOVER FORM",
  bd_proposal: "BD PROPOSAL",
  signed_scope: "SIGNED SCOPE",
  cam_notes: "CAM NOTES",
  filled_checklist: "FILLED CHECKLIST FILE",
  other: "OTHER DOCUMENT",
};
const DOC_ORDER = Object.keys(DOC_HEADINGS) as DocType[];

export type InputFile = { doc_type: DocType; file_name: string; extracted_text: string | null };
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
    .sort((a, b) => DOC_ORDER.indexOf(a.doc_type) - DOC_ORDER.indexOf(b.doc_type));

  const sections = sorted.map(
    (f) => `=== ${DOC_HEADINGS[f.doc_type]} (${f.file_name}) ===\n${f.extracted_text!.trim()}`
  );
  return [
    `CUSTOMER: ${project.customer_name}\nENGAGEMENT TYPE: ${project.engagement_type}`,
    ...sections,
  ].join("\n\n");
}

export function buildFilledChecklistBlock(items: InputChecklistItem[]): string {
  const lines = items.map(
    (item) =>
      `${item.code} [${item.category}] ${item.question}\nAnswer: ${item.answer?.trim() || "(not answered)"}`
  );
  return ["=== FILLED CHECKLIST ===", ...lines].join("\n");
}

export function buildChecklistMessage(project: InputProject, files: InputFile[]) {
  return `${buildDocumentBlock(project, files)}\n\nWrite the checklist now.`;
}
