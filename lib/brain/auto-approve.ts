import { z } from "zod";

// Per-project timed auto-approval (supabase/migrations/0017_auto_approve.sql).
//
// When on, the Brain's run loop (brain/orch/run.py, auto_resolve_overdue) auto-answers routine gate
// decisions that have been open longer than `seconds`, so a run does not park in "Needs a person" while
// no one objects: a human work task is auto-skipped (non-blocking), a content/readiness gate is approved,
// and email/call sends are approved. Exception decisions (goal check, a failed task, a paid call of
// unknown outcome) are always left for a person. Off (null) keeps the default: every gate waits.
//
// The Brain reads auto_approve_seconds directly (brain/supabase_store.py, auto_approve_seconds), so no
// read-only platform-developer file (run_for.py) is involved.

/** Up to a day: a window longer than this is almost certainly a mistake. */
export const MAX_AUTO_APPROVE_SECONDS = 24 * 60 * 60;
export const AUTO_APPROVE_DEFAULT_SECONDS = 5;

export type AutoApproveSettings = { enabled: boolean; seconds: number };

export const autoApproveSchema = z.object({
  enabled: z.boolean(),
  seconds: z.coerce
    .number()
    .int()
    .min(1, "At least 1 second.")
    .max(MAX_AUTO_APPROVE_SECONDS, `At most ${MAX_AUTO_APPROVE_SECONDS} seconds.`),
});
export type AutoApproveInput = z.infer<typeof autoApproveSchema>;

/** The column to write: the number of seconds when on, null when off. */
export function autoApproveColumns(input: AutoApproveInput) {
  return { auto_approve_seconds: input.enabled ? input.seconds : null };
}

/** The project's saved choice. `available` is false before 0017_auto_approve.sql is applied. */
export function readAutoApprove(project: { auto_approve_seconds?: number | null }): AutoApproveSettings & {
  available: boolean;
} {
  const value = project.auto_approve_seconds;
  const enabled = typeof value === "number" && value > 0;
  return {
    available: value !== undefined,
    enabled,
    seconds: enabled ? value : AUTO_APPROVE_DEFAULT_SECONDS,
  };
}
