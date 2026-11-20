"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CheckCircle2, CircleDashed, Loader2, Mail, Phone, RefreshCw, RotateCcw, TriangleAlert, type LucideIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { AccountStatus, AccountStatusValue, ToolAccountStatus } from "@/lib/brain/account-status";
import { refreshToolAccountStatus } from "@/app/(app)/projects/[id]/execution/actions";
import { cn } from "@/lib/utils";

const BADGE: Record<AccountStatusValue, { label: string; tone: string; icon: LucideIcon }> = {
  created: { label: "Account created", tone: "bg-emerald-500/15 text-emerald-800", icon: CheckCircle2 },
  reused: { label: "Existing account used", tone: "bg-muted text-muted-foreground", icon: RotateCcw },
  error: { label: "Error", tone: "bg-destructive/10 text-destructive", icon: TriangleAlert },
  unknown: { label: "Not created yet", tone: "bg-amber-500/15 text-amber-900", icon: CircleDashed },
};

const dateTime = new Intl.DateTimeFormat("en-GB", { dateStyle: "medium", timeStyle: "short" });

function Row({ icon: Icon, tool, status }: { icon: LucideIcon; tool: string; status: ToolAccountStatus }) {
  const badge = BADGE[status.status];
  const BadgeIcon = badge.icon;
  return (
    <div className="flex flex-wrap items-start gap-3 py-3">
      <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
        <Icon className="size-4" aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="font-medium">{tool}</p>
          <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", badge.tone)}>
            <BadgeIcon className="size-3.5" aria-hidden />
            {badge.label}
          </span>
        </div>
        {status.account && <p className="truncate text-sm text-muted-foreground">{status.account}</p>}
        {/* The detail only adds something for an error, or when there is no account line to carry it —
            otherwise the badge ("Existing account used") + the account email already say it. */}
        {status.detail && (status.status === "error" || !status.account) && (
          <p className="text-sm text-muted-foreground">{status.detail}</p>
        )}
        {status.status === "unknown" && !status.detail && (
          <p className="text-sm text-muted-foreground">No account activity yet — it is created on this customer&apos;s first campaign.</p>
        )}
        {status.checkedAt && (
          <p className="mt-0.5 text-xs text-muted-foreground">Checked {dateTime.format(new Date(status.checkedAt))}</p>
        )}
      </div>
    </div>
  );
}

const HEADING = {
  email: { title: "Account status", note: "Whether this customer's email account has been created on the tool." },
  calls: { title: "Account status", note: "Whether this customer's calling account has been created on the tool." },
  both: { title: "Tool accounts", note: "Whether this customer's email and calling accounts have been created on the tools." },
} as const;

/**
 * Shows whether the tool account(s) were created / reused / failed, for acknowledgement.
 * `only` limits it to one tool, for the Email and Calls pages; omitted shows both (the dashboard).
 */
export function AccountStatusCard({
  projectId,
  status,
  only,
}: {
  projectId: string;
  status: AccountStatus;
  only?: "email" | "calls";
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const heading = HEADING[only ?? "both"];

  const recheck = async () => {
    setBusy(true);
    try {
      const result = await refreshToolAccountStatus({ projectId });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success("Account status updated.");
      router.refresh();
    } catch {
      toast.error("Could not refresh the status. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-label={heading.title} className="grid gap-1 rounded-xl border p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="font-semibold">{heading.title}</p>
          <p className="text-sm text-muted-foreground">{heading.note}</p>
        </div>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void recheck()}>
          {busy ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
          Recheck
        </Button>
      </div>
      <div className="divide-y">
        {only !== "calls" && <Row icon={Mail} tool="Email account" status={status.email} />}
        {only !== "email" && <Row icon={Phone} tool="Calling account" status={status.calls} />}
      </div>
    </section>
  );
}
