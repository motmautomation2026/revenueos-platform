"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowDown,
  ArrowRight,
  Check,
  Download,
  Loader2,
  PencilLine,
  RefreshCw,
  Send,
  Upload,
} from "lucide-react";
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
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { isSecretQuestion } from "@/lib/checklist-secrets";
import { SENDER_CATEGORY, SENDER_CODES } from "@/lib/checklist-sender";
import { cn } from "@/lib/utils";
import {
  generateChecklistAction,
  importChecklistExcel,
  reopenChecklist,
  saveAnswer,
  submitChecklist,
} from "@/app/(app)/projects/[id]/checklist/actions";

export type ChecklistItemView = {
  id: string;
  code: string;
  category: string;
  question: string;
  options: string[];
  prefilled_answer: string | null;
  answer: string | null;
};

const SAVE_DELAY_MS = 800;
const OTHER = "__other__";

type SaveState = "idle" | "saving" | "saved" | "error";

export function ChecklistForm({
  projectId,
  items,
  submitted,
  canReopen,
  canRegenerate,
  countWarning,
  internalNotes,
}: {
  projectId: string;
  items: ChecklistItemView[];
  submitted: boolean;
  canReopen: boolean;
  canRegenerate: boolean;
  countWarning: string | null;
  /** The MOTM-only notes panel, rendered between the summary and the questions. */
  internalNotes?: React.ReactNode;
}) {
  const router = useRouter();
  const [answers, setAnswers] = useState<Record<string, string>>(() =>
    Object.fromEntries(items.map((item) => [item.id, item.answer ?? ""]))
  );
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [busy, setBusy] = useState<null | "submit" | "regenerate" | "import" | "reopen">(null);
  const [dialog, setDialog] = useState<null | "submit" | "regenerate">(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Autosave: one debounce timer per question, plus a count of saves in flight.
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const pending = useRef(new Map<string, string>());
  const inFlight = useRef(new Set<Promise<void>>());

  const save = useCallback(
    (itemId: string) => {
      const answer = pending.current.get(itemId);
      if (answer === undefined) return;
      pending.current.delete(itemId);
      timers.current.delete(itemId);

      const request = saveAnswer({ projectId, itemId, answer })
        .then((result) => {
          if (!result.ok) throw new Error(result.error);
        })
        .catch((error: Error) => {
          setSaveState("error");
          toast.error(error.message || "Could not save the answer.");
        })
        .finally(() => {
          inFlight.current.delete(request);
          if (!inFlight.current.size && !pending.current.size) {
            setSaveState((state) => (state === "error" ? state : "saved"));
          }
        });
      inFlight.current.add(request);
    },
    [projectId]
  );

  const setAnswer = useCallback(
    (itemId: string, value: string) => {
      setAnswers((current) => ({ ...current, [itemId]: value }));
      setSaveState("saving");
      pending.current.set(itemId, value);
      clearTimeout(timers.current.get(itemId));
      timers.current.set(itemId, setTimeout(() => save(itemId), SAVE_DELAY_MS));
    },
    [save]
  );

  /** Sends anything still waiting on its debounce and waits for all saves to land. */
  const flush = useCallback(async () => {
    for (const [itemId, timer] of timers.current) {
      clearTimeout(timer);
      save(itemId);
    }
    await Promise.all(inFlight.current);
  }, [save]);

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (pending.current.size || inFlight.current.size) event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, []);

  const groups = useMemo(() => {
    const byCategory = new Map<string, ChecklistItemView[]>();
    for (const item of items) {
      byCategory.set(item.category, [...(byCategory.get(item.category) ?? []), item]);
    }
    return [...byCategory];
  }, [items]);

  const answered = items.filter((item) => answers[item.id]?.trim()).length;
  const unanswered = items.length - answered;
  const percent = items.length ? Math.round((answered / items.length) * 100) : 0;

  const doSubmit = async () => {
    setDialog(null);
    setBusy("submit");
    await flush();
    const result = await submitChecklist(projectId);
    setBusy(null);
    if (!result.ok) return void toast.error(result.error);
    toast.success("Checklist submitted.");
    router.refresh();
  };

  const doReopen = async () => {
    setBusy("reopen");
    const result = await reopenChecklist(projectId);
    setBusy(null);
    if (!result.ok) return void toast.error(result.error);
    router.refresh();
  };

  const doRegenerate = async () => {
    setDialog(null);
    setBusy("regenerate");
    await flush();
    try {
      const result = await generateChecklistAction(projectId);
      if (!result.ok) return void toast.error(result.error);
      toast.success(`New checklist written with ${result.questionCount} questions.`);
      router.refresh();
    } catch {
      toast.error("Writing the checklist did not finish. Refresh the page to check.");
    } finally {
      setBusy(null);
    }
  };

  const doImport = async (file: File) => {
    setBusy("import");
    await flush();
    const formData = new FormData();
    formData.set("projectId", projectId);
    formData.set("file", file);
    try {
      const result = await importChecklistExcel(formData);
      if (!result.ok) return void toast.error(result.error);
      setAnswers((current) => ({ ...current, ...result.answers }));
      const parts = [`${result.updated} answer${result.updated === 1 ? "" : "s"} updated`];
      if (result.unchanged) parts.push(`${result.unchanged} unchanged`);
      if (result.notMatched) {
        parts.push(`${result.notMatched} row${result.notMatched === 1 ? "" : "s"} not matched`);
      }
      toast.success(parts.join(", "));
    } catch {
      toast.error("Could not read this file.");
    } finally {
      setBusy(null);
    }
  };

  const locked = submitted || busy !== null;

  const goToNextUnanswered = () => {
    const next = items.find((item) => !answers[item.id]?.trim());
    if (!next) return;
    const field = document.getElementById(`answer-${next.id}`);
    field?.scrollIntoView({ block: "center" });
    field?.focus({ preventScroll: true });
  };

  const prefilledCount = items.filter((item) => item.prefilled_answer).length;
  const toConfirm = items.filter(
    (item) => item.prefilled_answer && answers[item.id] === item.prefilled_answer
  ).length;

  return (
    <div className="grid gap-6">
      <div className="grid gap-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
          <div className="grid gap-1">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <h2 className="text-lg font-semibold">Discovery checklist</h2>
              {submitted ? (
                <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-semibold text-emerald-800">
                  Submitted
                </span>
              ) : (
                <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary">
                  Being filled in
                </span>
              )}
              {countWarning && (
                <Badge variant="outline" className="border-amber-500/50 text-amber-700 dark:text-amber-400">
                  <AlertTriangle aria-hidden />
                  {countWarning}
                </Badge>
              )}
              <SaveIndicator state={saveState} />
            </div>
            <p className="text-sm text-muted-foreground">
              {submitted
                ? "These are the answers the plan was written from."
                : "Answer each question, or download the Excel file, fill it in and upload it back. Answers save as you type."}
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <a
              href={`/api/projects/${projectId}/checklist/export`}
              className={buttonVariants({ variant: "outline" })}
              onClick={() => void flush()}
            >
              <Download aria-hidden />
              Download Excel
            </a>
            {!submitted && (
              <Button variant="outline" disabled={busy !== null} onClick={() => fileInput.current?.click()}>
                {busy === "import" ? <Loader2 className="animate-spin" aria-hidden /> : <Upload aria-hidden />}
                Upload filled Excel
              </Button>
            )}
            <input
              ref={fileInput}
              type="file"
              accept=".xlsx"
              className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                e.target.value = "";
                if (file) void doImport(file);
              }}
            />
            {canRegenerate && (
              <Button variant="outline" disabled={busy !== null} onClick={() => setDialog("regenerate")}>
                {busy === "regenerate" ? <Loader2 className="animate-spin" aria-hidden /> : <RefreshCw aria-hidden />}
                {busy === "regenerate" ? "Writing new checklist…" : "Regenerate"}
              </Button>
            )}
          </div>
        </div>
        {busy === "regenerate" && (
          <p className="text-sm text-muted-foreground">
            The AI is reading the documents again. This usually takes one to three minutes.
          </p>
        )}

        <div className="grid gap-3 md:grid-cols-3">
          <div className="grid content-start gap-2 rounded-xl border p-4">
            <p className="text-sm text-muted-foreground">Answered</p>
            <p className="text-2xl leading-none font-semibold tabular-nums">
              {answered}
              <span className="text-base font-normal text-muted-foreground"> / {items.length}</span>
            </p>
            <div
              className="h-1.5 overflow-hidden rounded-full bg-muted"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={items.length}
              aria-valuenow={answered}
              aria-label="Questions answered"
            >
              <div
                className={cn("h-full rounded-full transition-[width]", percent >= 100 ? "bg-emerald-600" : "bg-primary")}
                style={{ width: `${percent}%` }}
              />
            </div>
          </div>
          <div className="grid content-start gap-2 rounded-xl border p-4">
            <p className="text-sm text-muted-foreground">Prefilled from the documents</p>
            <p className="text-2xl leading-none font-semibold tabular-nums">{prefilledCount}</p>
            <p className="text-xs text-muted-foreground">
              {submitted
                ? `${prefilledCount - toConfirm} of them were changed before submitting`
                : `${toConfirm} still as the AI wrote them; check each one`}
            </p>
          </div>
          <div className="grid content-start gap-2 rounded-xl border p-4">
            <p className="text-sm text-muted-foreground">Sections</p>
            <p className="text-2xl leading-none font-semibold tabular-nums">
              {groups.filter(([, list]) => list.every((item) => answers[item.id]?.trim())).length}
              <span className="text-base font-normal text-muted-foreground"> / {groups.length} complete</span>
            </p>
          </div>
        </div>

        {internalNotes}

        <nav aria-label="Checklist sections" className="flex flex-wrap gap-1.5">
          {groups.map(([category, categoryItems], index) => {
            const done = categoryItems.filter((item) => answers[item.id]?.trim()).length;
            const complete = done === categoryItems.length;
            return (
              <a
                key={category}
                href={`#section-${index}`}
                className="inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                {complete ? (
                  <Check className="size-3.5 text-emerald-700" aria-hidden />
                ) : (
                  <span className="size-2 rounded-full bg-amber-500" aria-hidden />
                )}
                <span className="font-medium">{category}</span>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {done}/{categoryItems.length}
                </span>
              </a>
            );
          })}
        </nav>
      </div>

      {groups.map(([category, categoryItems], index) => (
        <section key={category} id={`section-${index}`} className="scroll-mt-20 overflow-hidden rounded-xl border">
          <h3 className="flex items-baseline justify-between gap-3 border-b bg-muted/50 px-4 py-2.5 sm:px-5">
            <span className="grid gap-0.5">
              <span className="font-semibold">{category}</span>
              {category === SENDER_CATEGORY && (
                <span className="text-xs font-normal text-muted-foreground">
                  The mailbox your emails will be sent from. These can also be changed later on the Email page.
                </span>
              )}
            </span>
            <span className="text-sm text-muted-foreground tabular-nums">
              {categoryItems.filter((item) => answers[item.id]?.trim()).length} of {categoryItems.length} answered
            </span>
          </h3>
          <ol className="grid gap-x-8 gap-y-5 p-4 sm:p-5 xl:grid-cols-2">
            {categoryItems.map((item) => (
              <QuestionRow
                key={item.id}
                item={item}
                number={items.indexOf(item) + 1}
                value={answers[item.id] ?? ""}
                disabled={locked}
                readOnly={submitted}
                onChange={(value) => setAnswer(item.id, value)}
              />
            ))}
          </ol>
        </section>
      ))}

      <div className="sticky bottom-0 z-10 -mx-4 -mb-4 flex flex-wrap items-center gap-3 rounded-b-xl border-t bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:-mb-6 sm:px-6 xl:-mx-8 xl:-mb-8 xl:px-8">
        {submitted ? (
          <>
            <p className="flex-1 text-sm text-muted-foreground">
              This checklist has been submitted.
            </p>
            {canReopen && (
              <Button variant="outline" disabled={busy !== null} onClick={doReopen}>
                <PencilLine aria-hidden />
                Edit answers
              </Button>
            )}
            <Link href={`/projects/${projectId}/plan`} className={buttonVariants()}>
              Continue to plan
              <ArrowRight aria-hidden />
            </Link>
          </>
        ) : (
          <>
            <p className="flex-1 text-sm text-muted-foreground">
              {unanswered === 0
                ? "All questions answered."
                : `${unanswered} question${unanswered === 1 ? "" : "s"} unanswered.`}
            </p>
            {unanswered > 0 && (
              <Button variant="outline" disabled={busy !== null} onClick={goToNextUnanswered}>
                <ArrowDown aria-hidden />
                Next unanswered
              </Button>
            )}
            <Button
              disabled={busy !== null}
              onClick={() => (unanswered > 0 ? setDialog("submit") : void doSubmit())}
            >
              {busy === "submit" ? <Loader2 className="animate-spin" aria-hidden /> : <Send aria-hidden />}
              Submit checklist
            </Button>
          </>
        )}
      </div>

      <AlertDialog open={dialog === "submit"} onOpenChange={(open) => !open && setDialog(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {unanswered} question{unanswered === 1 ? "" : "s"} unanswered — continue?
            </AlertDialogTitle>
            <AlertDialogDescription>
              You can submit with blanks, but the plan will be weaker and may be blocked until the
              missing information is provided.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Keep editing</AlertDialogCancel>
            <Button onClick={doSubmit}>Submit anyway</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={dialog === "regenerate"} onOpenChange={(open) => !open && setDialog(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Regenerate the checklist?</AlertDialogTitle>
            <AlertDialogDescription>
              The AI will write a new checklist from the uploaded documents. The current checklist
              and its answers are kept on record but will no longer be the active one.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button onClick={doRegenerate}>Regenerate</Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function SaveIndicator({ state }: { state: SaveState }) {
  if (state === "idle") return null;
  return (
    <span className="inline-flex items-center gap-1 text-sm text-muted-foreground" aria-live="polite">
      {state === "saving" && (
        <>
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Saving…
        </>
      )}
      {state === "saved" && (
        <>
          <Check className="size-3.5" aria-hidden />
          Saved
        </>
      )}
      {state === "error" && <span className="text-destructive">Not saved</span>}
    </span>
  );
}

/** How a sender question is typed in: one line, with an example, never free-form "Other". */
const SENDER_FIELDS: Record<string, { type: string; placeholder: string; inputMode?: "email" | "url" }> = {
  "SENDER-NAME": { type: "text", placeholder: "e.g. Durel Sales Team" },
  "SENDER-EMAIL": { type: "email", placeholder: "e.g. sales@yourcompany.com", inputMode: "email" },
  "SENDER-SMTP-HOST": { type: "text", placeholder: "e.g. smtp.gmail.com", inputMode: "url" },
  "SENDER-IMAP-HOST": { type: "text", placeholder: "e.g. imap.gmail.com (optional)", inputMode: "url" },
};

function QuestionRow({
  item,
  number,
  value,
  disabled,
  readOnly,
  onChange,
}: {
  item: ChecklistItemView;
  /** The question's place in the whole checklist, shown beside it. */
  number: number;
  value: string;
  disabled: boolean;
  /** Submitted: show the answer as text instead of a greyed-out field. */
  readOnly: boolean;
  onChange: (value: string) => void;
}) {
  const hasOptions = item.options.length > 0;
  const [other, setOther] = useState(() => hasOptions && !!value && !item.options.includes(value));
  // An Excel import can bring in an answer that is not one of the options.
  const showOther = other || (hasOptions && !!value && !item.options.includes(value));
  const fieldId = `answer-${item.id}`;
  const secret = isSecretQuestion(item.question);
  const [revealed, setRevealed] = useState(false);
  const prefilled = !secret && !!item.prefilled_answer && value === item.prefilled_answer;
  const sender = SENDER_CODES.includes(item.code);
  const senderField = SENDER_FIELDS[item.code];

  const selectItems = useMemo(
    () => [
      ...item.options.map((option) => ({ value: option, label: option })),
      // A sender setting must be one of the choices the email tool accepts.
      ...(sender ? [] : [{ value: OTHER, label: "Other…" }]),
    ],
    [item.options, sender]
  );

  if (readOnly) {
    return (
      <li className="grid content-start gap-1.5">
        <p className="flex gap-2 text-sm font-medium">
          <span className="w-6 shrink-0 text-muted-foreground tabular-nums">{number}.</span>
          <span>{item.question}</span>
        </p>
        <p
          className={cn(
            "ml-8 rounded-lg px-3 py-2 text-sm leading-relaxed whitespace-pre-line",
            value.trim() ? "bg-muted/60" : "border border-dashed text-muted-foreground"
          )}
        >
          {value.trim() ? (secret ? "•••••••• (kept hidden)" : value.trim()) : "Not answered"}
        </p>
      </li>
    );
  }

  return (
    <li className="grid content-start gap-1.5">
      <label htmlFor={fieldId} className="flex gap-2 text-sm font-medium">
        <span className="w-6 shrink-0 text-muted-foreground tabular-nums">{number}.</span>
        <span>{item.question}</span>
      </label>
      <div className="grid gap-1.5 pl-8">
        {hasOptions ? (
          <>
            <Select
              items={selectItems}
              value={showOther ? OTHER : value || null}
              disabled={disabled}
              onValueChange={(next) => {
                if (next === OTHER) {
                  setOther(true);
                  if (item.options.includes(value)) onChange("");
                } else {
                  setOther(false);
                  onChange((next as string | null) ?? "");
                }
              }}
            >
              <SelectTrigger id={fieldId} className="w-full sm:w-80">
                <SelectValue placeholder="Choose an answer" />
              </SelectTrigger>
              <SelectContent>
                {selectItems.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {showOther && (
              <Input
                aria-label={`Other answer for question ${number}`}
                placeholder="Type your answer"
                value={value}
                disabled={disabled}
                onChange={(e) => onChange(e.target.value)}
              />
            )}
          </>
        ) : secret ? (
          <>
            <div className="flex gap-2">
              <Input
                id={fieldId}
                type={revealed ? "text" : "password"}
                // "new-password" stops the browser filling in a saved login, which would be stored as the answer.
                autoComplete="new-password"
                data-1p-ignore
                data-lpignore="true"
                spellCheck={false}
                value={value}
                disabled={disabled}
                className="sm:max-w-sm"
                onChange={(e) => onChange(e.target.value)}
              />
              <Button type="button" variant="outline" disabled={disabled} onClick={() => setRevealed((shown) => !shown)}>
                {revealed ? "Hide" : "Show"}
              </Button>
            </div>
            <span className="text-xs text-muted-foreground">
              Stored with this checklist. Not sent to the AI and not written into the Excel file.
            </span>
          </>
        ) : senderField ? (
          <Input
            id={fieldId}
            type={senderField.type}
            inputMode={senderField.inputMode}
            placeholder={senderField.placeholder}
            autoComplete="off"
            spellCheck={false}
            value={value}
            disabled={disabled}
            className="sm:max-w-md"
            onChange={(e) => onChange(e.target.value)}
          />
        ) : (
          <Textarea
            id={fieldId}
            rows={2}
            value={value}
            disabled={disabled}
            onChange={(e) => onChange(e.target.value)}
          />
        )}
        {prefilled && (
          <span className="w-fit rounded-md bg-amber-500/10 px-1.5 py-0.5 text-xs text-amber-700 dark:text-amber-400">
            Prefilled from documents — please confirm
          </span>
        )}
      </div>
    </li>
  );
}
