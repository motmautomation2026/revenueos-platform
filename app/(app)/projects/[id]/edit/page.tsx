import Link from "next/link";
import { redirect } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ProjectEditForm } from "@/components/project-edit-form";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { requireProject } from "@/lib/auth";
import { createAdminClient } from "@/lib/supabase/admin";

export default async function EditProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { project, user } = await requireProject(id);
  // Editing the client's credentials (incl. the password) is the owner/admin's job.
  if (project.owner_id !== user.id) redirect(`/projects/${project.id}`);

  // The password is read back here (owner only) so the admin can see and edit it, rather than
  // retype a secret they can't see. Read with the server-side key; this never reaches a non-owner.
  const { data: account } = await createAdminClient()
    .from("revenueos_brain_accounts")
    .select("workspace_password")
    .eq("project_id", project.id)
    .maybeSingle<{ workspace_password: string | null }>();

  return (
    <div className="mx-auto grid w-full max-w-xl gap-4">
      <Link
        href={`/projects/${project.id}`}
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Back to project
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>Edit project</CardTitle>
          <CardDescription>Change the client&apos;s details. The password is only replaced if you enter a new one.</CardDescription>
        </CardHeader>
        <CardContent>
          <ProjectEditForm
            projectId={project.id}
            defaults={{
              clientName: project.customer_name,
              clientEmail: project.customer_email ?? "",
              clientPassword: account?.workspace_password ?? "",
              description: project.description ?? "",
            }}
          />
        </CardContent>
      </Card>
    </div>
  );
}
