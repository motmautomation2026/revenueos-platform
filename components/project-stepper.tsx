"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Check, Lock } from "lucide-react";
import { cn } from "@/lib/utils";
import { STEPS } from "@/lib/projects";

export function ProjectStepper({
  projectId,
  reached,
  complete,
}: {
  projectId: string;
  reached: number;
  /** The plan is ready, so the last step counts as done too. */
  complete: boolean;
}) {
  const pathname = usePathname();
  const segment = pathname.split("/")[3] ?? "";
  // Steps 3 and 4 share /plan: the page shows the run, then the finished plan.
  const activeNumber =
    segment === "plan" ? Math.max(3, Math.min(reached, 4)) : segment === "checklist" ? 2 : 1;

  return (
    <nav aria-label="Project steps">
      <ol className="flex items-start">
        {STEPS.map((step, index) => {
          const unlocked = step.number <= reached;
          const done = step.number < reached || complete;
          const active = step.number === activeNumber;

          const body = (
            <>
              <span
                className={cn(
                  "flex size-8 shrink-0 items-center justify-center rounded-full border-2 text-sm font-semibold transition-colors",
                  active && "border-primary bg-primary text-primary-foreground",
                  !active && done && "border-primary bg-background text-primary",
                  !active && !done && unlocked && "border-primary/50 bg-background text-primary",
                  !unlocked && "border-border bg-background text-muted-foreground"
                )}
              >
                {done && !active ? (
                  <Check className="size-4" aria-hidden />
                ) : unlocked ? (
                  step.number
                ) : (
                  <Lock className="size-3.5" aria-hidden />
                )}
              </span>
              <span
                className={cn(
                  "text-center text-xs leading-tight font-medium sm:text-sm",
                  active ? "text-foreground" : "text-muted-foreground"
                )}
              >
                {step.label}
              </span>
            </>
          );
          const className = "flex flex-col items-center gap-1.5 rounded-lg px-1 py-1 outline-none";

          return (
            <li key={step.key} className="relative flex flex-1 justify-center">
              {index > 0 && (
                <span
                  aria-hidden
                  className={cn(
                    "absolute top-5 right-1/2 h-0.5 w-full -translate-y-1/2",
                    step.number <= reached ? "bg-primary" : "bg-border"
                  )}
                />
              )}
              <div className="relative z-10">
                {unlocked ? (
                  <Link
                    href={`/projects/${projectId}/${step.path}`}
                    className={cn(className, "hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50")}
                    aria-current={active ? "step" : undefined}
                  >
                    {body}
                  </Link>
                ) : (
                  <span
                    className={cn(className, "cursor-not-allowed")}
                    aria-disabled
                    title="Finish the previous step first"
                  >
                    {body}
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
