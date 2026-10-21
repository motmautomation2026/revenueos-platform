"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Pause, Play } from "lucide-react";
import { cn } from "@/lib/utils";

const REFRESH_MS = 5000;
const clock = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/**
 * Keeps a page current: while the page is visible it re-reads the Brain's data every few seconds
 * (every 5 on the Execution pages; the Projects overview asks for less), so progress appears
 * without reloading.
 */
export function LiveIndicator({ intervalMs = REFRESH_MS }: { intervalMs?: number }) {
  const router = useRouter();
  const [live, setLive] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    if (!live) return;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      router.refresh();
      setUpdatedAt(new Date());
    };
    const first = setTimeout(() => setUpdatedAt(new Date()), 0);
    const timer = setInterval(tick, intervalMs);
    document.addEventListener("visibilitychange", tick);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
      document.removeEventListener("visibilitychange", tick);
    };
  }, [live, router, intervalMs]);

  return (
    <div className="flex items-center justify-end gap-2 text-xs text-muted-foreground print:hidden">
      <span
        aria-hidden
        className={cn("size-2 rounded-full", live ? "animate-pulse bg-emerald-600" : "bg-muted-foreground/40")}
      />
      <span>
        {live ? "Live" : "Paused"}
        {updatedAt && <> · updated {clock.format(updatedAt)}</>}
      </span>
      <button
        type="button"
        onClick={() => setLive((on) => !on)}
        aria-label={live ? "Pause live updates" : "Resume live updates"}
        title={live ? "Pause live updates" : "Resume live updates"}
        className="flex size-7 items-center justify-center rounded-md outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {live ? <Pause className="size-3.5" aria-hidden /> : <Play className="size-3.5" aria-hidden />}
      </button>
    </div>
  );
}
