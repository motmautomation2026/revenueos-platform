export const AGENT_NAMES = ["checklist", "diagnosis", "strategy", "execution"] as const;
export type AgentName = (typeof AGENT_NAMES)[number];

/** Prompt file for each agent, inside prompts/. */
export const PROMPT_FILES: Record<AgentName, string> = {
  checklist: "checklist.md",
  diagnosis: "diagnose.md",
  strategy: "strategy.md",
  execution: "execution.md",
};

/** The one place the model is chosen. Override with OPENAI_MODEL. */
export const OPENAI_MODEL = process.env.OPENAI_MODEL || "gpt-4.1";
export const OPENAI_TEMPERATURE = 0.2;
export const OPENAI_MAX_TOKENS = 16_000;
export const OPENAI_TIMEOUT_MS = 240_000;
/** Retries on network errors, 5xx and 429, with the SDK's exponential backoff. */
export const OPENAI_MAX_RETRIES = 1;

export const CHECKLIST_MIN_QUESTIONS = 40;
export const CHECKLIST_MAX_QUESTIONS = 50;
