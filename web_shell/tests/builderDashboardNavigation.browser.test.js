import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ui = path.resolve(shell, "../chat-ui/src");
test("builder dashboard menu resolves owned build context on desktop and mobile", async (t) => {
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
}, api: { get: async (url) => { window.dashboardRequests.push(url); if (window.dashboardDelay) await new Promise(resolve => { window.resolveDashboard = resolve; }); if (window.dashboardFailure) throw new Error("unavailable"); return {path: window.dashboardPath}; } } });
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
window.dashboardRequests = []; window.dashboardPath = "/apps/notes-owned/overview";
window.shellNavigation = { landing_spot: "/notes", pages: [{ path: "/notes", label: "Personal Notes", meta: { requiresAuth: true } }], headerPages: [], header: { actions: [] }, profile: { menu: [{ id: "app-dashboard", label: "App Dashboard", path: "/apps" }, { id: "admin", label: "Admin", path: "/admin", requiresRole: "admin" }] }, notifications: { show: false }, auth: { runtime: { enabled: true } } };
function App() {
  const location = useLocation();
  const [actor, setActor] = React.useState({ id: "reader", name: "Reader", roles: ["user"] });
  window.switchUser = setActor;
  return <><Header user={new URLSearchParams(location.search).has("anonymous") ? null : actor} chatTheme={{ branding: { name: "Notes App" } }} /><h1>{location.pathname}</h1></>;
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
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await t.test(`${viewport.width}px account navigation`, async () => {
      const page = await browser.newPage({ viewport });
      page.setDefaultTimeout(5000);
      const origin = `http://127.0.0.1:${server.address().port}`;
      for (const scenario of [
        { route: "/me", target: "/apps", calls: 0 },
        { route: "/chat?workflow=AppGenerator", target: "/apps", calls: 0 },
        { route: "/chat?workflow=AppGenerator&chat_id=chat_owned&app_id=forged", target: "/apps/notes-owned/overview", calls: 1 },
        { route: "/chat?workflow=AppGenerator&chat_id=chat_new", target: "/apps", calls: 1, path: "/apps" },
        { route: "/chat?workflow=AppGenerator&chat_id=chat_foreign", target: "/apps", calls: 1, failure: true },
        { route: "/chat?workflow=AppGenerator&chat_id=chat_owned", target: "/apps", calls: 1, path: "https://foreign.test" },
      ]) {
        await page.goto(origin + scenario.route);
        await page.evaluate(({ path, failure }) => {
          window.dashboardPath = path || "/apps/notes-owned/overview";
          window.dashboardFailure = failure;
        }, scenario);
        await page.getByTitle("Reader", { exact: true }).click();
        assert.equal(await page.getByText("Admin", { exact: true }).count(), 0);
        await page.getByText("App Dashboard", { exact: true }).click();
        await expect(page.getByRole("heading", { name: scenario.target, exact: true })).toBeVisible();
        const calls = await page.evaluate(() => window.dashboardRequests);
        assert.equal(calls.length, scenario.calls);
        if (calls.length) assert.ok(!calls[0].includes("forged"));
      }
      await page.goto(origin + "/chat?workflow=AppGenerator&chat_id=chat_owned");
      await page.evaluate(() => { window.dashboardDelay = true; });
      await page.getByTitle("Reader", { exact: true }).click();
      await page.getByText("App Dashboard", { exact: true }).click();
      await page.waitForFunction(() => typeof window.resolveDashboard === "function");
      await page.evaluate(() => {
        window.history.pushState({}, "", "/me?tab=support-tickets");
        window.dispatchEvent(new PopStateEvent("popstate"));
      });
      await expect(page.getByRole("heading", { name: "/me", exact: true })).toBeVisible();
      await page.evaluate(() => window.resolveDashboard());
      await expect(page).toHaveURL(origin + "/me?tab=support-tickets");
      await page.goto(origin + "/chat?workflow=AppGenerator&chat_id=chat_owned");
      await page.evaluate(() => { window.dashboardDelay = true; });
      await page.getByTitle("Reader", { exact: true }).click();
      await page.getByText("App Dashboard", { exact: true }).click();
      await page.waitForFunction(() => typeof window.resolveDashboard === "function");
      await page.evaluate(() => window.switchUser({ id: "new-reader", name: "New Reader", roles: ["user"] }));
      await expect(page.getByTitle("New Reader", { exact: true })).toBeVisible();
      await page.evaluate(() => window.resolveDashboard());
      await expect(page).toHaveURL(origin + "/chat?workflow=AppGenerator&chat_id=chat_owned");
      await page.goto(origin + "/me?anonymous=1");
      await page.getByTitle("Guest", { exact: true }).click();
      assert.equal(await page.getByText("App Dashboard", { exact: true }).count(), 0);
      await page.close();
    });
  }
});
