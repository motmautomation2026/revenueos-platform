import "server-only";
import type { SupabaseClient } from "@supabase/supabase-js";
import { z } from "zod";
import {
  buildDocumentBlock,
  buildFilledChecklistBlock,
  type InputChecklistItem,
  type InputFile,
  type InputProject,
} from "@/lib/agents/build-inputs";
import { buildPlanDocument, type PlanInternal } from "@/lib/plan/document";
import { renderPlanHtml } from "@/lib/plan/render-html";
import {
  PLAN_STEPS,
  RUNNING_STATUSES,
  STALE_RUN_MS,
  type Blocker,
  type PlanProgress,
  type PlanStatus,
  type PlanStep,
} from "@/lib/plan/types";
import type { Project } from "@/lib/projects";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";
import { OPENAI_MODEL } from "./config";
import { AgentError, runAgent } from "./run-agent";

export type PlanRow = {
  id: string;
  project_id: string;
  version: number;
  status: PlanStatus;
  diagnosis: Record<string, unknown> | null;
  strategy: Record<string, unknown> | null;
  execution: Record<string, unknown> | null;
  blockers: Blocker[] | null;
  override_reason: string | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string;
};
const PLAN_COLUMNS =
  "id, project_id, version, status, diagnosis, strategy, execution, blockers, override_reason, error, started_at, finished_at, updated_at";

const isRunning = (status: PlanStatus) => RUNNING_STATUSES.includes(status);
const isStale = (plan: Pick<PlanRow, "status" | "updated_at">) =>
  isRunning(plan.status) && Date.now() - Date.parse(plan.updated_at) > STALE_RUN_MS;

export function planFooter(plan: Pick<PlanRow, "version" | "finished_at">, model: string) {
  const date = new Intl.DateTimeFormat("en-IN", { dateStyle: "long" }).format(
    plan.finished_at ? new Date(plan.finished_at) : new Date()
  );
  return `Generated on ${date} · model ${model} · version ${plan.version}`;
}

// ---------------------------------------------------------------------------
// Starting, resuming and unblocking (runs as the signed-in user, under RLS)
// ---------------------------------------------------------------------------

export const planActionSchema = z.discriminatedUnion("action", [
  z.object({ action: z.literal("start") }),
  z.object({ action: z.literal("resume") }),
  z.object({
    action: z.literal("answer_blockers"),
    answers: z
      .array(z.object({ question: z.string().trim().min(1).max(2000), answer: z.string().trim().max(10_000) }))
      .min(1)
      .max(30),
  }),
  z.object({
    action: z.literal("override"),
    reason: z.string().trim().min(10, "Give a reason of at least 10 characters.").max(2000),
  }),
]);
export type PlanAction = z.infer<typeof planActionSchema>;

type ActionResult = { ok: true; planId: string } | { ok: false; error: string; status: number };
const refuse = (error: string, status = 409): ActionResult => ({ ok: false, error, status });
const ALREADY_RUNNING = "A plan is already being written for this project.";

async function latestPlan(supabase: SupabaseClient, projectId: string) {
  const { data } = await supabase
    .from(TABLES.plans)
    .select(PLAN_COLUMNS)
    .eq("project_id", projectId)
    .order("version", { ascending: false })
    .limit(1)
    .maybeSingle<PlanRow>();
  return data;
}

/**
 * Applies a user action and leaves the plan `queued` for runPipeline to pick up.
 * The partial unique index on running plans is what enforces one job per project.
 */
export async function applyPlanAction(
  supabase: SupabaseClient,
  project: Project,
  userId: string,
  action: PlanAction
): Promise<ActionResult> {
  const { data: checklist } = await supabase
    .from(TABLES.checklists)
    .select("id, status")
    .eq("project_id", project.id)
    .eq("is_active", true)
    .maybeSingle<{ id: string; status: string }>();
  if (!checklist || checklist.status !== "submitted") {
    return refuse("Submit the checklist before generating the plan.");
  }

  const latest = await latestPlan(supabase, project.id);
  const markProjectRunning = () =>
    supabase.from(TABLES.projects).update({ status: "plan_running" }).eq("id", project.id);

  /** Moves the latest plan back to `queued`, only if it is still in the state we looked at. */
  const requeue = async (plan: PlanRow, changes: Record<string, unknown>): Promise<ActionResult> => {
    const { data, error } = await supabase
      .from(TABLES.plans)
      .update({ ...changes, status: "queued", error: null, finished_at: null })
      .eq("id", plan.id)
      .eq("status", plan.status)
      .select("id");
    if (error || !data?.length) return refuse(ALREADY_RUNNING);
    await markProjectRunning();
    return { ok: true, planId: plan.id };
  };

  switch (action.action) {
    case "start": {
      if (latest && isRunning(latest.status)) {
        if (!isStale(latest)) return refuse(ALREADY_RUNNING);
        // Retire the crashed run so a new version can start.
        await supabase
          .from(TABLES.plans)
          .update({ status: "failed", error: "The run stopped unexpectedly.", finished_at: new Date().toISOString() })
          .eq("id", latest.id);
      }
      const { data: plan, error } = await supabase
        .from(TABLES.plans)
        .insert({
          project_id: project.id,
          version: (latest?.version ?? 0) + 1,
          status: "queued",
          started_at: new Date().toISOString(),
        })
        .select("id")
        .single<{ id: string }>();
      if (error || !plan) return refuse(ALREADY_RUNNING);
      await markProjectRunning();
      return { ok: true, planId: plan.id };
    }

    case "resume": {
      if (!latest || !(latest.status === "failed" || isStale(latest))) {
        return refuse("There is no failed run to resume.");
      }
      return requeue(latest, {});
    }

    case "answer_blockers": {
      if (!latest || latest.status !== "blocked") return refuse("This plan is not waiting for answers.");
      const answered = action.answers.filter((a) => a.answer);
      if (!answered.length) return refuse("Answer at least one question first.", 400);

      const { data: last } = await supabase
        .from(TABLES.checklistItems)
        .select("position")
        .eq("checklist_id", checklist.id)
        .order("position", { ascending: false })
        .limit(1)
        .maybeSingle<{ position: number }>();
      const base = last?.position ?? 0;
      const now = new Date().toISOString();

      const { error } = await supabase.from(TABLES.checklistItems).insert(
        answered.map((a, i) => ({
          checklist_id: checklist.id,
          position: base + i + 1,
          code: `Q-${base + i + 1}`,
          category: "Follow-up",
          question: a.question,
          options: [],
          answer: a.answer,
          reason: "Asked by the Diagnosis Agent to resolve a blocker.",
          answered_by: userId,
          answered_at: now,
        }))
      );
      if (error) return refuse("Could not save the answers.", 500);
      // Clearing the diagnosis makes the pipeline run Step A again with the new answers.
      return requeue(latest, { diagnosis: null, blockers: null });
    }

    case "override": {
      if (!latest || latest.status !== "blocked") return refuse("This plan is not blocked.");
      return requeue(latest, { override_reason: action.reason });
    }
  }
}

// ---------------------------------------------------------------------------
// The pipeline (runs in the background with the service-role client)
// ---------------------------------------------------------------------------

async function loadInputs(admin: SupabaseClient, projectId: string) {
  const [{ data: project }, { data: files }, { data: checklist }] = await Promise.all([
    admin
      .from(TABLES.projects)
      .select("customer_name, engagement_type")
      .eq("id", projectId)
      .single<InputProject>(),
    admin
      .from(TABLES.projectFiles)
      .select("doc_type, file_name, extracted_text")
      .eq("project_id", projectId)
      .eq("extraction_status", "done")
      .order("created_at", { ascending: true })
      .returns<InputFile[]>(),
    admin
      .from(TABLES.checklists)
      .select("id")
      .eq("project_id", projectId)
      .eq("is_active", true)
      .maybeSingle<{ id: string }>(),
  ]);
  if (!project || !checklist) throw new AgentError("The project or its checklist could not be loaded.");

  const { data: items } = await admin
    .from(TABLES.checklistItems)
    .select("code, category, question, answer")
    .eq("checklist_id", checklist.id)
    .order("position", { ascending: true })
    .returns<InputChecklistItem[]>();

  return {
    project,
    base: `${buildDocumentBlock(project, files ?? [])}\n\n${buildFilledChecklistBlock(items ?? [])}`,
  };
}

function userMessage(step: PlanStep, base: string, plan: PlanRow) {
  const diagnosis = `=== DIAGNOSIS (Part 1, JSON) ===\n${JSON.stringify(plan.diagnosis)}`;
  const strategy = `=== STRATEGY (Part 2, JSON) ===\n${JSON.stringify(plan.strategy)}`;
  switch (step) {
    case "diagnosis":
      return `${base}\n\nWrite the diagnosis now.`;
    case "strategy":
      return `${base}\n\n${diagnosis}\n\nWrite the strategy now.`;
    case "execution":
      return `${base}\n\n${diagnosis}\n\n${strategy}\n\nWrite the execution plan now.`;
  }
}

/**
 * Runs whatever steps a queued plan still needs. Each step is saved before the next starts,
 * so a failed run resumes from the failed step. Never throws: failures land on the plan row.
 */
export async function runPipeline(planId: string): Promise<void> {
  const admin = createAdminClient();
  const update = (changes: Record<string, unknown>) =>
    admin.from(TABLES.plans).update(changes).eq("id", planId);

  const { data: plan } = await admin
    .from(TABLES.plans)
    .select(PLAN_COLUMNS)
    .eq("id", planId)
    .maybeSingle<PlanRow>();
  if (!plan || plan.status !== "queued") return;

  const setProject = (status: Project["status"]) =>
    admin.from(TABLES.projects).update({ status }).eq("id", plan.project_id);

  try {
    const firstStep = PLAN_STEPS.find((step) => !plan[step]);
    if (firstStep) {
      // Claim the run: only one worker can move it out of `queued`.
      const { data: claimed } = await admin
        .from(TABLES.plans)
        .update({ status: `${firstStep}_running` })
        .eq("id", planId)
        .eq("status", "queued")
        .select("id");
      if (!claimed?.length) return;
    }

    const { project, base } = await loadInputs(admin, plan.project_id);

    for (const step of PLAN_STEPS) {
      if (plan[step]) continue;
      if (step !== firstStep) await update({ status: `${step}_running` });

      const { data } = await runAgent<Record<string, unknown>>({
        agent: step,
        projectId: plan.project_id,
        planId,
        userMessage: userMessage(step, base, plan),
        supabase: admin,
      });
      plan[step] = data;
      const { error } = await update({ [step]: data });
      if (error) throw new AgentError(`Could not save the ${step} result.`);

      if (step === "diagnosis" && !plan.override_reason) {
        const readiness = (data.readiness ?? {}) as { can_proceed_to_strategy?: boolean; blockers?: Blocker[] };
        if (readiness.can_proceed_to_strategy === false) {
          await update({
            status: "blocked",
            blockers: readiness.blockers ?? [],
            finished_at: new Date().toISOString(),
          });
          await setProject("plan_blocked");
          return;
        }
      }
    }

    // plans.html is the customer-facing file: it never contains the internal section.
    const finishedAt = new Date().toISOString();
    const html = renderPlanHtml(
      buildPlanDocument({ customerName: project.customer_name, ...plan }),
      planFooter({ version: plan.version, finished_at: finishedAt }, OPENAI_MODEL)
    );
    await update({ status: "ready", html, error: null, finished_at: finishedAt });
    await setProject("plan_ready");
  } catch (error) {
    console.error(`[plan ${planId}] pipeline failed:`, error instanceof Error ? error.message : error);
    await update({
      status: "failed",
      error:
        error instanceof AgentError
          ? error.message
          : "Something went wrong while writing the plan. You can resume from the failed step.",
      finished_at: new Date().toISOString(),
    });
    await setProject("failed");
  }
}

// ---------------------------------------------------------------------------
// Reading progress and the internal section
// ---------------------------------------------------------------------------

export async function getPlan(supabase: SupabaseClient, planId: string) {
  const { data } = await supabase
    .from(TABLES.plans)
    .select(PLAN_COLUMNS)
    .eq("id", planId)
    .maybeSingle<PlanRow>();
  return data;
}

type RunRow = { agent: string; duration_ms: number | null; status: string; model: string | null; repairs: unknown };

async function planRuns(supabase: SupabaseClient, planId: string) {
  const { data } = await supabase
    .from(TABLES.agentRuns)
    .select("agent, duration_ms, status, model, repairs")
    .eq("plan_id", planId)
    .order("created_at", { ascending: true })
    .returns<RunRow[]>();
  return data ?? [];
}

export async function getPlanProgress(supabase: SupabaseClient, plan: PlanRow): Promise<PlanProgress> {
  const runs = await planRuns(supabase, plan.id);
  const failedStep = plan.status === "failed" ? PLAN_STEPS.find((step) => !plan[step]) : undefined;

  return {
    id: plan.id,
    version: plan.version,
    status: plan.status,
    error: plan.error,
    blockers: Array.isArray(plan.blockers) ? plan.blockers : [],
    stale: isStale(plan),
    serverTime: new Date().toISOString(),
    steps: PLAN_STEPS.map((step) => {
      const running = plan.status === `${step}_running`;
      return {
        step,
        state: plan[step] ? "done" : running ? "running" : step === failedStep ? "failed" : "pending",
        runningSince: running ? plan.updated_at : null,
        durationMs: runs
          .filter((run) => run.agent === step)
          .reduce((sum, run) => sum + (run.duration_ms ?? 0), 0),
      };
    }),
  };
}

/** Everything the "Internal (MOTM only)" part needs, plus the model that wrote the plan. */
export async function loadPlanInternal(
  supabase: SupabaseClient,
  plan: PlanRow
): Promise<{ internal: PlanInternal; model: string }> {
  const [runs, { data: checklist }] = await Promise.all([
    planRuns(supabase, plan.id),
    supabase
      .from(TABLES.checklists)
      .select("cam_notes, flags")
      .eq("project_id", plan.project_id)
      .eq("is_active", true)
      .maybeSingle<{ cam_notes: unknown; flags: unknown }>(),
  ]);

  const repairs = runs
    .filter((run) => run.status === "repaired" && Array.isArray(run.repairs))
    .flatMap((run) =>
      (run.repairs as { path?: unknown; fix?: unknown }[]).map((r) => ({
        agent: run.agent,
        path: String(r.path ?? ""),
        fix: String(r.fix ?? ""),
      }))
    );

  return {
    internal: {
      camNotes: checklist?.cam_notes ?? [],
      flags: checklist?.flags ?? [],
      overrideReason: plan.override_reason,
      repairs,
    },
    model: runs.find((run) => run.model)?.model ?? OPENAI_MODEL,
  };
}
