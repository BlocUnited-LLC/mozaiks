import fs from "node:fs";
import path from "node:path";
import http from "node:http";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { build } = require("esbuild");
const { chromium, expect } = require("@playwright/test");
const config = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const entry = `
  import React from 'react';
  import {createRoot} from 'react-dom/client';
  import {FriendListTab} from ${JSON.stringify(path.join(config.workspace, "ui/components/SocialProfileTabs.jsx"))};
  import Messages from ${JSON.stringify(path.join(config.workspace, "ui/components/MessagingProfileTab.jsx"))};
  import {moduleAction} from ${JSON.stringify(path.join(config.workspace, "ui/lib/moduleApi.js"))};
  const tokens=${JSON.stringify(config.tokens)};
  let actor='alice';
  window.mozaiksAuth={getAccessToken:()=>tokens[actor]};
  const root=createRoot(document.getElementById('root'));
  async function show(user){
    actor=user;
    const [friends,messages]=await Promise.all([
      moduleAction('friends','list_friends',{}),
      moduleAction('messages','list_threads',{})
    ]);
    root.render(<main key={actor}>
      <h1>{actor}</h1>
      {Object.keys(tokens).map(user=><button key={user} onClick={()=>show(user)}>View {user}</button>)}
      <FriendListTab data={friends} isOwner/>
      <Messages data={messages}/>
    </main>);
  }
  show(actor);
`;
const bundle = await build({
  stdin: { contents: entry, resolveDir: shell, loader: "jsx" },
  bundle: true,
  write: false,
  format: "esm",
  jsx: "automatic",
  nodePaths: [path.join(shell, "node_modules")],
  define: {
    "import.meta.env.VITE_API_URL": JSON.stringify(config.api),
    "process.env.NODE_ENV": '"test"',
  },
});
const server = http.createServer((request, response) => {
  const script = request.url === "/fixture.js";
  response.setHeader("Content-Type", script ? "text/javascript" : "text/html");
  response.end(
    script
      ? bundle.outputFiles[0].text
      : '<main id="root"></main><script type="module" src="/fixture.js"></script>',
  );
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage();
  page.setDefaultTimeout(15000);
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByLabel("Friend's username").fill("charlie");
  await page
    .getByRole("button", { name: "Send friend request", exact: true })
    .click();
  await expect(page.getByLabel("Friend's username")).toHaveValue("");
  await page.getByRole("button", { name: "View charlie", exact: true }).click();
  await page.getByRole("heading", { name: "charlie", exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Accept request", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Remove friend", exact: true })
    .waitFor();
  await page.getByRole("button", { name: "View alice", exact: true }).click();
  await page.getByRole("heading", { name: "alice", exact: true }).waitFor();
  await page.getByLabel("Message conversation").fill("Browser hello");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("paragraph").filter({ hasText: /^Browser hello$/ }).waitFor();
  await page.getByRole("button", { name: "View bob", exact: true }).click();
  await page.getByRole("heading", { name: "bob", exact: true }).waitFor();
  await page.getByRole("paragraph").filter({ hasText: /^Browser hello$/ }).waitFor();
  await page.getByLabel("Message conversation").fill("Browser reply");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("paragraph").filter({ hasText: /^Browser reply$/ }).waitFor();
  await page.reload();
  await page.getByRole("paragraph").filter({ hasText: /^Browser reply$/ }).waitFor();
  expect(errors).toEqual([]);
  console.log(
    "Real HTTP/Mongo browser journey passed friend request, acceptance, DM, reply, and reload.",
  );
} finally {
  await browser.close();
  await new Promise((resolve) => server.close(resolve));
}
