"use client";

import Link from "next/link";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/form-field";
import { ENGAGEMENT_TYPES } from "@/lib/projects";
import { newProjectSchema, type NewProjectInput } from "@/lib/validation";
import { createProject } from "../actions";

export default function NewProjectPage() {
  const {
    register,
    control,
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
          <CardDescription>Create one project for each customer.</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="grid gap-4" noValidate>
            <FormField id="customerName" label="Customer name" error={errors.customerName?.message}>
              <Input id="customerName" autoComplete="off" {...register("customerName")} />
            </FormField>

            <FormField
              id="engagementType"
              label="Engagement type"
              error={errors.engagementType?.message}
            >
              <Controller
                control={control}
                name="engagementType"
                render={({ field }) => (
                  <Select value={field.value ?? null} onValueChange={field.onChange}>
                    <SelectTrigger
                      id="engagementType"
                      className="w-full"
                      aria-invalid={!!errors.engagementType}
                      onBlur={field.onBlur}
                    >
                      <SelectValue placeholder="Choose one" />
                    </SelectTrigger>
                    <SelectContent>
                      {ENGAGEMENT_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {type}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                )}
              />
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
