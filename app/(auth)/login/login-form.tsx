"use client";

import { useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import {
  forgotPasswordSchema,
  loginSchema,
  type ForgotPasswordInput,
  type LoginInput,
} from "@/lib/validation";
import { logIn, requestPasswordReset } from "../actions";

export function LoginForm({ linkError }: { linkError: boolean }) {
  const [mode, setMode] = useState<"login" | "forgot">("login");
  return mode === "login" ? (
    <PasswordLogin linkError={linkError} onForgot={() => setMode("forgot")} />
  ) : (
    <ForgotPassword onBack={() => setMode("login")} />
  );
}

function PasswordLogin({ linkError, onForgot }: { linkError: boolean; onForgot: () => void }) {
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<LoginInput>({ resolver: zodResolver(loginSchema) });

  const onSubmit = handleSubmit(async (values) => {
    const result = await logIn(values);
    if (result && !result.ok) setError("root", { message: result.error });
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Log in</CardTitle>
        <CardDescription>Use your RevenueOS account.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          {linkError && !errors.root && (
            <p role="alert" className="text-sm text-destructive">
              That link is invalid or has expired. Log in or request a new one.
            </p>
          )}
          <FormField id="email" label="Email" error={errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" {...register("email")} />
          </FormField>
          <FormField id="password" label="Password" error={errors.password?.message}>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              {...register("password")}
            />
          </FormField>
          {errors.root && (
            <p role="alert" className="text-sm text-destructive">
              {errors.root.message}
            </p>
          )}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Logging in…" : "Log in"}
          </Button>
          <div className="flex items-center justify-between text-sm">
            <button
              type="button"
              onClick={onForgot}
              className="text-muted-foreground underline-offset-4 hover:underline"
            >
              Forgot password?
            </button>
            <Link href="/signup" className="underline-offset-4 hover:underline">
              Create an account
            </Link>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function ForgotPassword({ onBack }: { onBack: () => void }) {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotPasswordInput>({ resolver: zodResolver(forgotPasswordSchema) });

  const onSubmit = handleSubmit(async (values) => {
    const result = await requestPasswordReset(values);
    if (result.ok) {
      toast.success(result.message);
      onBack();
    } else {
      toast.error(result.error);
    }
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Reset your password</CardTitle>
        <CardDescription>We will email you a link to set a new password.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          <FormField id="email" label="Email" error={errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" {...register("email")} />
          </FormField>
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Sending…" : "Send reset link"}
          </Button>
          <button
            type="button"
            onClick={onBack}
            className="text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            Back to log in
          </button>
        </form>
      </CardContent>
    </Card>
  );
}
