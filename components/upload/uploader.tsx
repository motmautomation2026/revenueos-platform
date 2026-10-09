"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileText,
  Loader2,
  RotateCw,
  Sparkles,
  Trash2,
  UploadCloud,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
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
  fileProblem,
  guessDocType,
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

  return (
    <div className="grid gap-6">
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
          "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-10 text-center outline-none transition-colors focus-visible:border-ring",
          dragging ? "border-primary bg-muted" : "hover:bg-muted/50"
        )}
      >
        <UploadCloud className="size-8 text-muted-foreground" aria-hidden />
        <p className="font-medium">Drop documents here, or click to choose</p>
        <p className="text-sm text-muted-foreground">
          PDF, DOCX, XLSX, CSV, TXT or MD · up to 20 MB each · up to {MAX_FILES_PER_PROJECT} files
        </p>
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

      {items.length > 0 && (
        <ul className="divide-y rounded-xl border">
          {items.map((item) => (
            <li key={item.key} className="grid gap-2 px-4 py-3 sm:grid-cols-[1fr_auto] sm:items-center">
              <div className="flex min-w-0 items-start gap-3">
                <FileText className="mt-0.5 size-5 shrink-0 text-muted-foreground" aria-hidden />
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
              <div className="flex items-center gap-2 pl-8 sm:pl-0">
                {(item.phase === "failed" || item.phase === "pending") &&
                  (item.fileId || item.source) && (
                    <Button variant="outline" size="sm" onClick={() => retry(item)}>
                      <RotateCw aria-hidden />
                      {item.phase === "pending" ? "Extract text" : "Retry"}
                    </Button>
                  )}
                <Select
                  items={DOC_TYPES}
                  value={item.docType}
                  onValueChange={(value) => changeDocType(item, value as DocType)}
                >
                  <SelectTrigger size="sm" className="w-40" aria-label="Document type">
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
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete ${item.fileName}`}
                  disabled={item.phase === "uploading" || item.phase === "extracting"}
                  onClick={() => remove(item)}
                >
                  <Trash2 aria-hidden />
                </Button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {items.length > 0 && (
        <div className="grid gap-3">
          <p className="text-sm text-muted-foreground">
            {items.length} of {MAX_FILES_PER_PROJECT} files · {number.format(totalChars)} characters
            in total
          </p>
          {totalChars > TOTAL_CHARS_WARNING && (
            <p className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600" aria-hidden />
              The documents add up to more than {number.format(TOTAL_CHARS_WARNING)} characters,
              which is a lot for the AI to read in one go. Consider removing files that are not
              needed.
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-3">
        <Button disabled={!ready || busy || generating || generatingElsewhere} onClick={generate}>
          {generating || generatingElsewhere ? (
            <Loader2 className="animate-spin" aria-hidden />
          ) : (
            <Sparkles aria-hidden />
          )}
          {generating || generatingElsewhere
            ? "Writing checklist…"
            : hasChecklist
              ? "Generate a new checklist"
              : "Generate checklist"}
        </Button>
        {generating || generatingElsewhere ? (
          <p className="text-sm text-muted-foreground">
            The AI is reading the documents. This usually takes one to three minutes.
          </p>
        ) : !ready ? (
          <p className="text-sm text-muted-foreground">
            Needs at least one file with extracted text, and at least one BD handover or BD
            proposal.
          </p>
        ) : hasChecklist ? (
          <p className="text-sm text-muted-foreground">
            This replaces the current checklist; the old one is kept on record.
          </p>
        ) : null}
        {hasChecklist && !generating && !generatingElsewhere && (
          <Link
            href={`/projects/${projectId}/checklist`}
            className={buttonVariants({ variant: "outline", className: "ml-auto" })}
          >
            Go to checklist
            <ArrowRight aria-hidden />
          </Link>
        )}
      </div>
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
