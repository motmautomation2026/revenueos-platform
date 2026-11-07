"use server";

import { requireProject } from "@/lib/auth";
import { placeTestCall, sendTestEmail, type TestResult } from "@/lib/tools/test-tools";

/** Place a test call. requireProject checks the signed-in user can access this project. */
export async function testCall(input: unknown): Promise<TestResult> {
  const { projectId, target } = (input ?? {}) as { projectId?: string; target?: string };
  if (typeof projectId !== "string" || typeof target !== "string") return { ok: false, detail: "Invalid request." };
  await requireProject(projectId);
  return placeTestCall(projectId, target.trim());
}

export async function testEmail(input: unknown): Promise<TestResult> {
  const { projectId, target } = (input ?? {}) as { projectId?: string; target?: string };
  if (typeof projectId !== "string" || typeof target !== "string") return { ok: false, detail: "Invalid request." };
  await requireProject(projectId);
  return sendTestEmail(projectId, target.trim());
}
