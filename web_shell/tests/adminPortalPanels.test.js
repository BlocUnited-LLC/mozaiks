// The admin portal's framework panels show only numbers an admin endpoint
// really returns. Token and cost numbers come from /api/admin/usage; the
// lifecycle endpoints (/stats, /runs, /sessions) carry none.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "esbuild";

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const admin = path.resolve(shell, "../chat-ui/src/admin");
const require = createRequire(import.meta.url);

const usageHelpers = await import(pathToFileURL(path.join(admin, "adminUsage.js")).href);
const { USAGE_ENDPOINT, USAGE_EVENT_LIMIT } = usageHelpers;

// Test doubles for the shared atoms: plain markup, and a fetch hook that reads
// the endpoint's response from a fixture table.
const primitives = `import React from "react";
const fixtures = () => globalThis.__ADMIN_FIXTURES__ || {};
export const useAdminFetch = (endpoint) => ({
  data: Object.prototype.hasOwnProperty.call(fixtures(), endpoint) ? fixtures()[endpoint] : null,
  loading: false,
  error: (globalThis.__ADMIN_ERRORS__ || {})[endpoint] || null,
  refresh() {},
});
export const StatCard = ({ label, value, sub }) => (
  <div data-stat={label}><b>{value ?? "—"}</b>{sub ? <i>{sub}</i> : null}</div>
);
export const SectionHeading = ({ children }) => <h2>{children}</h2>;
export const Badge = ({ children, variant }) => <span data-variant={variant}>{children}</span>;
export const ErrorBox = ({ message }) => <p role="alert">{message}</p>;
export const Spinner = () => <p>Loading…</p>;
`;

async function loadSections() {
  const bundle = await build({
    stdin: {
      contents: `import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { OverviewSection } from ${JSON.stringify(path.join(admin, "pages/OverviewSection.jsx"))};
import { UsageSection } from ${JSON.stringify(path.join(admin, "pages/UsageSection.jsx"))};
import { OperationsSection } from ${JSON.stringify(path.join(admin, "pages/OperationsSection.jsx"))};
const sections = { OverviewSection, UsageSection, OperationsSection };
export const render = (name, props = {}) => renderToStaticMarkup(React.createElement(sections[name], props));
`,
      resolveDir: shell,
      loader: "jsx",
    },
    bundle: true,
    write: false,
    format: "cjs",
    platform: "node",
    jsx: "automatic",
    alias: {
      react: path.join(shell, "node_modules/react"),
      "react-dom": path.join(shell, "node_modules/react-dom"),
    },
    plugins: [{
      name: "admin-primitives-double",
      setup(builder) {
        builder.onResolve({ filter: /AdminPrimitives\.jsx$/ }, (args) => ({ path: args.path, namespace: "double" }));
        builder.onLoad({ filter: /.*/, namespace: "double" }, () => ({
          contents: primitives,
          loader: "jsx",
          resolveDir: shell,
        }));
      },
    }],
  });
  const loaded = { exports: {} };
  new Function("require", "module", "exports", bundle.outputFiles[0].text)(require, loaded, loaded.exports);
  return loaded.exports.render;
}

const render = await loadSections();

function withFixtures(fixtures, errors, run) {
  globalThis.__ADMIN_FIXTURES__ = fixtures;
  globalThis.__ADMIN_ERRORS__ = errors || {};
  try {
    return run();
  } finally {
    delete globalThis.__ADMIN_FIXTURES__;
    delete globalThis.__ADMIN_ERRORS__;
  }
}

const stat = (html, label) => {
  const match = new RegExp(`<div data-stat="${label}"><b>(.*?)</b>(?:<i>(.*?)</i>)?</div>`).exec(html);
  assert.ok(match, `no "${label}" card in ${html}`);
  return { value: match[1], sub: match[2] || null };
};

// Body rows of the runs or sessions table, as the text of each cell.
const tableRows = (html) =>
  [...html.matchAll(/<tr class="border-b border-border\/50[^>]*>(.*?)<\/tr>/g)].map((m) =>
    [...m[1].matchAll(/<td[^>]*>(.*?)<\/td>/g)].map((cell) => cell[1].replace(/<[^>]+>/g, "")),
  );

const STATS = { active_chats: 2, tracked_chats: 10, completed_chats: 7, failed_chats: 1 };
const usage = (overrides = {}) => ({
  totals: {
    prompt_tokens: 1000,
    completion_tokens: 234,
    total_tokens: 1234,
    llm_calls: 12,
    estimated_cost_usd: 0.0123,
  },
  cost_source: "default_table",
  by_run: [],
  ...overrides,
});

test("usage endpoint asks for the cap it reports", () => {
  assert.equal(USAGE_ENDPOINT, `/api/admin/usage?limit=${USAGE_EVENT_LIMIT}`);
});

test("usage summary says which calls the totals cover and whether cost is priced", () => {
  const { summarizeUsage } = usageHelpers;
  assert.equal(summarizeUsage(null), null);
  assert.equal(summarizeUsage({}), null);

  const priced = summarizeUsage(usage());
  assert.deepEqual(
    [priced.totalTokens, priced.promptTokens, priced.completionTokens, priced.modelCalls, priced.cost],
    [1234, 1000, 234, 12, 0.0123],
  );
  assert.equal(priced.windowNote, "All recorded model calls");

  const unpriced = summarizeUsage(usage({ cost_source: "not_configured", totals: { ...usage().totals, estimated_cost_usd: 0 } }));
  assert.equal(unpriced.cost, null);
  assert.equal(unpriced.costNote, "No price is set for the models used");

  const capped = summarizeUsage(usage({ totals: { ...usage().totals, llm_calls: USAGE_EVENT_LIMIT } }));
  assert.equal(capped.windowNote, `Latest ${USAGE_EVENT_LIMIT.toLocaleString()} model calls`);

  // "mixed" only says the calls were priced from more than one source. The note
  // must come from whether any call has no price at all.
  assert.equal(summarizeUsage(usage({ cost_source: "mixed" })).costNote, "Estimated from model prices");
  assert.equal(
    summarizeUsage(usage({ cost_source: "mixed", pricing_health: { unpriced_event_count: 0 } })).costNote,
    "Estimated from model prices",
  );
  assert.equal(
    summarizeUsage(usage({ cost_source: "mixed", pricing_health: { unpriced_event_count: 1 } })).costNote,
    "Some models have no price set",
  );
  assert.equal(
    summarizeUsage(usage({ cost_source: "mixed", events: [{ cost_source: "catalog" }, { cost_source: "not_configured" }] })).costNote,
    "Some models have no price set",
  );
});

test("formatters never turn a missing number into a zero", () => {
  const { formatCost, formatCount, formatDuration, sessionStatus } = usageHelpers;
  assert.equal(formatCount(undefined), "—");
  assert.equal(formatCount(null), "—");
  assert.equal(formatCount(0), "0");
  assert.equal(formatCost(null), "—");
  assert.equal(formatCost(0.0123), "$0.0123");
  assert.equal(formatCost(12.5), "$12.50");
  assert.equal(formatDuration(undefined), "—");
  assert.equal(formatDuration(42.25), "42.3s");
  assert.equal(formatDuration(125), "2m 5s");
  assert.deepEqual(sessionStatus(0), { label: "in progress", variant: "warning" });
  assert.deepEqual(sessionStatus(1), { label: "complete", variant: "success" });
  assert.deepEqual(sessionStatus(2), { label: "failed", variant: "error" });
});

test("overview shows conversation counts and usage totals from their own endpoints", () => {
  const html = withFixtures({ "/api/admin/stats": STATS, [USAGE_ENDPOINT]: usage() }, null, () => render("OverviewSection"));

  assert.equal(stat(html, "Running now").value, "2");
  assert.equal(stat(html, "Conversations").value, "10");
  assert.equal(stat(html, "Completed").value, "7");
  assert.deepEqual(stat(html, "Failed"), { value: "1", sub: "Ended with an error" });
  assert.deepEqual(stat(html, "Tokens used"), { value: "1,234", sub: "All recorded model calls" });
  assert.equal(stat(html, "Model calls").value, "12");
  assert.equal(stat(html, "Estimated cost").value, "$0.0123");
  // Cards the server has no number for are gone, not shown as zero.
  for (const removed of ["Errors", "Agent Turns", "Admin Extensions", "Runtime Panels", "Token Usage"]) {
    assert.ok(!html.includes(`data-stat="${removed}"`), `${removed} card should not exist`);
  }
});

test("overview shows no dollar figure when no model has a price", () => {
  const unpriced = usage({ cost_source: "not_configured", totals: { ...usage().totals, estimated_cost_usd: 0 } });
  const html = withFixtures({ "/api/admin/stats": STATS, [USAGE_ENDPOINT]: unpriced }, null, () => render("OverviewSection"));

  assert.deepEqual(stat(html, "Estimated cost"), { value: "—", sub: "No price is set for the models used" });
  assert.ok(!html.includes("$"), "an unpriced app must not show a dollar amount");
});

test("overview reports an endpoint failure instead of zeros", () => {
  const html = withFixtures(
    { [USAGE_ENDPOINT]: usage() },
    { "/api/admin/stats": "500 Internal Server Error" },
    () => render("OverviewSection"),
  );

  assert.match(html, /role="alert">Conversation counts unavailable: 500 Internal Server Error/);
  assert.ok(!html.includes('data-stat="Running now"'));
  assert.equal(stat(html, "Tokens used").value, "1,234");
});

test("usage page joins each run to its ledger row and marks failed runs failed", () => {
  const runs = {
    runs: [
      { chat_id: "c-running", workflow_name: "Plan", app_id: "a", user_id: "u1", status: 0, agent_turns: 3, runtime_sec: 12, ended_at: null },
      { chat_id: "c-done", workflow_name: "Build", app_id: "a", user_id: "u2", status: 1, agent_turns: 9, runtime_sec: 75, ended_at: "2026-05-08T12:00:00Z" },
      { chat_id: "c-failed", workflow_name: "Ship", app_id: "a", user_id: "u3", status: 2, agent_turns: 4, runtime_sec: 30, ended_at: "2026-05-08T12:01:00Z" },
    ],
  };
  const ledger = usage({
    by_run: [{ chat_id: "c-done", total_tokens: 5000, estimated_cost_usd: 0.25 }],
  });
  const html = withFixtures(
    { "/api/admin/runs": runs, [USAGE_ENDPOINT]: ledger },
    null,
    () => render("UsageSection", { runtimePanels: [{ id: "stats", page: "usage" }, { id: "runs", page: "usage" }] }),
  );

  assert.deepEqual(stat(html, "Total tokens"), { value: "1,234", sub: "All recorded model calls" });
  assert.equal(stat(html, "Input tokens").value, "1,000");
  assert.equal(stat(html, "Output tokens").value, "234");
  assert.equal(stat(html, "Model calls").value, "12");

  assert.deepEqual(tableRows(html), [
    ["Plan", "a", "u1", "in progress", "3", "—", "—", "12.0s"],
    ["Build", "a", "u2", "complete", "9", "5,000", "$0.2500", "1m 15s"],
    ["Ship", "a", "u3", "failed", "4", "—", "—", "30.0s"],
  ]);
  assert.match(html, /data-variant="error">failed/);
});

test("a run whose models have no price shows no dollar figure, even when other runs are priced", () => {
  const run = (chat_id, workflow_name) => ({ chat_id, workflow_name, app_id: "a", user_id: "u", status: 1, agent_turns: 1, runtime_sec: 5 });
  const runs = { runs: [run("c-priced", "Priced"), run("c-unpriced", "Unpriced"), run("c-part", "Part")] };
  // The shape /api/admin/usage returns: per-run totals in by_run, and the
  // price source of each model call in events.
  const ledger = usage({
    cost_source: "mixed",
    by_run: [
      { chat_id: "c-priced", total_tokens: 3040, estimated_cost_usd: 0.000699 },
      { chat_id: "c-unpriced", total_tokens: 810, estimated_cost_usd: 0 },
      { chat_id: "c-part", total_tokens: 500, estimated_cost_usd: 0.0002 },
    ],
    events: [
      { chat_id: "c-priced", cost_source: "catalog" },
      { chat_id: "c-unpriced", cost_source: "not_configured" },
      { chat_id: "c-part", cost_source: "not_configured" },
      { chat_id: "c-part", cost_source: "catalog" },
    ],
    pricing_health: { unpriced_event_count: 2 },
  });
  const html = withFixtures(
    { "/api/admin/runs": runs, [USAGE_ENDPOINT]: ledger },
    null,
    () => render("UsageSection", { runtimePanels: [{ id: "stats", page: "usage" }, { id: "runs", page: "usage" }] }),
  );

  assert.deepEqual(tableRows(html), [
    ["Priced", "a", "u", "complete", "1", "3,040", "$0.0007", "5.0s"],
    // A cost of 0 for unpriced models is not a measurement.
    ["Unpriced", "a", "u", "complete", "1", "810", "—", "5.0s"],
    // Partly priced: the priced calls are shown, and the total says some are not.
    ["Part", "a", "u", "complete", "1", "500", "$0.0002", "5.0s"],
  ]);
  assert.deepEqual(stat(html, "Estimated cost"), { value: "$0.0123", sub: "Some models have no price set" });
});

test("usage page renders only the panels the config names", () => {
  const html = withFixtures({ [USAGE_ENDPOINT]: usage() }, null, () =>
    render("UsageSection", { runtimePanels: [{ id: "stats", label: "AI Usage", page: "usage" }] }),
  );
  assert.match(html, /<h2>AI Usage<\/h2>/);
  assert.ok(!html.includes("Recent Runs"));
});

test("operations lists recent sessions by status with no token or cost column", () => {
  const sessions = {
    sessions: [0, 1, 2].map((status) => ({
      id: `s${status}`,
      workflow_name: `W${status}`,
      app_id: "a",
      user_id: `u${status}`,
      status,
      duration_sec: 10 * (status + 1),
      created_at: "2026-05-08T12:00:00Z",
    })),
  };
  const html = withFixtures({ "/api/admin/sessions?limit=25": sessions }, null, () =>
    render("OperationsSection", { runtimePanels: [{ id: "sessions", page: "operations" }] }),
  );

  assert.match(html, /<h2>Recent Sessions<\/h2>/);
  assert.match(html, /data-variant="warning">in progress/);
  assert.match(html, /data-variant="success">complete/);
  assert.match(html, /data-variant="error">failed/);
  const headers = [...html.matchAll(/<th\s[^>]*>(.*?)<\/th>/g)].map((m) => m[1]);
  assert.deepEqual(headers, ["Workflow", "App", "User", "Status", "Duration", "Started"]);
});

test("operations reports a failed sessions query", () => {
  const html = withFixtures(
    { "/api/admin/sessions?limit=25": { sessions: [], total: 0, error: "Sessions query failed" } },
    null,
    () => render("OperationsSection", { runtimePanels: [{ id: "sessions", page: "operations" }] }),
  );
  assert.match(html, /role="alert">Sessions unavailable: Sessions query failed/);
  assert.ok(!html.includes("No sessions recorded yet."));
});
