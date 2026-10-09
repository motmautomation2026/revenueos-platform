"use server";

import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";
import { generateChecklist, type GenerateChecklistResult } from "@/lib/agents/checklist";
import { requireProject } from "@/lib/auth";
import { ExcelFormatError, parseChecklistWorkbook } from "@/lib/files/excel";
import { TABLES } from "@/lib/supabase/tables";

type Result<T = unknown> = ({ ok: true } & T) | { ok: false; error: string };

const INVALID = { ok: false, error: "Invalid request." } as const;
const LOCKED = {
  ok: false,
  error: "This checklist has been submitted. Reopen it to change answers.",
} as const;
const id = z.uuid();
const MAX_EXCEL_BYTES = 4 * 1024 * 1024;

type ActiveChecklist = { id: string; status: "draft" | "submitted" };

async function activeChecklist(supabase: SupabaseClient, projectId: string) {
  const { data } = await supabase
    .from(TABLES.checklists)
    .select("id, status")
    .eq("project_id", projectId)
    .eq("is_active", true)
    .maybeSingle<ActiveChecklist>();
  return data;
}

export async function generateChecklistAction(projectId: unknown): Promise<GenerateChecklistResult> {
  const parsed = id.safeParse(projectId);
  if (!parsed.success) return INVALID;
  return generateChecklist(parsed.data);
}

const answerSchema = z.object({ projectId: id, itemId: id, answer: z.string().max(10_000) });

export async function saveAnswer(input: unknown): Promise<Result> {
  const parsed = answerSchema.safeParse(input);
  if (!parsed.success) return INVALID;
  const { supabase, user, project } = await requireProject(parsed.data.projectId);

  const checklist = await activeChecklist(supabase, project.id);
  if (!checklist) return { ok: false, error: "Checklist not found." };
  if (checklist.status !== "draft") return LOCKED;

  const answer = parsed.data.answer.trim();
  const { data, error } = await supabase
    .from(TABLES.checklistItems)
    .update({
      answer: answer || null,
      answered_by: user.id,
      answered_at: new Date().toISOString(),
    })
    .eq("id", parsed.data.itemId)
    .eq("checklist_id", checklist.id)
    .select("id");
  if (error || !data?.length) return { ok: false, error: "Could not save the answer." };
  return { ok: true };
}

export async function submitChecklist(projectId: unknown): Promise<Result> {
  const parsed = id.safeParse(projectId);
  if (!parsed.success) return INVALID;
  const { supabase, project } = await requireProject(parsed.data);

  const checklist = await activeChecklist(supabase, project.id);
  if (!checklist || project.status !== "checklist_ready") {
    return { ok: false, error: "This checklist cannot be submitted right now." };
  }

  const { error } = await supabase
    .from(TABLES.checklists)
    .update({ status: "submitted", submitted_at: new Date().toISOString() })
    .eq("id", checklist.id);
  if (error) return { ok: false, error: "Could not submit the checklist." };

  await supabase.from(TABLES.projects).update({ status: "checklist_submitted" }).eq("id", project.id);
  return { ok: true };
}

/** Lets the user fix answers after submitting, as long as no plan has been started. */
export async function reopenChecklist(projectId: unknown): Promise<Result> {
  const parsed = id.safeParse(projectId);
  if (!parsed.success) return INVALID;
  const { supabase, project } = await requireProject(parsed.data);

  const checklist = await activeChecklist(supabase, project.id);
  if (!checklist || project.status !== "checklist_submitted") {
    return { ok: false, error: "This checklist cannot be reopened right now." };
  }

  const { error } = await supabase
    .from(TABLES.checklists)
    .update({ status: "draft", submitted_at: null })
    .eq("id", checklist.id);
  if (error) return { ok: false, error: "Could not reopen the checklist." };

  await supabase.from(TABLES.projects).update({ status: "checklist_ready" }).eq("id", project.id);
  return { ok: true };
}

export async function importChecklistExcel(
  formData: FormData
): Promise<Result<{ updated: number; unchanged: number; notMatched: number; answers: Record<string, string> }>> {
  const parsedId = id.safeParse(formData.get("projectId"));
  const file = formData.get("file");
  if (!parsedId.success || !(file instanceof File)) return INVALID;
  const { supabase, user, project } = await requireProject(parsedId.data);

  if (!file.name.toLowerCase().endsWith(".xlsx")) {
    return { ok: false, error: "Upload the checklist as an .xlsx file." };
  }
  if (file.size === 0 || file.size > MAX_EXCEL_BYTES) {
    return { ok: false, error: "The file is empty or larger than 4 MB." };
  }

  const checklist = await activeChecklist(supabase, project.id);
  if (!checklist) return { ok: false, error: "Checklist not found." };
  if (checklist.status !== "draft") return LOCKED;

  let rows;
  try {
    rows = await parseChecklistWorkbook(Buffer.from(await file.arrayBuffer()));
  } catch (error) {
    return {
      ok: false,
      error: error instanceof ExcelFormatError ? error.message : "Could not read this file.",
    };
  }

  const { data: items } = await supabase
    .from(TABLES.checklistItems)
    .select("id, position, answer")
    .eq("checklist_id", checklist.id)
    .returns<{ id: string; position: number; answer: string | null }[]>();
  const byNumber = new Map((items ?? []).map((item) => [item.position, item]));

  const answers: Record<string, string> = {};
  let unchanged = 0;
  let notMatched = 0;
  const seen = new Set<number>();
  const now = new Date().toISOString();
  const writes: PromiseLike<{ error: unknown }>[] = [];

  for (const row of rows) {
    const item = row.number == null ? undefined : byNumber.get(row.number);
    if (!item || seen.has(item.position)) {
      notMatched++;
      continue;
    }
    seen.add(item.position);
    const answer = row.answer.slice(0, 10_000);
    if (answer === (item.answer ?? "")) {
      unchanged++;
      continue;
    }
    answers[item.id] = answer;
    writes.push(
      supabase
        .from(TABLES.checklistItems)
        .update({ answer: answer || null, answered_by: user.id, answered_at: now })
        .eq("id", item.id)
        .eq("checklist_id", checklist.id)
    );
  }

  const results = await Promise.all(writes);
  if (results.some((r) => r.error)) {
    return { ok: false, error: "Some answers could not be saved. Refresh the page and try again." };
  }
  return { ok: true, updated: writes.length, unchanged, notMatched, answers };
}
