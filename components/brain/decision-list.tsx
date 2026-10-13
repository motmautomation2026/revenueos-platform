"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  MAX_NOTE_LENGTH,
  answerHelp,
  answerOptions,
  decisionIntro,
  noteRequired,
  type AnswerOption,
} from "@/lib/brain/answers";
import type { DecisionCard } from "@/lib/brain/view";
import { answerDecision } from "@/app/(app)/projects/[id]/execution/actions";

/** Open questions the Brain is waiting on, each with the answers the Brain allows. */
export function DecisionList({ projectId, decisions }: { projectId: string; decisions: DecisionCard[] }) {
  return (
    <ul className="grid gap-3 lg:grid-cols-2 2xl:grid-cols-3">
      {decisions.map((decision) => (
        <DecisionItem key={decision.id} projectId={projectId} decision={decision} />
      ))}
    </ul>
  );
}

function DecisionItem({ projectId, decision }: { projectId: string; decision: DecisionCard }) {
  const router = useRouter();
  const [note, setNote] = useState("");
  const [sending, setSending] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<AnswerOption | null>(null);
  const [noteError, setNoteError] = useState(false);

  const options = answerOptions(decision.kind, decision.allowed);
  const needsNote = noteRequired(decision.kind);
  const noteId = `note-${decision.id}`;
  const human = decision.kind === "human_task" || decision.kind === "human_gate_task";
  const task = decision.task;
  const intro = decisionIntro(decision.kind);
  // The Brain's own sentence for a person's task repeats what is laid out below it.
  const question = human && task ? "" : decision.text || "The Brain is waiting for an answer.";

  const send = async (option: AnswerOption) => {
    setConfirming(null);
    setSending(option.answer);
    try {
      const result = await answerDecision({ projectId, decisionId: decision.id, answer: option.answer, note });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(result.message);
      router.refresh();
    } catch {
      toast.error("The answer could not be sent. Check your connection and try again.");
    } finally {
      setSending(null);
    }
  };

  const choose = (option: AnswerOption) => {
    if (needsNote && !note.trim()) {
      setNoteError(true);
      document.getElementById(noteId)?.focus();
      return;
    }
    if (option.confirm) setConfirming(option);
    else void send(option);
  };

  return (
    <li className="grid content-start gap-3 rounded-xl border border-amber-500/40 bg-background p-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="rounded-full bg-amber-500/20 px-2 py-0.5 text-xs font-semibold text-amber-900">
          {decision.title}
        </span>
        {decision.taskId && (
          <span className="text-xs text-muted-foreground">
            {decision.taskId}
            {task?.agent ? ` · ${task.agent}` : ""}
          </span>
        )}
        <span className="ml-auto text-xs text-muted-foreground tabular-nums" title="The Brain's number for this question">
          #{decision.id}
        </span>
      </div>

      {task && <p className="leading-snug font-semibold">{task.title}</p>}
      {intro && !human && <p className="text-sm leading-relaxed text-muted-foreground">{intro}</p>}
      {question && <p className="text-sm leading-relaxed font-medium">{question}</p>}
      {decision.why && <p className="text-sm leading-relaxed text-muted-foreground">Why: {decision.why}</p>}

      {human && task && task.doneWhen && (
        <p className="text-sm leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground">Done when:</span> {task.doneWhen}
        </p>
      )}
      {human && task && task.steps.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-primary">
            How to do it ({task.steps.length} step{task.steps.length === 1 ? "" : "s"})
          </summary>
          <ol className="mt-1 grid list-decimal gap-1 pl-5 leading-relaxed text-muted-foreground">
            {task.steps.map((step, i) => (
              <li key={i}>{step}</li>
            ))}
          </ol>
        </details>
      )}

      {decision.samples.length > 0 && (
        <details className="rounded-lg border text-sm">
          <summary className="cursor-pointer px-3 py-2 font-medium">
            {decision.samples.length} sample email{decision.samples.length === 1 ? "" : "s"}
          </summary>
          <ul className="divide-y border-t">
            {decision.samples.map((sample, i) => (
              <li key={i} className="grid gap-1 px-3 py-2">
                {sample.to && <p className="text-xs text-muted-foreground">To: {sample.to}</p>}
                <p className="font-medium">{sample.subject}</p>
                <p className="leading-relaxed whitespace-pre-line text-muted-foreground">{sample.text}</p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {decision.contacts.length > 0 && (
        <details className="rounded-lg border text-sm">
          <summary className="cursor-pointer px-3 py-2 font-medium">
            {decision.contacts.length} contact{decision.contacts.length === 1 ? "" : "s"} to call
          </summary>
          <ul className="divide-y border-t">
            {decision.contacts.map((contact, i) => (
              <li key={i} className="flex flex-wrap justify-between gap-2 px-3 py-2">
                <span>
                  {contact.name}
                  {contact.company && <span className="text-muted-foreground"> · {contact.company}</span>}
                </span>
                <span className="text-muted-foreground tabular-nums">{contact.phone}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {options.length === 0 ? (
        <p className="text-sm text-muted-foreground">This kind of decision cannot be answered from the app yet.</p>
      ) : (
        <div className="grid gap-2">
          <label htmlFor={noteId} className="text-xs font-medium text-muted-foreground">
            {needsNote ? "Note (required)" : "Note (optional)"}
          </label>
          <Textarea
            id={noteId}
            rows={2}
            maxLength={MAX_NOTE_LENGTH}
            value={note}
            disabled={sending !== null}
            aria-invalid={noteError}
            placeholder={human ? "What was done" : undefined}
            onChange={(e) => {
              setNote(e.target.value);
              if (e.target.value.trim()) setNoteError(false);
            }}
          />
          {noteError && (
            <p role="alert" className="text-xs text-destructive">
              Add a short note first.
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            {options.map((option) => (
              <Button
                key={option.answer}
                variant={option.tone === "primary" ? "default" : option.tone === "danger" ? "destructive" : "outline"}
                disabled={sending !== null}
                title={answerHelp(decision.kind, option.answer) || undefined}
                onClick={() => choose(option)}
              >
                {sending === option.answer && <Loader2 className="animate-spin" aria-hidden />}
                {option.label}
              </Button>
            ))}
          </div>
        </div>
      )}

      <AlertDialog open={confirming !== null} onOpenChange={(open) => !open && setConfirming(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirming?.label}: {decision.title.toLowerCase()}?
            </AlertDialogTitle>
            <AlertDialogDescription>{confirming?.confirm}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              variant={confirming?.tone === "danger" ? "destructive" : "default"}
              onClick={() => confirming && void send(confirming)}
            >
              {confirming?.label}
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </li>
  );
}
