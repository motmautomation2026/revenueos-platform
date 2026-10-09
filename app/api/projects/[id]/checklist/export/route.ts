import { requireProject } from "@/lib/auth";
import { buildChecklistWorkbook, type ExportItem } from "@/lib/files/excel";
import { sanitizeFileName } from "@/lib/files/rules";
import { TABLES } from "@/lib/supabase/tables";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { supabase, project } = await requireProject(id);

  const { data: checklist } = await supabase
    .from(TABLES.checklists)
    .select("id")
    .eq("project_id", project.id)
    .eq("is_active", true)
    .maybeSingle<{ id: string }>();
  if (!checklist) return Response.json({ error: "Checklist not found" }, { status: 404 });

  // Only the four customer-facing columns are selected; reason and notes never leave the server.
  const { data: items } = await supabase
    .from(TABLES.checklistItems)
    .select("code, category, question, options, answer")
    .eq("checklist_id", checklist.id)
    .order("position", { ascending: true })
    .returns<ExportItem[]>();

  const workbook = await buildChecklistWorkbook(items ?? []);
  const fileName = sanitizeFileName(`${project.customer_name} checklist.xlsx`);

  return new Response(new Uint8Array(workbook), {
    headers: {
      "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "Content-Disposition": `attachment; filename="${fileName}"`,
      "Cache-Control": "no-store",
    },
  });
}
