import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { ChecklistForm, type ChecklistItemView } from "@/components/checklist/checklist-form";
import { InternalNotes } from "@/components/checklist/internal-notes";
import { CHECKLIST_MAX_QUESTIONS, CHECKLIST_MIN_QUESTIONS } from "@/lib/agents/config";
import { requireProject } from "@/lib/auth";
import { reachedStep } from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";

export const metadata: Metadata = { title: "Checklist" };

// Regenerating the checklist runs the AI inside this page's server actions.
export const maxDuration = 800;

export default async function ChecklistPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { supabase, project } = await requireProject(id);
  if (reachedStep(project.status) < 2) redirect(`/projects/${project.id}/upload`);

  const { data: checklist } = await supabase
    .from(TABLES.checklists)
    .select("id, status, flags, cam_notes")
    .eq("project_id", project.id)
    .eq("is_active", true)
    .maybeSingle<{ id: string; status: "draft" | "submitted"; flags: unknown; cam_notes: unknown }>();
  if (!checklist) redirect(`/projects/${project.id}/upload`);

  // `reason` is deliberately not selected: it must never reach the customer-facing list.
  const { data: items } = await supabase
    .from(TABLES.checklistItems)
    .select("id, code, category, question, options, prefilled_answer, answer")
    .eq("checklist_id", checklist.id)
    .order("position", { ascending: true })
    .returns<ChecklistItemView[]>();

  const generated = (items ?? []).filter((item) => item.category !== "Follow-up").length;
  const countWarning =
    generated < CHECKLIST_MIN_QUESTIONS || generated > CHECKLIST_MAX_QUESTIONS
      ? `${generated} questions (expected ${CHECKLIST_MIN_QUESTIONS}–${CHECKLIST_MAX_QUESTIONS})`
      : null;

  return (
    <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <ChecklistForm
        key={checklist.id}
        projectId={project.id}
        items={items ?? []}
        submitted={checklist.status === "submitted"}
        canReopen={project.status === "checklist_submitted"}
        canRegenerate={project.status === "checklist_ready" || project.status === "checklist_submitted"}
        countWarning={countWarning}
      />
      <InternalNotes flags={checklist.flags} camNotes={checklist.cam_notes} />
    </div>
  );
}
