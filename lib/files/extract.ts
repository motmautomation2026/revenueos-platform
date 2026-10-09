import "server-only";
import ExcelJS from "exceljs";
import mammoth from "mammoth";
import { extractText as extractPdfText, getDocumentProxy } from "unpdf";
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

async function extractPdf(bytes: Uint8Array) {
  const pdf = await getDocumentProxy(bytes);
  const { text } = await extractPdfText(pdf, { mergePages: false });
  return text.join("\n\n");
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
        ? "No text found. This PDF looks like a scan (images only); upload a text version."
        : "No text found in this file."
    );
  }
  return text;
}
