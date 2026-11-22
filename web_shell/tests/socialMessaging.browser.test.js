import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import test from "node:test";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const { build } = require("esbuild");
const { chromium } = require("@playwright/test");
const root = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const modules = path.join(root, "web_shell/node_modules");

test("materialized profile controls send friend requests, accept them, and reply to messages", async (t) => {
  const entry = `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import {FriendListTab} from ${JSON.stringify(path.join(root, "factory_app/build_context/social/templates/ui/components/SocialProfileTabs.jsx"))};
    import Messages from ${JSON.stringify(path.join(root, "factory_app/build_context/messaging/templates/ui/components/MessagingProfileTab.jsx"))};
    import Owner from ${JSON.stringify(path.join(root, "factory_app/workflows/_shared/ui/app_preview/OwnerAccessCard.jsx"))};
    window.fetch=async()=>Response.json({user_id:'charlie'}); window.actions=[]; window.requests=[{request_id:'incoming',requester_name:'Bob'}]; window.friends=[];
    window.messages=[{message_id:'one',sender_id:'bob',body:'Hello Alice'}];
    const threads=[{thread_id:'thread',title:'Bob'}];
    createRoot(document.getElementById('root')).render(<><Owner files={{'app.json':JSON.stringify({admins:['creator@example.test']})}}/><FriendListTab data={{friends:[]}} isOwner/><Messages data={{threads}}/></>);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: root, loader: "jsx" },
    bundle: true,
    write: false,
    format: "esm",
    jsx: "automatic",
    nodePaths: [modules],
    define: { "process.env.NODE_ENV": '"test"' },
    plugins: [
      {
        name: "module-api",
        setup(builder) {
          builder.onResolve({ filter: /moduleApi\.js$/ }, () => ({
            path: "api",
            namespace: "fixture",
          }));
          builder.onLoad({ filter: /.*/, namespace: "fixture" }, () => ({
            loader: "js",
            contents: `
        export const API_BASE=''; export function authHeaders(){return {Authorization:'Bearer fixture'};}
        export async function moduleAction(module, action, params){
          window.actions.push({module,action,params});
          if(action==='list_friend_requests')return {requests:window.requests};
          if(action==='list_friends')return {friends:window.friends};
          if(action==='accept_friend_request'){window.requests=[];window.friends=[{friendship_id:'friend',friend_user_id:'bob'}];}
          if(action==='create_thread')return {success:true,thread:{thread_id:'created',title:'Charlie'}};
          if(action==='get_thread')return {thread:{thread_id:'thread',title:'Bob'},messages:window.messages};
          if(action==='send_message'&&window.refuseSend)return {success:false,error:'Access denied'};
          if(action==='send_message')window.messages.push({message_id:'two',sender_id:'alice',body:params.body});
          return {success:true};
        }`,
          }));
        },
      },
    ],
  });
  const server = http.createServer((req, res) => {
    res.setHeader(
      "Content-Type",
      req.url === "/fixture.js" ? "text/javascript" : "text/html",
    );
    res.end(
      req.url === "/fixture.js"
        ? bundle.outputFiles[0].text
        : '<div id="root"></div><script type="module" src="/fixture.js"></script>',
    );
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByText(/creator@example.test/).waitFor();
  await page.getByLabel("Friend's username").fill("charlie");
  await page.getByRole("button", { name: "Send friend request" }).click();
  await page.waitForFunction(() =>
    window.actions.some((row) => row.action === "send_friend_request"),
  );
  await page.getByRole("button", { name: "Accept request" }).click();
  await page.getByRole("button", { name: "Remove friend" }).waitFor();
  await page.getByText("Hello Alice", { exact: true }).waitFor();
  await page.getByLabel("Message conversation").fill("Hello Bob");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByText("Hello Bob", { exact: true }).waitFor();
  const actions = await page.evaluate(() => window.actions);
  assert.ok(
    actions.some(
      (row) =>
        row.action === "send_friend_request" &&
        row.params.recipient_id === "charlie",
    ),
  );
  assert.ok(
    actions.some(
      (row) =>
        row.action === "accept_friend_request" &&
        row.params.request_id === "incoming",
    ),
  );
  assert.ok(
    actions.some(
      (row) => row.action === "send_message" && row.params.body === "Hello Bob",
    ),
  );
  await page.getByRole("button", {name: "New conversation", exact: true}).click();
  await page.getByLabel("Usernames", {exact: true}).fill("charlie");
  await page.getByRole("button", {name: "Start conversation", exact: true}).click();
  await page.waitForFunction(() => window.actions.some(row => row.action === 'create_thread'));
  assert.equal(await page.getByRole('link', {name: 'New conversation'}).count(), 0);
  await page.evaluate(() => { window.refuseSend = true; });
  await page.getByLabel("Message conversation").fill("Unsent draft");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("alert").filter({ hasText: "Could not send message." }).waitFor();
  assert.equal(await page.getByLabel("Message conversation").inputValue(), "Unsent draft");
  assert.deepEqual(errors, []);
});
