"use client";

import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { newPasswordSchema, type NewPasswordInput } from "@/lib/validation";
import { setNewPassword } from "@/app/(auth)/actions";

/** Used on /reset-password (after the email link) and on /account. */
export function NewPasswordForm({ redirectTo }: { redirectTo?: string }) {
  const router = useRouter();
  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<NewPasswordInput>({ resolver: zodResolver(newPasswordSchema) });

  const onSubmit = handleSubmit(async (values) => {
    const result = await setNewPassword(values);
    if (!result.ok) {
      setError("root", { message: result.error });
      return;
    }
    toast.success(result.message);
    reset();
    if (redirectTo) router.push(redirectTo);
  });

  return (
    <form onSubmit={onSubmit} className="grid gap-4" noValidate>
      <FormField
        id="password"
        label="New password"
        error={errors.password?.message}
        hint="At least 8 characters."
      >
        <Input id="password" type="password" autoComplete="new-password" {...register("password")} />
      </FormField>
      <FormField
        id="confirmPassword"
        label="Confirm new password"
        error={errors.confirmPassword?.message}
      >
        <Input
          id="confirmPassword"
          type="password"
          autoComplete="new-password"
          {...register("confirmPassword")}
        />
      </FormField>
      {errors.root && (
        <p role="alert" className="text-sm text-destructive">
          {errors.root.message}
        </p>
      )}
      <Button type="submit" disabled={isSubmitting} className="justify-self-start">
        {isSubmitting ? "Saving…" : "Update password"}
      </Button>
    </form>
  );
}
