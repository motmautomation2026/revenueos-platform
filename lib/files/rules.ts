// Upload rules shared by the browser (early feedback) and the server (enforcement).

export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const MAX_FILES_PER_PROJECT = 15;
export const TOTAL_CHARS_WARNING = 300_000;

export const DOC_TYPES = [
  { value: "bd_handover", label: "BD handover" },
  { value: "bd_proposal", label: "BD proposal" },
  { value: "signed_scope", label: "Signed scope" },
  { value: "cam_notes", label: "CAM notes" },
  { value: "filled_checklist", label: "Filled checklist" },
  { value: "other", label: "Other document" },
] as const;

export type DocType = (typeof DOC_TYPES)[number]["value"];
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
  if (!allowedMimes) return "Unsupported file type. Use PDF, DOCX, XLSX, CSV, TXT or MD.";
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
