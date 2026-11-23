import { test } from "node:test";
import assert from "node:assert/strict";
import { callScriptOf } from "../lib/brain/tools";

test("reads the agent script from a quickhowl request output", () => {
  const script = callScriptOf({
    agent: {
      name: "Simran",
      language: "hinglish",
      voice_id: "simran_enhi_customer",
      speech_pace: 1.0,
      welcome_message: "{time_greeting} {name}, main Simran bol rahi hoon Tester 101 se.",
      system_prompt: "Hum ek AI-powered automation solution dete hain…",
      variables: ["name", "time_greeting"],
    },
    campaign: { name: "x" },
  });
  assert.equal(script?.name, "Simran");
  assert.equal(script?.language, "hinglish");
  assert.equal(script?.welcomeMessage.startsWith("{time_greeting}"), true);
  assert.equal(script?.systemPrompt.length > 0, true);
  assert.deepEqual(script?.variables, ["name", "time_greeting"]);
});

test("variables can be an object of placeholder -> description", () => {
  const script = callScriptOf({ agent: { welcome_message: "Hi", variables: { name: "the person", company: "their firm" } } });
  assert.deepEqual(script?.variables.sort(), ["company", "name"]);
});

test("no agent, or an empty script, returns null", () => {
  assert.equal(callScriptOf({ campaign: { name: "x" } }), null);
  assert.equal(callScriptOf({ agent: {} }), null);
  assert.equal(callScriptOf({ agent: { welcome_message: "", system_prompt: "" } }), null);
  assert.equal(callScriptOf(null), null);
});

test("a welcome line alone is enough (system prompt optional)", () => {
  const script = callScriptOf({ agent: { welcome_message: "Namaste" } });
  assert.equal(script?.welcomeMessage, "Namaste");
  assert.equal(script?.systemPrompt, "");
});
