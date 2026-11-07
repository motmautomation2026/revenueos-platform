"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { RequestResult } from "@/lib/brain/request-store";

/**
 * Level 2 re-run: stop the current run and re-plan execution from the edited GTM plan. Destructive, so
 * it confirms first. Shown only to the owner, only while a run exists.
 */
export function ReplanButton({
  projectId,
  replan,
}: {
  projectId: string;
  replan: (input: { projectId: string }) => Promise<RequestResult>;
}) {
  const router = useRouter();
  const [confirming, setConfirming] = useState(false);
  const [loading, setLoading] = useState(false);

  const run = async () => {
    setLoading(true);
    try {
      const result = await replan({ projectId });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(result.message);
      setConfirming(false);
      router.refresh();
    } catch {
      toast.error("Could not re-plan execution. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/5 p-4 text-sm">
      <p className="font-medium">Changed the GTM plan?</p>
      <p className="mt-0.5 text-muted-foreground">
        Re-run execution with the updated plan. This <strong>stops the current run</strong> and plans a fresh one from
        the edited plan — current progress is closed (kept as history), and email/calls start over (with approval).
      </p>
      {!confirming ? (
        <Button type="button" variant="outline" size="sm" className="mt-2" onClick={() => setConfirming(true)}>
          <RefreshCw className="size-4" aria-hidden /> Re-run execution with the updated plan
        </Button>
      ) : (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={run} disabled={loading}>
            {loading && <Loader2 className="size-4 animate-spin" aria-hidden />} Confirm — stop &amp; re-plan
          </Button>
          <Button type="button" variant="ghost" size="sm" onClick={() => setConfirming(false)} disabled={loading}>
            Cancel
          </Button>
        </div>
      )}
    </div>
  );
}
