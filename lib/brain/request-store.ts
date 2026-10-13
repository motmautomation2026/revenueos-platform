import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import { loadSetupGaps } from "./setup";
import { brainCustomerFor } from "./read";
import {
  ACTIVE_STATUSES,
  APPROVER_MAX,
  buildRequestView,
  planAnswerProblem,
  type BrainRequestRow,
  type PlanAnswer,
  type RequestView,
} from "./requests";

// The app's side of public.revenueos_brain_requests. The table has row level security with no
// policies, so it is reached only with the service-role client, and only after the caller has
// checked that the signed-in user owns the project (requireProject).
//
// Nothing here runs the Brain. A request is a row; the Brain's worker picks it up.

const TABLE = "revenueos_brain_requests";
const COLUMNS =
  "id, project_id, customer_key, status, month, test_mode, requested_by_name, plan, revenue_analysis, plan_errors, brain_message, answer, approved_by, approved_at, failed_step, error, created_at, updated_at";
const ACTIVE_RUNS = ["planning", "running", "waiting_human"];

export type RequestResult = { ok: true; message: string } | { ok: false; error: string };
export type RequestState = { available: boolean; view: RequestView };

/** The project's latest request. `available` is false until the database update that adds the table is applied. */
export const loadRequest = cache(async (projectId: string): Promise<RequestState> => {
  try {
    const { data, error } = await createAdminClient()
      .from(TABLE)
      .select(COLUMNS)
      .eq("project_id", projectId)
      .order("created_at", { ascending: false })
      .limit(1)
      .maybeSingle<BrainRequestRow>();
    if (error) return { available: false, view: buildRequestView(null) };
    return { available: true, view: buildRequestView(data ?? null) };
  } catch {
    return { available: false, view: buildRequestView(null) };
  }
});

/** "Start execution": asks the Brain to plan a month for this project. */
export async function createRequest(input: {
  projectId: string;
  userId: string;
  userName: string;
}): Promise<RequestResult> {
  const customer = await brainCustomerFor(input.projectId);
  if (!customer) return { ok: false, error: "This project has no Brain key yet." };

  // The Brain's setup check needs this customer's own email and calling accounts.
  const gaps = await loadSetupGaps(input.projectId);
  if (gaps.email) return { ok: false, error: "Add this customer's sender details first (Tools > Email)." };
  if (gaps.calls) return { ok: false, error: "Choose this customer's calling account first (Tools > Calls)." };

  const admin = createAdminClient();
  const { data: runs, error: runsError } = await admin
    .from("brain_customer_runs")
    .select("run_key")
    .eq("customer", customer)
    .in("status", ACTIVE_RUNS)
    .limit(1);
  if (runsError) return { ok: false, error: "The Brain's data could not be read. Try again." };
  if (runs?.length) return { ok: false, error: "Execution is already running for this customer." };

  const { error } = await admin.from(TABLE).insert({
    project_id: input.projectId,
    customer_key: customer,
    requested_by: input.userId,
    requested_by_name: input.userName.slice(0, APPROVER_MAX),
  });
  // 23505: the one-active-request-per-project index.
  if (error?.code === "23505") return { ok: false, error: "A request for this project is already in progress." };
  if (error) return { ok: false, error: "The request could not be saved. Try again." };
  return { ok: true, message: "Sent to the Brain. It will write the month plan and ask for your approval here." };
}

/** Records the person's answer to the published month plan. The Brain reads it and checks it again. */
export async function answerRequest(input: {
  projectId: string;
  requestId: string;
  answer: PlanAnswer;
  note: string;
  answeredBy: string;
}): Promise<RequestResult> {
  const admin = createAdminClient();
  const { data: row } = await admin
    .from(TABLE)
    .select("id, status, answer, plan_errors")
    .eq("id", input.requestId)
    .eq("project_id", input.projectId) // never another project's request
    .maybeSingle<{ id: string; status: string; answer: string | null; plan_errors: unknown }>();
  if (!row) return { ok: false, error: "This request no longer exists." };
  if (row.status !== "awaiting_approval") return { ok: false, error: "The month plan is not waiting for approval any more." };
  if (row.answer) return { ok: false, error: "An answer was already sent. The Brain is reading it." };

  const broken = Array.isArray(row.plan_errors) ? row.plan_errors.length : 0;
  const note = input.note.trim();
  const problem = planAnswerProblem(input.answer, note, broken);
  if (problem) return { ok: false, error: problem };

  const { data: updated, error } = await admin
    .from(TABLE)
    .update({
      answer: input.answer,
      answer_text: input.answer === "yes" ? null : note,
      answered_by: input.answeredBy.slice(0, APPROVER_MAX),
      answered_at: new Date().toISOString(),
    })
    .eq("id", row.id)
    .eq("status", "awaiting_approval")
    .is("answer", null) // only the first answer counts
    .select("id");
  if (error) return { ok: false, error: "The answer could not be saved. Try again." };
  if (!updated?.length) return { ok: false, error: "Someone else answered just now." };

  return {
    ok: true,
    message:
      input.answer === "changes"
        ? "Changes sent. The Brain will write the plan again and ask once more."
        : "Approval sent. The Brain will prepare the run and start.",
  };
}

/** Stops a request that is still in progress. The Brain stops at its next check; nothing is approved. */
export async function cancelRequest(projectId: string, requestId: string): Promise<RequestResult> {
  const { data, error } = await createAdminClient()
    .from(TABLE)
    .update({ status: "cancelled" })
    .eq("id", requestId)
    .eq("project_id", projectId)
    .in("status", [...ACTIVE_STATUSES].filter((status) => status !== "finishing")) // an approved plan is not cancelled here
    .select("id");
  if (error) return { ok: false, error: "The request could not be cancelled. Try again." };
  if (!data?.length) return { ok: false, error: "This request can no longer be cancelled." };
  return { ok: true, message: "Cancelled. Nothing was approved or started." };
}

/**
 * "Try again" on a failed request: hands the same request back to the Brain. A request that
 * failed after its plan was approved resumes there, so the plan is not written a second time.
 */
export async function retryRequest(projectId: string, requestId: string): Promise<RequestResult> {
  const { data, error } = await createAdminClient()
    .from(TABLE)
    .update({ status: "requested", error: null, brain_message: null, answer: null, answer_text: null })
    .eq("id", requestId)
    .eq("project_id", projectId)
    .eq("status", "failed")
    .select("id");
  if (error?.code === "23505") return { ok: false, error: "A request for this project is already in progress." };
  if (error) return { ok: false, error: "The request could not be sent again. Try again." };
  if (!data?.length) return { ok: false, error: "This request can no longer be tried again." };
  return { ok: true, message: "Sent to the Brain again." };
}
