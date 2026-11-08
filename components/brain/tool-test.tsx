"use client";

import { useState } from "react";
import { CheckCircle2, Loader2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { TestResult } from "@/lib/tools/test-tools";
import { cn } from "@/lib/utils";

type Props = {
  title: string;
  hint: string;
  inputLabel: string;
  placeholder: string;
  buttonLabel: string;
  inputType?: "tel" | "email";
  defaultTarget?: string;
  run: (target: string) => Promise<TestResult>;
};

/**
 * A compact "set up, then verify here" test row for a tool-setup card. The person enters their own
 * number or email and runs a direct test (no Brain run, no task, no approval); the outcome shows inline.
 * This keeps setup and testing in one place, instead of a separate testing page.
 */
export function ToolTest({
  title,
  hint,
  inputLabel,
  placeholder,
  buttonLabel,
  inputType = "tel",
  defaultTarget = "",
  run,
}: Props) {
  const [target, setTarget] = useState(defaultTarget);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);

  const go = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await run(target.trim()));
    } catch {
      setResult({ ok: false, detail: "Something went wrong. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid gap-3 border-t pt-4">
      <div className="grid gap-0.5">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Input
          type={inputType}
          inputMode={inputType === "tel" ? "tel" : "email"}
          autoComplete="off"
          placeholder={placeholder}
          value={target}
          disabled={busy}
          onChange={(e) => setTarget(e.target.value)}
          className="sm:w-72"
          aria-label={inputLabel}
        />
        <Button type="button" variant="outline" disabled={busy || !target.trim()} onClick={() => void go()}>
          {busy && <Loader2 className="animate-spin" aria-hidden />}
          {buttonLabel}
        </Button>
      </div>
      {result && (
        <p
          role="status"
          className={cn(
            "flex items-start gap-2 text-sm",
            result.ok ? "text-emerald-800" : result.pending ? "text-muted-foreground" : "text-destructive",
          )}
        >
          {result.ok ? (
            <CheckCircle2 className="mt-0.5 size-4 shrink-0" aria-hidden />
          ) : result.pending ? null : (
            <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          )}
          <span>{result.detail}</span>
        </p>
      )}
    </div>
  );
}
