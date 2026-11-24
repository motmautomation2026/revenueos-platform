import "server-only";
import { cache } from "react";
import { createAdminClient } from "@/lib/supabase/admin";
import { DEFAULT_LANGUAGE, DEFAULT_VOICE_ID, findVoice, type ChosenVoice } from "./voices";

// The project's chosen calling voice (revenueos_brain_accounts.calls_voice_id / calls_language). Read by
// the Brain when it builds a campaign. Service-role client, only after requireProject has confirmed the
// signed-in user owns the project.

const TABLE = "revenueos_brain_accounts";

export type VoiceState = {
  /** false until supabase/migrations/0018_calls_agent_voice.sql is applied. */
  available: boolean;
  /** The saved voice, or the default when nothing is set. */
  chosen: ChosenVoice;
  /** true when the project has explicitly picked a voice (not the default). */
  isCustom: boolean;
};

const fallback = (available: boolean): VoiceState => ({
  available,
  chosen: { voiceId: DEFAULT_VOICE_ID, language: DEFAULT_LANGUAGE },
  isCustom: false,
});

export const loadVoice = cache(async (projectId: string): Promise<VoiceState> => {
  try {
    const { data, error } = await createAdminClient()
      .from(TABLE)
      .select("calls_voice_id, calls_language")
      .eq("project_id", projectId)
      .maybeSingle<{ calls_voice_id: string | null; calls_language: string | null }>();
    if (error) return fallback(false); // column missing (migration not applied) or no access
    const saved = (data?.calls_voice_id ?? "").trim();
    const found = saved ? findVoice(saved) : null;
    if (!found) return fallback(true);
    return { available: true, chosen: { voiceId: found.voice.id, language: found.language.language }, isCustom: true };
  } catch {
    return fallback(false);
  }
});

export async function saveVoice(
  projectId: string,
  ownerId: string,
  voiceId: string
): Promise<{ ok: true; message: string } | { ok: false; error: string }> {
  const found = findVoice((voiceId ?? "").trim());
  if (!found) return { ok: false, error: "That voice is not available." };
  const { error } = await createAdminClient()
    .from(TABLE)
    .upsert(
      {
        project_id: projectId,
        updated_by: ownerId,
        calls_voice_id: found.voice.id,
        calls_language: found.language.language,
        // Stored so the Brain can name the agent and write its script to match the voice's persona,
        // without keeping its own copy of the voice catalogue.
        calls_voice_name: found.voice.name,
        calls_voice_gender: found.voice.gender,
      },
      { onConflict: "project_id" }
    );
  if (error) return { ok: false, error: "The voice could not be saved. Try again." };
  return { ok: true, message: `Voice set to ${found.voice.name} (${found.language.label}). It applies to the next call campaign.` };
}
