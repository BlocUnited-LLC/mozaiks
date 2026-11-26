import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';
import { reactAlias } from './artifactPreviewFixture.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const src = file => JSON.stringify(path.resolve(shell, '../chat-ui/src', file));

test('a saved workflow opens after catalog discovery recovers', async t => {
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React from 'react';
      import { createRoot } from 'react-dom/client';
      import { BrowserRouter } from 'react-router-dom';
      import ChatPage from ${src('pages/ChatPage.js')};
      import { ChatUIProvider } from ${src('context/ChatUIContext.jsx')};
      import NavigationProvider from ${src('providers/NavigationProvider.jsx')};
      import { WebSocketApiAdapter } from ${src('adapters/api.js')};
      import catalog from ${src('config/workflowConfig.js')};
      const auth = { getAccessToken: () => 'fixture', getCurrentUser: async () => ({id:'owner'}) };
      window.mozaiksAuth = auth;
      window.refreshCatalog = () => catalog.fetchWorkflowConfigs();
      const api = new WebSocketApiAdapter({baseUrl:location.origin, wsUrl:'ws://catalog.test', auth});
      createRoot(document.querySelector('main')).render(
        <BrowserRouter><NavigationProvider config={{appId:'sample', chat_startup_mode:'workflow', entry_point:'Study'}}>
          <ChatUIProvider authAdapter={auth} apiAdapter={api} uiConfig={{chat:{defaultAppId:'sample'}}}>
            <ChatPage/>
          </ChatUIProvider>
        </NavigationProvider></BrowserRouter>);
    ` },
    bundle: true, write: false, format: 'esm', outdir: 'out', jsx: 'automatic',
    loader: { '.js': 'jsx', '.png': 'dataurl' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { ...reactAlias, 'react-router-dom':path.join(shell, 'node_modules/react-router-dom') },
    define: { 'process.env.NODE_ENV':'"test"', 'process.env':'{}', 'import.meta.env':'{}' },
    plugins: [{ name: 'test-workflows', setup(builder) {
      builder.onResolve({filter:/^virtual:mozaiks-workflow-ui$/}, () => ({path:'registry',namespace:'fixture'}));
      builder.onLoad({filter:/.*/,namespace:'fixture'}, () => ({contents:'export default {}; export const transitionComponents={};'}));
    } }],
  });
  const browser = await chromium.launch();
  t.after(() => browser.close());
  for (const recovery of ['Reconnect', 'background discovery']) {
    await t.test(recovery, async t => {
      const page = await browser.newPage();
      t.after(() => page.close());
      const errors = [], sockets = [], closed = [], starts = [], turns = [];
      let activeSocket;
      let available = false, discoveryFailed = false;
      page.on('pageerror', error => errors.push(error.message));
      page.on('console', entry => {
        if (entry.text().includes('All workflow fetch attempts failed')) discoveryFailed = true;
      });
      await page.route('**/*', async route => {
        const { pathname } = new URL(route.request().url());
        if (pathname === '/chat') return route.fulfill({ contentType:'text/html', body:'<!doctype html><main></main>' });
        if (pathname === '/api/workflows') return route.fulfill(available
          ? { json:{workflows:[{name:'Study', startup_mode:'AgentDriven'}]} } : {status:503});
        if (pathname.includes('/api/chats/exists/')) return route.fulfill({json:{exists:true,workflow_name:'Study'}});
        if (pathname.includes('/api/chats/meta/')) return route.fulfill({json:{chat_id:'saved-study',workflow_name:'Study',status:0}});
        if (pathname.endsWith('/start') && route.request().method() === 'POST') starts.push(pathname);
        return route.fulfill({json:{}});
      });
      await page.routeWebSocket('**/ws/**', socket => {
        activeSocket = socket;
        const pathname = new URL(socket.url()).pathname;
        sockets.push(pathname);
        socket.onClose(() => closed.push(pathname));
        socket.onMessage(message => {
          const frame = JSON.parse(message);
          if (frame.type === 'user.input.submit') turns.push(frame);
          if (frame.type === 'chat.switch_workflow') {
            socket.send(JSON.stringify({type:'chat.text', content:'Saved study conversation', agent:'Guide'}));
            socket.send(JSON.stringify({type:'chat.awaiting_reply', data:{chat_id:'saved-study'}}));
          }
        });
        socket.send(JSON.stringify({type:'chat_meta', data:{chat_id:'saved-study', workflow_name:'Study', chat_exists:true}}));
      });
      await page.goto('http://catalog.test/chat?workflow=Study&chat_id=saved-study&mode=workflow');
      await page.addScriptTag({type:'module',content:bundle.outputFiles.find(file => file.path.endsWith('.js')).text});
      await expect.poll(() => errors[0] || discoveryFailed).toBe(true);
      assert.deepEqual(sockets, []);
      available = true;
      if (recovery === 'Reconnect') await page.getByRole('button', {name:'Reconnect'}).click();
      else await page.evaluate(() => window.refreshCatalog());
      await expect(page.getByText('Saved study conversation', {exact:true})).toBeVisible();
      assert.deepEqual(closed, []);
      await expect.poll(() => errors[0] || sockets).toEqual(['/ws/Study/sample/saved-study/owner']);
      assert.deepEqual(starts, []);
      const accepted = recovery === 'Reconnect';
      await t.test(accepted ? 'retry after token top-up' : 'continue after uncertain delivery', async () => {
        const input = page.getByRole('textbox').last();
        const send = page.getByRole('button', {name:'Send message'}).last();
        await input.fill('Review the study');
        await send.click();
        await expect.poll(() => turns.length).toBe(1);
        const receipt = (type, data) => activeSocket.send(JSON.stringify({type, data}));
        const ids = {chat_id:'saved-study', client_message_id:turns[0].context.client_message_id};
        const error = accepted
          ? {error_code:'INSUFFICIENT_TOKENS', message:'Insufficient token balance to run this AI action.'}
          : {error_code:'USER_MESSAGE_FAILED', message:'User message failed'};
        if (accepted) receipt('chat.input_ack', {...ids,status:'accepted'});
        await input.fill('My follow-up');
        if (accepted) {
          receipt('error', {chat_id:ids.chat_id,...error});
          await expect(page.getByText(error.message,{exact:false})).toBeVisible();
        }
        const failure = {...ids,...error,delivery_state:accepted ? 'accepted' : 'unknown'};
        receipt('chat.error', {...failure,client_message_id:'previous-send'});
        receipt('chat.error', {...failure,chat_id:'other-room'});
        receipt('chat.error', {...failure,replay:true});
        await expect(send).toBeDisabled();
        receipt('chat.error', failure);
        await expect(send).toBeEnabled();
        await expect(input).toHaveValue('My follow-up');
        await expect(page.getByRole('status',{name:'Assistant is typing'})).toHaveCount(0);
        await expect(page.getByText('View unsent message')).toHaveCount(0);
        assert.equal(turns.length, 1);
        await send.click();
        await expect.poll(() => turns.length).toBe(2);
        assert.equal(turns[1].text,'My follow-up');
        await input.fill('My next draft');
        receipt('chat.error', failure);
        await expect(send).toBeDisabled();
        await expect(input).toHaveValue('My next draft');
      });
    });
  }
});
