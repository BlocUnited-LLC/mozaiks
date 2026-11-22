// A saved key is not a working connection. Studio must not list a service as
// connected when the last check of its connector found it cannot work.
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const pages = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../factory_app/app/admin/pages");
const model = await import(pathToFileURL(path.join(pages, "connectorStatusModel.js")).href);
const {
  checkOutcome,
  checkProblemMessage,
  connectorCredentialState,
  displayTone,
  failedCheckReason,
  hasFailedCheck,
  integrationGroup,
  isRejected,
  needsAttention,
  saveAndCheckConnector,
  statusLabel,
  withConnectorOverlay,
} = model;

// A catalog row from workspace_integrations.list_integrations.
const item = (overrides = {}) => ({ id: "anthropic", name: "Anthropic", category: "llms", status: "missing", app_usage_count: 0, ...overrides });

// A saved connector as list_workspace_connectors returns it. The health
// records below were captured from a running Studio.
const savedConnector = (health = {}, overrides = {}) => ({
  service: "anthropic",
  ready: true,
  status: "active",
  lifecycle_status: "active",
  secret_available: true,
  secret_storage: "mongo",
  health: { status: "pending_validation", message: null, error_code: null, health_check_supported: true, ...health },
  ...overrides,
});
// The provider answered and refused the key.
const REFUSED = { status: "unhealthy", message: "Anthropic credential is invalid or has insufficient permissions.", error_code: "auth_failed" };
// The service needs two values and one was saved. The check says so in
// error_code and leaves status alone, and the connector stays `ready`.
const INCOMPLETE_VALUES = {
  status: "pending_validation",
  message: "Connector has required configuration; run its provider health check before use.",
  error_code: "missing_credentials",
};
// The provider could not be reached. Nothing is known about the key.
const UNREACHABLE = { status: "unknown", message: "Could not reach the provider.", error_code: "timeout" };

const classify = (catalogRow, connector) => {
  const merged = withConnectorOverlay(catalogRow, connector);
  return { group: integrationGroup(merged), label: statusLabel(merged.status, merged), tone: displayTone(merged) };
};

test("what a health record says", () => {
  assert.equal(checkOutcome({ status: "healthy" }), "passed");
  assert.equal(checkOutcome(REFUSED), "failed");
  assert.equal(checkOutcome(INCOMPLETE_VALUES), "failed");
  assert.equal(checkOutcome({ status: "unknown", error_code: "invalid_key_format" }), "failed");
  assert.equal(checkOutcome(UNREACHABLE), "inconclusive");
  assert.equal(checkOutcome({ status: "unknown", error_code: "connection_error" }), "inconclusive");
  // A database provider reports a connection error as unhealthy: that is a failure.
  assert.equal(checkOutcome({ status: "unhealthy", error_code: "connection_error" }), "failed");
  assert.equal(checkOutcome({ status: "pending_validation", error_code: null }), "none");
  assert.equal(checkOutcome(null), "none");
});

test("a saved key whose check passed, or has not run yet, is connected", () => {
  assert.deepEqual(classify(item(), savedConnector({ status: "healthy" })), { group: "connected", label: "Connected", tone: "success" });
  assert.deepEqual(classify(item(), savedConnector()), { group: "connected", label: "Connected", tone: "success" });
});

test("a saved key the provider refused is not listed as connected", () => {
  const connector = savedConnector(REFUSED);
  assert.equal(hasFailedCheck(connector), true);
  assert.equal(isRejected(connector), true);
  assert.deepEqual(classify(item(), connector), { group: "attention", label: "Check failed", tone: "destructive" });
  assert.equal(needsAttention(item(), connector), true);
});

test("a connector whose check found values missing is not listed as connected", () => {
  // The server still calls this connector ready.
  const connector = savedConnector(INCOMPLETE_VALUES);
  assert.equal(hasFailedCheck(connector), true);
  assert.equal(isRejected(connector), false);
  assert.deepEqual(classify(item(), connector), { group: "attention", label: "Check failed", tone: "destructive" });
});

test("a provider that could not be reached does not change the list", () => {
  const connector = savedConnector(UNREACHABLE);
  assert.equal(hasFailedCheck(connector), false);
  assert.deepEqual(classify(item(), connector), { group: "connected", label: "Connected", tone: "success" });
});

test("a failed check is not hidden by an environment variable or an incomplete connector", () => {
  // The service is also configured by an environment variable (status
  // configured), and the saved connector is not ready.
  const notReady = savedConnector(REFUSED, { ready: false, lifecycle_status: null });
  assert.deepEqual(classify(item({ status: "configured" }), notReady), { group: "attention", label: "Check failed", tone: "destructive" });
  // Without a failed check, an incomplete connector leaves the environment status in place.
  const incomplete = savedConnector({}, { ready: false, lifecycle_status: null });
  assert.deepEqual(classify(item({ status: "configured" }), incomplete), { group: "connected", label: "Connected", tone: "success" });
});

test("the other states keep their lists and labels", () => {
  // Expiring soon: still needs attention, and is no longer labelled "Available".
  assert.deepEqual(
    classify(item(), savedConnector({ status: "healthy" }, { lifecycle_status: "expiring", days_until_expiry: 3 })),
    { group: "attention", label: "Expiring soon", tone: "warning" },
  );
  // Expired outranks a failed check.
  assert.equal(classify(item(), savedConnector(REFUSED, { ready: false, lifecycle_status: "expired" })).group, "attention");
  // Saved but incomplete.
  assert.deepEqual(classify(item({ status: "partial" }), null), { group: "attention", label: "Needs setup", tone: "warning" });
  // Nothing saved, an app depends on it.
  assert.deepEqual(classify(item({ app_usage_count: 2 }), null), { group: "attention", label: "Needs setup", tone: "destructive" });
  // Nothing saved, nothing depends on it.
  assert.deepEqual(classify(item(), null), { group: "available", label: "Available", tone: "default" });
  // Configured by an environment variable, no saved connector.
  assert.deepEqual(classify(item({ status: "configured" }), null), { group: "connected", label: "Connected", tone: "success" });
});

test("every service lands in exactly one list", () => {
  const rows = [
    withConnectorOverlay(item({ id: "a" }), savedConnector({ status: "healthy" })),
    withConnectorOverlay(item({ id: "b" }), savedConnector(REFUSED)),
    withConnectorOverlay(item({ id: "c", app_usage_count: 1 }), null),
    withConnectorOverlay(item({ id: "d" }), null),
  ];
  assert.deepEqual(rows.map((row) => `${row.id}:${integrationGroup(row)}`), ["a:connected", "b:attention", "c:attention", "d:available"]);
});

test("whether a connector can be used", () => {
  assert.equal(connectorCredentialState(null), "missing");
  assert.equal(connectorCredentialState({ secret_available: false, ready: false }), "missing");
  assert.equal(connectorCredentialState(savedConnector({ status: "healthy" })), "ok");
  assert.equal(connectorCredentialState(savedConnector()), "ok");
  assert.equal(connectorCredentialState(savedConnector(REFUSED)), "check_failed");
  assert.equal(connectorCredentialState(savedConnector(INCOMPLETE_VALUES)), "check_failed");
  // A credential is saved but the server says setup is not complete.
  assert.equal(connectorCredentialState(savedConnector({}, { ready: false, lifecycle_status: null })), "incomplete");
  assert.equal(connectorCredentialState(savedConnector({ status: "healthy" }, { lifecycle_status: "expired" })), "expired");
  // An older response without `ready` is not called incomplete.
  const { ready, ...withoutReady } = savedConnector({ status: "healthy" });
  assert.equal(ready, true);
  assert.equal(connectorCredentialState(withoutReady), "ok");
});

test("what the owner is told about the check that follows a save", () => {
  assert.equal(
    checkProblemMessage(REFUSED, "Anthropic"),
    "The key was saved, but the check with Anthropic failed: Anthropic credential is invalid or has insufficient permissions.",
  );
  assert.equal(
    checkProblemMessage({ status: "unknown", error_code: "missing_credentials", message: "Twilio requires both TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN." }, "Twilio"),
    "The key was saved, but the check with Twilio failed: Twilio requires both TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN.",
  );
  assert.equal(checkProblemMessage({ status: "unhealthy" }, ""), "The key was saved, but the check with the provider failed.");
  assert.equal(
    checkProblemMessage(UNREACHABLE, "Anthropic"),
    "The key was saved, but Anthropic could not be checked: Could not reach the provider. Use Test connection to try again.",
  );
  // A check that passed, or reported nothing, is not a problem.
  assert.equal(checkProblemMessage({ status: "healthy" }, "Anthropic"), null);
  assert.equal(checkProblemMessage({ status: "pending_validation" }, "Anthropic"), null);
  assert.equal(checkProblemMessage(null, "Anthropic"), null);
});

test("Test connection reports what the check found, without claiming a save", () => {
  assert.equal(
    checkProblemMessage(REFUSED, "Anthropic", { afterSave: false }),
    "The check with Anthropic failed: Anthropic credential is invalid or has insufficient permissions.",
  );
  assert.equal(
    checkProblemMessage(UNREACHABLE, "Anthropic", { afterSave: false }),
    "Anthropic could not be checked: Could not reach the provider. Use Test connection to try again.",
  );
  assert.equal(checkProblemMessage({ status: "healthy" }, "Anthropic", { afterSave: false }), null);
});

test("the stored record gives a reason only when the provider gave one", () => {
  assert.equal(failedCheckReason(savedConnector(REFUSED)), "Anthropic credential is invalid or has insufficient permissions.");
  // Values missing: the stored message is a generic status line, not a reason.
  assert.equal(failedCheckReason(savedConnector(INCOMPLETE_VALUES)), "");
  assert.equal(failedCheckReason(savedConnector({ status: "healthy", message: "ok" })), "");
  assert.equal(failedCheckReason(null), "");
});

// The page's save flow, with the page's three calls replaced by recorders.
function flow({ saveResult = { success: true, health_check_supported: true }, health = { status: "healthy" }, saveError = null, checkError = null }) {
  const calls = [];
  return {
    calls,
    steps: {
      displayName: "Anthropic",
      save: async () => { calls.push("save"); if (saveError) throw saveError; return saveResult; },
      check: async () => { calls.push("check"); if (checkError) throw checkError; return health; },
      reload: async () => { calls.push("reload"); },
    },
  };
}

test("a save asks the provider before it reloads the list", async () => {
  const accepted = flow({});
  assert.deepEqual(await saveAndCheckConnector(accepted.steps), { problem: null });
  // The reload must come after the check, or the list is read before the result exists.
  assert.deepEqual(accepted.calls, ["save", "check", "reload"]);

  const refused = flow({ health: REFUSED });
  assert.deepEqual(await saveAndCheckConnector(refused.steps), {
    problem: "The key was saved, but the check with Anthropic failed: Anthropic credential is invalid or has insufficient permissions.",
  });
  assert.deepEqual(refused.calls, ["save", "check", "reload"]);
});

test("a save for a service with no health check does not call one", async () => {
  const unchecked = flow({ saveResult: { success: true, health_check_supported: false } });
  assert.deepEqual(await saveAndCheckConnector(unchecked.steps), { problem: null });
  assert.deepEqual(unchecked.calls, ["save", "reload"]);
});

test("a check that cannot run is reported, not treated as success", async () => {
  const broken = flow({ checkError: new Error("502 Bad Gateway") });
  assert.deepEqual(await saveAndCheckConnector(broken.steps), {
    problem: "The key was saved, but the check with Anthropic could not run. Use Test connection to try again.",
  });
  assert.deepEqual(broken.calls, ["save", "check", "reload"]);
});

test("a refused save reloads the list and reports the refusal", async () => {
  const refusal = new Error("The key could not be stored.");
  const refused = flow({ saveError: refusal });
  await assert.rejects(() => saveAndCheckConnector(refused.steps), refusal);
  // No check for a key that was not stored; the list is still refreshed.
  assert.deepEqual(refused.calls, ["save", "reload"]);
});
