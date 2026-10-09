import "server-only";
import ExcelJS from "exceljs";

export type ExportItem = {
  code: string;
  category: string;
  question: string;
  options: string[];
  answer: string | null;
};

const HEADERS = ["#", "Category", "Question", "Answer"] as const;

/** The customer-facing sheet: exactly #, Category, Question, Answer and nothing else. */
export async function buildChecklistWorkbook(items: ExportItem[]): Promise<Buffer> {
  const workbook = new ExcelJS.Workbook();
  const sheet = workbook.addWorksheet("Checklist");
  sheet.columns = [{ width: 8 }, { width: 24 }, { width: 70 }, { width: 60 }];

  sheet.addRow([...HEADERS]).font = { bold: true };
  for (const item of items) {
    const question = item.options.length
      ? `${item.question} (Options: ${item.options.join(" / ")})`
      : item.question;
    sheet.addRow([item.code, item.category, question, item.answer ?? ""]);
  }
  sheet.eachRow((row) => {
    row.alignment = { vertical: "top", wrapText: true };
  });
  sheet.views = [{ state: "frozen", ySplit: 1 }];

  return Buffer.from(await workbook.xlsx.writeBuffer());
}

function cellText(cell: ExcelJS.Cell) {
  try {
    return cell.text.trim();
  } catch {
    return String(cell.value ?? "").trim();
  }
}

/** "Q-12", "q12" and "12" all mean question 12. */
export function questionNumber(value: string): number | null {
  const match = value.trim().match(/^q?[\s\-_.]*(\d+)\.?$/i);
  return match ? Number(match[1]) : null;
}

export class ExcelFormatError extends Error {}

/** Reads a filled checklist. Columns are found by header name, in any order. */
export async function parseChecklistWorkbook(
  buffer: Buffer
): Promise<{ number: number | null; answer: string }[]> {
  const workbook = new ExcelJS.Workbook();
  try {
    await workbook.xlsx.load(buffer as unknown as ExcelJS.Buffer);
  } catch {
    throw new ExcelFormatError("Could not read this file. Upload the .xlsx you downloaded.");
  }

  for (const sheet of workbook.worksheets) {
    let headerRow = 0;
    let numberCol = 0;
    let answerCol = 0;

    for (let r = 1; r <= Math.min(sheet.rowCount, 15) && !headerRow; r++) {
      let n = 0;
      let a = 0;
      sheet.getRow(r).eachCell((cell, col) => {
        const header = cellText(cell).toLowerCase();
        if (!n && (header === "#" || header === "id")) n = col;
        if (!a && (header === "answer" || header === "your answer")) a = col;
      });
      if (n && a) [headerRow, numberCol, answerCol] = [r, n, a];
    }
    if (!headerRow) continue;

    const rows: { number: number | null; answer: string }[] = [];
    for (let r = headerRow + 1; r <= sheet.rowCount; r++) {
      const row = sheet.getRow(r);
      const id = cellText(row.getCell(numberCol));
      const answer = cellText(row.getCell(answerCol));
      if (!id && !answer) continue;
      rows.push({ number: questionNumber(id), answer });
    }
    return rows;
  }
  throw new ExcelFormatError('Could not find the "#" and "Answer" columns in this file.');
}
