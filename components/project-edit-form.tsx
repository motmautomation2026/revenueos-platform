"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/form-field";
import { updateProject } from "@/app/(app)/projects/actions";
import { editProjectSchema, type EditProjectInput } from "@/lib/validation";

export function ProjectEditForm({
  projectId,
  defaults,
}: {
  projectId: string;
  defaults: { clientName: string; clientEmail: string; clientPassword: string; description: string };
}) {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<EditProjectInput>({
    resolver: zodResolver(editProjectSchema),
    defaultValues: defaults,
  });

  const onSubmit = handleSubmit(async (values) => {
    const result = await updateProject(projectId, values);
    if ("error" in result) {
      setError("root", { message: result.error });
      return;
    }
    toast.success("Project updated.");
    router.push(`/projects/${projectId}`);
    router.refresh();
  });

  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      <FormField id="clientName" label="Client name" error={errors.clientName?.message}>
        <Input id="clientName" autoComplete="off" {...register("clientName")} />
      </FormField>

      <FormField
        id="clientEmail"
        label="Client email"
        error={errors.clientEmail?.message}
        hint="Opens the client's email and calling workspaces. Avoid changing it once execution has started."
      >
        <Input id="clientEmail" type="email" autoComplete="off" {...register("clientEmail")} />
      </FormField>

      <FormField
        id="clientPassword"
        label="Client password"
        error={errors.clientPassword?.message}
        hint="Used to open the client's email and calling tools. At least 8 characters. Shown so you can check or change it."
      >
        <Input id="clientPassword" type="text" autoComplete="off" {...register("clientPassword")} />
      </FormField>

      <FormField id="description" label="Short description (optional)" error={errors.description?.message}>
        <Textarea id="description" rows={3} {...register("description")} />
      </FormField>

      {errors.root && (
        <p role="alert" className="text-sm text-destructive">
          {errors.root.message}
        </p>
      )}
      <div className="flex gap-2">
        <Button type="submit" disabled={isSubmitting}>
          {isSubmitting ? "Saving…" : "Save changes"}
        </Button>
        <Link href={`/projects/${projectId}`} className={buttonVariants({ variant: "ghost" })}>
          Cancel
        </Link>
      </div>
    </form>
  );
}
