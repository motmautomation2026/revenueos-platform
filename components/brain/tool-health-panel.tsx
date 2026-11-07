"use client";

import { useState } from "react";
import { Loader2, Wifi } from "lucide-react";
import type { ToolReach } from "@/lib/brain/tool-health";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const DOT: Record<ToolReach["state"], string> = {
  up: "bg-emerald-500",
  degraded: "bg-amber-500",
  down: "bg-destructive",
};

/**
 * Phase 2a: on-demand reachability check of the tool hosts. The check runs on the server (GET-only, no
 * credentials); this just triggers it and shows the result. "Reachable" means the host replied at all.
 */
export function ToolHealthPanel({ check }: { check: () => Promise<ToolReach[]> }) {
  const [results, setResults] = useState<ToolReach[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setLoading(true);
    setError(null);
    try {
      setResults(await check());
    } catch {
      setError("The connectivity check could not run. Try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-semibold">Connectivity</h3>
        <Button type="button" variant="outline" size="sm" onClick={run} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" aria-hidden /> : <Wifi className="size-4" aria-hidden />}
          {results ? "Check again" : "Check connectivity"}
        </Button>
        <span className="text-xs text-muted-foreground">Pings each tool&apos;s service — no data is sent.</span>
      </div>

      {error && <p className="text-sm text-destructive">{error}</p>}

      {results && (
        <ul className="grid gap-2">
          {results.map((tool) => (
            <li
              key={tool.key}
              className={cn(
                "flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl border p-3 text-sm",
                tool.state === "down" ? "border-destructive/30 bg-destructive/5" : tool.state === "degraded" ? "border-amber-500/40 bg-amber-500/5" : ""
              )}
            >
              <span className={cn("size-2.5 shrink-0 rounded-full", DOT[tool.state])} aria-hidden />
              <span className="font-medium">{tool.label}</span>
              <span className="ml-auto text-xs text-muted-foreground tabular-nums">{tool.url}</span>
              <p className="w-full text-muted-foreground">{tool.detail}</p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
