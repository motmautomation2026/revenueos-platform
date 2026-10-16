"use client";

import { Input } from "@/components/ui/input";
import { MAX_STAGING_COMPANIES, MAX_STAGING_PEOPLE, type StagingSettings } from "@/lib/brain/staging";
import { cn } from "@/lib/utils";

/** The Staging / Production switch and, in staging, the two limits: companies and profiles. */
export function RunModeFields({
  value,
  onChange,
  disabled = false,
}: {
  value: StagingSettings;
  onChange: (change: Partial<StagingSettings>) => void;
  disabled?: boolean;
}) {
  const staging = value.mode === "staging";
  return (
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <div role="radiogroup" aria-label="Run mode" className="inline-flex rounded-lg border p-0.5">
        {(["production", "staging"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            role="radio"
            aria-checked={value.mode === mode}
            disabled={disabled}
            onClick={() => onChange({ mode })}
            className={cn(
              "rounded-md px-3 py-1.5 text-sm font-medium outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60",
              value.mode === mode
                ? mode === "staging"
                  ? "bg-amber-500 text-white"
                  : "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted"
            )}
          >
            {mode === "staging" ? "Staging" : "Production"}
          </button>
        ))}
      </div>

      {staging ? (
        <>
          <label className="grid gap-1 text-sm font-medium">
            Companies
            <Input
              type="number"
              min={1}
              max={MAX_STAGING_COMPANIES}
              value={value.companies}
              disabled={disabled}
              onChange={(e) => onChange({ companies: Number(e.target.value) })}
              className="w-24"
            />
          </label>
          <label className="grid gap-1 text-sm font-medium">
            Profiles
            <Input
              type="number"
              min={1}
              max={MAX_STAGING_PEOPLE}
              value={value.people}
              disabled={disabled}
              onChange={(e) => onChange({ people: Number(e.target.value) })}
              className="w-24"
            />
          </label>
        </>
      ) : (
        <p className="min-w-0 flex-1 pb-2 text-sm text-muted-foreground">The plan&apos;s full targets.</p>
      )}
    </div>
  );
}
