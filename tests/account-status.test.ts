import { test } from "node:test";
import assert from "node:assert/strict";
import { emailStatusFrom, callsStatusFrom } from "../lib/brain/account-status";

test("email launch: user_created true -> created, with the account email", () => {
  const s = emailStatusFrom({ user_created: true, email: "client@acme.com", mailbox_status: "connected" }, false, "");
  assert.equal(s.status, "created");
  assert.equal(s.account, "client@acme.com");
});

test("email launch: user_created false + mailbox connected -> reused", () => {
  const s = emailStatusFrom({ user_created: false, email: "client@acme.com", mailbox_status: "connected" }, false, "");
  assert.equal(s.status, "reused");
});

test("email launch: a mailbox error -> error, surfacing the message", () => {
  const s = emailStatusFrom(
    { user_created: false, email: "client@acme.com", mailbox_error: "Could not reach the SMTP server." },
    false,
    ""
  );
  assert.equal(s.status, "error");
  assert.match(s.detail, /SMTP/);
});

test("email launch: a failed tool call -> error even without a mailbox message", () => {
  assert.equal(emailStatusFrom({ email: "client@acme.com" }, true, "launch failed").status, "error");
});

test("calls store: account.status created -> created, with the login email", () => {
  const s = callsStatusFrom({ account: { status: "created", login_email: "client@acme.com" } }, false, "");
  assert.equal(s.status, "created");
  assert.equal(s.account, "client@acme.com");
});

test("calls store: account.status reused -> reused", () => {
  assert.equal(callsStatusFrom({ account: { status: "reused", login_email: "shared@motm.tech" } }, false, "").status, "reused");
});

test("calls store: a failed/erroring tool call -> error", () => {
  assert.equal(callsStatusFrom({ account: { status: "created" } }, true, "409 conflict").status, "error");
});

test("missing/empty responses stay 'unknown' for calls (no account block)", () => {
  assert.equal(callsStatusFrom({}, false, "").status, "unknown");
});
