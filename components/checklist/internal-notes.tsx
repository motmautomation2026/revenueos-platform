import { ChevronDown, Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { ChecklistFlag } from "@/lib/agents/checklist";

const KICKOFF_PREFIX = "confirm in kickoff:";

function asFlags(value: unknown): ChecklistFlag[] {
  return Array.isArray(value)
    ? value.filter((f): f is ChecklistFlag => !!f && typeof f === "object" && "flag_type" in f)
    : [];
}

function asNotes(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((n): n is string => typeof n === "string") : [];
}

/** For the MOTM account manager only. Never rendered in the customer-facing list or the Excel file. */
export function InternalNotes({ flags, camNotes }: { flags: unknown; camNotes: unknown }) {
  const flagList = asFlags(flags);
  const notes = asNotes(camNotes);

  return (
    <details className="group rounded-xl border bg-muted/30 lg:sticky lg:top-18 print:hidden">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 font-medium [&::-webkit-details-marker]:hidden">
        <Lock className="size-4 text-muted-foreground" aria-hidden />
        <span className="flex-1">Internal notes (MOTM only)</span>
        <Badge variant="secondary">{flagList.length + notes.length}</Badge>
        <ChevronDown className="size-4 transition-transform group-open:rotate-180" aria-hidden />
      </summary>

      <div className="grid gap-5 border-t px-4 py-4 text-sm">
        <section className="grid gap-2">
          <h3 className="font-medium">Flags</h3>
          {flagList.length === 0 ? (
            <p className="text-muted-foreground">No flags raised.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[28rem] text-left align-top">
                <thead className="text-xs text-muted-foreground">
                  <tr className="border-b">
                    <th className="py-1.5 pr-2 font-medium">Type</th>
                    <th className="py-1.5 pr-2 font-medium">Field</th>
                    <th className="py-1.5 pr-2 font-medium">Detail</th>
                    <th className="py-1.5 font-medium">Blocks targets</th>
                  </tr>
                </thead>
                <tbody>
                  {flagList.map((flag, i) => (
                    <tr key={i} className="border-b align-top last:border-0">
                      <td className="py-1.5 pr-2 capitalize">{flag.flag_type}</td>
                      <td className="py-1.5 pr-2">{flag.field}</td>
                      <td className="py-1.5 pr-2">
                        {flag.detail}
                        {(flag.handover_says || flag.proposal_says) && (
                          <span className="mt-1 block text-xs text-muted-foreground">
                            {flag.handover_says && <>Handover: {flag.handover_says}</>}
                            {flag.handover_says && flag.proposal_says && " · "}
                            {flag.proposal_says && <>Proposal: {flag.proposal_says}</>}
                          </span>
                        )}
                      </td>
                      <td className={cn("py-1.5", flag.blocks_targets && "font-medium text-destructive")}>
                        {flag.blocks_targets ? "Yes" : "No"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="grid gap-2">
          <h3 className="font-medium">CAM notes</h3>
          {notes.length === 0 ? (
            <p className="text-muted-foreground">No notes.</p>
          ) : (
            <ul className="grid gap-1.5">
              {notes.map((note, i) => (
                <li
                  key={i}
                  className={cn(
                    "rounded-md px-2 py-1",
                    note.trim().toLowerCase().startsWith(KICKOFF_PREFIX) &&
                      "border border-amber-500/40 bg-amber-500/10"
                  )}
                >
                  {note}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </details>
  );
}
