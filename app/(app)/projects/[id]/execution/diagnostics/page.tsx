import { redirect } from "next/navigation";
import type { ExecutionPageProps } from "../load";

/**
 * Diagnostics was folded into the Activity log (its "Right now" section). This route stays only to
 * redirect old links and bookmarks to the merged page, keeping the selected run.
 */
export default async function DiagnosticsPage({ params, searchParams }: ExecutionPageProps) {
  const { id } = await params;
  const { run } = await searchParams;
  redirect(`/projects/${id}/execution/activity${run ? `?run=${encodeURIComponent(run)}` : ""}`);
}
