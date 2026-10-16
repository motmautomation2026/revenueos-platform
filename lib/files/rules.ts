// Upload rules shared by the browser (early feedback) and the server (enforcement).

export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const MAX_FILES_PER_PROJECT = 25;
export const TOTAL_CHARS_WARNING = 300_000;

/**
 * How far an agent may trust a document. Told to the agents with every document, so a brochure's
 * marketing claim is never treated like a signed commitment, and an old email is never a source of facts.
 */
export type DocTrust = "agreed" | "internal" | "customer" | "style" | "reference";

export const TRUST: Record<DocTrust, { label: string; rule: string }> = {
  agreed: {
    label: "Agreed facts",
    rule: "Agreed with the customer. Treat what it states as facts.",
  },
  internal: {
    label: "MOTM notes",
    rule: "MOTM's own notes. Useful context; where it disagrees with an agreed document, the agreed document wins.",
  },
  customer: {
    label: "Customer material",
    rule:
      "The customer's own material. Use it for facts about the customer's company, products, specifications and clients, " +
      "as written. Marketing claims in it are not commitments: never turn them into promises, numbers or targets.",
  },
  style: {
    label: "Style only",
    rule: "Examples of how the customer writes. Use only for tone and format. Never take facts, numbers, names or offers from it.",
  },
  reference: {
    label: "Reference",
    rule: "Use only facts it states plainly. If unsure, treat the fact as not stated.",
  },
};

export const DOC_TYPES = [
  { value: "bd_handover", label: "BD handover", trust: "agreed" },
  { value: "bd_proposal", label: "BD proposal", trust: "agreed" },
  { value: "signed_scope", label: "Signed scope", trust: "agreed" },
  { value: "filled_checklist", label: "Filled checklist", trust: "agreed" },
  { value: "cam_notes", label: "CAM notes", trust: "internal" },
  { value: "company_profile", label: "Company profile", trust: "customer" },
  { value: "product_catalogue", label: "Product catalogue", trust: "customer" },
  { value: "brochure", label: "Brochure", trust: "customer" },
  { value: "presentation", label: "Presentation (PPT)", trust: "customer" },
  { value: "case_study", label: "Case study", trust: "customer" },
  { value: "email_templates", label: "Existing email templates", trust: "style" },
  { value: "other", label: "Other document", trust: "reference" },
] as const satisfies readonly { value: string; label: string; trust: DocTrust }[];

export type DocType = (typeof DOC_TYPES)[number]["value"];

export function trustOf(docType: DocType): DocTrust {
  return DOC_TYPES.find((type) => type.value === docType)?.trust ?? "reference";
}
export const DOC_TYPE_VALUES = DOC_TYPES.map((d) => d.value) as [DocType, ...DocType[]];

export type ExtractionStatus = "pending" | "done" | "failed";

/** A project file as the upload page sees it (no extracted text). */
export type ProjectFileRow = {
  id: string;
  doc_type: DocType;
  file_name: string;
  size_bytes: number | null;
  char_count: number;
  extraction_status: ExtractionStatus;
  extraction_error: string | null;
};
export const PROJECT_FILE_COLUMNS =
  "id, doc_type, file_name, size_bytes, char_count, extraction_status, extraction_error";

const OFFICE = "application/vnd.openxmlformats-officedocument";
// Browsers report text formats inconsistently (often empty for .md, Excel's type for .csv).
const TEXT_MIMES = ["", "text/plain", "application/octet-stream"];

export const ACCEPTED_TYPES: Record<string, string[]> = {
  pdf: ["application/pdf"],
  docx: [`${OFFICE}.wordprocessingml.document`],
  xlsx: [`${OFFICE}.spreadsheetml.sheet`],
  pptx: [`${OFFICE}.presentationml.presentation`, "application/octet-stream", ""],
  csv: [...TEXT_MIMES, "text/csv", "application/csv", "application/vnd.ms-excel"],
  txt: TEXT_MIMES,
  md: [...TEXT_MIMES, "text/markdown", "text/x-markdown"],
};
export const ACCEPT_ATTRIBUTE = Object.keys(ACCEPTED_TYPES)
  .map((ext) => `.${ext}`)
  .join(",");

export function fileExtension(fileName: string) {
  const dot = fileName.lastIndexOf(".");
  return dot === -1 ? "" : fileName.slice(dot + 1).toLowerCase();
}

/** Returns a readable problem with the file, or null when it is acceptable. */
export function fileProblem(file: { name: string; type: string; size: number }): string | null {
  const allowedMimes = ACCEPTED_TYPES[fileExtension(file.name)];
  if (!allowedMimes) return "Unsupported file type. Use PDF, DOCX, PPTX, XLSX, CSV, TXT or MD.";
  if (!allowedMimes.includes(file.type.toLowerCase().split(";")[0].trim())) {
    return "The file's content type does not match its extension.";
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_FILE_BYTES) return "The file is larger than 20 MB.";
  return null;
}

export function guessDocType(fileName: string): DocType {
  const name = fileName.toLowerCase();
  if (name.includes("handover")) return "bd_handover";
  if (name.includes("proposal")) return "bd_proposal";
  if (name.includes("scope")) return "signed_scope";
  if (name.includes("checklist")) return "filled_checklist";
  if (name.includes("catalog")) return "product_catalogue";
  if (name.includes("brochure") || name.includes("leaflet") || name.includes("flyer")) return "brochure";
  if (name.includes("profile")) return "company_profile";
  if (name.includes("case stud") || name.includes("case-stud") || name.includes("case_stud")) return "case_study";
  if (name.includes("template") || name.includes("email")) return "email_templates";
  if (fileExtension(name) === "pptx" || name.includes("deck") || name.includes("presentation")) return "presentation";
  return "other";
}

/** Keeps letters, digits, dot, dash and underscore; everything else becomes a dash. */
export function sanitizeFileName(fileName: string) {
  const ext = fileExtension(fileName);
  const base = (ext ? fileName.slice(0, -(ext.length + 1)) : fileName)
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 80);
  return `${base || "file"}${ext ? `.${ext}` : ""}`;
}

export function canGenerateChecklist(files: Pick<ProjectFileRow, "doc_type" | "extraction_status">[]) {
  return (
    files.some((f) => f.extraction_status === "done") &&
    files.some((f) => f.doc_type === "bd_handover" || f.doc_type === "bd_proposal")
  );
}
