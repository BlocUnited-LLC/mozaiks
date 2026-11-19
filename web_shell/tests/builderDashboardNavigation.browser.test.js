import assert from "node:assert/strict";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";
import { chromium, expect } from "@playwright/test";
import postcss from "postcss";
import tailwindcss from "@tailwindcss/postcss";
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
import MobileBottomBar from ${JSON.stringify(path.join(ui, "components/layout/MobileBottomBar.jsx"))};
window.dashboardRequests = []; window.dashboardPath = "/apps/notes-owned/overview";
const globalPages = [
  { id: "marketplace", label: "Discover", path: "/marketplace", icon: "store" },
  { id: "operations", label: "Operations", path: "/operations", requiresRole: "admin" },
];
window.shellNavigation = { landing_spot: "/notes", pages: [{ path: "/notes", label: "Personal Notes", meta: { requiresAuth: true } }], headerPages: globalPages, header: { actions: [{ id: "create", label: "Create App", path: "/create" }] }, mobile: { bottomBar: { items: globalPages } }, profile: { menu: [{ id: "app-dashboard", label: "App Dashboard", path: "/apps" }, { id: "admin", label: "Admin", path: "/admin", requiresRole: "admin" }] }, notifications: { show: true }, auth: { runtime: { enabled: true } } };
function App() {
  const location = useLocation();
  const [actor, setActor] = React.useState({ id: "reader", name: "Reader", roles: ["user"] });
  window.switchUser = setActor;
  const user = new URLSearchParams(location.search).has("anonymous") ? null : actor;
  const hasMobileBottomBar = location.pathname === "/me";
  return <>
    <Header user={user} chatTheme={{ branding: { name: "Northumberland Community Membership and International Events Directory" } }} hasMobileBottomBar={hasMobileBottomBar || undefined} />
    {hasMobileBottomBar && <MobileBottomBar />}
    <h1>{location.pathname}</h1>
  </>;
}
createRoot(document.getElementById("root")).render(<BrowserRouter><App /></BrowserRouter>);
`, resolveDir: shell, loader: "jsx" }, alias: { react: path.join(shell, "node_modules/react"), "react-dom": path.join(shell, "node_modules/react-dom"), "react-icons": path.join(shell, "node_modules/react-icons"), "react-router-dom": path.join(shell, "node_modules/react-router-dom") }, bundle: true, write: false, format: "esm", jsx: "automatic", loader: { ".js": "jsx", ".css": "empty", ".png": "dataurl" }, plugins: [{ name: "fixtures", setup(builder) {
    builder.onResolve({ filter: /.*/ }, (args) => args.path in stubs ? { path: args.path, namespace: "fixture" } : null);
    builder.onLoad({ filter: /.*/, namespace: "fixture" }, (args) => ({ contents: stubs[args.path], loader: "js", resolveDir: shell }));
  } }] });
  const stylesheet = path.join(shell, "styles.css");
  const layout = path.join(ui, "components/layout");
  const styles = await postcss([tailwindcss()]).process(
    (await fs.readFile(stylesheet, "utf8")) + '\n@source "' + layout.replaceAll("\\", "/") + '";',
    { from: stylesheet },
  );
  const layoutCss = await fs.readFile(path.join(layout, "header-styles.css"), "utf8");
  const html = `<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles.css}\n${layoutCss}</style><div id="root"></div><script type="module" src="/fixture.js"></script>`;
  const server = http.createServer((req, res) => {
    const script = req.url === "/fixture.js";
    res.setHeader("Content-Type", script ? "text/javascript" : "text/html");
    res.end(script ? bundle.outputFiles[0].text : html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const origin = `http://127.0.0.1:${server.address().port}`;
  for (const width of [320, 390, 767, 768, 957, 1280, 1440]) {
    await t.test(`${width}px Discover and portfolio`, async (t) => {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      t.after(() => page.close());
      for (const route of ["/me", "/chat?workflow=AppGenerator"]) {
        await page.goto(origin + route);
        const navigation = width < 768 && route === "/me"
          ? page.getByRole("navigation", { name: "Mobile app navigation", exact: true })
          : page.getByRole("banner");
        const discover = navigation.getByRole("button", { name: "Discover", exact: true });
        await expect(discover).toBeInViewport({ ratio: 1 });
        await expect(page.getByTitle("Reader", { exact: true })).toBeInViewport({ ratio: 1 });
        await expect(page.getByRole("button", { name: "Discover", exact: true })).toHaveCount(1);
        await expect(page.getByText("Operations", { exact: true })).toHaveCount(0);
        await discover.click();
        await expect(page).toHaveURL(origin + "/marketplace");
        await page.goto(origin + route);
        await page.getByTitle("Reader", { exact: true }).click();
        await page.getByRole("button", { name: "App Dashboard", exact: true }).click();
        await expect(page).toHaveURL(origin + "/apps");
      }
    });
  }
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    await t.test(`${viewport.width}px account navigation`, async () => {
      const page = await browser.newPage({ viewport });
      page.setDefaultTimeout(5000);
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
