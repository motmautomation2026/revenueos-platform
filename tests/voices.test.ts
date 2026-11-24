import assert from "node:assert/strict";
import { test } from "node:test";
import {
  DEFAULT_LANGUAGE,
  DEFAULT_VOICE_ID,
  findVoice,
  voiceChoice,
  voiceLabel,
} from "../lib/brain/voices";

test("voices: the default is chosen when nothing (or something unknown) is set", () => {
  const def = { voiceId: DEFAULT_VOICE_ID, language: DEFAULT_LANGUAGE };
  assert.deepEqual(voiceChoice(null), def);
  assert.deepEqual(voiceChoice(""), def);
  assert.deepEqual(voiceChoice("not-a-real-voice"), def);
});

test("voices: a known voice resolves with its own language, and a label reads nicely", () => {
  assert.deepEqual(voiceChoice("dev_en_conversational"), {
    voiceId: "dev_en_conversational",
    language: "english",
  });
  const found = findVoice("dev_en_conversational");
  assert.equal(found?.voice.name, "Dev");
  assert.equal(found?.voice.gender, "male");
  assert.equal(found?.language.language, "english");
  assert.equal(voiceLabel("dev_en_conversational"), "Dev · English (Indian)");
  assert.equal(voiceLabel("simran_enhi_customer"), "Simran · Hinglish (Hindi–English mix)");
});
