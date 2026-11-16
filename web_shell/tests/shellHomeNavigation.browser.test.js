import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ui = path.resolve(shell, "../chat-ui/src");
test("profile menu returns from account and conversation to the app home", async (t) => {
  const stubs = {
    "../../providers/NavigationProvider": `const useNavigation = () => window.shellNavigation;
export {
  useNavigation
};
`,
    "../../navigation/useNavigationActions": `import { useNavigate } from "react-router-dom";
const useNavigationActions = () => {
  const go = useNavigate();
  return (item) => go(item.path || item.href);
};
export {
  useNavigationActions
};
`,
    "../../context/ChatUIContext": `const useChatUI = () => ({ login: () => {
}, logout: () => {
} });
export {
  useChatUI
};
`,
    "../../ui/hooks/useAppEventBus.js": `const useAppEventBus = () => {
};
export {
  useAppEventBus
};
`,
    "./notificationApi.js": `const fetchNotificationCount = async () => null;
const clearNotifications = async () => {
};
export {
  clearNotifications,
  fetchNotificationCount
};
`
  };
  const bundle = await build({ stdin: { contents: `import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, useLocation } from "react-router-dom";
import Header from ${JSON.stringify(path.join(ui, "components/layout/Header.js"))};
window.shellNavigation = { landing_spot: "/notes", pages: [{ path: "/notes", label: "Personal Notes", meta: { requiresAuth: true } }], headerPages: [], header: { actions: [] }, profile: { menu: [{ id: "admin", label: "Admin", path: "/admin", requiresRole: "admin" }] }, notifications: { show: false }, auth: { runtime: { enabled: true } } };
function App() {
  const location = useLocation();
  return <><Header user={{ id: "reader", name: "Reader", roles: ["user"] }} chatTheme={{ branding: { name: "Notes App" } }} /><h1>{location.pathname}</h1></>;
}
createRoot(document.getElementById("root")).render(<BrowserRouter><App /></BrowserRouter>);
`, resolveDir: shell, loader: "jsx" }, alias: { react: path.join(shell, "node_modules/react"), "react-dom": path.join(shell, "node_modules/react-dom"), "react-router-dom": path.join(shell, "node_modules/react-router-dom") }, bundle: true, write: false, format: "esm", jsx: "automatic", loader: { ".js": "jsx", ".css": "empty", ".png": "dataurl" }, plugins: [{ name: "fixtures", setup(builder) {
    builder.onResolve({ filter: /.*/ }, (args) => args.path in stubs ? { path: args.path, namespace: "fixture" } : null);
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, (args) => ({ contents: stubs[args.path], loader: "js", resolveDir: shell }));
  } }] });
  const server = http.createServer((req, res) => {
    const script = req.url === "/fixture.js";
    res.setHeader("Content-Type", script ? "text/javascript" : "text/html");
    res.end(script ? bundle.outputFiles[0].text : '<div id="root"></div><script type="module" src="/fixture.js"><\/script>');
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  for (const route of ["/me", "/chat"]) {
    const page = await browser.newPage();
    page.setDefaultTimeout(5e3);
    page.on("pageerror", (error) => console.log(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}${route}`);
    await expect(page.getByRole("img", { name: "App", exact: true })).toBeVisible();
    await page.getByTitle("Reader", { exact: true }).click();
    assert.equal(await page.getByText("Admin", { exact: true }).count(), 0);
    await page.getByText("Personal Notes", { exact: true }).click();
    await page.getByRole("heading", { name: "/notes", exact: true }).waitFor();
    await page.close();
  }
});
