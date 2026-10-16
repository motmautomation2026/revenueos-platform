import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import { TABLES } from "@/lib/supabase/tables";
import {
  SENDER_CATEGORY,
  SENDER_CODES,
  SENDER_ITEMS,
  buildSenderView,
  senderAnswers,
  type SenderInput,
  type SenderView,
} from "@/lib/checklist-sender";

// The customer's sender details live in the project's checklist (category "Email sender setup"),
// and the customer's email on the project. This reads and writes both for the Email page's
// Sender account card, which stays editable after the checklist itself is locked: a mailbox
// password changes long after the plan is written.
//
// Service-role client, and only after the caller has checked that the signed-in user owns the
// project (requireProject). The password is written and read by the Brain; it is never returned.

export type SenderState = { available: boolean; view: SenderView };

type Item = { id: string; code: string; position: number; answer: string | null };

async function activeChecklist(projectId: string): Promise<{ id: string; items: Item[] } | null> {
  const admin = createAdminClient();
  const { data: checklist } = await admin
    .from(TABLES.checklists)
    .select("id")
    .eq("project_id", projectId)
    .eq("is_active", true)
    .maybeSingle<{ id: string }>();
  if (!checklist) return null;
  const { data: items } = await admin
    .from(TABLES.checklistItems)
    .select("id, code, position, answer")
    .eq("checklist_id", checklist.id)
    .returns<Item[]>();
  return { id: checklist.id, items: items ?? [] };
}

/** `available` is false until the database update that adds the customer email is applied. */
export const loadSender = cache(async (projectId: string): Promise<SenderState> => {
  const admin = createAdminClient();
  const empty = { available: false, view: buildSenderView({}, null) };
  try {
    const { data: project, error } = await admin
      .from(TABLES.projects)
      .select("customer_email")
      .eq("id", projectId)
      .maybeSingle<{ customer_email: string | null }>();
    if (error) return empty;

    // A customer whose email account already exists (Tools > Tool accounts) keeps using it.
    const { data: account } = await admin
      .from("revenueos_brain_accounts")
      .select("email_client_reference")
      .eq("project_id", projectId)
      .maybeSingle<{ email_client_reference: string | null }>();

    const checklist = await activeChecklist(projectId);
    const answers = Object.fromEntries(
      (checklist?.items ?? []).filter((item) => SENDER_CODES.includes(item.code)).map((item) => [item.code, item.answer])
    );
    return {
      available: true,
      view: buildSenderView(answers, project?.customer_email, !!account?.email_client_reference?.trim()),
    };
  } catch {
    return empty;
  }
});

export async function saveSender(
  projectId: string,
  userId: string,
  input: SenderInput
): Promise<{ ok: true } | { ok: false; error: string }> {
  const admin = createAdminClient();
  const failed = { ok: false as const, error: "The sender details could not be saved. Try again." };

  const { error: projectError } = await admin
    .from(TABLES.projects)
    .update({ customer_email: input.loginEmail.trim().toLowerCase() || null })
    .eq("id", projectId);
  if (projectError) return failed;

  const checklist = await activeChecklist(projectId);
  if (!checklist) return { ok: false, error: "This project has no checklist yet. Generate it first." };

  const answers = senderAnswers(input);
  const byCode = new Map(checklist.items.map((item) => [item.code, item]));
  const now = new Date().toISOString();
  let position = Math.max(0, ...checklist.items.map((item) => item.position));

  for (const item of SENDER_ITEMS) {
    const existing = byCode.get(item.code);
    const hasAnswer = item.code in answers;
    if (existing) {
      if (!hasAnswer) continue; // an empty password keeps the saved one
      const { error } = await admin
        .from(TABLES.checklistItems)
        .update({ answer: answers[item.code], answered_by: userId, answered_at: now })
        .eq("id", existing.id);
      if (error) return failed;
    } else {
      // A checklist written before these questions existed: add them, so the answers have a home.
      position += 1;
      const { error } = await admin.from(TABLES.checklistItems).insert({
        checklist_id: checklist.id,
        position,
        code: item.code,
        category: SENDER_CATEGORY,
        question: item.question,
        options: [...item.options],
        answer: hasAnswer ? answers[item.code] : null,
        answered_by: hasAnswer ? userId : null,
        answered_at: hasAnswer ? now : null,
        reason: "Needed to send emails from the customer's own mailbox.",
      });
      if (error) return failed;
    }
  }
  return { ok: true };
}
