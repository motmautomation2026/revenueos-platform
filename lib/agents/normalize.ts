// Forgiving parser for agent output: fixes small slips instead of failing on them,
// and records every fix so it can be audited in agent_runs.repairs.

export type JsonSchema = {
  type?: string | string[];
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  [key: string]: unknown;
};

export type Repair = { path: string; fix: string };

export class OutputParseError extends Error {}

/** Strips ``` fences and any prose around the outermost JSON object. */
export function extractJson(raw: string, repairs: Repair[] = []): string {
  let text = raw.trim().replace(/^﻿/, "");

  const fenced = text.match(/^```[a-zA-Z]*\s*\n?([\s\S]*?)\n?```$/);
  if (fenced) {
    text = fenced[1].trim();
    repairs.push({ path: "$", fix: "removed markdown code fence" });
  }

  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start === -1 || end <= start) throw new OutputParseError("The output contained no JSON object.");
  if (start > 0 || end < text.length - 1) {
    text = text.slice(start, end + 1);
    repairs.push({ path: "$", fix: "removed text around the JSON object" });
  }
  return text;
}

function typesOf(schema: JsonSchema): string[] {
  if (Array.isArray(schema.type)) return schema.type;
  if (schema.type) return [schema.type];
  if (schema.properties) return ["object"];
  if (schema.items) return ["array"];
  return [];
}

function emptyValue(schema: JsonSchema): unknown {
  const types = typesOf(schema);
  if (types.includes("null")) return null;
  switch (types[0]) {
    case "string":
      return "";
    case "number":
    case "integer":
      return 0;
    case "boolean":
      return false;
    case "array":
      return [];
    case "object": {
      const value: Record<string, unknown> = {};
      for (const key of schema.required ?? []) {
        value[key] = emptyValue(schema.properties?.[key] ?? {});
      }
      return value;
    }
    default:
      return null;
  }
}

const squash = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, "");

function matchEnum(value: unknown, options: unknown[]): unknown {
  if (options.includes(value)) return value;
  if (typeof value !== "string") return undefined;
  const wanted = squash(value);
  if (!wanted) return undefined;
  const strings = options.filter((o): o is string => typeof o === "string");
  return (
    strings.find((o) => squash(o) === wanted) ??
    strings.find((o) => squash(o).startsWith(wanted) || wanted.startsWith(squash(o))) ??
    strings.find((o) => wanted.includes(squash(o)))
  );
}

const TRUE_WORDS = ["true", "yes", "y", "1"];
const FALSE_WORDS = ["false", "no", "n", "0"];

function describe(value: unknown) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  return typeof value;
}

function fix(value: unknown, schema: JsonSchema, path: string, repairs: Repair[]): unknown {
  const types = typesOf(schema);
  const nullable = types.includes("null");
  const note = (message: string) => repairs.push({ path, fix: message });

  if (value === undefined || value === null) {
    if (nullable || types.length === 0) return value ?? null;
    note(`replaced null with empty ${types[0]}`);
    return emptyValue(schema);
  }

  if (nullable && typeof value === "string" && ["", "null", "none", "n/a"].includes(value.trim().toLowerCase())) {
    if (value !== "") note(`turned "${value}" into null`);
    return null;
  }

  if (schema.enum) {
    const match = matchEnum(value, schema.enum);
    if (match === undefined) return value; // left for validation to report
    if (match !== value) note(`matched "${String(value)}" to "${String(match)}"`);
    return match;
  }

  if (types.includes("object")) {
    if (typeof value !== "object" || Array.isArray(value)) {
      note(`replaced ${describe(value)} with empty object`);
      return emptyValue({ ...schema, type: "object" });
    }
    const source = value as Record<string, unknown>;
    const properties = schema.properties ?? {};
    const result: Record<string, unknown> = {};

    for (const key of Object.keys(source)) {
      if (schema.properties && !(key in properties)) {
        repairs.push({ path: `${path}.${key}`, fix: "dropped unknown key" });
        continue;
      }
      result[key] = fix(source[key], properties[key] ?? {}, `${path}.${key}`, repairs);
    }
    for (const key of schema.required ?? []) {
      if (!(key in result)) {
        result[key] = emptyValue(properties[key] ?? {});
        repairs.push({ path: `${path}.${key}`, fix: "added missing field with an empty value" });
      }
    }
    return result;
  }

  if (types.includes("array")) {
    let list: unknown[];
    if (Array.isArray(value)) {
      list = value;
    } else {
      note(`wrapped ${describe(value)} in an array`);
      list = [value];
    }
    return list.map((entry, i) => fix(entry, schema.items ?? {}, `${path}[${i}]`, repairs));
  }

  if (types.includes("number") || types.includes("integer")) {
    if (typeof value === "number") return value;
    if (typeof value === "string") {
      const parsed = Number(value.replace(/[,\s₹%]/g, ""));
      if (value.trim() !== "" && Number.isFinite(parsed)) {
        note(`converted string "${value}" to number`);
        return parsed;
      }
    }
    return value;
  }

  if (types.includes("boolean")) {
    if (typeof value === "boolean") return value;
    const word = String(value).trim().toLowerCase();
    if (TRUE_WORDS.includes(word) || FALSE_WORDS.includes(word)) {
      note(`converted ${describe(value)} "${String(value)}" to boolean`);
      return TRUE_WORDS.includes(word);
    }
    return value;
  }

  if (types.includes("string")) {
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") {
      note(`converted ${typeof value} to string`);
      return String(value);
    }
    if (Array.isArray(value) && value.every((v) => typeof v === "string" || typeof v === "number")) {
      note("joined array into one string");
      return value.join(", ");
    }
    return value;
  }

  return value;
}

/** Parses raw model output and repairs it towards the schema. Throws OutputParseError if it is not JSON. */
export function normalize(raw: string, schema: JsonSchema): { data: unknown; repairs: Repair[] } {
  const repairs: Repair[] = [];
  const json = extractJson(raw, repairs);

  let parsed: unknown;
  try {
    parsed = JSON.parse(json);
  } catch (error) {
    throw new OutputParseError(`The output was not valid JSON (${(error as Error).message}).`);
  }
  return { data: fix(parsed, schema, "$", repairs), repairs };
}
