import type { Metadata } from "next";
import Link from "next/link";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NewPasswordForm } from "@/components/new-password-form";
import { createClient } from "@/lib/supabase/server";

export const metadata: Metadata = { title: "Set a new password" };

export default async function ResetPasswordPage() {
  const supabase = await createClient();
  const {
    data: { user },
  } = await supabase.auth.getUser();

  return (
    <Card>
      <CardHeader>
        <CardTitle>Set a new password</CardTitle>
        <CardDescription>
          {user
            ? `For ${user.email}`
            : "This reset link is invalid or has expired. Request a new one from the log in page."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {user ? (
          <NewPasswordForm redirectTo="/projects" />
        ) : (
          <Link href="/login" className="text-sm underline-offset-4 hover:underline">
            Back to log in
          </Link>
        )}
      </CardContent>
    </Card>
  );
}
