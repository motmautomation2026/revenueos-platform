import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";

// The project's edited AI calling script (revenueos_brain_accounts.calls_welcome_message /
// calls_system_prompt / calls_script_language). Read by the Brain when it builds a campaign; when set, it
// uses this text instead of writing the script with the LLM. Service-role client, only after requireProject
// has confirmed the signed-in user owns the project.

const TABLE = "revenueos_brain_accounts";
const MAX = 6000;

export type ScriptState = {
  /** false until supabase/migrations/0019_calls_agent_script.sql is applied. */
  available: boolean;
  /** The saved opening line, or "" when the script has not been edited. */
  welcomeMessage: string;
  /** The saved agent instructions, or "". */
  systemPrompt: string;
  /** true when the project has an edited script (both fields set). */
  isCustom: boolean;
};

const EMPTY: ScriptState = { available: false, welcomeMessage: "", systemPrompt: "", isCustom: false };

export const loadScript = cache(async (projectId: string): Promise<ScriptState> => {
  try {
    const { data, error } = await createAdminClient()
      .from(TABLE)
      .select("calls_welcome_message, calls_system_prompt")
      .eq("project_id", projectId)
      .maybeSingle<{ calls_welcome_message: string | null; calls_system_prompt: string | null }>();
    if (error) return EMPTY; // columns missing (migration not applied) or no access
    const welcome = (data?.calls_welcome_message ?? "").trim();
    const system = (data?.calls_system_prompt ?? "").trim();
    return { available: true, welcomeMessage: welcome, systemPrompt: system, isCustom: !!(welcome && system) };
  } catch {
    return EMPTY;
  }
});

type Result = { ok: true; message: string } | { ok: false; error: string };

export async function saveScript(
  projectId: string,
  ownerId: string,
  welcomeMessage: string,
  systemPrompt: string,
  language: string
): Promise<Result> {
  const welcome = (welcomeMessage ?? "").trim();
  const system = (systemPrompt ?? "").trim();
  if (!welcome || !system) {
    return { ok: false, error: "Both the opening line and what the agent says are needed." };
  }
  if (welcome.length > MAX || system.length > MAX) {
    return { ok: false, error: "The script is too long." };
  }
  const { error } = await createAdminClient().from(TABLE).upsert(
    {
      project_id: projectId,
      updated_by: ownerId,
      calls_welcome_message: welcome,
      calls_system_prompt: system,
      calls_script_language: (language ?? "").trim() || null,
    },
    { onConflict: "project_id" }
  );
  if (error) return { ok: false, error: "The script could not be saved. Try again." };
  return { ok: true, message: "Call script saved. It applies to the next call campaign." };
}

/** Clear the edit so the Brain writes the script automatically again. */
export async function resetScript(projectId: string, ownerId: string): Promise<Result> {
  const { error } = await createAdminClient().from(TABLE).upsert(
    {
      project_id: projectId,
      updated_by: ownerId,
      calls_welcome_message: null,
      calls_system_prompt: null,
      calls_script_language: null,
    },
    { onConflict: "project_id" }
  );
  if (error) return { ok: false, error: "The script could not be reset. Try again." };
  return { ok: true, message: "The agent will write its own script again for the next campaign." };
}
