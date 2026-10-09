"use client";

import { useState } from "react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { MailCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { signupSchema, type SignupInput } from "@/lib/validation";
import { signUp } from "../actions";

export default function SignupPage() {
  const [sentMessage, setSentMessage] = useState<string | null>(null);
  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<SignupInput>({ resolver: zodResolver(signupSchema) });

  const onSubmit = handleSubmit(async (values) => {
    const result = await signUp(values);
    if (!result) return;
    if (result.ok) setSentMessage(result.message ?? "Check your email to confirm your account.");
    else setError("root", { message: result.error });
  });

  if (sentMessage) {
    return (
      <Card>
        <CardHeader>
          <MailCheck className="size-6 text-muted-foreground" aria-hidden />
          <CardTitle>Check your email</CardTitle>
          <CardDescription>{sentMessage}</CardDescription>
        </CardHeader>
        <CardContent>
          <Link href="/login" className="text-sm underline-offset-4 hover:underline">
            Back to log in
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Create an account</CardTitle>
        <CardDescription>You will confirm your email before logging in.</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="grid gap-4" noValidate>
          <FormField id="fullName" label="Full name" error={errors.fullName?.message}>
            <Input id="fullName" autoComplete="name" {...register("fullName")} />
          </FormField>
          <FormField id="email" label="Email" error={errors.email?.message}>
            <Input id="email" type="email" autoComplete="email" {...register("email")} />
          </FormField>
          <FormField
            id="password"
            label="Password"
            error={errors.password?.message}
            hint="At least 8 characters."
          >
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              {...register("password")}
            />
          </FormField>
          {errors.root && (
            <p role="alert" className="text-sm text-destructive">
              {errors.root.message}
            </p>
          )}
          <Button type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Creating account…" : "Sign up"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="text-foreground underline-offset-4 hover:underline">
              Log in
            </Link>
          </p>
        </form>
      </CardContent>
    </Card>
  );
}
