import { z } from "zod";
import { getPlan, loadPlanInternal, planFooter } from "@/lib/agents/pipeline";
import { requireProject } from "@/lib/auth";
import { sanitizeFileName } from "@/lib/files/rules";
import { buildPlanDocument } from "@/lib/plan/document";
import { renderPlanHtml } from "@/lib/plan/render-html";
import { createClient } from "@/lib/supabase/server";

/** Downloads the plan as one self-contained HTML file. `?internal=1` adds the MOTM-only part. */
export async function GET(request: Request, { params }: { params: Promise<{ planId: string }> }) {
  const { planId } = await params;
  if (!z.uuid().safeParse(planId).success) {
    return Response.json({ error: "Not found" }, { status: 404 });
  }

  const plan = await getPlan(await createClient(), planId);
  if (!plan || plan.status !== "ready") return Response.json({ error: "Not found" }, { status: 404 });
  const { supabase, project } = await requireProject(plan.project_id);

  const withInternal = new URL(request.url).searchParams.get("internal") === "1";
  const { internal, model } = await loadPlanInternal(supabase, plan);
  const html = renderPlanHtml(
    buildPlanDocument({
      customerName: project.customer_name,
      diagnosis: plan.diagnosis,
      strategy: plan.strategy,
      execution: plan.execution,
      internal: withInternal ? internal : undefined,
    }),
    planFooter(plan, model)
  );

  const suffix = withInternal ? " internal" : "";
  const fileName = sanitizeFileName(`${project.customer_name} GTM plan v${plan.version}${suffix}.html`);
  return new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Disposition": `attachment; filename="${fileName}"`,
      "Cache-Control": "no-store",
    },
  });
}
