import type { Metadata } from "next";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NewPasswordForm } from "@/components/new-password-form";
import { requireUser } from "@/lib/auth";
import { signOut } from "@/app/(auth)/actions";
import { ProfileForm } from "./profile-form";
import { TABLES } from "@/lib/supabase/tables";

export const metadata: Metadata = { title: "Account" };

export default async function AccountPage() {
  const { supabase, user } = await requireUser();
  const { data: profile } = await supabase
    .from(TABLES.profiles)
    .select("full_name")
    .eq("id", user.id)
    .maybeSingle<{ full_name: string | null }>();

  const fullName =
    profile?.full_name ?? (user.user_metadata?.full_name as string | undefined) ?? "";

  return (
    <div className="mx-auto grid w-full max-w-xl gap-6">
      <h1 className="text-2xl font-semibold tracking-tight">Account</h1>

      <Card>
        <CardHeader>
          <CardTitle>Profile</CardTitle>
        </CardHeader>
        <CardContent>
          <ProfileForm fullName={fullName} email={user.email ?? ""} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Change password</CardTitle>
        </CardHeader>
        <CardContent>
          <NewPasswordForm />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Sign out</CardTitle>
          <CardDescription>End your session on this device.</CardDescription>
        </CardHeader>
        <CardContent>
          <form action={signOut}>
            <Button type="submit" variant="outline">
              <LogOut aria-hidden />
              Sign out
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
