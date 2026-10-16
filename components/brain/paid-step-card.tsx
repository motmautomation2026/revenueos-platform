"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { CircleX, Coins, Hand, Loader2 } from "lucide-react";
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
import { Button } from "@/components/ui/button";
import type { PaidStep } from "@/lib/brain/paid-steps";
import { startPaidStep } from "@/app/(app)/projects/[id]/execution/actions";

/**
 * The Brain stopped before a task that buys data. It never starts one on its own: a person
 * does, here.
 */
export function PaidStepCard({
  projectId,
  step,
  task,
}: {
  projectId: string;
  step: PaidStep;
  /** What the task is, from the month plan. */
  task: { agent: string; what: string; target: string } | null;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setConfirming(false);
    setBusy(true);
    try {
      const result = await startPaidStep({ projectId, stepId: step.id });
      if (result.ok) toast.success(result.message);
      else toast.error(result.error);
      router.refresh();
    } catch {
      toast.error("That could not be sent. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  const name = `${step.taskId}${task?.agent ? ` · ${task.agent}` : ""}`;

  if (step.status === "approved" || step.status === "running") {
    return (
      <section aria-label="Data being bought" className="flex flex-wrap items-center gap-4 rounded-xl border p-5">
        <Loader2 className="size-5 shrink-0 animate-spin text-primary" aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            {step.status === "running" ? `The Brain is working on ${name}` : `${name} is starting`}
          </p>
          <p className="text-sm text-muted-foreground">
            {step.approvedBy ? `Started by ${step.approvedBy}. ` : ""}Finding and checking data takes several minutes;
            results appear under Data research.
          </p>
        </div>
      </section>
    );
  }

  const failed = step.status === "failed";
  return (
    <section aria-label="A step that buys data" className="grid gap-3">
      <div className="flex items-center gap-3 rounded-xl bg-primary px-4 py-3 text-primary-foreground">
        <Hand className="size-5 shrink-0" aria-hidden />
        <div>
          <p className="text-xs font-semibold tracking-wide uppercase opacity-85">Your next step</p>
          <p className="font-semibold">Start {name}: this buys data</p>
        </div>
      </div>

      <div className="flex flex-wrap items-start gap-4 rounded-xl border border-amber-500/40 p-5">
        {failed ? (
          <CircleX className="mt-0.5 size-5 shrink-0 text-destructive" aria-hidden />
        ) : (
          <Coins className="mt-0.5 size-5 shrink-0 text-amber-700" aria-hidden />
        )}
        <div className="grid min-w-0 flex-1 gap-1 text-sm">
          {task?.what && <p className="leading-relaxed font-medium">{task.what}</p>}
          {task?.target && <p className="text-muted-foreground">Target: {task.target}</p>}
          {failed ? (
            <p className="leading-relaxed break-words text-destructive">
              The last try stopped: {step.error || "no reason was recorded."}
            </p>
          ) : (
            <p className="leading-relaxed text-muted-foreground">
              The Brain has done everything it can without spending. This task searches QuickICP, which uses paid
              credits, so it waits for a person to start it.
            </p>
          )}
        </div>
        <Button disabled={busy} onClick={() => setConfirming(true)}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          {failed ? "Try again" : `Start ${step.taskId}`}
        </Button>
      </div>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Start {name}?</AlertDialogTitle>
            <AlertDialogDescription>
              The Brain will search QuickICP for this task, which uses paid credits. It cannot be undone once the
              search has run. No email is sent and no call is made by this step.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Not now</AlertDialogCancel>
            <Button onClick={() => void start()}>Start</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </section>
  );
}
