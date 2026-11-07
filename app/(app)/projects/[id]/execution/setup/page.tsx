import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, CheckCircle2, FlaskConical, Mail, Phone, TriangleAlert } from "lucide-react";
import { requireProject } from "@/lib/auth";
import { loadSetupGaps } from "@/lib/brain/setup";
import { cn } from "@/lib/utils";

export const metadata: Metadata = { title: "Set up your tools" };

/**
 * Phase 1 of the "set up & test your tools" feature (docs/tool-setup-and-test.md): the post-GTM gate that
 * shows, before execution sends anything, whether the email sender and the call line are set — and routes
 * to where each is configured. The test actions (send to your own inbox / call your own number) come in
 * Phase 2 (Brain side). Readiness comes from loadSetupGaps, so nothing is duplicated.
 */
export default async function SetupPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { project } = await requireProject(id);
  const gaps = await loadSetupGaps(project.id);
  const base = `/projects/${project.id}/execution`;

  const tools = [
    {
      key: "email",
      title: "Email sender",
      icon: Mail,
      ready: !gaps.email,
      fixHref: `${base}/email`,
      fixLabel: "Set the sender on the Email page",
      readyText: "A sending account is set — your emails have a sender.",
      gapText: "No sending account yet. Set who your emails come from before execution sends any.",
    },
    {
      key: "calls",
      title: "Call line",
      icon: Phone,
      ready: !gaps.calls,
      fixHref: `${base}/calls`,
      fixLabel: "Set the calling account on the Calls page",
      readyText: "A calling account and caller line are set — calls can dial.",
      gapText: "No calling account / caller line yet. Calls can't connect without the line they dial from.",
    },
  ];
  const allReady = gaps.known && tools.every((t) => t.ready);

  return (
    <div className="grid gap-6">
      <header className="grid gap-1">
        <h2 className="text-xl font-semibold tracking-tight">Set up your tools</h2>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Before execution sends anything, confirm who your emails come from and which line your calls dial from.
          {allReady ? " Everything is set — you're ready to run." : " Finish the items below first."}
        </p>
      </header>

      {!gaps.known ? (
        <p className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 text-sm text-muted-foreground">
          Setup status is unavailable — the tool-accounts database update has not been applied yet
          (supabase/migrations/0004_brain_accounts.sql).
        </p>
      ) : (
        <section aria-label="Tool setup" className="grid gap-3 sm:grid-cols-2">
          {tools.map((tool) => {
            const Icon = tool.icon;
            return (
              <div
                key={tool.key}
                className={cn(
                  "grid gap-2 rounded-xl border p-4",
                  tool.ready ? "border-emerald-500/30" : "border-amber-500/40 bg-amber-500/5"
                )}
              >
                <div className="flex items-center gap-2">
                  <Icon className="size-4 text-muted-foreground" aria-hidden />
                  <span className="font-medium">{tool.title}</span>
                  {tool.ready ? (
                    <CheckCircle2 className="ml-auto size-5 text-emerald-600" aria-hidden />
                  ) : (
                    <TriangleAlert className="ml-auto size-5 text-amber-600" aria-hidden />
                  )}
                </div>
                <p className="text-sm text-muted-foreground">{tool.ready ? tool.readyText : tool.gapText}</p>
                {!tool.ready && (
                  <Link
                    href={tool.fixHref}
                    className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
                  >
                    {tool.fixLabel} <ArrowRight className="size-4" aria-hidden />
                  </Link>
                )}
              </div>
            );
          })}
        </section>
      )}

      {/* Test (Phase 2) — described, not a dead button */}
      <section aria-label="Test your tools" className="grid gap-2 rounded-xl border border-dashed p-4">
        <div className="flex items-center gap-2">
          <FlaskConical className="size-4 text-muted-foreground" aria-hidden />
          <h3 className="text-sm font-semibold">Test your tools (coming soon)</h3>
        </div>
        <p className="max-w-3xl text-sm text-muted-foreground">
          Next, you&apos;ll send a test email to your own inbox and place a test call to your own number — so you can
          check deliverability and call quality (and that the caller line actually dials) before the real run.
        </p>
      </section>
    </div>
  );
}
