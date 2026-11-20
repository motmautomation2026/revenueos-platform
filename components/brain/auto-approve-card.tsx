"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Zap } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { autoApproveSchema, type AutoApproveSettings } from "@/lib/brain/auto-approve";
import { saveAutoApprove } from "@/app/(app)/projects/[id]/execution/actions";

/**
 * Timed auto-approval: when on, the Brain answers routine gates (sample emails, start calling, a person's
 * task) on its own after the chosen number of seconds, so the run does not stop and wait. Off = every gate
 * waits for a person. Exception decisions (a failed task, a missed target) always wait, whatever this says.
 */
export function AutoApproveCard({ projectId, saved }: { projectId: string; saved: AutoApproveSettings }) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(saved);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const found = autoApproveSchema.safeParse(value).error?.issues[0]?.message ?? null;
    setProblem(found);
    if (found) return;
    setBusy(true);
    try {
      const result = await saveAutoApprove({ projectId, settings: value });
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

  const summary = saved.enabled ? `On · gates proceed after ${saved.seconds}s` : "Off · every gate waits for you";

  return (
    <section aria-label="Auto-approval" className="grid gap-3 rounded-xl border px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <Zap className={saved.enabled ? "size-5 shrink-0 text-amber-600" : "size-5 shrink-0 text-muted-foreground"} aria-hidden />
        <p className="min-w-0 flex-1 text-sm">
          <span className="font-semibold">Auto-approval</span>
          <span className="text-muted-foreground"> · {summary}</span>
        </p>
        {!editing && (
          <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
            Change
          </Button>
        )}
      </div>

      {editing && (
        <div className="grid gap-3 border-t pt-3">
          <p className="text-sm text-muted-foreground">
            When on, routine gates — sample emails, start calling, and a person&apos;s task — are answered automatically after
            the wait, so the run keeps moving. A person&apos;s task is skipped (not marked done). A failed task or a missed
            target always waits for you.
          </p>
          <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
            <div role="radiogroup" aria-label="Auto-approval" className="inline-flex rounded-lg border p-0.5">
              {[
                { on: false, label: "Off" },
                { on: true, label: "On" },
              ].map((choice) => (
                <button
                  key={choice.label}
                  type="button"
                  role="radio"
                  aria-checked={value.enabled === choice.on}
                  disabled={busy}
                  onClick={() => {
                    setProblem(null);
                    setValue((current) => ({ ...current, enabled: choice.on }));
                  }}
                  className={
                    "rounded-md px-3 py-1.5 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60 " +
                    (value.enabled === choice.on
                      ? choice.on
                        ? "bg-amber-500 text-white"
                        : "bg-primary text-primary-foreground"
                      : "text-muted-foreground hover:bg-muted")
                  }
                >
                  {choice.label}
                </button>
              ))}
            </div>

            {value.enabled && (
              <label className="grid gap-1 text-sm font-medium">
                Wait (seconds)
                <Input
                  type="number"
                  min={1}
                  value={value.seconds}
                  disabled={busy}
                  onChange={(e) => {
                    setProblem(null);
                    setValue((current) => ({ ...current, seconds: Number(e.target.value) }));
                  }}
                  className="w-28"
                />
              </label>
            )}
          </div>
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
