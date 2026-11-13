import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ui = path.resolve(shell, "../chat-ui/src");
test("admin pages use declared metadata and preserve Studio section routing", async (t) => {
  const stubs = {
    "../context/ChatUIContext": `export const useChatUI=()=>({user:{roles:['admin']}});`,
    "../admin/components/AdminPrimitives.jsx": `import React from 'react';export const useAdminFetch=()=>({data:{pages:[{id:'notes',label:'Note Administration'}],module_panels:[{id:'notes-panel',label:'Notes panel',page:'notes'}]}});export const AdminExtensionPanels=({panels})=><div>{panels.map(panel=><p key={panel.id}>{panel.label}</p>)}</div>;`,
    "../admin/components/AdminWorkspaceLayout.jsx": `export const AdminWorkspaceLayout=({children})=>children;`
  };
  const sections = `import React from 'react';export const OverviewSection=()=> <h1>Overview</h1>;export const UsersSection=()=> <h1>Users</h1>;export const UsageSection=()=> <h1>Usage</h1>;export const OperationsSection=()=> <h1>Operations</h1>;export const SettingsSection=()=> <h1>Settings</h1>;`;
  const bundle = await build({
    stdin: { contents: `import React from 'react';import {createRoot} from 'react-dom/client';import {BrowserRouter} from 'react-router-dom';import AdminPage from ${JSON.stringify(path.join(ui, "pages/AdminPage.jsx"))};const route=location.pathname==='/admin/notes'?{meta:{adminPage:'notes'}}:undefined;createRoot(document.getElementById('root')).render(<BrowserRouter><AdminPage route={route}/></BrowserRouter>);`, resolveDir: shell, loader: "jsx" },
    bundle: true,
    write: false,
    format: "esm",
    jsx: "automatic",
    alias: { react: path.join(shell, "node_modules/react"), "react-dom": path.join(shell, "node_modules/react-dom"), "react-router-dom": path.join(shell, "node_modules/react-router-dom") },
    plugins: [{ name: "admin-fixtures", setup(builder) {
      builder.onResolve({ filter: /.*/ }, (args) => args.path in stubs ? { path: args.path, namespace: "fixture" } : args.path.startsWith("../admin/pages/") ? { path: args.path, namespace: "section" } : null);
      builder.onLoad({ filter: /.*/, namespace: "fixture" }, (args) => ({ contents: stubs[args.path], loader: "jsx", resolveDir: shell }));
      builder.onLoad({ filter: /.*/, namespace: "section" }, () => ({ contents: sections, loader: "jsx", resolveDir: shell }));
    } }]
  });
  const server = http.createServer((req, res) => {
    const script = req.url === "/fixture.js";
    res.setHeader("Content-Type", script ? "text/javascript" : "text/html");
    res.end(script ? bundle.outputFiles[0].text : '<div id="root"></div><script type="module" src="/fixture.js"><\/script>');
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}/admin/notes`);
  await expect(page.getByRole("heading", { name: "Note Administration", exact: true })).toBeVisible();
  await expect(page.getByText("Notes panel", { exact: true })).toBeVisible();
  assert.equal(await page.getByRole("heading", { name: "Overview", exact: true }).count(), 0);
  await page.goto(`http://127.0.0.1:${server.address().port}/apps/customer/usage`);
  await expect(page.getByRole("heading", { name: "Usage", exact: true })).toBeVisible();
});
