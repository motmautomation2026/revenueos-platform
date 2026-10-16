"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  ArrowRight,
  CheckCircle2,
  Circle,
  Loader2,
  RotateCw,
  Sparkles,
  Trash2,
  UploadCloud,
  XCircle,
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
import { Button, buttonVariants } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { createClient } from "@/lib/supabase/client";
import { STORAGE_BUCKET } from "@/lib/supabase/tables";
import {
  ACCEPT_ATTRIBUTE,
  DOC_TYPES,
  MAX_FILES_PER_PROJECT,
  TOTAL_CHARS_WARNING,
  canGenerateChecklist,
  fileExtension,
  fileProblem,
  guessDocType,
  TRUST,
  trustOf,
  sanitizeFileName,
  type DocType,
  type ProjectFileRow,
} from "@/lib/files/rules";
import {
  deleteFile,
  extractFile,
  registerFile,
  updateDocType,
} from "@/app/(app)/projects/[id]/upload/actions";
import { generateChecklistAction } from "@/app/(app)/projects/[id]/checklist/actions";

type Phase = "uploading" | "extracting" | "ready" | "failed" | "pending";

type Item = {
  /** Local key; equals the database id once the file is registered. */
  key: string;
  fileId: string | null;
  fileName: string;
  sizeBytes: number | null;
  docType: DocType;
  charCount: number;
  phase: Phase;
  error: string | null;
  /** Kept only while an upload can still be retried from the browser. */
  source?: File;
};

const number = new Intl.NumberFormat("en-IN");

function formatBytes(bytes: number | null) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function fromRow(row: ProjectFileRow): Item {
  return {
    key: row.id,
    fileId: row.id,
    fileName: row.file_name,
    sizeBytes: row.size_bytes,
    docType: row.doc_type,
    charCount: row.char_count,
    phase:
      row.extraction_status === "done"
        ? "ready"
        : row.extraction_status === "failed"
          ? "failed"
          : "pending",
    error: row.extraction_error,
  };
}

export function Uploader({
  projectId,
  ownerId,
  initialFiles,
  hasChecklist,
  generatingElsewhere,
}: {
  projectId: string;
  ownerId: string;
  initialFiles: ProjectFileRow[];
  /** A checklist already exists; generating again replaces it. */
  hasChecklist: boolean;
  /** A generation started in another tab or before a reload is still running. */
  generatingElsewhere: boolean;
}) {
  const router = useRouter();
  const [generating, setGenerating] = useState(false);
  const [confirming, setConfirming] = useState(false);

  // Nothing here is waiting on the other run, so poll until the project status moves on.
  useEffect(() => {
    if (!generatingElsewhere) return;
    const timer = setInterval(() => router.refresh(), 5000);
    return () => clearInterval(timer);
  }, [generatingElsewhere, router]);

  const generate = async () => {
    setGenerating(true);
    try {
      const result = await generateChecklistAction(projectId);
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
      toast.success(`Checklist written with ${result.questionCount} questions.`);
      router.push(`/projects/${projectId}/checklist`);
      router.refresh();
    } catch {
      toast.error("Writing the checklist did not finish. Refresh the page to check.");
    } finally {
      setGenerating(false);
    }
  };

  const [items, setItems] = useState<Item[]>(() => initialFiles.map(fromRow));
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const itemsRef = useRef(items);
  itemsRef.current = items;

  const patch = useCallback((key: string, changes: Partial<Item>) => {
    setItems((current) => current.map((it) => (it.key === key ? { ...it, ...changes } : it)));
  }, []);

  const runExtraction = useCallback(
    async (key: string, fileId: string) => {
      patch(key, { phase: "extracting", error: null });
      try {
        const result = await extractFile({ projectId, fileId });
        if (!result.ok) {
          patch(key, { phase: "failed", error: result.error });
          return;
        }
        patch(key, { ...fromRow(result.file), key });
      } catch {
        patch(key, { phase: "failed", error: "Text extraction did not finish. Try again." });
      }
    },
    [patch, projectId]
  );

  const uploadOne = useCallback(
    async (key: string, file: File, docType: DocType) => {
      patch(key, { phase: "uploading", error: null });
      const storagePath = `${ownerId}/${projectId}/${crypto.randomUUID()}-${sanitizeFileName(file.name)}`;

      const { error: uploadError } = await createClient()
        .storage.from(STORAGE_BUCKET)
        .upload(storagePath, file, { contentType: file.type || undefined, upsert: false });
      if (uploadError) {
        patch(key, { phase: "failed", error: `Upload failed: ${uploadError.message}` });
        return;
      }

      let registered;
      try {
        registered = await registerFile({
          projectId,
          storagePath,
          fileName: file.name,
          mimeType: file.type,
          sizeBytes: file.size,
          docType,
        });
      } catch {
        patch(key, { phase: "failed", error: "Could not save the file. Try again." });
        return;
      }
      if (!registered.ok) {
        patch(key, { phase: "failed", error: registered.error });
        return;
      }

      const fileId = registered.file.id;
      // The user may have changed the type while the upload was in flight.
      const chosen = itemsRef.current.find((it) => it.key === key)?.docType ?? docType;
      patch(key, { fileId, source: undefined });
      if (chosen !== docType) void updateDocType({ projectId, fileId, docType: chosen });
      await runExtraction(key, fileId);
    },
    [ownerId, patch, projectId, runExtraction]
  );

  const addFiles = useCallback(
    (fileList: FileList | File[]) => {
      const files = Array.from(fileList);
      const room = MAX_FILES_PER_PROJECT - itemsRef.current.length;
      if (files.length > room) {
        toast.error(
          room <= 0
            ? `This project already has ${MAX_FILES_PER_PROJECT} files. Remove one first.`
            : `Only ${room} more file${room === 1 ? "" : "s"} can be added (max ${MAX_FILES_PER_PROJECT}).`
        );
      }

      const accepted: { item: Item; file: File }[] = [];
      for (const file of files.slice(0, Math.max(room, 0))) {
        const problem = fileProblem(file);
        if (problem) {
          toast.error(`${file.name}: ${problem}`);
          continue;
        }
        accepted.push({
          file,
          item: {
            key: crypto.randomUUID(),
            fileId: null,
            fileName: file.name,
            sizeBytes: file.size,
            docType: guessDocType(file.name),
            charCount: 0,
            phase: "uploading",
            error: null,
            source: file,
          },
        });
      }
      if (!accepted.length) return;

      setItems((current) => [...current, ...accepted.map((a) => a.item)]);
      for (const { item, file } of accepted) void uploadOne(item.key, file, item.docType);
    },
    [uploadOne]
  );

  const changeDocType = async (item: Item, docType: DocType) => {
    const previous = item.docType;
    patch(item.key, { docType });
    if (!item.fileId) return; // still uploading: applied once the file is registered
    const result = await updateDocType({ projectId, fileId: item.fileId, docType });
    if (!result.ok) {
      patch(item.key, { docType: previous });
      toast.error(result.error);
    }
  };

  const remove = async (item: Item) => {
    if (item.fileId) {
      const result = await deleteFile({ projectId, fileId: item.fileId });
      if (!result.ok) {
        toast.error(result.error);
        return;
      }
    }
    setItems((current) => current.filter((it) => it.key !== item.key));
  };

  const retry = (item: Item) => {
    if (item.fileId) void runExtraction(item.key, item.fileId);
    else if (item.source) void uploadOne(item.key, item.source, item.docType);
  };

  const totalChars = useMemo(() => items.reduce((sum, it) => sum + it.charCount, 0), [items]);
  const busy = items.some((it) => it.phase === "uploading" || it.phase === "extracting");
  const ready = canGenerateChecklist(
    items.map((it) => ({
      doc_type: it.docType,
      extraction_status: it.phase === "ready" ? "done" : "pending",
    }))
  );

  const hasBrief = items.some((it) => it.docType === "bd_handover" || it.docType === "bd_proposal");
  const hasText = items.some((it) => it.phase === "ready");
  const working = generating || generatingElsewhere;
  const overLimit = totalChars > TOTAL_CHARS_WARNING;
  const fill = Math.min(100, Math.round((totalChars / TOTAL_CHARS_WARNING) * 100));

  const dropZone = (
    <div
      role="button"
      tabIndex={0}
      aria-label="Add documents"
      onClick={() => inputRef.current?.click()}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        addFiles(e.dataTransfer.files);
      }}
      className={cn(
        "flex cursor-pointer items-center gap-4 rounded-xl border-2 border-dashed text-left outline-none transition-colors focus-visible:border-ring",
        // Large and central while empty; a slim "add more" strip once documents are in.
        items.length === 0 ? "flex-col px-6 py-14 text-center" : "px-4 py-3",
        dragging ? "border-primary bg-primary/5" : "hover:border-primary/50 hover:bg-muted/50"
      )}
    >
      <span
        className={cn(
          "flex shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary",
          items.length === 0 ? "size-14" : "size-10"
        )}
      >
        <UploadCloud className={items.length === 0 ? "size-7" : "size-5"} aria-hidden />
      </span>
      <div className="min-w-0 flex-1">
        <p className="font-medium">
          {items.length === 0 ? "Drop documents here, or click to choose" : "Add more documents"}
        </p>
        <p className="text-sm text-muted-foreground">
          PDF, DOCX, PPTX, XLSX, CSV, TXT or MD · up to 20 MB each · up to {MAX_FILES_PER_PROJECT} files
        </p>
      </div>
      {items.length > 0 && <span className={buttonVariants({ variant: "outline" })}>Choose files</span>}
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT_ATTRIBUTE}
        className="hidden"
        onChange={(e) => {
          if (e.target.files) addFiles(e.target.files);
          e.target.value = "";
        }}
      />
    </div>
  );

  return (
    <div className="grid gap-6">
      {items.length === 0 ? (
        dropZone
      ) : (
        <>
          <div className="grid gap-3 md:grid-cols-3">
            <div className="grid content-start gap-1 rounded-xl border p-4">
              <p className="text-sm text-muted-foreground">Documents</p>
              <p className="text-2xl leading-none font-semibold tabular-nums">
                {items.length}
                <span className="text-base font-normal text-muted-foreground"> / {MAX_FILES_PER_PROJECT}</span>
              </p>
            </div>

            <div className={cn("grid content-start gap-2 rounded-xl border p-4", overLimit && "border-amber-500/60 bg-amber-500/5")}>
              <p className="text-sm text-muted-foreground">Text for the AI to read</p>
              <p className="text-2xl leading-none font-semibold tabular-nums">
                {number.format(totalChars)}
                <span className="text-base font-normal text-muted-foreground"> characters</span>
              </p>
              <div className="h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                <div className={cn("h-full rounded-full", overLimit ? "bg-amber-600" : "bg-primary")} style={{ width: `${fill}%` }} />
              </div>
              <p className="text-xs text-muted-foreground">
                {overLimit
                  ? "More than the AI reads well in one go. Remove files that are not needed."
                  : `Comfortable up to ${number.format(TOTAL_CHARS_WARNING)}`}
              </p>
            </div>

            <div className="grid content-start gap-2 rounded-xl border p-4">
              <p className="text-sm text-muted-foreground">Needed for the checklist</p>
              <ul className="grid gap-1.5 text-sm">
                {[
                  { met: hasBrief, label: "A BD handover or a BD proposal" },
                  { met: hasText, label: "At least one document read successfully" },
                ].map((need) => (
                  <li key={need.label} className="flex items-start gap-2">
                    {need.met ? (
                      <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-700" aria-hidden />
                    ) : (
                      <Circle className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    <span className={need.met ? undefined : "text-muted-foreground"}>
                      {need.label}
                      <span className="sr-only">{need.met ? " (done)" : " (missing)"}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <section className="grid gap-3">
            <h3 className="text-base font-semibold">Uploaded documents</h3>
            <ul className="divide-y rounded-xl border">
              {items.map((item) => (
                <li key={item.key} className="grid gap-3 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
                  <div className="flex min-w-0 items-center gap-3">
                    <span
                      aria-hidden
                      className="flex h-10 w-11 shrink-0 items-center justify-center rounded-lg bg-muted text-[0.65rem] font-bold tracking-wide text-muted-foreground uppercase"
                    >
                      {fileExtension(item.fileName) || "file"}
                    </span>
                    <div className="min-w-0">
                      <p className="truncate font-medium" title={item.fileName}>
                        {item.fileName}
                      </p>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-sm text-muted-foreground">
                        <span>{formatBytes(item.sizeBytes)}</span>
                        <PhaseLabel item={item} />
                      </div>
                      {item.error && (
                        <p role="alert" className="mt-1 text-sm text-destructive">
                          {item.error}
                        </p>
                      )}
                    </div>
                  </div>
                  <div className="flex items-end gap-2">
                    {(item.phase === "failed" || item.phase === "pending") && (item.fileId || item.source) && (
                      <Button variant="outline" onClick={() => retry(item)}>
                        <RotateCw aria-hidden />
                        {item.phase === "pending" ? "Extract text" : "Retry"}
                      </Button>
                    )}
                    <div className="grid gap-1">
                      <span className="text-xs text-muted-foreground">Document type</span>
                      <Select
                        items={DOC_TYPES}
                        value={item.docType}
                        onValueChange={(value) => changeDocType(item, value as DocType)}
                      >
                        <SelectTrigger className="w-52" aria-label={`Document type of ${item.fileName}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {DOC_TYPES.map((type) => (
                            <SelectItem key={type.value} value={type.value}>
                              {type.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                      <span className="text-xs text-muted-foreground" title={TRUST[trustOf(item.docType)].rule}>
                        AI uses it as: {TRUST[trustOf(item.docType)].label.toLowerCase()}
                      </span>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Delete ${item.fileName}`}
                      title="Delete"
                      disabled={item.phase === "uploading" || item.phase === "extracting"}
                      onClick={() => remove(item)}
                    >
                      <Trash2 aria-hidden />
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
            {dropZone}
          </section>
        </>
      )}

      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border bg-muted/40 p-4">
        <div className="min-w-0 flex-1 basis-72">
          <p className="font-semibold">
            {working
              ? "Writing the checklist…"
              : hasChecklist
                ? "The checklist is already written"
                : ready
                  ? "Ready to write the checklist"
                  : "Not ready yet"}
          </p>
          <p className="text-sm text-muted-foreground">
            {working
              ? "The AI is reading the documents. This usually takes one to three minutes."
              : hasChecklist
                ? "Continue to the checklist. Generate a new one only if the documents have changed."
                : ready
                  ? "The AI reads every document above and writes 40 to 50 questions for this customer."
                  : "Add a BD handover or BD proposal, and wait until at least one document shows Ready."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {hasChecklist ? (
            <>
              <Button variant="outline" disabled={!ready || busy || working} onClick={() => setConfirming(true)}>
                {working ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
                {working ? "Writing…" : "Generate a new checklist"}
              </Button>
              <Link href={`/projects/${projectId}/checklist`} className={buttonVariants({ size: "lg" })}>
                Go to checklist
                <ArrowRight aria-hidden />
              </Link>
            </>
          ) : (
            <Button size="lg" disabled={!ready || busy || working} onClick={generate}>
              {working ? <Loader2 className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
              {working ? "Writing checklist…" : "Generate checklist"}
            </Button>
          )}
        </div>
      </div>

      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Write a new checklist?</AlertDialogTitle>
            <AlertDialogDescription>
              The AI reads the documents again and writes a fresh checklist. The current checklist and its
              answers are kept on record but will no longer be the active one, so the answers must be filled in
              again. The plan already written is not changed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <Button
              onClick={() => {
                setConfirming(false);
                void generate();
              }}
            >
              Generate a new checklist
            </Button>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function PhaseLabel({ item }: { item: Item }) {
  switch (item.phase) {
    case "uploading":
      return (
        <span className="inline-flex items-center gap-1">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Uploading…
        </span>
      );
    case "extracting":
      return (
        <span className="inline-flex items-center gap-1">
          <Loader2 className="size-3.5 animate-spin" aria-hidden />
          Extracting text…
        </span>
      );
    case "ready":
      return (
        <span className="inline-flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
          <CheckCircle2 className="size-3.5" aria-hidden />
          Ready · {number.format(item.charCount)} characters
        </span>
      );
    case "failed":
      return (
        <span className="inline-flex items-center gap-1 text-destructive">
          <XCircle className="size-3.5" aria-hidden />
          Failed
        </span>
      );
    case "pending":
      return <span>Text not extracted yet</span>;
  }
}
