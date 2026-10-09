import { after } from "next/server";
import { applyPlanAction, planActionSchema, runPipeline } from "@/lib/agents/pipeline";
import { requireProject } from "@/lib/auth";

// The pipeline keeps running after the response is sent, so it does not depend on the browser.
export const maxDuration = 800;

/** Starts a new plan version, resumes a failed one, or unblocks a blocked one. */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { supabase, user, project } = await requireProject(id);

  const parsed = planActionSchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return Response.json(
      { error: parsed.error.issues[0]?.message ?? "Invalid request." },
      { status: 400 }
    );
  }
  if (!process.env.SUPABASE_SERVICE_ROLE_KEY || !process.env.OPENAI_API_KEY) {
    return Response.json(
      { error: "The server is missing SUPABASE_SERVICE_ROLE_KEY or OPENAI_API_KEY." },
      { status: 500 }
    );
  }

  const result = await applyPlanAction(supabase, project, user.id, parsed.data);
  if (!result.ok) return Response.json({ error: result.error }, { status: result.status });

  after(() => runPipeline(result.planId));
  return Response.json({ planId: result.planId }, { status: 202 });
}
