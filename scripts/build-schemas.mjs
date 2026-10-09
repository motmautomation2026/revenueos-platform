// Derives schemas/<agent>.schema.json from the OUTPUT FORMAT template at the end of each
// agent prompt, so the schema never drifts from the prompt. Run: npm run schemas
import { readFileSync, writeFileSync } from "node:fs";

const AGENTS = { diagnosis: "diagnose.md", strategy: "strategy.md", execution: "execution.md" };

function leaf(text) {
  const value = text.trim();
  if (value === "text") return { type: "string" };
  if (value === "text or null") return { type: ["string", "null"] };
  if (value === "number") return { type: "number" };
  if (value === "number or null") return { type: ["number", "null"] };
  if (value === "true or false") return { type: "boolean" };
  if (value.includes("|")) return { type: "string", enum: value.split("|").map((v) => v.trim()) };
  throw new Error(`Unrecognised template value: "${text}"`);
}

function toSchema(template) {
  if (typeof template === "string") return leaf(template);
  if (Array.isArray(template)) return { type: "array", items: toSchema(template[0]) };
  const properties = {};
  for (const [key, value] of Object.entries(template)) properties[key] = toSchema(value);
  return {
    type: "object",
    additionalProperties: false,
    required: Object.keys(template),
    properties,
  };
}

for (const [agent, file] of Object.entries(AGENTS)) {
  const prompt = readFileSync(`prompts/${file}`, "utf8");
  const format = prompt.slice(prompt.lastIndexOf("OUTPUT FORMAT"));
  const template = JSON.parse(format.slice(format.indexOf("{"), format.lastIndexOf("}") + 1));
  const schema = {
    $schema: "http://json-schema.org/draft-07/schema#",
    title: `${agent[0].toUpperCase()}${agent.slice(1)} agent output`,
    ...toSchema(template),
  };
  writeFileSync(`schemas/${agent}.schema.json`, `${JSON.stringify(schema, null, 2)}\n`);
  console.log(`schemas/${agent}.schema.json  (${Object.keys(template).length} top-level keys)`);
}
