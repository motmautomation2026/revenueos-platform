"use server";

import { z } from "zod";
import { requireProject } from "@/lib/auth";
import { ExtractionError, extractText } from "@/lib/files/extract";
import {
  DOC_TYPE_VALUES,
  MAX_FILES_PER_PROJECT,
  PROJECT_FILE_COLUMNS,
  fileProblem,
  type ProjectFileRow,
} from "@/lib/files/rules";
import { STORAGE_BUCKET, TABLES } from "@/lib/supabase/tables";

type Result<T = unknown> = ({ ok: true } & T) | { ok: false; error: string };

const INVALID = { ok: false, error: "Invalid request." } as const;
const id = z.uuid();

const registerSchema = z.object({
  projectId: id,
  storagePath: z.string().min(1).max(500),
  fileName: z.string().trim().min(1).max(255),
  mimeType: z.string().max(200),
  sizeBytes: z.number().int().positive(),
  docType: z.enum(DOC_TYPE_VALUES),
});

/** Records a file the browser has just uploaded to storage. Extraction is a separate call. */
export async function registerFile(input: unknown): Promise<Result<{ file: ProjectFileRow }>> {
  const parsed = registerSchema.safeParse(input);
  if (!parsed.success) return INVALID;
  const { projectId, storagePath, fileName, mimeType, sizeBytes, docType } = parsed.data;
  const { supabase, user, project } = await requireProject(projectId);

  const removeUpload = () => supabase.storage.from(STORAGE_BUCKET).remove([storagePath]);

  // The object must sit in this user's folder for this project, and nowhere else.
  const folder = `${user.id}/${project.id}/`;
  const objectName = storagePath.slice(folder.length);
  if (!storagePath.startsWith(folder) || !objectName || /[\\/]|\.\./.test(objectName)) {
    return INVALID;
  }

  const problem = fileProblem({ name: fileName, type: mimeType, size: sizeBytes });
  if (problem) {
    await removeUpload();
    return { ok: false, error: problem };
  }

  const { count } = await supabase
    .from(TABLES.projectFiles)
    .select("id", { count: "exact", head: true })
    .eq("project_id", project.id);
  if ((count ?? 0) >= MAX_FILES_PER_PROJECT) {
    await removeUpload();
    return { ok: false, error: `A project can hold at most ${MAX_FILES_PER_PROJECT} files.` };
  }

  const { data: file, error } = await supabase
    .from(TABLES.projectFiles)
    .insert({
      project_id: project.id,
      owner_id: user.id,
      doc_type: docType,
      file_name: fileName,
      storage_path: storagePath,
      mime_type: mimeType || null,
      size_bytes: sizeBytes,
    })
    .select(PROJECT_FILE_COLUMNS)
    .single<ProjectFileRow>();
  if (error || !file) {
    await removeUpload();
    return { ok: false, error: "Could not save the file. Please try again." };
  }

  if (project.status === "draft") {
    await supabase.from(TABLES.projects).update({ status: "uploading" }).eq("id", project.id);
  }
  return { ok: true, file };
}

const fileRefSchema = z.object({ projectId: id, fileId: id });

/** Downloads the stored file, extracts its text on the server and saves it. Safe to retry. */
export async function extractFile(input: unknown): Promise<Result<{ file: ProjectFileRow }>> {
  const parsed = fileRefSchema.safeParse(input);
  if (!parsed.success) return INVALID;
  const { supabase, project } = await requireProject(parsed.data.projectId);

  const { data: row } = await supabase
    .from(TABLES.projectFiles)
    .select("id, file_name, storage_path")
    .eq("id", parsed.data.fileId)
    .eq("project_id", project.id)
    .maybeSingle<{ id: string; file_name: string; storage_path: string }>();
  if (!row) return { ok: false, error: "File not found." };

  let update: Record<string, unknown>;
  try {
    const { data: blob, error } = await supabase.storage
      .from(STORAGE_BUCKET)
      .download(row.storage_path);
    if (error || !blob) throw new ExtractionError("Could not download the file from storage.");

    const text = await extractText(Buffer.from(await blob.arrayBuffer()), row.file_name);
    update = {
      extracted_text: text,
      char_count: text.length,
      extraction_status: "done",
      extraction_error: null,
    };
  } catch (error) {
    update = {
      extracted_text: null,
      char_count: 0,
      extraction_status: "failed",
      extraction_error:
        error instanceof ExtractionError ? error.message : "Text extraction failed unexpectedly.",
    };
  }

  const { data: file, error: saveError } = await supabase
    .from(TABLES.projectFiles)
    .update(update)
    .eq("id", row.id)
    .select(PROJECT_FILE_COLUMNS)
    .single<ProjectFileRow>();
  if (saveError || !file) return { ok: false, error: "Could not save the extracted text." };
  return { ok: true, file };
}

const docTypeSchema = fileRefSchema.extend({ docType: z.enum(DOC_TYPE_VALUES) });

export async function updateDocType(input: unknown): Promise<Result> {
  const parsed = docTypeSchema.safeParse(input);
  if (!parsed.success) return INVALID;
  const { supabase, project } = await requireProject(parsed.data.projectId);

  const { error } = await supabase
    .from(TABLES.projectFiles)
    .update({ doc_type: parsed.data.docType })
    .eq("id", parsed.data.fileId)
    .eq("project_id", project.id);
  return error ? { ok: false, error: "Could not change the document type." } : { ok: true };
}

export async function deleteFile(input: unknown): Promise<Result> {
  const parsed = fileRefSchema.safeParse(input);
  if (!parsed.success) return INVALID;
  const { supabase, project } = await requireProject(parsed.data.projectId);

  const { data: row, error } = await supabase
    .from(TABLES.projectFiles)
    .delete()
    .eq("id", parsed.data.fileId)
    .eq("project_id", project.id)
    .select("storage_path")
    .maybeSingle<{ storage_path: string }>();
  if (error) return { ok: false, error: "Could not delete the file." };
  if (row) await supabase.storage.from(STORAGE_BUCKET).remove([row.storage_path]);
  return { ok: true };
}
