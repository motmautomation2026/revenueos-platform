"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { FlaskConical, Loader2, Rocket } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { RunModeFields } from "@/components/brain/run-mode-fields";
import { stagingSchema, type StagingSettings } from "@/lib/brain/staging";
import { saveRunMode } from "@/app/(app)/projects/[id]/execution/actions";

/**
 * The mode of the run that is going on now: Production, or Staging with its two limits. It can
 * be changed while the run is going; the change applies to the steps that have not started yet.
 */
export function RunModeCard({ projectId, saved }: { projectId: string; saved: StagingSettings }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(saved);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const staging = saved.mode === "staging";

  const save = async () => {
    const found = stagingSchema.safeParse(value).error?.issues[0]?.message ?? null;
    setProblem(found);
    if (found) return;
    setBusy(true);
    try {
      const result = await saveRunMode({ projectId, run: value });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(result.message);
      setEditing(false);
      router.refresh();
    } catch {
      toast.error("That could not be saved. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="Run mode"
      className={
        staging
          ? "grid gap-3 rounded-xl border border-amber-500/50 bg-amber-500/5 px-4 py-3"
          : "grid gap-3 rounded-xl border px-4 py-3"
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        {staging ? (
          <FlaskConical className="size-5 shrink-0 text-amber-700" aria-hidden />
        ) : (
          <Rocket className="size-5 shrink-0 text-primary" aria-hidden />
        )}
        <p className="min-w-0 flex-1 text-sm">
          <span className="font-semibold">{staging ? "Staging" : "Production"}</span>
          <span className="text-muted-foreground">
            {staging
              ? ` · up to ${saved.companies} ${saved.companies === 1 ? "company" : "companies"} and ${saved.people} ${saved.people === 1 ? "profile" : "profiles"}`
              : " · the plan's full targets"}
          </span>
        </p>
        {!editing && (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Change
          </Button>
        )}
      </div>

      {editing && (
        <div className="grid gap-3 border-t pt-3">
          <RunModeFields
            value={value}
            onChange={(change) => {
              setProblem(null);
              setValue((current) => ({ ...current, ...change }));
            }}
            disabled={busy}
          />
          {problem && (
            <p role="alert" className="text-sm text-destructive">
              {problem}
            </p>
          )}
          <div className="flex gap-2">
            <Button size="sm" disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              Save
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={busy}
              onClick={() => {
                setValue(saved);
                setProblem(null);
                setEditing(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
