import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { TABLES } from "@/lib/supabase/tables";
import { trustOf } from "@/lib/files/rules";
import type { InputFile } from "./build-inputs";
import { OPENAI_MODEL } from "./config";
import { getOpenAI } from "./run-agent";

// Large customer documents reach the planning agents as a short fact sheet, written once and
// stored with the file (supabase/migrations/0011_document_fact_sheets.sql). Small documents, and
// every agreed document (BD proposal, handover, signed scope, checklist), are always read in full.
//
// Anything that goes wrong here falls back to the full text: a missing fact sheet never stops a
// checklist or a plan.

/** A customer document longer than this is read as its fact sheet. About 8-10 pages of text. */
export const FACT_SHEET_THRESHOLD = 20_000;

const INSTRUCTIONS = `You turn ONE of a customer's own documents (a catalogue, brochure, company profile, presentation or case study) into a fact sheet for MOTM's planning agents.

RULES
- Only what the document states. Never add, infer, generalise or improve anything.
- Keep exact names, model numbers, specifications, sizes, units, numbers, client names, project names and places.
- Where the text marks slides or sheets (for example "--- Slide 4 ---" or "--- Sheet: Prices ---"), give the reference in brackets after the fact, e.g. (slide 4).
- Marketing claims ("the best", "leading", "unmatched") go under "Claims made" only, word for word. They are claims, not facts.
- Leave out a section when the document says nothing for it. Do not write "not stated".
- Plain text, short lines, at most about 1,500 words.

SECTIONS, in this order
Company
Products and services (with specifications)
Clients, projects and case studies
Certifications and approvals
Locations, capacity and delivery
Numbers stated
Claims made`;

type FileWithId = InputFile & { id: string };

const needsSheet = (file: InputFile) =>
  trustOf(file.doc_type) === "customer" && (file.extracted_text?.length ?? 0) > FACT_SHEET_THRESHOLD;

/** The fact sheet of one document, or null if the AI could not write one. */
async function writeFactSheet(file: FileWithId): Promise<string | null> {
  try {
    const completion = await getOpenAI().chat.completions.create({
      model: OPENAI_MODEL,
      temperature: 0,
      max_tokens: 4_000,
      messages: [
        { role: "system", content: INSTRUCTIONS },
        { role: "user", content: `DOCUMENT: ${file.file_name}\n\n${file.extracted_text}` },
      ],
    });
    const text = completion.choices[0]?.message?.content?.trim() ?? "";
    // A sheet cut off at the length limit may have lost whole sections: the full text is safer.
    return completion.choices[0]?.finish_reason === "stop" && text ? text : null;
  } catch {
    return null;
  }
}

/**
 * The files as the planning agents should read them: each large customer document with its fact
 * sheet attached (written now if missing or out of date, and saved), everything else unchanged.
 */
export async function withFactSheets(client: SupabaseClient, files: FileWithId[]): Promise<InputFile[]> {
  const wanted = files.filter(needsSheet);
  if (!wanted.length) return files;

  const { data: saved, error } = await client
    .from(TABLES.projectFiles)
    .select("id, ai_summary, ai_summary_chars")
    .in(
      "id",
      wanted.map((file) => file.id)
    )
    .returns<{ id: string; ai_summary: string | null; ai_summary_chars: number | null }[]>();
  if (error) return files; // the database update is not applied yet: read everything in full

  const sheets = new Map<string, string>();
  await Promise.all(
    wanted.map(async (file) => {
      const row = saved?.find((s) => s.id === file.id);
      const current = row?.ai_summary && row.ai_summary_chars === file.extracted_text!.length ? row.ai_summary : null;
      const sheet = current ?? (await writeFactSheet(file));
      if (!sheet) return;
      sheets.set(file.id, sheet);
      if (!current) {
        await client
          .from(TABLES.projectFiles)
          .update({ ai_summary: sheet, ai_summary_chars: file.extracted_text!.length, ai_summary_at: new Date().toISOString() })
          .eq("id", file.id);
      }
    })
  );
  return files.map((file) => (sheets.has(file.id) ? { ...file, ai_summary: sheets.get(file.id)! } : file));
}
