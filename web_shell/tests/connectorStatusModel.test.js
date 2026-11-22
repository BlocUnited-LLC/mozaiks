// A saved key is not a working connection. Studio must not list a service as
// connected when the last check of its connector failed.
import assert from "node:assert/strict";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const pages = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../factory_app/app/admin/pages");
const model = await import(pathToFileURL(path.join(pages, "connectorStatusModel.js")).href);
const {
  connectorCredentialState,
  displayTone,
  failedCheckMessage,
  hasFailedCheck,
  integrationGroup,
  isRejected,
  needsAttention,
  statusLabel,
  withConnectorOverlay,
} = model;

// A catalog row from workspace_integrations.list_integrations.
const item = (overrides = {}) => ({ id: "anthropic", name: "Anthropic", category: "llms", status: "missing", app_usage_count: 0, ...overrides });

// A saved connector as list_workspace_connectors returns it after the provider
// refused the key (captured from a running Studio).
const savedConnector = (health = {}) => ({
  service: "anthropic",
  ready: true,
  status: "active",
  lifecycle_status: "active",
  secret_available: true,
  secret_storage: "mongo",
  health: { status: "unknown", message: null, error_code: null, health_check_supported: true, ...health },
});
const REFUSED = { status: "unhealthy", message: "Anthropic credential is invalid or has insufficient permissions.", error_code: "auth_failed" };
const UNREACHABLE = { status: "unhealthy", message: "Provider health check failed.", error_code: "provider_check_failed" };

const classify = (catalogRow, connector) => {
  const merged = withConnectorOverlay(catalogRow, connector);
  return { group: integrationGroup(merged), label: statusLabel(merged.status, merged), tone: displayTone(merged) };
};

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

test("a check that could not reach the provider also needs attention, but is not called a refused key", () => {
  const connector = savedConnector(UNREACHABLE);
  assert.equal(hasFailedCheck(connector), true);
  assert.equal(isRejected(connector), false);
  assert.equal(classify(item(), connector).group, "attention");
});

test("the other states keep their lists and labels", () => {
  // Expiring soon: still needs attention, and is no longer labelled "Available".
  assert.deepEqual(
    classify(item(), { ...savedConnector({ status: "healthy" }), lifecycle_status: "expiring", days_until_expiry: 3 }),
    { group: "attention", label: "Expiring soon", tone: "warning" },
  );
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

test("credential state for pages that only need to know whether a connector works", () => {
  assert.equal(connectorCredentialState({ secret_available: false }), "missing");
  assert.equal(connectorCredentialState(null), "missing");
  assert.equal(connectorCredentialState(savedConnector({ status: "healthy" })), "ok");
  assert.equal(connectorCredentialState(savedConnector()), "ok");
  assert.equal(connectorCredentialState(savedConnector(REFUSED)), "check_failed");
});

test("after a save, a failed check is reported with the server's reason", () => {
  assert.equal(
    failedCheckMessage(REFUSED, "Anthropic"),
    "The key was saved, but the check with Anthropic failed: Anthropic credential is invalid or has insufficient permissions.",
  );
  assert.equal(failedCheckMessage({ status: "unhealthy" }, ""), "The key was saved, but the check with the provider failed.");
  // A check that passed, did not run, or could not be asked is not a failure.
  assert.equal(failedCheckMessage({ status: "healthy" }, "Anthropic"), null);
  assert.equal(failedCheckMessage({ status: "unknown" }, "Anthropic"), null);
  assert.equal(failedCheckMessage(null, "Anthropic"), null);
});
