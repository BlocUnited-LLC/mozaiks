// App health is a list of specific problems from signals Studio has, not a score.
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const pages = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../factory_app/app/admin/pages");
const { buildHealthState, validationLabel, validationTone } = await import(
  pathToFileURL(path.join(pages, "studioHealthModel.js")).href
);

test("nothing wrong reports no issues and no score", () => {
  const health = buildHealthState({ latestValidationStatus: "passed", failedRuns: 0, trackedRuns: 12, integrationIssues: 0 });
  assert.deepEqual(health, { label: "No issues found", tone: "success", issues: [] });
  assert.ok(!("score" in health), "health must not carry a numeric score");
});

test("an app with no build and no runs is not reported as broken", () => {
  assert.deepEqual(buildHealthState(), { label: "No issues found", tone: "success", issues: [] });
});

test("a failed build validation is a problem", () => {
  const health = buildHealthState({ latestValidationStatus: "failed" });
  assert.equal(health.label, "Problems found");
  assert.equal(health.tone, "destructive");
  assert.deepEqual(health.issues, ["The latest build failed validation."]);
});

test("a build that has not passed yet needs attention", () => {
  const health = buildHealthState({ latestValidationStatus: "pending" });
  assert.deepEqual(health, {
    label: "Needs attention",
    tone: "warning",
    issues: ["The latest build has not passed validation yet."],
  });
});

test("failed runs are counted against the recorded total", () => {
  assert.deepEqual(buildHealthState({ latestValidationStatus: "passed", failedRuns: 2, trackedRuns: 10 }).issues, [
    "2 of 10 runs ended with an error.",
  ]);
  assert.deepEqual(buildHealthState({ failedRuns: 1 }).issues, ["1 run ended with an error."]);
  assert.equal(buildHealthState({ failedRuns: 1, trackedRuns: 1 }).tone, "destructive");
});

test("integrations missing a credential need attention", () => {
  assert.deepEqual(buildHealthState({ latestValidationStatus: "passed", integrationIssues: 1 }), {
    label: "Needs attention",
    tone: "warning",
    issues: ["1 integration still needs a credential."],
  });
  assert.deepEqual(buildHealthState({ integrationIssues: 3 }).issues, ["3 integrations still need a credential."]);
});

test("every finding is listed, most serious state wins", () => {
  const health = buildHealthState({ latestValidationStatus: "failed", failedRuns: 4, trackedRuns: 9, integrationIssues: 2 });
  assert.equal(health.label, "Problems found");
  assert.deepEqual(health.issues, [
    "The latest build failed validation.",
    "4 of 9 runs ended with an error.",
    "2 integrations still need a credential.",
  ]);
});

test("counts that are missing or not numbers are treated as none", () => {
  assert.deepEqual(buildHealthState({ failedRuns: undefined, trackedRuns: null, integrationIssues: "x" }).issues, []);
  assert.deepEqual(buildHealthState({ failedRuns: -3 }).issues, []);
});

test("validation wording", () => {
  assert.deepEqual(
    ["passed", "failed", "pending", null].map((status) => [validationLabel(status), validationTone(status)]),
    [["Passed", "success"], ["Failed", "destructive"], ["Not passed yet", "warning"], ["No build yet", "warning"]],
  );
});
