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

/** Sample batches the email tool returned without emails (the Brain's NOT_APPROVABLE states). */
const SAMPLE_PROBLEMS: Record<string, { title: string; next: string }> = {
  failed: {
    title: "The email tool could not write every sample email",
    next:
      "Nothing was sent, and these samples cannot be approved until all of them are written. Use Ask for changes with a short note " +
      "(e.g. \"try again\") to have them written again. If none were written at all, the email tool's own AI key may have been " +
      "rejected or be out of credits; that is fixed on the email tool's server first.",
  },
  stale: {
    title: "These samples are out of date",
    next: "The campaign was edited in the email tool after they were written. Use Ask for changes to have them written again.",
  },
};

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
  const emails = decision.kind === "email_approve";
  const review = decision.review;
  const canChange = options.some((option) => option.answer === "feedback");
  const task = decision.task;
  // Samples the tool could not write (or that are out of date) are empty: say why instead of "read below".
  const sampleProblem = emails && review ? SAMPLE_PROBLEMS[review.status] : undefined;
  const intro = sampleProblem ? "" : decisionIntro(decision.kind);
  // The Brain's own sentence for a person's task, or for an email review, repeats what is laid out below it.
  const question = (human && task) || emails ? "" : decision.text || "The Brain is waiting for an answer.";

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
    if ((needsNote || option.answer === "feedback") && !note.trim()) {
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

      {review && (
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
          <span className="rounded-full bg-primary/10 px-2 py-0.5 font-semibold text-primary">Version {review.version}</span>
          {review.roundsLeft > 0
            ? `${review.roundsLeft} round${review.roundsLeft === 1 ? "" : "s"} of changes left`
            : "No rounds of changes left"}
        </p>
      )}

      {sampleProblem && (
        <div className="grid gap-1 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-semibold text-destructive">{sampleProblem.title}</p>
          {review && review.missing.length > 0 && decision.samples.length > 0 && (
            <ul className="grid list-disc gap-0.5 pl-5 text-muted-foreground">
              {review.missing.map((item, i) => (
                <li key={i}>Not written: {item}</li>
              ))}
            </ul>
          )}
          <p className="leading-relaxed text-muted-foreground">{sampleProblem.next}</p>
        </div>
      )}

      {review && review.checks.length > 0 && (
        <div className="grid gap-1 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm">
          <p className="font-semibold text-destructive">
            {review.checks.length} problem{review.checks.length === 1 ? "" : "s"} found, so this version cannot be approved
          </p>
          <ul className="grid list-disc gap-0.5 pl-5 text-muted-foreground">
            {review.checks.map((check, i) => (
              <li key={i}>{check}</li>
            ))}
          </ul>
        </div>
      )}

      {decision.samples.length > 0 && (
        <details className="rounded-lg border text-sm" open={emails}>
          <summary className="cursor-pointer px-3 py-2 font-medium">
            {decision.samples.length} sample email{decision.samples.length === 1 ? "" : "s"}
          </summary>
          <ul className="max-h-[32rem] divide-y overflow-y-auto border-t">
            {decision.samples.map((sample, i) => (
              <li key={i} className="grid gap-1 px-3 py-2">
                {sample.to && <p className="text-xs text-muted-foreground">For {sample.to}</p>}
                <p className="font-medium">{sample.subject}</p>
                {sample.preheader && <p className="text-xs text-muted-foreground italic">{sample.preheader}</p>}
                <p className="leading-relaxed whitespace-pre-line text-muted-foreground">{sample.text}</p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {review && review.previousFeedback.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-primary">
            Changes asked for so far ({review.previousFeedback.length})
          </summary>
          <ol className="mt-1 grid list-decimal gap-1 pl-5 leading-relaxed text-muted-foreground">
            {review.previousFeedback.map((feedback, i) => (
              <li key={i}>{feedback}</li>
            ))}
          </ol>
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
            {needsNote
              ? "Note (required)"
              : canChange
                ? "What should change (needed for Ask for changes)"
                : "Note (optional)"}
          </label>
          <Textarea
            id={noteId}
            rows={2}
            maxLength={MAX_NOTE_LENGTH}
            value={note}
            disabled={sending !== null}
            aria-invalid={noteError}
            placeholder={human ? "What was done" : canChange ? "e.g. Shorter, and mention the 2-year warranty" : undefined}
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
