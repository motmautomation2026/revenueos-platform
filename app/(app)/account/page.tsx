import type { Metadata } from "next";
import { LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NewPasswordForm } from "@/components/new-password-form";
import { requireUser } from "@/lib/auth";
import { TABLES } from "@/lib/supabase/tables";
import { COMPANY_FIELDS, type CompanyInput } from "@/lib/validation";
import { signOut } from "@/app/(auth)/actions";
import { CompanyForm } from "./company-form";
import { ProfileForm } from "./profile-form";

export const metadata: Metadata = { title: "Profile" };

export default async function AccountPage() {
  const { supabase, user } = await requireUser();
  const { data: profile } = await supabase
    .from(TABLES.profiles)
    .select("full_name")
    .eq("id", user.id)
    .maybeSingle<{ full_name: string | null }>();

  const fullName =
    profile?.full_name ?? (user.user_metadata?.full_name as string | undefined) ?? "";

  // Saved company details, with any missing or malformed field shown as blank.
  const saved = (user.user_metadata?.company ?? {}) as Record<string, unknown>;
  const company = Object.fromEntries(
    COMPANY_FIELDS.map((field) => [field, typeof saved[field] === "string" ? saved[field] : ""])
  ) as CompanyInput;

  return (
    <div className="mx-auto grid w-full max-w-3xl gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Profile</h1>
        <p className="text-sm text-muted-foreground">Your details and your company&apos;s.</p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Your details</CardTitle>
        </CardHeader>
        <CardContent>
          <ProfileForm fullName={fullName} email={user.email ?? ""} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Company details</CardTitle>
          <CardDescription>All fields are optional.</CardDescription>
        </CardHeader>
        <CardContent>
          <CompanyForm values={company} />
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
          <CardTitle>Log out</CardTitle>
          <CardDescription>End your session on this device.</CardDescription>
        </CardHeader>
        <CardContent>
          <form action={signOut}>
            <Button type="submit" variant="outline">
              <LogOut aria-hidden />
              Log out
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
