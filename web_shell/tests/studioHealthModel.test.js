// App health is a list of specific problems from signals Studio has, not a score.
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const pages = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../factory_app/app/admin/pages");
const { averageFinishedRunSeconds, buildHealthState, formatRunDuration, validationLabel, validationTone } = await import(
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

test("an integration that fails its check is a problem, even though a credential is saved", () => {
  assert.deepEqual(buildHealthState({ latestValidationStatus: "passed", integrationCheckFailures: 1 }), {
    label: "Problems found",
    tone: "destructive",
    issues: ["1 integration failed its last check."],
  });
  assert.deepEqual(buildHealthState({ integrationCheckFailures: 2 }).issues, ["2 integrations failed their last check."]);
});

test("every finding is listed, most serious state wins", () => {
  const health = buildHealthState({
    latestValidationStatus: "failed", failedRuns: 4, trackedRuns: 9, integrationIssues: 2, integrationCheckFailures: 1,
  });
  assert.equal(health.label, "Problems found");
  assert.deepEqual(health.issues, [
    "The latest build failed validation.",
    "4 of 9 runs ended with an error.",
    "1 integration failed its last check.",
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

test("average run time counts only runs that have ended", () => {
  // The rows /api/admin/runs returns: status 0 in progress, 1 completed, 2 failed.
  const runs = [
    { chat_id: "open", status: 0, runtime_sec: 86400 }, // nobody closed this conversation yesterday
    { chat_id: "done", status: 1, runtime_sec: 4 },
    { chat_id: "failed", status: 2, runtime_sec: 2 },
  ];
  assert.deepEqual(averageFinishedRunSeconds(runs), { seconds: 3, count: 2 });

  // Nothing has ended yet: there is no average, not a zero.
  assert.deepEqual(averageFinishedRunSeconds([{ chat_id: "open", status: 0, runtime_sec: 50 }]), { seconds: null, count: 0 });
  assert.deepEqual(averageFinishedRunSeconds([]), { seconds: null, count: 0 });
  assert.deepEqual(averageFinishedRunSeconds(undefined), { seconds: null, count: 0 });
  // A finished run with no usable duration is not counted as zero seconds.
  assert.deepEqual(
    averageFinishedRunSeconds([{ status: 1, runtime_sec: "n/a" }, { status: 2, runtime_sec: null }, { status: 1, runtime_sec: 6 }]),
    { seconds: 6, count: 1 },
  );
});

test("run durations read as time, and a missing one is not shown as 0s", () => {
  assert.deepEqual(
    [1.24, 9.96, 42.4, 125, 3725].map(formatRunDuration),
    ["1.2s", "10.0s", "42s", "2m 5s", "1h 2m"],
  );
  assert.equal(formatRunDuration(null), null);
  assert.equal(formatRunDuration(undefined), null);
  assert.equal(formatRunDuration(0), "0.0s");
});
