"use client";

import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { profileSchema, type ProfileInput } from "@/lib/validation";
import { updateProfile } from "./actions";

export function ProfileForm({ fullName, email }: { fullName: string; email: string }) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ProfileInput>({
    resolver: zodResolver(profileSchema),
    defaultValues: { fullName },
  });

  const onSubmit = handleSubmit(async (values) => {
    const result = await updateProfile(values);
    if (result.ok) toast.success("Name saved.");
    else toast.error(result.error);
  });

  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      <FormField id="email" label="Email">
        <Input id="email" value={email} readOnly disabled />
      </FormField>
      <FormField id="fullName" label="Full name" error={errors.fullName?.message}>
        <Input id="fullName" autoComplete="name" {...register("fullName")} />
      </FormField>
      <Button type="submit" disabled={isSubmitting} className="justify-self-start">
        {isSubmitting ? "Saving…" : "Save name"}
      </Button>
    </form>
  );
}
