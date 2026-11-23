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

test("an app in review says so and opens the build review, not its generation chat", () => {
  const app = {
    build_registry_id: "appreg_1", app_id: "draft-app-1", status: "review", chat_app_id: "mozaiks-factory",
    active_chat_id: "gen-chat", active_workflow_id: "AppGenerator",
    current_build_run: { artifact_version_id: "av_app_bundle_1" },
  };
  const versions = BUILD_STEPS.slice(0, 5).map((step) => row(step.family, 1, "current", "skipped"))
    .concat(row("app_bundle", 1, "draft", "passed"));
  const status = projectStatus({ versions, app });
  assert.equal(status.kind, "ready_for_review");
  assert.equal(projectStatusSentence(status), "Ready for your review.");
  assert.deepEqual(projectAction({ status, app, appId: "draft-app-1" }), {
    type: "link", label: "Review the app", href: "/apps/draft-app-1/activity",
  });
  // Without a saved build the recorded chat is the only way back.
  const noBuild = { ...app, current_build_run: {} };
  assert.equal(projectAction({ status, app: noBuild }).href, "/chat?workflow=AppGenerator&mode=workflow&chat_id=gen-chat&app_id=mozaiks-factory");
});

test("the lifecycle state My Apps shows wins over the next document step", () => {
  const versions = BUILD_STEPS.slice(0, 5).map((step) => row(step.family, 1, "current", "skipped"));
  const app = { ...buildingApp, status: "needs_revision", active_workflow_id: "AppGenerator" };
  const status = projectStatus({ versions, app });
  assert.equal(status.kind, "needs_change");
  assert.equal(projectStatusSentence(status), "Needs a change before the build can continue.");
  assert.deepEqual(projectAction({ status, app, appId: "draft-app-1" }), {
    type: "link", label: "Continue build",
    href: "/chat?workflow=AppGenerator&mode=workflow&chat_id=chat-1&app_id=mozaiks-factory",
  });
  // A failed or passed app version does not hide a requested revision either.
  for (const validation of ["failed", "passed"]) {
    const withApp = versions.concat(row("app_bundle", 2, validation === "passed" ? "current" : "draft", validation));
    assert.equal(projectStatus({ versions: withApp, app }).kind, "needs_change");
  }
  // lifecycle_state (the overview record) is read the same way as status (the list entry).
  assert.equal(projectStatus({ versions, app: { lifecycle_state: "review" } }).kind, "ready_for_review");
  assert.equal(projectStatus({ versions, app: { ...buildingApp } }).kind, "in_progress");
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
  for (const lifecycle of ["needs_revision", "building"]) {
    const app = { build_registry_id: "appreg_1", app_id: "draft-app-1", status: lifecycle };
    const status = projectStatus({ versions: [row("concept", 1, "current", "skipped")], app });
    assert.equal(projectAction({ status, app }).type, "none");
  }
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

test("a rebuild with a recorded build conversation is in progress, not built", () => {
  const versions = [row("app_bundle", 2, "current", "passed")];
  for (const lifecycle of ["building", "configuring", "draft"]) {
    const app = { ...buildingApp, status: lifecycle, active_workflow_id: "AppGenerator" };
    const status = projectStatus({ versions, app });
    assert.equal(status.kind, "in_progress");
    assert.equal(projectStatusSentence(status), "A new build is in progress.");
    assert.deepEqual(projectAction({ status, app, appId: "draft-app-1" }), {
      type: "link", label: "Continue build",
      href: "/chat?workflow=AppGenerator&mode=workflow&chat_id=chat-1&app_id=mozaiks-factory",
    });
  }
  // Without a conversation to reopen the saved build still reads as built.
  const quiet = { build_registry_id: "appreg_1", app_id: "draft-app-1", status: "building" };
  assert.equal(projectStatus({ versions, app: quiet }).kind, "built");
});

test("live, deploying and archived apps get honest sentences", () => {
  const discoveryOnly = [row("application_inventory", 1, "draft", "pending"), row("risk_report", 1, "draft", "pending")];
  const live = { build_registry_id: "appreg_1", app_id: "imported-1", status: "active" };
  const liveStatus = projectStatus({ versions: discoveryOnly, app: live });
  assert.equal(projectStatusSentence(liveStatus), "This app is live.");
  assert.deepEqual(projectAction({ status: liveStatus, app: live }), {
    type: "link", label: "Open overview", href: "/apps/imported-1/overview",
  });
  // A live app whose current version passed reads as built, with a change offered.
  const built = projectStatus({ versions: [row("app_bundle", 1, "current", "passed")], app: live });
  assert.equal(built.kind, "built");
  for (const [lifecycle, sentence] of [["deploying", "Being deployed."], ["archived", "Archived."]]) {
    const app = { ...live, status: lifecycle };
    const status = projectStatus({ versions: [row("app_bundle", 1, "current", "passed")], app });
    assert.equal(projectStatusSentence(status), sentence);
    assert.deepEqual(projectAction({ status, app }), { type: "none", label: null, reason: null });
  }
});

test("a next step in a family that could not be read is not guessed", () => {
  const versions = [row("concept", 1, "current", "skipped")];
  const status = projectStatus({ versions, app: buildingApp, unknownFamilies: ["theme_capture"] });
  assert.equal(projectStatusSentence(status), "First build in progress.");
  const known = projectStatus({ versions, app: buildingApp, unknownFamilies: ["design_docs"] });
  assert.equal(projectStatusSentence(known), "First build in progress. Next step: choose the look and feel.");
  // Unknown families mean the app may have documents: never "hasn't started".
  const app = { build_registry_id: "appreg_1", app_id: "draft-app-1", status: "draft" };
  assert.notEqual(projectStatus({ versions: [], app, unknownFamilies: ["concept"] }).kind, "not_started");
});

test("follow-up reads are needed only for families missing from a full first page", () => {
  const { familiesToFollowUp, HISTORY_PAGE_LIMIT } = model;
  const short = [row("concept", 1, "current", "skipped")];
  assert.deepEqual(familiesToFollowUp(short, ["concept", "app_bundle"]), []);
  const full = Array.from({ length: HISTORY_PAGE_LIMIT }, (_, index) => row("app_context_version", index + 1, "current", "passed"));
  full[0] = row("concept", 1, "current", "skipped");
  assert.deepEqual(familiesToFollowUp(full, ["concept", "theme_capture", "app_bundle"]), ["theme_capture", "app_bundle"]);
});

test("request counts: one history read per visit unless a full page hides documents", async () => {
  const api = await import(pathToFileURL(path.join(pages, "projectRoomApi.js")).href);
  const requests = [];
  let firstPage = [];
  const pageFor = (url) => {
    const params = new URL(url, "http://local").searchParams;
    const family = params.get("build_family");
    return family ? [row(family, 1, "current", family === "app_bundle" ? "passed" : "skipped")] : firstPage;
  };
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    requests.push(String(url));
    return { ok: true, json: async () => ({ artifact_versions: pageFor(url) }) };
  };
  try {
    firstPage = [row("concept", 1, "current", "skipped"), row("app_bundle", 1, "draft", "pending")];
    let project = await api.fetchProjectVersions("appreg_1");
    assert.equal(project.requests, 1);
    assert.equal(requests.length, 1);
    assert.match(requests[0], /\/api\/studio\/build\/history\?build_registry_id=appreg_1&limit=100$/);
    assert.equal(project.capped, false);

    // A full page of index records: each missing document family is read once.
    requests.length = 0;
    firstPage = Array.from({ length: 100 }, (_, index) => row("app_context_version", index + 1, "current", "passed"));
    firstPage[0] = row("concept", 1, "current", "skipped");
    project = await api.fetchProjectVersions("appreg_1");
    assert.equal(project.requests, 11);
    assert.equal(requests.length, 11);
    assert.equal(project.capped, true);
    assert.ok(!requests.some((url) => url.includes("build_family=concept")));

    // My Apps: one read, plus one for the app version only when a full page left it out.
    requests.length = 0;
    let recent = await api.fetchRecentVersions("appreg_1");
    assert.equal(recent.requests, 2);
    assert.equal(requests.length, 2);
    assert.match(requests[1], /build_family=app_bundle/);
    assert.deepEqual(recent.unknownFamilies, ["theme_capture", "design_docs", "subscription_contract", "workflow_bundle"]);
    requests.length = 0;
    firstPage = [row("concept", 1, "current", "skipped")];
    recent = await api.fetchRecentVersions("appreg_1");
    assert.equal(recent.requests, 1);
    assert.deepEqual(recent.unknownFamilies, []);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("a failed first history read fails the Project read; a failed follow-up leaves its family unknown", async () => {
  const api = await import(pathToFileURL(path.join(pages, "projectRoomApi.js")).href);
  const originalFetch = globalThis.fetch;
  try {
    globalThis.fetch = async () => ({ ok: false, status: 429, json: async () => ({}) });
    await assert.rejects(api.fetchProjectVersions("appreg_1"), /429/);
    const full = Array.from({ length: 100 }, (_, index) => row("app_context_version", index + 1, "current", "passed"));
    globalThis.fetch = async (url) => (String(url).includes("build_family=risk_report")
      ? { ok: false, status: 429, json: async () => ({}) }
      : { ok: true, json: async () => ({ artifact_versions: String(url).includes("build_family") ? [] : full }) });
    const result = await api.fetchProjectVersions("appreg_1");
    assert.deepEqual(result.unknownFamilies, ["risk_report"]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
