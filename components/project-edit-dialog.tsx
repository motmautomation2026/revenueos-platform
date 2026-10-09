"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Pencil } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { updateProject } from "@/app/(app)/projects/actions";

export function ProjectEditDialog({
  projectId,
  values,
}: {
  projectId: string;
  values: { customerName: string; engagementType: string; description: string };
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const {
    register,
    control,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<NewProjectInput>({ resolver: zodResolver(newProjectSchema) });

  const openDialog = () => {
    // Engagement types saved before the list changed are left for the user to re-pick.
    reset({ ...values, engagementType: values.engagementType as NewProjectInput["engagementType"] });
    setOpen(true);
  };

  const onSubmit = handleSubmit(async (input) => {
    const result = await updateProject(projectId, input);
    if (!result.ok) {
      setError("root", { message: result.error });
      return;
    }
    toast.success("Project updated.");
    setOpen(false);
    router.refresh();
  });

  return (
    <>
      <Button variant="outline" size="sm" onClick={openDialog}>
        <Pencil aria-hidden />
        Rename
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Edit project</DialogTitle>
            <DialogDescription>
              The customer name is used in the checklist and plan. Changes apply to anything
              generated from now on; documents already written keep the old name.
            </DialogDescription>
          </DialogHeader>

          <form onSubmit={onSubmit} className="grid gap-4" noValidate>
            <FormField id="edit-customerName" label="Customer name" error={errors.customerName?.message}>
              <Input id="edit-customerName" autoComplete="off" {...register("customerName")} />
            </FormField>

            <FormField
              id="edit-engagementType"
              label="Engagement type"
              error={errors.engagementType?.message}
            >
              <Controller
                control={control}
                name="engagementType"
                render={({ field }) => (
                  <Select value={field.value ?? null} onValueChange={field.onChange}>
                    <SelectTrigger
                      id="edit-engagementType"
                      className="w-full"
                      aria-invalid={!!errors.engagementType}
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
              id="edit-description"
              label="Short description (optional)"
              error={errors.description?.message}
            >
              <Textarea id="edit-description" rows={3} {...register("description")} />
            </FormField>

            {errors.root && (
              <p role="alert" className="text-sm text-destructive">
                {errors.root.message}
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={isSubmitting}>
                {isSubmitting ? "Saving…" : "Save changes"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </>
  );
}
