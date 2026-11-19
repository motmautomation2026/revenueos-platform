import { test } from "node:test";
import assert from "node:assert/strict";
import { normaliseDid } from "../lib/brain/caller";

test("accepts a valid Indian number written every common way", () => {
  for (const input of ["+918065382389", "918065382389", "08065382389", "8065382389", "+91 80 6538 2389", "+91-806-538-2389"]) {
    assert.equal(normaliseDid(input), "+918065382389", input);
  }
});

test("accepts a mobile number too", () => {
  assert.equal(normaliseDid("+919876543210"), "+919876543210");
  assert.equal(normaliseDid("9876543210"), "+919876543210");
});

test("rejects a national number that starts with 0", () => {
  assert.equal(normaliseDid("+910065382389"), null);
  assert.equal(normaliseDid("0065382389"), null);
});

test("rejects the wrong number of national digits", () => {
  assert.equal(normaliseDid("+9180653823"), null); // 8 national digits
  assert.equal(normaliseDid("+91806538238900"), null); // 12 national digits
  assert.equal(normaliseDid("806538238"), null); // 9 digits
});

test("rejects non-numeric and empty input", () => {
  for (const input of ["", "   ", "not a number", "+91abcdefghij", "++918065382389"]) {
    assert.equal(normaliseDid(input), null, input);
  }
});

test("rejects a bare number with no country code that the old rule would have mangled", () => {
  // 8 to 15 digits used to pass and become "+<digits>"; now a non-Indian-shaped number is refused.
  assert.equal(normaliseDid("12345678"), null);
  assert.equal(normaliseDid("+14155552671"), null); // a US number: not an Indian DID
});
