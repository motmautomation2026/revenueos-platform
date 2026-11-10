import { editPlanSection, planEditSchema } from "@/lib/agents/pipeline";
import { requireProject } from "@/lib/auth";

// One synchronous LLM call revises the section and saves a new version; give it room to finish.
export const maxDuration = 300;

/** Applies a plain-language change to one section of the latest ready plan, saved as a new version. */
export async function POST(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { supabase, user, project } = await requireProject(id);

  const parsed = planEditSchema.safeParse(await request.json().catch(() => null));
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

  const result = await editPlanSection(supabase, project, user.id, parsed.data);
  if (!result.ok) return Response.json({ error: result.error }, { status: result.status });

  return Response.json({ planId: result.planId }, { status: 200 });
}
