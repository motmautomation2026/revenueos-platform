import type { Metadata } from "next";
import { KeyRound } from "lucide-react";
import { AccountsForm } from "@/components/brain/accounts-form";
import { StepPlaceholder } from "@/components/step-placeholder";
import { requireProject } from "@/lib/auth";
import { loadAccounts } from "@/lib/brain/account-store";
import { loadSetupGaps } from "@/lib/brain/setup";

export const metadata: Metadata = { title: "Tool accounts" };

export default async function AccountsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { project } = await requireProject(id);
  const [accounts, gaps] = await Promise.all([loadAccounts(project.id), loadSetupGaps(project.id)]);
  // A customer whose sender details are complete needs no existing email account here.
  const view = {
    ...accounts.view,
    missing: accounts.view.missing.filter((label) => (label.startsWith("Email:") ? gaps.email : gaps.calls)),
  };

  if (!accounts.available) {
    return (
      <StepPlaceholder icon={KeyRound} title="Tool accounts are not set up yet">
        The database update that adds them has not been applied: supabase/migrations/0004_brain_accounts.sql.
      </StepPlaceholder>
    );
  }

  return (
    <div className="grid gap-6">
      <header className="grid gap-1">
        <h2 className="text-xl font-semibold tracking-tight">Existing accounts, entered by hand</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Rarely needed. Use this only to type in the details of an account that already exists in the email tool or
          Quickhowl. For everything else, use the Sender account card on the Email page and the Calling account card
          on the Calls page.
        </p>
      </header>
      <AccountsForm projectId={project.id} view={view} />
    </div>
  );
}
