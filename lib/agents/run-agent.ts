import "server-only";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import Ajv, { type ValidateFunction } from "ajv";
import OpenAI from "openai";
import type { SupabaseClient } from "@supabase/supabase-js";
import { createClient } from "@/lib/supabase/server";
import { TABLES } from "@/lib/supabase/tables";
import {
  OPENAI_MAX_RETRIES,
  OPENAI_MAX_TOKENS,
  OPENAI_MODEL,
  OPENAI_TEMPERATURE,
  OPENAI_TIMEOUT_MS,
  PROMPT_FILES,
  type AgentName,
} from "./config";
import { OutputParseError, normalize, type JsonSchema, type Repair } from "./normalize";

/** An error whose message is safe and useful to show to the user. */
export class AgentError extends Error {}

type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

type LoadedAgent = {
  systemPrompt: string;
  promptFile: string;
  promptHash: string;
  schema: JsonSchema;
  validate: ValidateFunction;
};

const ajv = new Ajv({ allErrors: true, strict: false });
const agentCache = new Map<AgentName, Promise<LoadedAgent>>();

async function loadAgent(agent: AgentName): Promise<LoadedAgent> {
  const promptFile = `prompts/${PROMPT_FILES[agent]}`;
  const schemaFile = `schemas/${agent}.schema.json`;

  let prompt: string;
  let schemaText: string;
  try {
    prompt = (await readFile(path.join(process.cwd(), promptFile), "utf8")).trim();
  } catch {
    throw new AgentError(`The ${agent} agent's prompt (${promptFile}) has not been added yet.`);
  }
  try {
    schemaText = await readFile(path.join(process.cwd(), schemaFile), "utf8");
  } catch {
    throw new AgentError(`The ${agent} agent's schema (${schemaFile}) has not been added yet.`);
  }

  const schema = JSON.parse(schemaText) as JsonSchema;
  const systemPrompt =
    `${prompt}\n\nOUTPUT FORMAT\n` +
    "Return one JSON object exactly matching this JSON Schema. No markdown, no prose.\n" +
    JSON.stringify(schema);

  return {
    systemPrompt,
    promptFile,
    promptHash: createHash("sha256").update(systemPrompt).digest("hex"),
    schema,
    validate: ajv.compile(schema),
  };
}

function getAgent(agent: AgentName) {
  let loaded = agentCache.get(agent);
  if (!loaded) {
    loaded = loadAgent(agent);
    // Do not cache a failure: the files may be added while the server is running.
    loaded.catch(() => agentCache.delete(agent));
    agentCache.set(agent, loaded);
  }
  return loaded;
}

let openai: OpenAI | null = null;
function getOpenAI() {
  if (!process.env.OPENAI_API_KEY) {
    throw new AgentError("OPENAI_API_KEY is not set on the server.");
  }
  openai ??= new OpenAI({
    apiKey: process.env.OPENAI_API_KEY,
    timeout: OPENAI_TIMEOUT_MS,
    maxRetries: OPENAI_MAX_RETRIES,
  });
  return openai;
}

function readableApiError(error: unknown): string {
  if (error instanceof OpenAI.APIConnectionTimeoutError) {
    return "The AI took too long to answer (over 4 minutes). Try again.";
  }
  if (error instanceof OpenAI.APIError) {
    if (error.status === 401) return "The OpenAI API key was rejected. Check OPENAI_API_KEY.";
    if (error.status === 429) return "OpenAI is rate-limiting or the account is out of quota. Try again shortly.";
    if (error.status === 400 && /context|maximum|too long|tokens/i.test(error.message)) {
      return "The documents are too long for the AI to read in one go. Remove some files and try again.";
    }
    if (error.status && error.status >= 500) return "OpenAI had a server problem. Try again.";
    return `The AI request failed (${error.status ?? "network error"}).`;
  }
  return "The AI request failed unexpectedly.";
}

type CallResult = {
  runId: string;
  raw: string;
  data: unknown;
  repairs: Repair[];
  validationErrors: string[];
};

/** One LLM call = one agent_runs row. */
async function callOnce(
  supabase: SupabaseClient,
  loaded: LoadedAgent,
  context: { agent: AgentName; projectId: string; planId?: string },
  messages: ChatMessage[]
): Promise<CallResult> {
  const { data: run, error: insertError } = await supabase
    .from(TABLES.agentRuns)
    .insert({
      project_id: context.projectId,
      plan_id: context.planId ?? null,
      agent: context.agent,
      model: OPENAI_MODEL,
      prompt_file: loaded.promptFile,
      prompt_hash: loaded.promptHash,
      input_chars: messages.reduce((sum, m) => sum + m.content.length, 0),
      status: "running",
    })
    .select("id")
    .single<{ id: string }>();
  if (insertError || !run) throw new AgentError("Could not start the AI run (audit log write failed).");

  const startedAt = Date.now();
  const finish = (fields: Record<string, unknown>) =>
    supabase
      .from(TABLES.agentRuns)
      .update({ duration_ms: Date.now() - startedAt, ...fields })
      .eq("id", run.id);
  const fail = async (message: string, fields: Record<string, unknown> = {}) => {
    await finish({ status: "failed", error: message, ...fields });
    return new AgentError(message);
  };

  let completion: OpenAI.Chat.Completions.ChatCompletion;
  try {
    completion = await getOpenAI().chat.completions.create({
      model: OPENAI_MODEL,
      temperature: OPENAI_TEMPERATURE,
      max_tokens: OPENAI_MAX_TOKENS,
      response_format: { type: "json_object" },
      messages,
    });
  } catch (error) {
    throw await fail(error instanceof AgentError ? error.message : readableApiError(error));
  }

  const choice = completion.choices[0];
  const raw = choice?.message?.content ?? "";
  const usage = {
    raw_output: raw,
    prompt_tokens: completion.usage?.prompt_tokens ?? null,
    completion_tokens: completion.usage?.completion_tokens ?? null,
  };

  if (choice?.finish_reason === "length") {
    throw await fail(
      "Output too long: the AI's answer was cut off before it finished. Try again, or remove some documents.",
      usage
    );
  }

  let data: unknown = null;
  let repairs: Repair[] = [];
  let validationErrors: string[];
  try {
    ({ data, repairs } = normalize(raw, loaded.schema));
    validationErrors = loaded.validate(data)
      ? []
      : (loaded.validate.errors ?? []).map((e) => `${e.instancePath || "$"} ${e.message}`);
  } catch (error) {
    if (!(error instanceof OutputParseError)) throw await fail("Could not read the AI's answer.", usage);
    validationErrors = [error.message];
  }

  await finish({
    ...usage,
    parsed_output: data,
    repairs,
    validation_errors: validationErrors,
    status: validationErrors.length ? "failed" : repairs.length ? "repaired" : "ok",
    error: validationErrors.length ? "Output did not match the schema." : null,
  });
  return { runId: run.id, raw, data, repairs, validationErrors };
}

export type RunAgentInput = {
  agent: AgentName;
  projectId: string;
  planId?: string;
  userMessage: string;
  /** Earlier exchange to continue from, e.g. when asking the agent to redo its answer. */
  followUp?: { previousOutput: string; message: string };
  /** Defaults to the signed-in user's client. */
  supabase?: SupabaseClient;
};

export async function runAgent<T = unknown>(
  input: RunAgentInput
): Promise<{ data: T; repairs: Repair[]; runId: string; raw: string }> {
  const loaded = await getAgent(input.agent);
  const supabase = input.supabase ?? (await createClient());

  const messages: ChatMessage[] = [
    { role: "system", content: loaded.systemPrompt },
    { role: "user", content: input.userMessage },
  ];
  if (input.followUp) {
    messages.push(
      { role: "assistant", content: input.followUp.previousOutput },
      { role: "user", content: input.followUp.message }
    );
  }

  let result = await callOnce(supabase, loaded, input, messages);

  if (result.validationErrors.length) {
    const problems = result.validationErrors.slice(0, 30).join("; ");
    result = await callOnce(supabase, loaded, input, [
      ...messages,
      { role: "assistant", content: result.raw },
      {
        role: "user",
        content: `Your JSON had these problems: ${problems}. Return the corrected full JSON.`,
      },
    ]);
    if (result.validationErrors.length) {
      throw new AgentError(
        "The AI's answer did not match the expected format, even after one correction. Try again."
      );
    }
  }

  return { data: result.data as T, repairs: result.repairs, runId: result.runId, raw: result.raw };
}
