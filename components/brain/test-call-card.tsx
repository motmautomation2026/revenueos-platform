"use client";

import { useState } from "react";
import { CheckCircle2, ChevronDown, Loader2, PhoneCall, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormField } from "@/components/form-field";
import { normaliseDid } from "@/lib/brain/caller";
import { testCallerLine } from "@/app/(app)/projects/[id]/execution/actions";
import { cn } from "@/lib/utils";

/**
 * Test call to a contact the person types in — their own name and number, with no QuickICP lead and no
 * run. It dials once through this project's calling account; with "real agent" on it talks exactly like
 * the campaign would, so you can hear the agent on your own phone. Collapsed by default to keep the Calls
 * page tidy; a click opens the form.
 */
export function TestCallCard({ projectId }: { projectId: string }) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [useRealScript, setUseRealScript] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ ok: boolean; detail: string } | null>(null);

  const call = async () => {
    setError(null);
    setResult(null);
    const num = normaliseDid(phone);
    if (!num) {
      setError("Enter a valid Indian number with country code, e.g. +918065382389.");
      return;
    }
    setBusy(true);
    try {
      const res = await testCallerLine({ projectId, target: num, name: name.trim() || undefined, useRealScript });
      setResult(res);
    } catch {
      setResult({ ok: false, detail: "The call could not be started. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <details className="group rounded-xl border bg-background">
      <summary className="flex cursor-pointer list-none items-center gap-3 p-4 outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <PhoneCall className="size-4" aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block font-semibold">Test call</span>
          <span className="block text-sm text-muted-foreground">Dial a number you type in to check the line and hear the agent.</span>
        </span>
        <ChevronDown className="size-5 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden />
      </summary>

      <div className="grid gap-4 border-t p-5">
        <p className="text-sm text-muted-foreground">
          No lead data and no run — it dials once through this project&apos;s calling account. Use your own number so you
          can pick up.
        </p>

        <div className="grid gap-4 sm:grid-cols-2">
          <FormField id="test-call-name" label="Name" hint="The agent uses it to greet the person.">
            <Input
              id="test-call-name"
              autoComplete="off"
              placeholder="e.g. Rameshwar"
              value={name}
              disabled={busy}
              onChange={(e) => setName(e.target.value)}
            />
          </FormField>
          <FormField id="test-call-phone" label="Number to call" error={error ?? undefined} hint="With country code, e.g. +918065382389.">
            <Input
              id="test-call-phone"
              type="tel"
              autoComplete="off"
              placeholder="+9198XXXXXXXX"
              value={phone}
              disabled={busy}
              onChange={(e) => {
                setPhone(e.target.value);
                setError(null);
              }}
            />
          </FormField>
        </div>

        <label className="flex cursor-pointer items-start gap-2.5 rounded-lg border bg-muted/30 p-3 text-sm">
          <input
            type="checkbox"
            checked={useRealScript}
            disabled={busy}
            onChange={(e) => setUseRealScript(e.target.checked)}
            className="mt-0.5 size-4 shrink-0 rounded border-input"
          />
          <span>
            <span className="font-medium">Use this campaign&apos;s real agent script</span>
            <span className="block text-muted-foreground">Talks exactly like a real call. Falls back to a short line test if none is ready.</span>
          </span>
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <Button disabled={busy} onClick={() => void call()}>
            {busy ? <Loader2 className="animate-spin" aria-hidden /> : <PhoneCall aria-hidden />}
            Call now
          </Button>
          <span className="text-xs text-muted-foreground">It rings within a few seconds.</span>
        </div>

        {result && (
          <p
            className={cn(
              "flex items-start gap-2 rounded-lg border p-3 text-sm",
              result.ok
                ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-800"
                : "border-destructive/30 bg-destructive/5 text-destructive"
            )}
          >
            {result.ok ? (
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
            ) : (
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
            )}
            {result.detail}
          </p>
        )}
      </div>
    </details>
  );
}
