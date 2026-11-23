"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { CheckCircle2, Loader2, Phone, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { callerSchema, type CallerInput, type CallerView } from "@/lib/brain/caller";
import { cn } from "@/lib/utils";
import { saveCallerAccount } from "@/app/(app)/projects/[id]/execution/actions";

const CHOICES = [
  {
    mode: "own" as const,
    title: "Give this customer its own caller line",
    note: "Its own calling account and its own phone number that calls are made from.",
  },
  {
    mode: "existing" as const,
    title: "Use an existing calling account",
    note: "Calls go out from another project's account and number. Quick, for a trial.",
  },
];

/** Which calling account and caller line this customer's AI calls go out from. */
export function CallerCard({ projectId, view }: { projectId: string; view: CallerView }) {
  const router = useRouter();
  const [editing, setEditing] = useState(!view.ready);
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [value, setValue] = useState<CallerInput>({
    mode: view.mode === "existing" ? "existing" : "own",
    authId: view.authId,
    authToken: "",
    did: view.did,
    sourceProjectId: view.sourceProjectId || view.options[0]?.projectId || "",
    tokenSaved: view.tokenSaved,
  });
  const set = (change: Partial<CallerInput>) => {
    setErrors({});
    setValue((current) => ({ ...current, ...change }));
  };

  const save = async () => {
    const parsed = callerSchema.safeParse(value);
    if (!parsed.success) {
      setErrors(Object.fromEntries(parsed.error.issues.map((issue) => [String(issue.path[0]), issue.message])));
      return;
    }
    setBusy(true);
    try {
      const result = await saveCallerAccount({ projectId, caller: value });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(result.message);
      setValue((current) => ({ ...current, authToken: "", tokenSaved: current.tokenSaved || !!current.authToken }));
      setEditing(false);
      router.refresh();
    } catch {
      toast.error("That could not be saved. Check your connection and try again.");
    } finally {
      setBusy(false);
    }
  };

  const source = view.options.find((option) => option.projectId === view.sourceProjectId);

  return (
    <section aria-label="Calling account" className="grid gap-4 rounded-xl border p-5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Phone className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <p className="font-semibold">Calling account</p>
          {view.mode === "own" && view.ready ? (
            <p className="flex flex-wrap items-center gap-x-2 text-sm">
              <CheckCircle2 className="size-4 text-emerald-700" aria-hidden />
              <span className="font-medium tabular-nums">{view.did}</span>
              <span className="text-muted-foreground">· this customer&apos;s own caller line</span>
            </p>
          ) : view.mode === "existing" ? (
            <p className="flex flex-wrap items-center gap-x-2 text-sm">
              <CheckCircle2 className="size-4 text-emerald-700" aria-hidden />
              <span className="font-medium">{source ? `${source.name}'s calling account` : "An existing calling account"}</span>
              <span className="text-muted-foreground">· {view.existingReference}</span>
            </p>
          ) : (
            <p className="flex flex-wrap items-center gap-x-2 text-sm text-amber-900">
              <TriangleAlert className="size-4" aria-hidden />
              Calls cannot go out yet: this customer has no phone number to call from.
            </p>
          )}
        </div>
        {!editing && (
          <Button variant="outline" onClick={() => setEditing(true)}>
            Change
          </Button>
        )}
      </div>

      {editing && (
        <div className="grid gap-4 border-t pt-4">
          <div role="radiogroup" aria-label="Calling account" className="grid gap-3 md:grid-cols-2">
            {CHOICES.map((choice) => {
              const disabled = choice.mode === "existing" && view.options.length === 0;
              return (
                <button
                  key={choice.mode}
                  type="button"
                  role="radio"
                  aria-checked={value.mode === choice.mode}
                  disabled={busy || disabled}
                  onClick={() => set({ mode: choice.mode })}
                  className={cn(
                    "grid gap-1 rounded-lg border p-3 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
                    value.mode === choice.mode ? "border-primary bg-primary/5" : "hover:bg-muted"
                  )}
                >
                  <span className="text-sm font-semibold">{choice.title}</span>
                  <span className="text-sm text-muted-foreground">
                    {disabled ? "No other project has a calling account yet." : choice.note}
                  </span>
                </button>
              );
            })}
          </div>

          {value.mode === "own" ? (
            <div className="grid gap-4 md:grid-cols-3">
              <FormField id="caller-did" label="Caller number" error={errors.did} hint="With country code, e.g. +918065382389.">
                <Input id="caller-did" type="tel" autoComplete="off" value={value.did} disabled={busy} onChange={(e) => set({ did: e.target.value })} />
              </FormField>
              <FormField id="caller-auth-id" label="Vobiz account ID" error={errors.authId} hint="Starts with MA_.">
                <Input id="caller-auth-id" autoComplete="off" value={value.authId} disabled={busy} onChange={(e) => set({ authId: e.target.value })} />
              </FormField>
              <FormField
                id="caller-auth-token"
                label="Vobiz auth token"
                error={errors.authToken}
                hint={view.tokenSaved ? "Saved. Leave empty to keep it." : "Kept on the server and never shown again."}
              >
                <Input
                  id="caller-auth-token"
                  type="password"
                  autoComplete="new-password"
                  placeholder={view.tokenSaved ? "••••••••" : undefined}
                  value={value.authToken}
                  disabled={busy}
                  onChange={(e) => set({ authToken: e.target.value })}
                />
              </FormField>
            </div>
          ) : (
            <FormField
              id="caller-source"
              label="Use the calling account of"
              error={errors.sourceProjectId}
              hint="Each campaign replaces that account's saved company profile, so avoid this while its own call campaign is running."
            >
              <select
                id="caller-source"
                value={value.sourceProjectId}
                disabled={busy}
                onChange={(e) => set({ sourceProjectId: e.target.value })}
                className="h-9 w-full rounded-lg border border-input bg-transparent px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/50 sm:max-w-sm"
              >
                {view.options.map((option) => (
                  <option key={option.projectId} value={option.projectId}>
                    {option.name}
                  </option>
                ))}
              </select>
            </FormField>
          )}

          <div className="flex gap-2">
            <Button disabled={busy} onClick={() => void save()}>
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              Save
            </Button>
            {view.ready && (
              <Button variant="ghost" disabled={busy} onClick={() => setEditing(false)}>
                Cancel
              </Button>
            )}
            <Link href={`/projects/${projectId}/execution/accounts`} className="ml-auto self-center text-xs text-muted-foreground hover:underline">
              Enter an existing account by hand
            </Link>
          </div>
        </div>
      )}
    </section>
  );
}
