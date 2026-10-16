"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Download, Eye, EyeOff, Loader2, Lock, Printer, RefreshCw } from "lucide-react";
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
import { cn } from "@/lib/utils";
import { PlanParts } from "./plan-document";

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
  const [activeId, setActiveId] = useState(doc.parts[0]?.id ?? "");
  const [confirmRerun, setConfirmRerun] = useState(false);
  const [starting, setStarting] = useState(false);

  const parts = doc.parts.filter((part) => showInternal || !part.internal);
  const active = parts.find((part) => part.id === activeId) ?? parts[0];
  const versionItems = versions.map((v) => ({ value: String(v.version), label: v.label }));

  const toggleInternal = () => {
    const next = !showInternal;
    setShowInternal(next);
    const internal = doc.parts.find((part) => part.internal);
    // Turning it on takes you there; turning it off while on it returns to the first part.
    if (next && internal) setActiveId(internal.id);
    if (!next && active?.internal) setActiveId(doc.parts[0]?.id ?? "");
  };

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
      {/* Title, what this version is, and what you can do with it. */}
      <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4 print:hidden">
        <div className="min-w-0 flex-1 basis-80">
          <h2 className="text-2xl leading-tight font-semibold tracking-tight">GTM {doc.customerName}</h2>
          <p className="mt-1 text-xs text-muted-foreground">{footer}</p>
        </div>

        <div className="grid justify-items-end gap-2">
          <div className="flex flex-wrap justify-end gap-2">
            <a
              href={`/api/plans/${planId}/html${showInternal ? "?internal=1" : ""}`}
              className={buttonVariants({ size: "lg" })}
            >
              <Download aria-hidden />
              Download HTML
            </a>
            <Button variant="outline" size="lg" onClick={() => window.print()}>
              <Printer aria-hidden />
              Download PDF
            </Button>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            {versions.length > 1 && (
              <Select
                items={versionItems}
                value={String(version)}
                onValueChange={(value) => router.push(`/projects/${projectId}/plan?v=${value}`)}
              >
                <SelectTrigger className="w-44" aria-label="Plan version">
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
            <Button variant="ghost" onClick={toggleInternal} aria-pressed={showInternal}>
              {showInternal ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
              {showInternal ? "Hide internal notes" : "Show internal notes"}
            </Button>
            <Button variant="ghost" disabled={starting} onClick={() => setConfirmRerun(true)}>
              {starting ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
              Re-run plan
            </Button>
          </div>
        </div>
      </header>

      {/* One part at a time, so the page is a third as long and has a single navigation. */}
      <div className="sticky top-14 z-20 -mx-4 border-b bg-background/95 px-4 py-2 backdrop-blur sm:-mx-6 sm:px-6 xl:-mx-8 xl:px-8 print:hidden">
        <div role="tablist" aria-label="Parts of the plan" className="flex gap-1 overflow-x-auto">
          {parts.map((part) => {
            const selected = part.id === active?.id;
            return (
              <button
                key={part.id}
                type="button"
                role="tab"
                aria-selected={selected}
                onClick={() => {
                  setActiveId(part.id);
                  window.scrollTo({ top: 0 });
                }}
                className={cn(
                  "flex items-center gap-1.5 rounded-lg px-3.5 py-2 text-sm font-semibold whitespace-nowrap outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                  selected
                    ? part.internal
                      ? "bg-amber-500 text-white"
                      : "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                {part.internal && <Lock className="size-3.5" aria-hidden />}
                {part.title}
              </button>
            );
          })}
        </div>
      </div>

      <div className="print:hidden">{active && <PlanParts parts={[active]} />}</div>

      {/* The PDF is the whole document, whichever part is on screen. */}
      <article className="hidden gap-8 print:grid">
        <header className="border-b-2 border-foreground pb-4">
          <h1 className="text-3xl font-semibold tracking-tight">GTM {doc.customerName}</h1>
        </header>
        <PlanParts parts={parts} mode="print" />
        <footer className="border-t pt-3 text-xs text-muted-foreground">{footer}</footer>
      </article>

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
