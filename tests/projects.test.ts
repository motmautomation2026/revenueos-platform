import assert from "node:assert/strict";
import { test } from "node:test";
import { planningNextStep, type ProjectStatus } from "../lib/projects";

test("the overview says what a person has to do next on a project being planned", () => {
  assert.deepEqual(planningNextStep("draft"), { label: "Upload the handover and proposal", path: "upload" });
  assert.deepEqual(planningNextStep("checklist_ready"), { label: "Fill in and submit the checklist", path: "checklist" });
  assert.deepEqual(planningNextStep("checklist_submitted"), { label: "Generate the GTM plan", path: "plan" });
  assert.deepEqual(planningNextStep("plan_blocked"), { label: "Answer the plan's questions", path: "plan" });
  assert.deepEqual(planningNextStep("failed"), { label: "Resume the plan", path: "plan" });
});

test("nothing is asked while RevenueOS is working, or once the plan is ready (the Brain asks then)", () => {
  for (const status of ["checklist_generating", "plan_running", "plan_ready"] as ProjectStatus[]) {
    assert.equal(planningNextStep(status), null, status);
  }
});
