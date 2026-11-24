"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { MessagesSquare, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { saveAgentScript, resetAgentScript } from "@/app/(app)/projects/[id]/execution/actions";

/**
 * Edit the AI calling script (opening line + what the agent says). Shows the current script read-only with
 * an Edit button; editing saves an override the Brain uses instead of writing its own. "Edited" means an
 * override is saved; "Reset to auto" clears it. Changes apply to the next campaign.
 */
export function ScriptEdit({
  projectId,
  autoWelcome,
  autoSystem,
  isCustom,
  savedWelcome,
  savedSystem,
}: {
  projectId: string;
  autoWelcome: string;
  autoSystem: string;
  isCustom: boolean;
  savedWelcome: string;
  savedSystem: string;
}) {
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [pending, start] = useTransition();
  const baseWelcome = isCustom ? savedWelcome : autoWelcome;
  const baseSystem = isCustom ? savedSystem : autoSystem;
  const [welcome, setWelcome] = useState(baseWelcome);
  const [system, setSystem] = useState(baseSystem);

  const save = () =>
    start(async () => {
      const result = await saveAgentScript({ projectId, welcomeMessage: welcome, systemPrompt: system });
      if (result.ok) {
        toast.success(result.message);
        setEditing(false);
        router.refresh();
      } else {
        toast.error(result.error);
      }
    });

  const reset = () =>
    start(async () => {
      const result = await resetAgentScript({ projectId });
      if (result.ok) {
        toast.success(result.message);
        setEditing(false);
        router.refresh();
      } else {
        toast.error(result.error);
      }
    });

  const field =
    "w-full rounded-lg border bg-background p-3 text-sm leading-relaxed outline-none focus-visible:ring-3 focus-visible:ring-ring/50";
  const heading = "text-xs font-semibold tracking-wide text-muted-foreground uppercase";

  if (!editing) {
    return (
      <div className="grid gap-3">
        <div className="flex items-center justify-between gap-2">
          <p className={heading}>
            Opening line
            {isCustom && (
              <span className="ml-2 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary normal-case">
                Edited
              </span>
            )}
          </p>
          <button
            type="button"
            onClick={() => {
              setWelcome(baseWelcome);
              setSystem(baseSystem);
              setEditing(true);
            }}
            className="inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-xs font-medium outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            <Pencil className="size-3" aria-hidden />
            Edit
          </button>
        </div>
        <p className="rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">{baseWelcome || "—"}</p>
        {baseSystem && (
          <details className="group grid gap-1">
            <summary className="flex w-fit cursor-pointer list-none items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
              <MessagesSquare className="size-3.5" aria-hidden />
              What the agent says
              <span className="font-normal normal-case group-open:hidden">· show</span>
              <span className="hidden font-normal normal-case group-open:inline">· hide</span>
            </summary>
            <p className="mt-1 rounded-lg bg-muted/50 p-3 text-sm leading-relaxed whitespace-pre-wrap">{baseSystem}</p>
          </details>
        )}
      </div>
    );
  }

  return (
    <div className="grid gap-3">
      <div className="grid gap-1">
        <label htmlFor="agent-welcome" className={heading}>
          Opening line
        </label>
        <textarea
          id="agent-welcome"
          value={welcome}
          onChange={(e) => setWelcome(e.target.value)}
          rows={2}
          disabled={pending}
          className={field}
        />
      </div>
      <div className="grid gap-1">
        <label htmlFor="agent-system" className={heading}>
          What the agent says
        </label>
        <textarea
          id="agent-system"
          value={system}
          onChange={(e) => setSystem(e.target.value)}
          rows={8}
          disabled={pending}
          className={field}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        Keep placeholders like <code className="font-mono">{"{name}"}</code> and{" "}
        <code className="font-mono">{"{company}"}</code>. Don&apos;t mention internal terms (MOTM, segments, P1/P2) —
        the agent refuses a script that does. Applies to the next call campaign.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={save} disabled={pending}>
          {pending ? "Saving…" : "Save script"}
        </Button>
        <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={pending}>
          Cancel
        </Button>
        {isCustom && (
          <Button size="sm" variant="ghost" onClick={reset} disabled={pending} className="ml-auto text-muted-foreground">
            Reset to auto‑written
          </Button>
        )}
      </div>
    </div>
  );
}
