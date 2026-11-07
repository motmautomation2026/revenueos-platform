"use server";

import { requireUser } from "@/lib/auth";
import { pingTools, type ToolReach } from "@/lib/brain/tool-health";

/**
 * On-demand reachability check for the tool hosts (Phase 2a). Signed-in users only. Safe: GET-only pings,
 * no credentials and no side effects, so it is not gated to the project owner.
 */
export async function checkToolHealth(): Promise<ToolReach[]> {
  await requireUser();
  return pingTools();
}
