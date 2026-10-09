import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import Ajv from "ajv";
import { OutputParseError, normalize, type JsonSchema } from "../lib/agents/normalize";

const schema: JsonSchema = {
  type: "object",
  additionalProperties: false,
  required: ["name", "priority", "score", "confirmed", "tags", "note"],
  properties: {
    name: { type: "string" },
    priority: { type: "string", enum: ["high", "medium", "low"] },
    score: { type: "number" },
    confirmed: { type: "boolean" },
    tags: { type: "array", items: { type: "string" } },
    note: { type: ["string", "null"] },
  },
};
const valid = { name: "A", priority: "high", score: 3, confirmed: true, tags: ["x"], note: null };
const validate = new Ajv({ allErrors: true, strict: false }).compile(schema);
const fixes = (repairs: { path: string; fix: string }[]) => repairs.map((r) => `${r.path}: ${r.fix}`);

test("fenced JSON is unwrapped", () => {
  const { data, repairs } = normalize("```json\n" + JSON.stringify(valid) + "\n```", schema);
  assert.deepEqual(data, valid);
  assert.deepEqual(fixes(repairs), ["$: removed markdown code fence"]);
});

test("clean JSON needs no repairs", () => {
  const { data, repairs } = normalize(JSON.stringify(valid), schema);
  assert.deepEqual(data, valid);
  assert.equal(repairs.length, 0);
});

test("missing required fields are filled with empty values of the right type", () => {
  const { data, repairs } = normalize('{"name":"A","priority":"low"}', schema);
  assert.deepEqual(data, { name: "A", priority: "low", score: 0, confirmed: false, tags: [], note: null });
  assert.equal(repairs.length, 4);
  assert.ok(validate(data));
});

test("wrong enum case and spacing is fuzzy-matched", () => {
  const { data, repairs } = normalize(JSON.stringify({ ...valid, priority: " HIGH " }), schema);
  assert.equal((data as typeof valid).priority, "high");
  assert.deepEqual(fixes(repairs), ['$.priority: matched " HIGH " to "high"']);
});

test("numbers and booleans given as strings are coerced", () => {
  const { data, repairs } = normalize(
    JSON.stringify({ ...valid, score: "1,50,000", confirmed: "false" }),
    schema
  );
  assert.equal((data as typeof valid).score, 150000);
  assert.equal((data as typeof valid).confirmed, false);
  assert.equal(repairs.length, 2);
  assert.ok(validate(data));
});

test("unknown keys are dropped", () => {
  const { data, repairs } = normalize(JSON.stringify({ ...valid, extra: 1 }), schema);
  assert.deepEqual(data, valid);
  assert.deepEqual(fixes(repairs), ["$.extra: dropped unknown key"]);
});

test('nullable strings: "null" becomes null', () => {
  const { data } = normalize(JSON.stringify({ ...valid, note: "null" }), schema);
  assert.equal((data as typeof valid).note, null);
});

test("an unmatched enum value is left for validation to report", () => {
  const { data } = normalize(JSON.stringify({ ...valid, priority: "urgent" }), schema);
  assert.equal(validate(data), false);
});

test("output with no JSON throws a parse error", () => {
  assert.throws(() => normalize("Sorry, I cannot do that.", schema), OutputParseError);
  assert.throws(() => normalize('{"name": "A", ', schema), OutputParseError);
});

test("checklist schema: a sloppy but recoverable answer validates after repair", () => {
  const checklistSchema = JSON.parse(readFileSync("schemas/checklist.schema.json", "utf8"));
  const raw = JSON.stringify({
    customer_name: "Example Co",
    known_facts: [{ topic: "t", fact: "f", source: "Handover", source_quote: "q" }],
    flags: [{ flag_type: "Risk", field: "x", detail: "d", blocks_targets: "true" }],
    checklist_items: [{ id: "Q-1", category: "Business basics", question: "Name?", reason: "r", prefilled_answer: "" }],
  });
  const { data } = normalize(raw, checklistSchema);
  const check = new Ajv({ allErrors: true, strict: false }).compile(checklistSchema);
  assert.ok(check(data), JSON.stringify(check.errors));
  const out = data as { flags: { blocks_targets: boolean; handover_says: null }[]; cam_notes: string[] };
  assert.equal(out.flags[0].blocks_targets, true);
  assert.equal(out.flags[0].handover_says, null);
  assert.deepEqual(out.cam_notes, []);
});
