"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Download, Eye, EyeOff, Loader2, Printer, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { PlanDocument } from "@/lib/plan/document";
import { PlanParts, sectionAnchor } from "./plan-document";

export function PlanReview({
  projectId,
  planId,
  version,
  versions,
  doc,
  footer,
}: {
  projectId: string;
  planId: string;
  version: number;
  /** Every version of this project's plan, newest first. */
  versions: { version: number; label: string }[];
  /** Includes the internal part; it is only shown when toggled on. */
  doc: PlanDocument;
  footer: string;
}) {
  const router = useRouter();
  const [showInternal, setShowInternal] = useState(false);
  const [confirmRerun, setConfirmRerun] = useState(false);
  const [starting, setStarting] = useState(false);

  const parts = doc.parts.filter((part) => showInternal || !part.internal);
  const versionItems = versions.map((v) => ({ value: String(v.version), label: v.label }));

  const rerun = async () => {
    setConfirmRerun(false);
    setStarting(true);
    try {
      const response = await fetch(`/api/projects/${projectId}/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "start" }),
      });
      if (!response.ok) {
        const body = (await response.json().catch(() => null)) as { error?: string } | null;
        toast.error(body?.error ?? "Could not start a new plan.");
        return;
      }
      router.push(`/projects/${projectId}/plan`);
      router.refresh();
    } catch {
      toast.error("Could not start a new plan.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="grid gap-6">
      <div className="flex flex-wrap items-center gap-2 print:hidden">
        {versions.length > 1 && (
          <Select
            items={versionItems}
            value={String(version)}
            onValueChange={(value) => router.push(`/projects/${projectId}/plan?v=${value}`)}
          >
            <SelectTrigger size="sm" className="w-44" aria-label="Plan version">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {versionItems.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        <a
          href={`/api/plans/${planId}/html${showInternal ? "?internal=1" : ""}`}
          className={buttonVariants({ variant: "outline", size: "sm" })}
        >
          <Download aria-hidden />
          Download HTML
        </a>
        <Button variant="outline" size="sm" onClick={() => window.print()}>
          <Printer aria-hidden />
          Download PDF
        </Button>
        <Button variant="outline" size="sm" onClick={() => setShowInternal((shown) => !shown)}>
          {showInternal ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
          {showInternal ? "Hide internal section" : "Show internal section"}
        </Button>
        <Button variant="outline" size="sm" disabled={starting} onClick={() => setConfirmRerun(true)}>
          {starting ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
          Re-run plan
        </Button>
      </div>

      <div className="grid items-start gap-8 lg:grid-cols-[14rem_minmax(0,1fr)] print:block">
        <nav
          aria-label="Table of contents"
          className="hidden max-h-[calc(100svh-5.5rem)] overflow-y-auto text-sm lg:sticky lg:top-18 lg:block print:hidden"
        >
          {parts.map((part) => (
            <div key={part.id} className="mb-4">
              <a href={`#${part.id}`} className="font-semibold hover:underline">
                {part.title}
              </a>
              <ul className="mt-1.5 grid gap-1 border-l pl-3">
                {part.sections.map((section) => (
                  <li key={section.id}>
                    <a
                      href={`#${sectionAnchor(part, section.id)}`}
                      className="text-muted-foreground hover:text-foreground"
                    >
                      {section.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>

        <article className="grid min-w-0 gap-8">
          <header className="border-b-2 border-foreground pb-4">
            <h1 className="text-2xl font-semibold tracking-tight sm:text-3xl">{doc.title}</h1>
            <p className="mt-1 text-muted-foreground">
              {[doc.customerName, doc.subtitle].filter(Boolean).join(" · ")}
            </p>
          </header>
          <PlanParts parts={parts} />
          <footer className="border-t pt-3 text-xs text-muted-foreground">{footer}</footer>
        </article>
      </div>

      <AlertDialog open={confirmRerun} onOpenChange={setConfirmRerun}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Write a new version of the plan?</AlertDialogTitle>
            <AlertDialogDescription>
              All three agents run again on the current documents and checklist answers. This takes
              several minutes. Version {version} stays available in the version list.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={rerun}>Re-run plan</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
