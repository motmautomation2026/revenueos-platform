import "server-only";
import { requireProject } from "@/lib/auth";
import type { InputFile } from "@/lib/agents/build-inputs";
import { buildChecklistMessage } from "@/lib/agents/build-inputs";
import { canGenerateChecklist, type ExtractionStatus } from "@/lib/files/rules";
import { TABLES } from "@/lib/supabase/tables";
import { CHECKLIST_MAX_QUESTIONS, CHECKLIST_MIN_QUESTIONS } from "./config";
import { AgentError, runAgent } from "./run-agent";

export type ChecklistFlag = {
  flag_type: "missing" | "vague" | "conflict" | "risk";
  field: string;
  detail: string;
  handover_says: string | null;
  proposal_says: string | null;
  blocks_targets: boolean;
};

type ChecklistOutput = {
  customer_name: string;
  known_facts: unknown[];
  flags: ChecklistFlag[];
  checklist_items: {
    id: string;
    category: string;
    question: string;
    options: string[];
    prefilled_answer: string | null;
    reason: string;
  }[];
  cam_notes: string[];
};

/** A "generating" status older than this is treated as a crashed run and can be taken over. */
const STALE_LOCK_MS = 15 * 60 * 1000;

const questionsOf = (output: ChecklistOutput) =>
  output.checklist_items.filter((item) => item.question.trim());
const inRange = (count: number) =>
  count >= CHECKLIST_MIN_QUESTIONS && count <= CHECKLIST_MAX_QUESTIONS;

export type GenerateChecklistResult =
  | { ok: true; questionCount: number; inRange: boolean }
  | { ok: false; error: string };

export async function generateChecklist(projectId: string): Promise<GenerateChecklistResult> {
  const { supabase, project } = await requireProject(projectId);

  const lockIsFresh = Date.now() - Date.parse(project.updated_at) < STALE_LOCK_MS;
  if (project.status === "checklist_generating" && lockIsFresh) {
    return { ok: false, error: "A checklist is already being written for this project." };
  }
  if (project.status === "plan_running") {
    return { ok: false, error: "Wait for the plan to finish before regenerating the checklist." };
  }

  const { data: files } = await supabase
    .from(TABLES.projectFiles)
    .select("doc_type, file_name, extracted_text, extraction_status")
    .eq("project_id", project.id)
    .order("created_at", { ascending: true })
    .returns<(InputFile & { extraction_status: ExtractionStatus })[]>();
  if (!files || !canGenerateChecklist(files)) {
    return {
      ok: false,
      error: "Upload at least one readable file, including a BD handover or BD proposal.",
    };
  }

  // One running job per project: only one request can move the status out of its current value.
  const { data: locked } = await supabase
    .from(TABLES.projects)
    .update({ status: "checklist_generating" })
    .eq("id", project.id)
    .eq("status", project.status)
    .select("id");
  if (!locked?.length) {
    return { ok: false, error: "A checklist is already being written for this project." };
  }
  const restoreStatus = () =>
    supabase.from(TABLES.projects).update({ status: project.status }).eq("id", project.id);

  try {
    const userMessage = buildChecklistMessage(
      project,
      files.filter((f) => f.extraction_status === "done")
    );
    let run = await runAgent<ChecklistOutput>({ agent: "checklist", projectId: project.id, userMessage });

    let count = questionsOf(run.data).length;
    if (!inRange(count)) {
      const retry = await runAgent<ChecklistOutput>({
        agent: "checklist",
        projectId: project.id,
        userMessage,
        followUp: {
          previousOutput: run.raw,
          message: `You returned ${count} questions. Return the full checklist again with between ${CHECKLIST_MIN_QUESTIONS} and ${CHECKLIST_MAX_QUESTIONS} questions.`,
        },
      });
      // Keep whichever answer we end on; an out-of-range checklist is shown with a warning.
      run = retry;
      count = questionsOf(run.data).length;
    }
    if (count === 0) throw new AgentError("The AI returned no questions. Try again.");

    // Build the new checklist inactive, then swap it in, so a failure never loses the old one.
    const { data: checklist, error: checklistError } = await supabase
      .from(TABLES.checklists)
      .insert({
        project_id: project.id,
        is_active: false,
        known_facts: run.data.known_facts,
        flags: run.data.flags,
        cam_notes: run.data.cam_notes,
        agent_run_id: run.runId,
      })
      .select("id")
      .single<{ id: string }>();
    if (checklistError || !checklist) throw new AgentError("Could not save the checklist.");

    const { error: itemsError } = await supabase.from(TABLES.checklistItems).insert(
      questionsOf(run.data).map((item, index) => {
        const prefilled = item.prefilled_answer?.trim() || null;
        return {
          checklist_id: checklist.id,
          position: index + 1,
          code: `Q-${index + 1}`,
          category: item.category.trim() || "General",
          question: item.question.trim(),
          options: item.options.map((o) => o.trim()).filter(Boolean),
          prefilled_answer: prefilled,
          answer: prefilled,
          reason: item.reason,
        };
      })
    );
    if (itemsError) {
      await supabase.from(TABLES.checklists).delete().eq("id", checklist.id);
      throw new AgentError("Could not save the checklist questions.");
    }

    await supabase
      .from(TABLES.checklists)
      .update({ is_active: false })
      .eq("project_id", project.id)
      .eq("is_active", true);
    const { error: activateError } = await supabase
      .from(TABLES.checklists)
      .update({ is_active: true })
      .eq("id", checklist.id);
    if (activateError) throw new AgentError("Could not activate the new checklist.");

    await supabase.from(TABLES.projects).update({ status: "checklist_ready" }).eq("id", project.id);
    return { ok: true, questionCount: count, inRange: inRange(count) };
  } catch (error) {
    await restoreStatus();
    return {
      ok: false,
      error:
        error instanceof AgentError
          ? error.message
          : "Something went wrong while writing the checklist. Try again.",
    };
  }
}
