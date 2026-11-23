import { Bot, MessagesSquare } from "lucide-react";
import type { CallScript } from "@/lib/brain/tools";

/**
 * "How the agent talks" — the script this call campaign uses, shown so a person can confirm it before
 * approving, the way they review the sample emails. The opening line is always shown; the agent's full
 * instructions are behind a toggle because they can be long.
 */
export function CallScriptCard({ script }: { script: CallScript }) {
  const cap = (s: string) => (s ? s[0].toUpperCase() + s.slice(1) : s);
  const meta = [
    script.name && `Agent: ${script.name}`,
    script.language && cap(script.language),
    script.voiceId && `Voice: ${script.voiceId}`,
    script.speechPace != null && `Pace ${script.speechPace}×`,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <section aria-label="Call script" className="grid gap-3 rounded-xl border p-5">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Bot className="size-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <p className="font-semibold">How the agent talks</p>
          <p className="text-sm text-muted-foreground">
            The script this campaign calls with — confirm it as you would the sample emails. Placeholders like{" "}
            <code className="font-mono text-xs">{"{name}"}</code> are filled in per person.
          </p>
        </div>
      </div>

      {meta && <p className="text-xs text-muted-foreground">{meta}</p>}

      <div className="grid gap-1">
        <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">Opening line</p>
        <p className="rounded-lg bg-muted/50 p-3 text-sm leading-relaxed">
          {script.welcomeMessage || "—"}
        </p>
      </div>

      {script.systemPrompt && (
        <details className="group grid gap-1">
          <summary className="flex w-fit cursor-pointer list-none items-center gap-1.5 text-xs font-semibold tracking-wide text-muted-foreground uppercase outline-none focus-visible:ring-3 focus-visible:ring-ring/50 [&::-webkit-details-marker]:hidden">
            <MessagesSquare className="size-3.5" aria-hidden />
            What the agent says
            <span className="font-normal normal-case group-open:hidden">· show</span>
            <span className="hidden font-normal normal-case group-open:inline">· hide</span>
          </summary>
          <p className="mt-1 rounded-lg bg-muted/50 p-3 text-sm leading-relaxed whitespace-pre-wrap">
            {script.systemPrompt}
          </p>
        </details>
      )}
    </section>
  );
}
