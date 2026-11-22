import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ui = path.resolve(shell, "../chat-ui/src");

// Runtime admin config: one declared custom page, and module panels declared
// out of order so the shell's sort is observable.
const adminConfig = {
  pages: [{ id: "notes", label: "Note Administration" }],
  module_panels: [
    { id: "notes-second", label: "Second notes panel", page: "notes", order: 20 },
    { id: "notes-first", label: "First notes panel", page: "notes", order: 10 },
    { id: "overview-panel", label: "Overview notes panel", page: "overview" },
  ],
};

// What the connected app backend answers for GET /backend/api/admin/config.
const backendPanels = {
  schema_version: "mozaiks.admin.app_backend.v1",
  panels: [
    {
      id: "billing.later",
      label: "Invoices",
      section: "billing",
      order: 20,
      renderer: "schema",
      sections: [{ id: "invoices", primitive: "DataTable", config: { api_endpoint: "/api/admin/invoices" } }],
    },
    {
      id: "billing.first",
      label: "Plans",
      section: "billing",
      order: 10,
      renderer: "schema",
      sections: [{ id: "plans", primitive: "DataTable", config: { api_endpoint: "/api/admin/plans" } }],
    },
    { id: "members", label: "Members", section: "access", renderer: "custom_component", component: "MembersPanel" },
  ],
};

test("admin pages use declared metadata and preserve Studio section routing", async (t) => {
  const stubs = {
    "../context/ChatUIContext": `const useChatUI = () => ({
  user: { roles: ["admin"] },
  config: { appBackendUrl: location.origin + "/backend" },
  auth: null,
});
export {
  useChatUI
};
`,
    "../admin/components/AdminPrimitives.jsx": `import React from "react";
const useAdminFetch = () => ({ data: ${JSON.stringify(adminConfig)}, loading: false, error: null });
const AdminExtensionPanels = ({ panels }) => <div>{panels.map((panel) => <p key={panel.id} data-module-panel>{panel.label}</p>)}</div>;
const EmptyState = ({ children }) => <p data-empty>{children}</p>;
const ErrorBox = ({ message }) => <p role="alert">{message}</p>;
const Spinner = () => <p>Loading</p>;
const SectionHeading = ({ children }) => <h2>{children}</h2>;
const MissingPanelComponent = ({ componentName }) => <p data-missing>{componentName}</p>;
const StatCard = () => null;
const Badge = () => null;
export {
  AdminExtensionPanels,
  Badge,
  EmptyState,
  ErrorBox,
  MissingPanelComponent,
  SectionHeading,
  Spinner,
  StatCard,
  useAdminFetch
};
`,
    "../admin/components/AdminSchemaPanel.jsx": `import React from "react";
const AdminSchemaPanel = ({ panel }) => <p data-schema-panel>{panel.sections[0].config.api_endpoint}</p>;
export default AdminSchemaPanel;
`,
    "../registry/componentRegistry": `const getComponent = () => null;
export {
  getComponent
};
`,
    "../admin/components/AdminWorkspaceLayout.jsx": `const AdminWorkspaceLayout = ({ children }) => children;
export {
  AdminWorkspaceLayout
};
`
  };
  const sections = `import React from "react";
const OverviewSection = () => <p data-framework>overview content</p>;
const UsageSection = () => <p data-framework>usage content</p>;
const OperationsSection = () => <p data-framework>operations content</p>;
const SettingsSection = () => <p data-framework>settings content</p>;
export {
  OperationsSection,
  OverviewSection,
  SettingsSection,
  UsageSection
};
`;
  const bundle = await build({
    stdin: { contents: `import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import AdminPage from ${JSON.stringify(path.join(ui, "pages/AdminPage.jsx"))};
const route = location.pathname === "/admin/notes" ? { meta: { adminPage: "notes" } } : void 0;
createRoot(document.getElementById("root")).render(<BrowserRouter><AdminPage route={route} /></BrowserRouter>);
`, resolveDir: shell, loader: "jsx" },
    bundle: true,
    write: false,
    format: "esm",
    jsx: "automatic",
    define: { "process.env.NODE_ENV": '"production"' },
    alias: { react: path.join(shell, "node_modules/react"), "react-dom": path.join(shell, "node_modules/react-dom"), "react-router-dom": path.join(shell, "node_modules/react-router-dom") },
    plugins: [{ name: "admin-fixtures", setup(builder) {
      builder.onResolve({ filter: /.*/ }, (args) => args.path in stubs ? { path: args.path, namespace: "fixture" } : args.path.startsWith("../admin/pages/") ? { path: args.path, namespace: "section" } : null);
      builder.onLoad({ filter: /.*/, namespace: "fixture" }, (args) => ({ contents: stubs[args.path], loader: "jsx", resolveDir: shell }));
      builder.onLoad({ filter: /.*/, namespace: "section" }, () => ({ contents: sections, loader: "jsx", resolveDir: shell }));
    } }]
  });
  // "panels" serves the config, "missing" has no admin route, "broken" fails.
  let backendMode = "panels";
  const server = http.createServer((req, res) => {
    if (req.url === "/backend/api/admin/config") {
      res.statusCode = backendMode === "panels" ? 200 : backendMode === "missing" ? 404 : 500;
      res.setHeader("Content-Type", "application/json");
      res.end(backendMode === "panels" ? JSON.stringify(backendPanels) : "{}");
      return;
    }
    const script = req.url === "/fixture.js";
    res.setHeader("Content-Type", script ? "text/javascript" : "text/html");
    res.end(script ? bundle.outputFiles[0].text : '<div id="root"></div><script type="module" src="/fixture.js"><\/script>');
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const origin = `http://127.0.0.1:${server.address().port}`;
  const heading = (name) => page.getByRole("heading", { name, exact: true, level: 1 });
  const modulePanels = () => page.locator("[data-module-panel]").allTextContents();

  // A page the registry declares under its own id: registry label, its panels in `order`.
  await page.goto(`${origin}/admin/notes`);
  await expect(heading("Note Administration")).toBeVisible();
  await expect(page.getByText("First notes panel", { exact: true })).toBeVisible();
  assert.deepEqual(await modulePanels(), ["First notes panel", "Second notes panel"]);
  assert.equal(await heading("Overview").count(), 0);
  assert.equal(await page.locator("[data-framework]").count(), 0);
  assert.equal(await page.locator("[data-empty]").count(), 0);

  // Overview: framework content plus the panels that name it.
  await page.goto(`${origin}/admin`);
  await expect(heading("Overview")).toBeVisible();
  await expect(page.locator("[data-framework]")).toHaveText("overview content");
  assert.deepEqual(await modulePanels(), ["Overview notes panel"]);
  await expect(page.getByText("How your app is doing right now.", { exact: true })).toBeVisible();

  // Studio-style section routing still selects the page from the URL suffix.
  await page.goto(`${origin}/apps/customer/usage`);
  await expect(heading("Usage")).toBeVisible();
  await expect(page.locator("[data-framework]")).toHaveText("usage content");

  // App-backend panels render on the page their `section` names, in `order`.
  await page.goto(`${origin}/apps/customer/billing`);
  await expect(heading("Billing")).toBeVisible();
  await expect(page.locator("[data-schema-panel]")).toHaveText(["/api/admin/plans", "/api/admin/invoices"].map((endpoint) => `${origin}/backend${endpoint}`));
  await expect(page.getByRole("heading", { level: 2 })).toHaveText(["Plans", "Invoices"]);
  assert.equal(await page.locator("[data-empty]").count(), 0);

  // A custom panel whose component is not registered says so.
  await page.goto(`${origin}/apps/customer/access`);
  await expect(page.locator("[data-missing]")).toHaveText("MembersPanel");
  assert.equal(await page.locator("[data-empty]").count(), 0);

  // A standard page with nothing on it says so instead of rendering blank.
  await page.goto(`${origin}/apps/customer/support`);
  await expect(heading("Support")).toBeVisible();
  await expect(page.locator("[data-empty]")).toContainText("Nothing has been added to Support yet.");
  assert.equal(await page.getByRole("alert").count(), 0);

  // A backend with no admin config route contributes no panels and no error.
  backendMode = "missing";
  await page.goto(`${origin}/apps/customer/billing`);
  await expect(page.locator("[data-empty]")).toContainText("Nothing has been added to Billing yet.");
  assert.equal(await page.getByRole("alert").count(), 0);
  await page.goto(`${origin}/apps/customer/access`);
  await expect(page.locator("[data-empty]")).toContainText("sign-in provider");

  // A backend that fails is reported, on a framework page too.
  backendMode = "broken";
  await page.goto(`${origin}/admin`);
  await expect(page.getByRole("alert")).toContainText("Panels from the connected backend could not be loaded: 500");
  await expect(page.locator("[data-framework]")).toHaveText("overview content");
});
