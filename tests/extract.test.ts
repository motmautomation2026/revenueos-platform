import assert from "node:assert/strict";
import { test } from "node:test";
import ExcelJS from "exceljs";
import JSZip from "jszip";
import { ExtractionError, extractText } from "../lib/files/extract";

/** A one-page-per-string PDF with correct cross-reference offsets. */
function makePdf(pages: string[]) {
  const objects: string[] = [];
  const kids = pages.map((_, i) => `${3 + i * 2} 0 R`).join(" ");
  objects[1] = "<< /Type /Catalog /Pages 2 0 R >>";
  objects[2] = `<< /Type /Pages /Kids [${kids}] /Count ${pages.length} >>`;
  const fontId = 3 + pages.length * 2;
  pages.forEach((text, i) => {
    const stream = `BT /F1 12 Tf 72 720 Td (${text}) Tj ET`;
    objects[3 + i * 2] =
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents ${4 + i * 2} 0 R ` +
      `/Resources << /Font << /F1 ${fontId} 0 R >> >> >>`;
    objects[4 + i * 2] = `<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`;
  });
  objects[fontId] = "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>";

  let body = "%PDF-1.4\n";
  const offsets: number[] = [];
  for (let id = 1; id < objects.length; id++) {
    offsets[id] = body.length;
    body += `${id} 0 obj\n${objects[id]}\nendobj\n`;
  }
  const xref = body.length;
  body += `xref\n0 ${objects.length}\n0000000000 65535 f \n`;
  for (let id = 1; id < objects.length; id++) {
    body += `${String(offsets[id]).padStart(10, "0")} 00000 n \n`;
  }
  body += `trailer\n<< /Size ${objects.length} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
  return Buffer.from(body, "latin1");
}

async function makeDocx() {
  const p = (text: string) => `<w:p><w:r><w:t>${text}</w:t></w:r></w:p>`;
  const cell = (text: string) => `<w:tc>${p(text)}</w:tc>`;
  const zip = new JSZip();
  zip.file(
    "[Content_Types].xml",
    `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>`
  );
  zip.file(
    "_rels/.rels",
    `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>`
  );
  zip.file(
    "word/document.xml",
    `<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>${p("Kickoff notes")}<w:tbl><w:tr>${cell("Owner")}${cell("Sales head")}</w:tr></w:tbl></w:body></w:document>`
  );
  return zip.generateAsync({ type: "nodebuffer" });
}

async function makeXlsx() {
  const workbook = new ExcelJS.Workbook();
  const targets = workbook.addWorksheet("Targets");
  targets.addRow(["Month", "Leads", "Owner"]);
  targets.addRow(["April", 40, "Asha"]);
  targets.addRow(["May", 55, ""]);
  workbook.addWorksheet("Regions").addRows([["Region"], ["West"]]);
  return Buffer.from(await workbook.xlsx.writeBuffer());
}

test("PDF: all pages, in order", async () => {
  const text = await extractText(makePdf(["First page text", "Second page text"]), "handover.pdf");
  assert.match(text, /First page text[\s\S]*Second page text/);
});

test("DOCX: paragraphs and table contents", async () => {
  const text = await extractText(await makeDocx(), "notes.docx");
  assert.match(text, /Kickoff notes/);
  assert.match(text, /Owner/);
  assert.match(text, /Sales head/);
});

test("XLSX: every sheet, rows as Header: value pairs", async () => {
  const text = await extractText(await makeXlsx(), "targets.xlsx");
  assert.match(text, /--- Sheet: Targets ---/);
  assert.match(text, /Month: April \| Leads: 40 \| Owner: Asha/);
  assert.match(text, /Month: May \| Leads: 55$/m);
  assert.match(text, /--- Sheet: Regions ---\nRegion: West/);
});

test("plain text: BOM stripped, line endings normalised", async () => {
  const text = await extractText(Buffer.from("﻿a,b\r\n1,2\r\n", "utf8"), "data.csv");
  assert.equal(text, "a,b\n1,2");
});

test("content that does not match the extension is rejected", async () => {
  await assert.rejects(
    extractText(Buffer.from("just text"), "fake.pdf"),
    (error: unknown) => error instanceof ExtractionError && /not a valid PDF/.test(error.message)
  );
});

test("empty files give a readable error", async () => {
  await assert.rejects(extractText(Buffer.from("  \n"), "empty.txt"), /No text found/);
});
