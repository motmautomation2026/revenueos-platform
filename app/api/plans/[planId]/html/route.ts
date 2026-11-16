import { z } from "zod";
import { getPlan, loadPlanInternal, planFooter } from "@/lib/agents/pipeline";
import { requireProject } from "@/lib/auth";
import { sanitizeFileName } from "@/lib/files/rules";
import { buildPlanDocument } from "@/lib/plan/document";
import { renderPlanHtml } from "@/lib/plan/render-html";
import { createClient } from "@/lib/supabase/server";

// A tiny, self-contained print helper injected only for `?print=1`. It opens the browser's print
// dialog on load (where the user picks "Save as PDF"), then closes the tab after printing. This is
// what the Plan page's "Download PDF" uses, so the PDF is made from this clean, print-styled document
// rather than from the app page — giving a valid, openable PDF in every browser with no server-side
// PDF engine.
const PRINT_SCRIPT =
  `<script>window.addEventListener("load",function(){setTimeout(function(){try{window.focus();window.print();}catch(e){}},200);});` +
  `window.addEventListener("afterprint",function(){window.close();});</script>`;

/**
 * Serves the plan as one self-contained HTML document.
 * - Default: downloaded as an `.html` file. `?internal=1` adds the MOTM-only part.
 * - `?print=1`: rendered inline and auto-opens the print dialog, for "Save as PDF".
 */
export async function GET(request: Request, { params }: { params: Promise<{ planId: string }> }) {
  const { planId } = await params;
  if (!z.uuid().safeParse(planId).success) {
    return Response.json({ error: "Not found" }, { status: 404 });
  }

  const plan = await getPlan(await createClient(), planId);
  if (!plan || plan.status !== "ready") return Response.json({ error: "Not found" }, { status: 404 });
  const { supabase, project } = await requireProject(plan.project_id);

  const query = new URL(request.url).searchParams;
  const withInternal = query.get("internal") === "1";
  const autoPrint = query.get("print") === "1";
  const { internal, model } = await loadPlanInternal(supabase, plan);
  let html = renderPlanHtml(
    buildPlanDocument({
      customerName: project.customer_name,
      diagnosis: plan.diagnosis,
      strategy: plan.strategy,
      execution: plan.execution,
      internal: withInternal ? internal : undefined,
    }),
    planFooter(plan, model)
  );

  // For printing, render inline (so the browser displays and prints it) and inject the print helper.
  if (autoPrint) {
    html = html.replace("</body>", `${PRINT_SCRIPT}</body>`);
    return new Response(html, {
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
    });
  }

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
