import type { Metadata } from "next";
import { Uploader } from "@/components/upload/uploader";
import { requireProject } from "@/lib/auth";
import { PROJECT_FILE_COLUMNS, type ProjectFileRow } from "@/lib/files/rules";
import { reachedStep } from "@/lib/projects";
import { TABLES } from "@/lib/supabase/tables";

export const metadata: Metadata = { title: "Upload" };

// Text extraction and checklist generation run in this page's server actions.
export const maxDuration = 800;

export default async function UploadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { supabase, user, project } = await requireProject(id);

  const { data: files } = await supabase
    .from(TABLES.projectFiles)
    .select(PROJECT_FILE_COLUMNS)
    .eq("project_id", project.id)
    .order("created_at", { ascending: true })
    .returns<ProjectFileRow[]>();

  return (
    <div className="grid gap-4">
      <div>
        <h2 className="text-lg font-semibold">Upload documents</h2>
        <p className="text-sm text-muted-foreground">
          Add this customer&apos;s documents and pick a type for each. The text is extracted on the
          server and used to write the checklist.
        </p>
      </div>
      <Uploader
        projectId={project.id}
        ownerId={user.id}
        initialFiles={files ?? []}
        hasChecklist={reachedStep(project.status) >= 2}
        generatingElsewhere={
          // Older than 15 minutes means the run died; the button comes back so it can be retried.
          project.status === "checklist_generating" &&
          Date.now() - Date.parse(project.updated_at) < 15 * 60 * 1000
        }
      />
    </div>
  );
}
