import "server-only";
import ExcelJS from "exceljs";
import JSZip from "jszip";
import mammoth from "mammoth";
import { extractText as extractPdfText, getDocumentProxy } from "unpdf";
import { imagePages, readImagePages } from "./ocr";
import { fileExtension } from "./rules";

/** An error whose message is safe and useful to show to the user. */
export class ExtractionError extends Error {}

function startsWith(bytes: Uint8Array, signature: number[]) {
  return signature.every((byte, i) => bytes[i] === byte);
}
const PDF_SIGNATURE = [0x25, 0x50, 0x44, 0x46]; // %PDF
const ZIP_SIGNATURE = [0x50, 0x4b]; // PK (docx and xlsx are zip archives)

function clean(text: string) {
  return text
    .replace(/\u0000/g, "") // Postgres text cannot hold NUL
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * Every page's text. A page that is a picture of text (a scan, or a brochure exported as images)
 * is read with free OCR (lib/files/ocr.ts) and marked, so the agents know it may hold reading errors.
 */
async function extractPdf(bytes: Uint8Array) {
  const pdf = await getDocumentProxy(bytes);
  const { text } = await extractPdfText(pdf, { mergePages: false });
  const pictures = imagePages(text);
  if (!pictures.length) return text.join("\n\n");

  const { texts, skipped } = await readImagePages(pdf, pictures);
  const pages = text.map((pageText, index) => {
    const read = texts.get(index + 1);
    return read ? `[Page ${index + 1}, read from an image: may contain reading errors]\n${read}` : pageText;
  });
  if (skipped) pages.push(`[${skipped} more image page(s) were not read: only the first pages of a scan are read.]`);
  return pages.join("\n\n");
}

async function extractDocx(buffer: Buffer) {
  // Raw text includes the paragraphs inside table cells.
  const { value } = await mammoth.extractRawText({ buffer });
  return value;
}

function cellText(cell: ExcelJS.Cell) {
  try {
    return cell.text.replace(/\s+/g, " ").trim();
  } catch {
    // exceljs throws on a few exotic cell values (e.g. broken rich text).
    return String(cell.value ?? "").trim();
  }
}

/** Every sheet, every row as `Header: value | Header: value`. */
async function extractXlsx(buffer: Buffer) {
  const workbook = new ExcelJS.Workbook();
  // exceljs is typed against an older Buffer shape.
  await workbook.xlsx.load(buffer as unknown as ExcelJS.Buffer);

  const sheets: string[] = [];
  workbook.eachSheet((sheet) => {
    const lines: string[] = [];
    let headers: string[] | null = null;

    sheet.eachRow({ includeEmpty: false }, (row) => {
      const values: string[] = [];
      row.eachCell({ includeEmpty: true }, (cell, col) => {
        values[col - 1] = cellText(cell);
      });
      if (!values.some(Boolean)) return;

      if (!headers) {
        headers = values.map((v, i) => v || `Column ${i + 1}`);
        return;
      }
      const pairs: string[] = [];
      for (let i = 0; i < values.length; i++) {
        if (values[i]) pairs.push(`${headers[i] ?? `Column ${i + 1}`}: ${values[i]}`);
      }
      if (pairs.length) lines.push(pairs.join(" | "));
    });

    // A sheet with a single row has no data rows; keep that row rather than lose it.
    if (headers && lines.length === 0) lines.push((headers as string[]).filter(Boolean).join(" | "));
    if (lines.length) sheets.push(`--- Sheet: ${sheet.name} ---\n${lines.join("\n")}`);
  });
  return sheets.join("\n\n");
}

const XML_ENTITIES: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };
const decodeXml = (text: string) =>
  text.replace(/&(amp|lt|gt|quot|apos|#\d+|#x[0-9a-f]+);/gi, (_, entity: string) =>
    entity[0] === "#"
      ? String.fromCodePoint(entity[1].toLowerCase() === "x" ? parseInt(entity.slice(2), 16) : parseInt(entity.slice(1), 10))
      : XML_ENTITIES[entity.toLowerCase()]
  );

/** The text of one slide's XML: one line per paragraph (<a:p>), its runs (<a:t>) joined. */
function slideText(xml: string) {
  return xml
    .split(/<a:p[\s>]/)
    .map((paragraph) => [...paragraph.matchAll(/<a:t>([^<]*)<\/a:t>/g)].map((m) => decodeXml(m[1])).join(""))
    .map((line) => line.trim())
    .filter(Boolean)
    .join("\n");
}

/** Every slide in order as "--- Slide N ---", followed by its speaker notes when it has any. */
async function extractPptx(buffer: Buffer) {
  const zip = await JSZip.loadAsync(buffer);
  const number = (path: string) => Number(path.match(/(\d+)\.xml$/)?.[1] ?? 0);
  const slides = Object.keys(zip.files)
    .filter((path) => /^ppt\/slides\/slide\d+\.xml$/.test(path))
    .sort((a, b) => number(a) - number(b));
  const parts: string[] = [];
  for (const path of slides) {
    const text = slideText(await zip.file(path)!.async("string"));
    const notesFile = zip.file(`ppt/notesSlides/notesSlide${number(path)}.xml`);
    // Notes repeat the slide number as text; keep only real sentences.
    const notes = notesFile ? slideText(await notesFile.async("string")).replace(/^\d+$/gm, "").trim() : "";
    if (text || notes) parts.push([`--- Slide ${number(path)} ---`, text, notes && `Notes: ${notes}`].filter(Boolean).join("\n"));
  }
  return parts.join("\n\n");
}

function extractPlain(buffer: Buffer) {
  return new TextDecoder("utf-8").decode(buffer).replace(/^﻿/, "");
}

export async function extractText(buffer: Buffer, fileName: string): Promise<string> {
  const ext = fileExtension(fileName);
  const bytes = new Uint8Array(buffer);
  let text: string;

  try {
    switch (ext) {
      case "pdf":
        if (!startsWith(bytes, PDF_SIGNATURE)) throw new ExtractionError("This is not a valid PDF file.");
        text = await extractPdf(bytes);
        break;
      case "docx":
        if (!startsWith(bytes, ZIP_SIGNATURE)) throw new ExtractionError("This is not a valid DOCX file.");
        text = await extractDocx(buffer);
        break;
      case "xlsx":
        if (!startsWith(bytes, ZIP_SIGNATURE)) throw new ExtractionError("This is not a valid XLSX file.");
        text = await extractXlsx(buffer);
        break;
      case "pptx":
        if (!startsWith(bytes, ZIP_SIGNATURE)) throw new ExtractionError("This is not a valid PPTX file.");
        text = await extractPptx(buffer);
        break;
      case "csv":
      case "txt":
      case "md":
        text = extractPlain(buffer);
        break;
      default:
        throw new ExtractionError("Unsupported file type.");
    }
  } catch (error) {
    if (error instanceof ExtractionError) throw error;
    throw new ExtractionError(
      `Could not read this ${ext.toUpperCase()} file. It may be damaged or password-protected.`
    );
  }

  text = clean(text);
  if (!text) {
    throw new ExtractionError(
      ext === "pdf"
        ? "No readable text found, even with image reading. Upload a text version, or a clearer scan."
        : "No text found in this file."
    );
  }
  return text;
}
