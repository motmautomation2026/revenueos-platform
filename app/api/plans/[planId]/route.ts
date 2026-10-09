import { z } from "zod";
import { getPlan, getPlanProgress } from "@/lib/agents/pipeline";
import { requireProject } from "@/lib/auth";
import { createClient } from "@/lib/supabase/server";

/** Progress of one plan, polled by the plan page. */
export async function GET(_request: Request, { params }: { params: Promise<{ planId: string }> }) {
  const { planId } = await params;
  if (!z.uuid().safeParse(planId).success) {
    return Response.json({ error: "Not found" }, { status: 404 });
  }

  // First find the plan's project, then run the usual session + ownership check on it.
  // RLS already hides other users' plans from this lookup.
  const plan = await getPlan(await createClient(), planId);
  if (!plan) return Response.json({ error: "Not found" }, { status: 404 });
  const { supabase } = await requireProject(plan.project_id);

  return Response.json(await getPlanProgress(supabase, plan), {
    headers: { "Cache-Control": "no-store" },
  });
}
