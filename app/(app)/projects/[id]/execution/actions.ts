"use server";

import { revalidatePath } from "next/cache";
import { z } from "zod";
import { requireProject } from "@/lib/auth";
import { MAX_NOTE_LENGTH } from "@/lib/brain/answers";
import { saveAccounts } from "@/lib/brain/account-store";
import { callerSchema } from "@/lib/brain/caller";
import { saveCaller } from "@/lib/brain/caller-store";
import { approvePaidStep } from "@/lib/brain/paid-steps";
import { saveSender } from "@/lib/brain/sender-store";
import { brainCustomerFor } from "@/lib/brain/read";
import { contactColumns, contactSchema, stagingColumns, stagingSchema } from "@/lib/brain/staging";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";
import { senderSchema } from "@/lib/checklist-sender";
import { accountsSchema } from "@/lib/brain/accounts";
import {
  answerRequest,
  cancelRequest,
  createRequest,
  retryRequest,
  type RequestResult,
} from "@/lib/brain/request-store";
import { MAX_ANSWER_TEXT } from "@/lib/brain/requests";
import { recordAnswer, type AnswerResult } from "@/lib/brain/write";

const schema = z.object({
  projectId: z.uuid(),
  decisionId: z.number().int().positive(),
  answer: z.string().min(1).max(20),
  note: z.string().max(MAX_NOTE_LENGTH),
});

/** Answers one of the Brain's open decisions for a project the signed-in user owns. */
export async function answerDecision(input: unknown): Promise<AnswerResult> {
  const parsed = schema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Invalid request." };

  const { user, project } = await requireProject(parsed.data.projectId);
  if (project.status !== "plan_ready") return { ok: false, error: "Execution has not started for this project." };

  const name = (user.user_metadata?.full_name as string | undefined)?.trim();
  const result = await recordAnswer({
    projectId: project.id,
    decisionId: parsed.data.decisionId,
    answer: parsed.data.answer,
    note: parsed.data.note,
    answeredBy: name ? `${name} (${user.email})` : (user.email ?? user.id),
  });

  if (result.ok) revalidatePath(`/projects/${project.id}`, "layout");
  return result;
}

// ----- Starting execution: requests to the Brain (lib/brain/request-store.ts) -----

const requestSchema = z.object({ projectId: z.uuid(), requestId: z.uuid() });
const planAnswerSchema = requestSchema.extend({
  answer: z.enum(["yes", "override", "changes"]),
  note: z.string().max(MAX_ANSWER_TEXT),
});

/** A ready project the signed-in user owns, and the name the Brain records for what they do. */
async function readyProject(projectId: string) {
  const { supabase, user, project } = await requireProject(projectId);
  const name = (user.user_metadata?.full_name as string | undefined)?.trim();
  return { supabase, user, project, ready: project.status === "plan_ready", name: name || user.email || user.id };
}

const NOT_READY: RequestResult = { ok: false, error: "The GTM plan must be ready before execution starts." };

async function finish(projectId: string, result: RequestResult): Promise<RequestResult> {
  if (result.ok) revalidatePath(`/projects/${projectId}`, "layout");
  return result;
}

/** "Start execution": asks the Brain to write this month's plan. Nothing is sent or bought by this. */
export async function requestExecution(input: unknown): Promise<RequestResult> {
  const parsed = z.object({ projectId: z.uuid(), run: stagingSchema.optional() }).safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Invalid request." };
  const { supabase, user, project, ready, name } = await readyProject(parsed.data.projectId);
  if (!ready) return NOT_READY;
  if (project.archived_at) return { ok: false, error: "This project is archived. Restore it before starting execution." };

  // Staging or production is saved on the project first: the Brain's worker reads it from there.
  if (parsed.data.run) {
    const { error } = await supabase.from(TABLES.projects).update(stagingColumns(parsed.data.run)).eq("id", project.id);
    if (error) return { ok: false, error: "The staging settings could not be saved. Try again." };
  }
  return finish(project.id, await createRequest({ projectId: project.id, userId: user.id, userName: name }));
}

/** Approve the published month plan, approve it despite broken rules (with a reason), or ask for changes. */
export async function answerMonthPlan(input: unknown): Promise<RequestResult> {
  const parsed = planAnswerSchema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Invalid request." };
  const { project, ready, name } = await readyProject(parsed.data.projectId);
  if (!ready) return NOT_READY;
  return finish(
    project.id,
    await answerRequest({
      projectId: project.id,
      requestId: parsed.data.requestId,
      answer: parsed.data.answer,
      note: parsed.data.note,
      answeredBy: name,
    })
  );
}

export async function cancelExecutionRequest(input: unknown): Promise<RequestResult> {
  const parsed = requestSchema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Invalid request." };
  const { project } = await readyProject(parsed.data.projectId);
  return finish(project.id, await cancelRequest(project.id, parsed.data.requestId));
}

export async function retryExecutionRequest(input: unknown): Promise<RequestResult> {
  const parsed = requestSchema.safeParse(input);
  if (!parsed.success) return { ok: false, error: "Invalid request." };
  const { project, ready } = await readyProject(parsed.data.projectId);
  if (!ready) return NOT_READY;
  return finish(project.id, await retryRequest(project.id, parsed.data.requestId));
}

/** Saves this customer's own email and calling accounts. The password is never sent back to the browser. */
export async function saveToolAccounts(input: unknown): Promise<{ ok: true } | { ok: false; error: string }> {
  const parsed = z.object({ projectId: z.uuid(), accounts: accountsSchema }).safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Check the form and try again." };
  const { user, project } = await requireProject(parsed.data.projectId);
  const result = await saveAccounts(project.id, user.id, parsed.data.accounts);
  if (result.ok) revalidatePath(`/projects/${project.id}`, "layout");
  return result;
}

/** Saves the customer's email and sender details (kept in the checklist). The password is never sent back. */
export async function saveSenderAccount(input: unknown): Promise<{ ok: true } | { ok: false; error: string }> {
  const parsed = z.object({ projectId: z.uuid(), sender: senderSchema }).safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Check the form and try again." };
  const { user, project } = await requireProject(parsed.data.projectId);
  const result = await saveSender(project.id, user.id, parsed.data.sender);
  if (result.ok) revalidatePath(`/projects/${project.id}`, "layout");
  return result;
}

/** A person starts the task that buys data. The Brain's worker runs it; nothing is bought from here. */
export async function startPaidStep(input: unknown): Promise<RequestResult> {
  const parsed = z.object({ projectId: z.uuid(), stepId: z.uuid() }).safeParse(input);
  if (!parsed.success) return { ok: false, error: "Invalid request." };
  const { project, ready, name } = await readyProject(parsed.data.projectId);
  if (!ready) return NOT_READY;
  if (project.archived_at) return { ok: false, error: "This project is archived. Restore it first." };
  return finish(project.id, await approvePaidStep({ projectId: project.id, stepId: parsed.data.stepId, approvedBy: name }));
}

/** Staging or production for the steps of the current run that have not started yet. */
export async function saveRunMode(input: unknown): Promise<RequestResult> {
  const parsed = z.object({ projectId: z.uuid(), run: stagingSchema }).safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Check the form and try again." };
  const { supabase, project, ready } = await readyProject(parsed.data.projectId);
  if (!ready) return NOT_READY;
  const { error } = await supabase.from(TABLES.projects).update(stagingColumns(parsed.data.run)).eq("id", project.id);
  if (error) return { ok: false, error: "The run mode could not be saved. Try again." };
  return finish(project.id, {
    ok: true,
    message:
      parsed.data.run.mode === "staging"
        ? "Staging is on: the next steps fetch no more than your limits."
        : "Production is on for the next steps.",
  });
}

/**
 * Changes the email and phone on one profile the Brain found. The Brain's calling and email
 * steps read these, so this decides who is actually contacted. Only a profile filed under this
 * project's own customer key can be changed.
 */
export async function updateContact(input: unknown): Promise<{ ok: true } | { ok: false; error: string }> {
  const parsed = z
    .object({ projectId: z.uuid(), personId: z.number().int().positive(), contact: contactSchema })
    .safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Check the form and try again." };
  const { project } = await requireProject(parsed.data.projectId);
  if (project.run_mode !== "staging") return { ok: false, error: "Profiles can be edited only while the project is in Staging." };
  const customer = await brainCustomerFor(project.id);
  if (!customer) return { ok: false, error: "This project has no Brain key yet." };

  const { data, error } = await createAdminClient()
    .from("quickicp_revenueos_client_people_data")
    .update(contactColumns(parsed.data.contact))
    .eq("id", parsed.data.personId)
    .eq("customer", customer) // never another customer's profile
    .select("id");
  if (error) return { ok: false, error: "The profile could not be saved. Try again." };
  if (!data?.length) return { ok: false, error: "This profile no longer exists." };

  revalidatePath(`/projects/${project.id}/execution`, "layout");
  return { ok: true };
}

/** Which calling account and caller line this customer's AI calls go out from. Secrets are never sent back. */
export async function saveCallerAccount(input: unknown): Promise<RequestResult> {
  const parsed = z.object({ projectId: z.uuid(), caller: callerSchema }).safeParse(input);
  if (!parsed.success) return { ok: false, error: parsed.error.issues[0]?.message ?? "Check the form and try again." };
  const { user, project } = await requireProject(parsed.data.projectId);
  return finish(project.id, await saveCaller(project.id, user.id, parsed.data.caller));
}
