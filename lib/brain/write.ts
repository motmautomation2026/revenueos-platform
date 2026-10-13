import "server-only";
import { createAdminClient } from "@/lib/supabase/admin";
import { afterAnswer, answerProblem } from "./answers";
import { brainCustomerFor } from "./read";

// The ONLY place the web app writes to the Brain's tables, and it writes one thing: the answer
// to an open decision. It does what the Brain's own `orchestrator.py answer` command does
// (brain/state_store.py answer_decision): same allowed answers, same fields, and only if the
// decision is still open. Nothing is run from here; the Brain applies the answer on its next run.

export type AnswerResult = { ok: true; message: string } | { ok: false; error: string };

export async function recordAnswer(input: {
  projectId: string;
  decisionId: number;
  answer: string;
  note: string;
  /** Who answered, for the audit trail in the decision's reason. */
  answeredBy: string;
}): Promise<AnswerResult> {
  const customer = await brainCustomerFor(input.projectId);
  if (!customer) return { ok: false, error: "The Brain is not set up for this project." };

  const admin = createAdminClient();

  const { data: decision } = await admin
    .from("brain_decisions")
    .select("id, run_id, kind, status, answer")
    .eq("id", input.decisionId)
    .maybeSingle<{ id: number; run_id: number; kind: string; status: string; answer: string | null }>();
  if (!decision) return { ok: false, error: "This decision no longer exists." };

  // Never another customer's decision: its run must belong to this project's customer.
  const { data: run } = await admin
    .from("brain_customer_runs")
    .select("id")
    .eq("id", decision.run_id)
    .eq("customer", customer)
    .maybeSingle<{ id: number }>();
  if (!run) return { ok: false, error: "This decision no longer exists." };

  if (decision.status !== "open") {
    return { ok: false, error: `This decision was already answered (“${decision.answer ?? ""}”).` };
  }

  const note = input.note.trim();
  const problem = answerProblem(decision.kind, input.answer, note);
  if (problem) return { ok: false, error: problem };

  const reason = [note, `via RevenueOS app: ${input.answeredBy}`].filter(Boolean).join(" · ");
  const { data: updated, error } = await admin
    .from("brain_decisions")
    .update({
      status: "answered",
      answer: input.answer,
      decided_by: "user",
      reason,
      answered_at: new Date().toISOString(),
    })
    .eq("id", decision.id)
    .eq("status", "open") // only an open decision can be answered
    .select("id");
  if (error) return { ok: false, error: "The answer could not be saved. Try again." };
  if (!updated?.length) return { ok: false, error: "Someone else answered this decision just now." };

  return { ok: true, message: afterAnswer(decision.kind) };
}
