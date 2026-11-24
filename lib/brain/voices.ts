// The AI calling agent's voices, per language. The source of truth is the voice platform's
// backend/app/core/pipelines.py (LANGUAGES); this mirror lets the platform offer a voice picker and
// validate a choice without reaching the voice platform. Keep it in step with pipelines.py.
//
// voice_id is a Sarvam persona id; it is only valid for its own language, so the picker groups by
// language and a saved choice stores both the voice id and its language (the voice platform rejects a
// voice that does not match the language).

export type Voice = { id: string; name: string; gender: "female" | "male" };
export type VoiceLanguage = { language: string; label: string; voices: Voice[] };

export const DEFAULT_LANGUAGE = "hinglish";
export const DEFAULT_VOICE_ID = "simran_enhi_customer";

// Only the languages the voice platform has enabled (pipelines.py `_ON`). The first voice of each is its
// default, matching pipelines.default_voice (first of the list).
export const VOICE_LANGUAGES: VoiceLanguage[] = [
  {
    language: "hinglish",
    label: "Hinglish (Hindi–English mix)",
    voices: [
      { id: "simran_enhi_customer", name: "Simran", gender: "female" },
      { id: "ishita_enhi_customer", name: "Ishita", gender: "female" },
      { id: "shalini_enhi_customer", name: "Shalini", gender: "female" },
      { id: "sunny_enhi_customer", name: "Sunny", gender: "male" },
      { id: "shubh_enhi_companion", name: "Shubh", gender: "male" },
    ],
  },
  {
    language: "hindi",
    label: "Hindi",
    voices: [
      { id: "ritu_hi_conversation", name: "Ritu", gender: "female" },
      { id: "rahul_hi_conversational", name: "Rahul", gender: "male" },
      { id: "kavya_hi_conversational", name: "Kavya", gender: "female" },
      { id: "aditya_hi_conversational", name: "Aditya", gender: "male" },
      { id: "simran_hi_conversation", name: "Simran", gender: "female" },
      { id: "amit_hi_conversational", name: "Amit", gender: "male" },
    ],
  },
  {
    language: "english",
    label: "English (Indian)",
    voices: [
      { id: "simran_en_conversation", name: "Simran", gender: "female" },
      { id: "dev_en_conversational", name: "Dev", gender: "male" },
      { id: "neha_en_customer", name: "Neha", gender: "female" },
      { id: "deven_en_conversation", name: "Deven", gender: "male" },
    ],
  },
  {
    language: "marathi",
    label: "Marathi",
    voices: [
      { id: "ishita_mr_conversational", name: "Ishita", gender: "female" },
      { id: "nilesh_mr_conversation", name: "Nilesh", gender: "male" },
      { id: "neha_mr_narration", name: "Neha", gender: "female" },
      { id: "soham_mr_narration", name: "Soham", gender: "male" },
    ],
  },
  {
    language: "telugu",
    label: "Telugu",
    voices: [
      { id: "kavitha_te_conversation", name: "Kavitha", gender: "female" },
      { id: "pooja_te_conversation", name: "Pooja", gender: "female" },
      { id: "tarun_te_narration", name: "Tarun", gender: "male" },
    ],
  },
  {
    language: "kannada",
    label: "Kannada",
    voices: [
      { id: "chaitra_kn_conversation", name: "Chaitra", gender: "female" },
      { id: "chetan_kn_conversation", name: "Chetan", gender: "male" },
      { id: "suchitra_kn_narration", name: "Suchitra", gender: "female" },
    ],
  },
  {
    language: "punjabi",
    label: "Punjabi",
    voices: [
      { id: "anand_pa_conversation", name: "Anand", gender: "male" },
      { id: "harpreet_pa_narration", name: "Harpreet", gender: "female" },
      { id: "jaspal_pa_banking", name: "Jaspal", gender: "male" },
    ],
  },
  {
    language: "tamil",
    label: "Tamil",
    voices: [
      { id: "vijay_ta_narration", name: "Vijay", gender: "male" },
      { id: "gokul_ta_narration", name: "Gokul", gender: "male" },
    ],
  },
  {
    language: "bengali",
    label: "Bengali",
    voices: [
      { id: "arnab_bn_conversation", name: "Arnab", gender: "male" },
      { id: "roopa_bn_conversational", name: "Roopa", gender: "female" },
    ],
  },
  {
    language: "gujarati",
    label: "Gujarati",
    voices: [
      { id: "bhavik_gu_conversation", name: "Bhavik", gender: "male" },
      { id: "pooja_gu_conversational", name: "Pooja", gender: "female" },
    ],
  },
];

export type ChosenVoice = { voiceId: string; language: string };

/** The voice for a stored id, with its language. Null when the id is not one we know (so it is rejected). */
export function findVoice(voiceId: string): { voice: Voice; language: VoiceLanguage } | null {
  for (const language of VOICE_LANGUAGES) {
    const voice = language.voices.find((v) => v.id === voiceId);
    if (voice) return { voice, language };
  }
  return null;
}

/** The chosen voice, falling back to the default when nothing (or something unknown) is stored. */
export function voiceChoice(voiceId: string | null | undefined): ChosenVoice {
  const id = (voiceId ?? "").trim();
  const found = id ? findVoice(id) : null;
  if (!found) return { voiceId: DEFAULT_VOICE_ID, language: DEFAULT_LANGUAGE };
  return { voiceId: found.voice.id, language: found.language.language };
}

/** A short label like "Simran · English (Indian)" for a stored voice id. */
export function voiceLabel(voiceId: string | null | undefined): string {
  const found = findVoice((voiceId ?? "").trim() || DEFAULT_VOICE_ID);
  if (!found) return "Default voice";
  return `${found.voice.name} · ${found.language.label}`;
}
