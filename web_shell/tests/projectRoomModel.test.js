// The Project page shows only what the server records: saved build documents
// from GET /api/studio/build/history and the app's registry entry.
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const pages = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../factory_app/app/admin/pages");
const model = await import(pathToFileURL(path.join(pages, "projectRoomModel.js")).href);
const {
  BUILD_STEPS, buildSteps, buildsInProgressCandidates, documentContent, humanizeKey, isEmptyValue,
  isHexColor, isHiddenKey, itemTitle, latestDocuments, projectAction, projectStatus, projectStatusSentence,
  projectVersions, versionStatus,
} = model;

// Rows shaped like the server's history response (BuildRecord.model_dump(by_alias=False)).
function row(family, version, lifecycle, validation, extra = {}) {
  return {
    id: `av_${family}_${version}`,
    app_id: "draft-app-1",
    build_family: family,
    build_key: family,
    version_number: version,
    lifecycle_status: lifecycle,
    validation_status: validation,
    created_at: `2026-11-0${Math.min(version, 9)}T10:00:00`,
    updated_at: `2026-11-0${Math.min(version, 9)}T10:00:00`,
    commit_metadata: { message: `${family}`, metadata: { summary_payload: { app_name: "ToolLender" }, summary_format: "json" } },
    ...extra,
  };
}

const buildingApp = {
  build_registry_id: "appreg_1", app_id: "draft-app-1", status: "building", chat_app_id: "mozaiks-factory",
  active_chat_id: "chat-1", active_workflow_id: "DesignDocs", current_build_run: { build_id: "b1", phase: "genesis" },
};

test("the step order follows the build sequence, including plans and pricing", () => {
  assert.deepEqual(BUILD_STEPS.map((step) => step.family), [
    "concept", "theme_capture", "design_docs", "subscription_contract", "workflow_bundle", "app_bundle",
  ]);
});

test("internal families are never shown as documents", () => {
  const rows = [
    row("app_context_version", 40, "current", "passed"),
    row("app_intelligence_snapshot", 39, "current", "passed"),
    row("app_context_graph", 38, "current", "passed"),
    row("source_context_bundle", 37, "current", "passed"),
    row("brownfield_registration", 36, "current", "passed"),
    row("concept", 1, "current", "skipped"),
  ];
  assert.deepEqual(projectVersions(rows).map((version) => version.build_family), ["concept"]);
  assert.deepEqual(latestDocuments(rows).map((document) => document.name), ["Idea & value"]);
});

test("the latest current version is chosen, not the newest row", () => {
  const rows = [
    row("concept", 3, "draft", "skipped"),
    row("concept", 2, "current", "skipped"),
    row("concept", 1, "superseded", "skipped"),
  ];
  const [concept] = latestDocuments(rows);
  assert.equal(concept.version.version_number, 2);
  assert.equal(concept.status.label, "Current");
  assert.equal(concept.versionCount, 3);
});

test("a document with no current version shows its newest version", () => {
  const [plan] = latestDocuments([row("adoption_plan", 1, "draft", "pending"), row("adoption_plan", 2, "draft", "pending")]);
  assert.equal(plan.name, "Adoption plan");
  assert.equal(plan.version.version_number, 2);
  assert.equal(plan.status.label, "Draft");
});

test("version status chips use plain words", () => {
  assert.deepEqual(versionStatus({ lifecycle_status: "current", validation_status: "passed" }), { label: "Current", tone: "success" });
  assert.deepEqual(versionStatus({ lifecycle_status: "draft", validation_status: "pending" }), { label: "Draft", tone: "warning" });
  assert.deepEqual(versionStatus({ lifecycle_status: "superseded", validation_status: "skipped" }), { label: "Replaced", tone: "default" });
  assert.deepEqual(versionStatus({ lifecycle_status: "current", validation_status: "failed" }), { label: "Didn't pass checks", tone: "destructive" });
});

test("steps are done only with a current version; the first missing step is next", () => {
  const steps = buildSteps([
    row("concept", 2, "current", "skipped"),
    row("theme_capture", 1, "superseded", "skipped"),
    row("design_docs", 1, "current", "skipped"),
  ]);
  assert.deepEqual(steps.map((step) => step.state), ["done", "next", "done", "not_started", "not_started", "not_started"]);
  assert.equal(steps[0].date, "2026-11-02T10:00:00");
});

test("nothing saved and no build conversation: the build has not started", () => {
  const app = { build_registry_id: "appreg_1", app_id: "draft-app-1", status: "draft" };
  const status = projectStatus({ versions: [], app });
  assert.equal(status.kind, "not_started");
  assert.equal(projectStatusSentence(status), "This app's build hasn't started.");
  assert.deepEqual(projectAction({ status, app, appId: "draft-app-1" }), {
    type: "start", label: "Start build", workflowId: "ValueEngine", buildRegistryId: "appreg_1",
  });
});

test("no documents yet but a build conversation is recorded: the first build is in progress", () => {
  const status = projectStatus({ versions: [], app: { ...buildingApp, active_workflow_id: "ValueEngine" } });
  assert.equal(status.kind, "in_progress");
  assert.equal(projectStatusSentence(status), "First build in progress. Next step: describe the idea.");
});

test("a first build in progress resumes the app's recorded build conversation", () => {
  const versions = [row("concept", 1, "current", "skipped"), row("theme_capture", 1, "current", "skipped")];
  const status = projectStatus({ versions, app: buildingApp });
  assert.equal(projectStatusSentence(status), "First build in progress. Next step: design the app.");
  assert.deepEqual(projectAction({ status, app: buildingApp, appId: "draft-app-1" }), {
    type: "link",
    label: "Continue build",
    href: "/chat?workflow=DesignDocs&mode=workflow&chat_id=chat-1&app_id=mozaiks-factory",
  });
});

test("the build run's chat wins over stale top-level chat fields", () => {
  const app = {
    ...buildingApp, active_chat_id: "stale", active_workflow_id: "ValueEngine",
    current_build_run: { active_chat_id: "run-chat", active_workflow_id: "AppGenerator" },
  };
  const action = projectAction({ status: projectStatus({ versions: [row("concept", 1, "current", "skipped")], app }), app });
  assert.equal(action.href, "/chat?workflow=AppGenerator&mode=workflow&chat_id=run-chat&app_id=mozaiks-factory");
});

test("a passed draft app waiting for review opens the build review", () => {
  const app = {
    build_registry_id: "appreg_1", app_id: "draft-app-1", status: "review",
    current_build_run: { artifact_version_id: "av_app_bundle_1" },
  };
  const versions = BUILD_STEPS.slice(0, 5).map((step) => row(step.family, 1, "current", "skipped"))
    .concat(row("app_bundle", 1, "draft", "passed"));
  const status = projectStatus({ versions, app });
  assert.equal(projectStatusSentence(status), "First build in progress. Next step: review the app.");
  assert.deepEqual(projectAction({ status, app, appId: "draft-app-1" }), {
    type: "link", label: "Continue build", href: "/apps/draft-app-1/activity",
  });
});

test("a built app offers a change through the app's review chat", () => {
  const app = {
    build_registry_id: "appreg_1", app_id: "draft-app-1", status: "active",
    current_build_run: { artifact_version_id: "av_app_bundle_2" },
  };
  const versions = [row("app_bundle", 2, "current", "passed"), row("app_bundle", 1, "superseded", "passed")];
  const status = projectStatus({ versions, app });
  assert.equal(status.kind, "built");
  assert.equal(projectStatusSentence(status, () => "Nov 2, 2026"), "Built. Last updated Nov 2, 2026.");
  assert.deepEqual(projectAction({ status, app, appId: "draft-app-1" }), {
    type: "start", label: "Make a change", workflowId: "AppReview", buildRegistryId: "appreg_1",
  });
});

test("a failed newest app version points at what failed", () => {
  const versions = [row("app_bundle", 2, "draft", "failed"), row("app_bundle", 1, "current", "passed")];
  const status = projectStatus({ versions, app: buildingApp });
  assert.equal(status.kind, "failed");
  assert.equal(projectStatusSentence(status), "The last build didn't pass its checks.");
  assert.deepEqual(projectAction({ status, app: buildingApp, appId: "draft app" }), {
    type: "link", label: "See what failed", href: "/apps/draft%20app/activity",
  });
});

test("no recorded entry is reported instead of inventing one", () => {
  const app = { build_registry_id: "appreg_1", app_id: "draft-app-1", status: "needs_revision" };
  const status = projectStatus({ versions: [row("concept", 1, "current", "skipped")], app });
  assert.equal(projectAction({ status, app }).type, "none");
});

test("document content is the saved summary payload; workflow bundles show their plan", () => {
  assert.deepEqual(documentContent(row("concept", 1, "current", "skipped")), { app_name: "ToolLender" });
  const workflows = row("workflow_bundle", 1, "current", "pending", {
    commit_metadata: { metadata: {
      build_registry_id: "appreg_1", workflow_name: "Lending", artifact_path: null,
      workflow_plan_review: { review_id: "r1", status: "not_required", rationale: "Deterministic modules only." },
    } },
  });
  assert.deepEqual(documentContent(workflows), {
    workflow_name: "Lending", workflows: undefined,
    plan_review: { status: "not_required", rationale: "Deterministic modules only." },
  });
  assert.equal(documentContent({ commit_metadata: { metadata: {} } }), null);
});

test("readable rendering helpers", () => {
  assert.equal(humanizeKey("value_proposition"), "Value proposition");
  assert.equal(humanizeKey("api_endpoints"), "API endpoints");
  assert.equal(humanizeKey("fontHeading"), "Font heading");
  assert.ok(isHiddenKey("app_id") && isHiddenKey("id") && !isHiddenKey("identity"));
  assert.ok(isHexColor("#2F9C95") && isHexColor("#fff") && !isHexColor("2F9C95") && !isHexColor("#12345"));
  assert.ok(isEmptyValue({ app_id: "x", notes: [] }) && !isEmptyValue({ name: "x" }));
  assert.equal(itemTitle({ route: "/tools" }, 0), "/tools");
  assert.equal(itemTitle({}, 2), "Item 3");
});

test("My Apps lists in-build apps most recently updated first, at most five", () => {
  const apps = Array.from({ length: 7 }, (_, index) => ({
    build_registry_id: `r${index}`, app_id: `a${index}`, status: index === 6 ? "active" : "building",
    updated_at: `2026-11-1${index}T00:00:00Z`,
  }));
  apps.push({ app_id: "no-registry", status: "building" });
  const { apps: listed, more } = buildsInProgressCandidates(apps);
  assert.deepEqual(listed.map((app) => app.app_id), ["a5", "a4", "a3", "a2", "a1"]);
  assert.equal(more, 1);
});
