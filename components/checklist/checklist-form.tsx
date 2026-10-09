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
}: {
  projectId: string;
  items: ChecklistItemView[];
  submitted: boolean;
  canReopen: boolean;
  canRegenerate: boolean;
  countWarning: string | null;
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

  return (
    <div className="grid gap-6">
      <div className="grid gap-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h2 className="text-lg font-semibold">Discovery checklist</h2>
          {submitted && <Badge>Submitted</Badge>}
          {countWarning && (
            <Badge variant="outline" className="border-amber-500/50 text-amber-700 dark:text-amber-400">
              <AlertTriangle aria-hidden />
              {countWarning}
            </Badge>
          )}
          <SaveIndicator state={saveState} />
        </div>

        <div className="grid gap-1.5">
          <div
            className="h-2 overflow-hidden rounded-full bg-muted"
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={items.length}
            aria-valuenow={answered}
            aria-label="Questions answered"
          >
            <div className="h-full bg-primary transition-[width]" style={{ width: `${percent}%` }} />
          </div>
          <p className="text-sm text-muted-foreground">
            {answered} of {items.length} answered
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <a
            href={`/api/projects/${projectId}/checklist/export`}
            className={buttonVariants({ variant: "outline", size: "sm" })}
            onClick={() => void flush()}
          >
            <Download aria-hidden />
            Download Excel
          </a>
          {!submitted && (
            <Button
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => fileInput.current?.click()}
            >
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
            <Button
              variant="outline"
              size="sm"
              disabled={busy !== null}
              onClick={() => setDialog("regenerate")}
            >
              {busy === "regenerate" ? (
                <Loader2 className="animate-spin" aria-hidden />
              ) : (
                <RefreshCw aria-hidden />
              )}
              {busy === "regenerate" ? "Writing new checklist…" : "Regenerate checklist"}
            </Button>
          )}
        </div>
        {busy === "regenerate" && (
          <p className="text-sm text-muted-foreground">
            The AI is reading the documents again. This usually takes one to three minutes.
          </p>
        )}
      </div>

      {groups.map(([category, categoryItems]) => (
        <section key={category} className="grid gap-3">
          <h3 className="flex items-baseline justify-between gap-3 border-b pb-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <span>{category}</span>
            <span className="text-xs font-normal tracking-normal normal-case tabular-nums">
              {categoryItems.filter((item) => answers[item.id]?.trim()).length} of {categoryItems.length}
            </span>
          </h3>
          <ol className="grid gap-4">
            {categoryItems.map((item) => (
              <QuestionRow
                key={item.id}
                item={item}
                value={answers[item.id] ?? ""}
                disabled={locked}
                onChange={(value) => setAnswer(item.id, value)}
              />
            ))}
          </ol>
        </section>
      ))}

      <div className="sticky bottom-0 z-10 -mx-4 -mb-4 flex flex-wrap items-center gap-3 rounded-b-xl border-t bg-background/95 px-4 py-3 backdrop-blur sm:-mx-6 sm:-mb-6 sm:px-6">
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

function QuestionRow({
  item,
  value,
  disabled,
  onChange,
}: {
  item: ChecklistItemView;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const hasOptions = item.options.length > 0;
  const [other, setOther] = useState(() => hasOptions && !!value && !item.options.includes(value));
  // An Excel import can bring in an answer that is not one of the options.
  const showOther = other || (hasOptions && !!value && !item.options.includes(value));
  const fieldId = `answer-${item.id}`;
  const prefilled = !!item.prefilled_answer && value === item.prefilled_answer;

  const selectItems = useMemo(
    () => [
      ...item.options.map((option) => ({ value: option, label: option })),
      { value: OTHER, label: "Other…" },
    ],
    [item.options]
  );

  return (
    <li className="grid gap-1.5">
      <label htmlFor={fieldId} className="flex gap-2 text-sm font-medium">
        <span className="shrink-0 text-muted-foreground tabular-nums">{item.code.replace("Q-", "")}.</span>
        <span>{item.question}</span>
      </label>
      <div className="grid gap-1.5 pl-6">
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
                aria-label={`Other answer for question ${item.code}`}
                placeholder="Type your answer"
                value={value}
                disabled={disabled}
                onChange={(e) => onChange(e.target.value)}
              />
            )}
          </>
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
