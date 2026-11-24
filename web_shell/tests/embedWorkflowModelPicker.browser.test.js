import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';
import { reactAlias } from './artifactPreviewFixture.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const embed = path.resolve(shell, '../chat-ui/src/embed/MozaiksEmbed.jsx');

test('hosted inline and floating chat widgets scope and confirm model changes before sending', async t => {
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: [
      'import React from "react"; import {createRoot} from "react-dom/client";',
      'import {MozaiksEmbed} from '+JSON.stringify(embed)+';',
      'function Widget() { const [url,setUrl]=React.useState("https://runtime.test"); window.moveWidget=setUrl;',
      'return <MozaiksEmbed appId="orchard" workflowName="Review" runtimeUrl={url}',
      'userId="orchard-owner" authToken="widget-fixture" mode={location.pathname === "/inline" ? "inline" : "floating"}/>; }',
      'createRoot(document.querySelector("main")).render(<Widget/>);',
    ].join('\n') },
    write: false, bundle: true, jsx: 'automatic', format: 'esm', loader: {'.js':'jsx'},
    alias: reactAlias, nodePaths: [path.join(shell,'node_modules')],
    define: {'process.env.NODE_ENV': '"test"', 'process.env':'{}', 'import.meta.env':'{}'},
  });
  const browser = await chromium.launch();
  t.after(() => browser.close());
  for (const mode of ['inline', 'floating']) {
    await t.test(mode, async t => {
      const page = await browser.newPage();
      t.after(() => page.close());
      const errors = [], lookups = [], writes = [], turns = [];
      page.on('pageerror', error => errors.push(error.message));
      const id = 'chat-' + mode;
      let selection = null, releaseWrite;
      const catalog = () => ({
        default:'openai:small', selected:selection, locked:false,
        models:[{id:'openai:small',label:'Small'},{id:'openai:large',label:'Large'}],
      });
      await page.route('https://site.test/**', route => {
        assert.ok(!new URL(route.request().url()).pathname.startsWith('/api/'), 'Never fetch model settings from the embedding site');
        if (new URL(route.request().url()).pathname === '/fixture.js') {
          return route.fulfill({ contentType:'text/javascript', body:bundle.outputFiles[0].text });
        }
        return route.fulfill({contentType:'text/html',body:'<main></main><script type="module" src="/fixture.js"></script>'});
      });
      await page.route(/^https:\/\/(runtime|alternate)\.test\//, async route => {
        const request=route.request(), pathname=new URL(request.url()).pathname;
        const runtimeChat = new URL(request.url()).hostname === 'alternate.test' ? 'next-' + id : id;
        if (request.method() === 'OPTIONS') return route.fulfill({status:204,headers:{
          'Access-Control-Allow-Origin':'https://site.test', 'Access-Control-Allow-Methods':'GET,PUT,POST,OPTIONS',
          'Access-Control-Allow-Headers':'Authorization,Content-Type,X-User-ID',
        }});
        if (pathname === '/api/chats/orchard/Review/start') return route.fulfill({json:{chat_id:runtimeChat,workflow_name:'Review'}});
        assert.equal(pathname,'/api/chats/model/orchard/Review/'+runtimeChat);
        assert.equal(request.headers().authorization,'Bearer widget-fixture');
        assert.equal(request.headers()['x-user-id'],'orchard-owner');
        if (request.method() === 'PUT') {
          const {model}=request.postDataJSON();
          writes.push(model);
          if (model === 'openai:large') await new Promise(resolve => {releaseWrite=resolve;});
          selection=model;
        } else {
          assert.equal(request.method(),'GET');
          lookups.push(pathname);
        }
        await route.fulfill({json:catalog()});
      });
      await page.routeWebSocket(/^wss:\/\/(runtime|alternate)\.test\/ws\//, socket => {
        socket.onMessage(raw => {
          const frame = JSON.parse(raw);
          if (frame.type !== 'user.input.submit') return;
          assert.equal(frame.chat_id,id);
          turns.push(frame.text);
          socket.send(JSON.stringify(turns.length === 1
            ? {type:'chat.error',data:{error_code:'WORKFLOW_MODEL_UNAVAILABLE',message:'Choose an available model.'}}
            : {type:'chat.stream_end',data:{content:'The review is ready.'}}));
        });
      });
      await page.goto('https://site.test/'+mode);
      if (mode === 'floating') {
        assert.equal(lookups.length,0);
        await page.getByRole('button',{name:'Open Mozaiks AI assistant'}).click();
      }
      const picker=page.getByRole('combobox',{name:'AI model'});
      const input=page.getByRole('textbox'), send=page.getByRole('button',{name:'Send message'});
      await expect(picker).toHaveValue('');
      await input.fill('Review this app.');
      await picker.selectOption('openai:large');
      await expect.poll(()=>writes).toEqual(['openai:large']);
      await expect(send).toBeDisabled();
      await input.press('Enter');
      assert.deepEqual(turns,[]);
      await expect(input).toHaveValue('Review this app.');
      releaseWrite();
      await expect(send).toBeEnabled();
      if (mode === 'floating') {
        await page.getByRole('button', { name: 'Close chat' }).click();
        await page.getByRole('button', { name: 'Open Mozaiks AI assistant' }).click();
        await expect(picker).toHaveValue('openai:large');
        await expect(send).toBeEnabled();
      }
      await send.click();
      await expect(page.getByText('Choose an available model.')).toBeVisible();
      await expect(input).toHaveValue('Review this app.');
      await picker.selectOption('');
      await expect.poll(()=>writes).toEqual(['openai:large',null]);
      await expect(send).toBeEnabled();
      await input.press('Enter');
      await expect(page.getByText('The review is ready.')).toBeVisible();
      assert.deepEqual(turns,['Review this app.','Review this app.']);
      await page.evaluate(()=>window.moveWidget(null));
      await expect(picker).toHaveCount(0);
      await page.evaluate(()=>window.moveWidget('https://alternate.test'));
      await expect(picker).toBeEnabled();
      assert.ok(lookups.includes('/api/chats/model/orchard/Review/next-'+id));
      assert.deepEqual(errors,[]);
    });
  }
});
