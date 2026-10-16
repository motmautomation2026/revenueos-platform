import "server-only";
import path from "node:path";
import { renderPageAsImage, type getDocumentProxy } from "unpdf";

type PDFDocumentProxy = Awaited<ReturnType<typeof getDocumentProxy>>;

// Reading PDF pages that are pictures of text (scans, brochures exported as images), with
// Tesseract: free and open source, run on this server. No AI service and no credits are used.
// The English language data is installed with the app (@tesseract.js-data/eng), so nothing is
// downloaded while it runs.
//
// Tesseract is good with clear printed text and weaker with text over photos, decorative fonts
// and complex tables, so every page it read is marked as such for the agents.

/** A page with fewer characters of real text than this is treated as an image. */
export const OCR_MIN_CHARS = 30;
/** At most this many image pages are read per file, so one huge scan cannot hold up an upload. */
export const OCR_MAX_PAGES = 30;
/** Pages are drawn at this scale before reading: sharper text, better recognition. */
const RENDER_SCALE = 2;

const LANG_PATH = path.join(process.cwd(), "node_modules", "@tesseract.js-data", "eng", "4.0.0_best_int");

/** The page numbers (1-based) of pages with no real text of their own. */
export function imagePages(pageTexts: string[]): number[] {
  return pageTexts.flatMap((text, index) => (text.replace(/\s+/g, "").length < OCR_MIN_CHARS ? [index + 1] : []));
}

type Reader = { read: (image: Buffer) => Promise<string>; close: () => Promise<void> };

/** One Tesseract worker for a whole file. */
export async function createReader(): Promise<Reader> {
  const { createWorker } = await import("tesseract.js");
  const worker = await createWorker("eng", 1, { langPath: LANG_PATH, gzip: true, cacheMethod: "none" });
  return {
    read: async (image) => (await worker.recognize(image)).data.text,
    close: async () => {
      await worker.terminate();
    },
  };
}

/**
 * The text Tesseract reads on the given pages, by page number. Pages it cannot read are left out.
 * Returns how many image pages were skipped because of OCR_MAX_PAGES.
 */
export async function readImagePages(
  pdf: PDFDocumentProxy,
  pages: number[]
): Promise<{ texts: Map<number, string>; skipped: number }> {
  const texts = new Map<number, string>();
  const chosen = pages.slice(0, OCR_MAX_PAGES);
  if (!chosen.length) return { texts, skipped: 0 };

  const reader = await createReader();
  try {
    for (const page of chosen) {
      try {
        const image = await renderPageAsImage(pdf, page, {
          canvasImport: () => import("@napi-rs/canvas"),
          scale: RENDER_SCALE,
        });
        const text = (await reader.read(Buffer.from(image))).trim();
        if (text.replace(/\s+/g, "").length >= 10) texts.set(page, text);
      } catch {
        // One unreadable page does not stop the others.
      }
    }
  } finally {
    await reader.close();
  }
  return { texts, skipped: pages.length - chosen.length };
}
