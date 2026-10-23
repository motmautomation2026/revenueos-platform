"use client";

import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/form-field";
import { newProjectSchema, type NewProjectInput } from "@/lib/validation";
import { createProject } from "../actions";

export default function NewProjectPage() {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<NewProjectInput>({ resolver: zodResolver(newProjectSchema) });

  const onSubmit = handleSubmit(async (values) => {
    const result = await createProject(values);
    if (result?.error) setError("root", { message: result.error });
  });

  return (
    <div className="mx-auto grid w-full max-w-xl gap-4">
      <Link
        href="/projects"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="size-4" aria-hidden />
        Projects
      </Link>
      <Card>
        <CardHeader>
          <CardTitle>New project</CardTitle>
          <CardDescription>Create one project for each client.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4" noValidate>
            <FormField id="clientName" label="Client name" error={errors.clientName?.message}>
              <Input id="clientName" autoComplete="off" {...register("clientName")} />
            </FormField>

            <FormField
              id="clientEmail"
              label="Client email"
              error={errors.clientEmail?.message}
              hint="This client's email and calling workspaces are opened with it. It cannot be changed once execution has started."
            >
              <Input id="clientEmail" type="email" autoComplete="off" {...register("clientEmail")} />
            </FormField>

            <FormField
              id="clientPassword"
              label="Client password"
              error={errors.clientPassword?.message}
              hint="Used to create the client's workspace in the calling tool. At least 6 characters. Kept on the server and never shown again."
            >
              <Input id="clientPassword" type="password" autoComplete="new-password" {...register("clientPassword")} />
            </FormField>

            <FormField
              id="description"
              label="Short description (optional)"
              error={errors.description?.message}
            >
              <Textarea id="description" rows={3} {...register("description")} />
            </FormField>

            {errors.root && (
              <p role="alert" className="text-sm text-destructive">
                {errors.root.message}
              </p>
            )}
            <div className="flex gap-2">
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Creating…" : "Create project"}
              </Button>
              <Link href="/projects" className={buttonVariants({ variant: "ghost" })}>
                Cancel
              </Link>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
