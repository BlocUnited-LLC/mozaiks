import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { chromium, expect } from '@playwright/test';
import { build } from 'esbuild';
import postcss from 'postcss';
import tailwindcss from '@tailwindcss/postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.join(path.dirname(shell), 'chat-ui/src');

test('the AppReview composer lets the user select a model and preserves it across workflow changes', async t => {
  // Render the production picker state and composer, with only host dependencies mocked.
  const source = (await fs.readFile(path.join(ui, 'pages/ChatPage.js'), 'utf8')).replaceAll('\r\n', '\n');
  const start = source.indexOf('  const [refinementModels, setRefinementModels]');
  const end = source.indexOf('  const currentWorkflowNameRef', start);
  assert.ok(start >= 0 && end > start);
  const picker = source.slice(start, end);
  const bundle = await build({
    stdin: { resolveDir: shell, loader: 'jsx', contents: `
      import React, {useEffect, useRef, useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import ChatInterface from ${JSON.stringify(path.join(ui, 'components/chat/ChatInterface.jsx'))};
      import {ModelSelect, useWorkflowModelPicker} from ${JSON.stringify(path.join(ui, 'components/chat/WorkflowModelPicker.jsx'))};
      const authFetch = (url, options) => fetch(url, options);
      function Fixture() {
        const [currentWorkflowName, setCurrentWorkflowName] = useState('AppReview');
        const pendingHarnessDecision = null;
        const [currentChatId, changeChat] = useState('fixture-chat');
        const [conversationMode, changeMode] = useState('workflow');
        const loading = false;
        const [reconnecting, setReconnecting] = useState(false);
        window.setReconnecting = setReconnecting;
        ${picker}
        const workflowModel = useWorkflowModelPicker({appId:'app', userId:'owner', chatId:currentChatId,
          workflow:conversationMode === 'workflow' && !showRefinementModelPicker && !refinementModelPicker
            ? currentWorkflowName : null});
        window.changeWorkflow = setCurrentWorkflowName;
        window.showBuildModel = () => {
          setCurrentWorkflowName('ValueEngine');
          setBoundChatModels(new Map([['fixture-chat','gemini'], ['second-chat','openai']]));
        };
        window.changeChat = changeChat;
        window.changeMode = changeMode;
        window.selectedModel = () => refinementModelRef.current;
        return <ChatInterface messages={[]} onSendMessage={() => {window.sent = (window.sent || 0) + 1;}} loading={false}
          workflowName={currentWorkflowName} connectionStatus="connected"
          conversationMode={conversationMode} plainContainer={true} modelPicker={refinementModelPicker || workflowModel.picker}
          modelSaving={workflowModel.saving || (showRefinementModelPicker && refinementModels === null)} waitingForConnection={reconnecting} />;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    ` },
    bundle: true, write: false, jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl', '.css': 'empty' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'host-boundaries', setup(builder) {
      const stubs = {
        '../../adapters/api': 'export const authFetch = (url, options) => fetch(url,options);',
        'react-router-dom': 'export const useNavigate = () => () => {}; export const useParams = () => ({});',
        '../../core/ui/UIToolRenderer': 'export default function UIToolRenderer() { return null; }',
        '../../styles/brandAssets': 'export const getBrandLogoSrc = () => ""; export const applyBrandImageFallback = () => {};',
        '../../session/chatSessionStorage': 'export const logChatPersistence = () => {};',
      };
      builder.onResolve({ filter: /.*/ }, args => (
        Object.hasOwn(stubs, args.path) ? { path: args.path, namespace: 'fixture-stub' } : undefined
      ));
      builder.onLoad({ filter: /.*/, namespace: 'fixture-stub' }, args => ({ contents: stubs[args.path], loader: 'js' }));
    } }],
  });
  const css = await postcss([tailwindcss()]).process(
    (await fs.readFile(path.join(shell, 'styles.css'), 'utf8'))
      + `\n@source "${ui.replaceAll('\\', '/')}";`,
    {from:path.join(shell, 'styles.css')},
  );
  const choices = new Map();
  const server = http.createServer(async (request, response) => {
    if (request.url === '/styles.css') {
      response.setHeader('Content-Type', 'text/css');
      response.end(css.css);
      return;
    }
    if (request.url === '/api/studio/refinement/models') {
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({ default: 'default', models: [
        { id: 'default', label: 'Default model' }, { id: 'chosen', label: 'Chosen model' },
      ] }));
      return;
    }
    if (request.url.startsWith('/api/chats/model/')) {
      if (request.headers['x-user-id'] !== 'owner') {
        response.writeHead(403).end();
        return;
      }
      if (request.method === 'PUT') {
        let body = '';
        for await (const chunk of request) body += chunk;
        choices.set(request.url, JSON.parse(body).model);
      }
      response.setHeader('Content-Type', 'application/json');
      response.end(JSON.stringify({
        default:'local:m1', selected:choices.get(request.url) || null, locked:false,
        models:[{id:'local:m1', label:'First model'}, {id:'local:m2', label:'Second model'}],
      }));
      return;
    }
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : '<!doctype html><link rel="stylesheet" href="/styles.css"><div id="root"></div><script src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    const context = await browser.newContext({ viewport });
    const page = await context.newPage();
    t.after(() => context.close());
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const origin = `http://127.0.0.1:${server.address().port}/`;
    await page.goto(origin);
    const select = page.getByRole('combobox', { name: 'AI model' });
    await expect(select).toHaveValue('default');
    await select.selectOption('chosen');
    assert.equal(await page.evaluate(() => window.selectedModel()), 'chosen');
    await page.evaluate(() => window.changeWorkflow('Other'));
    await expect(select).toHaveValue('');
    await expect(select.locator('option:checked')).toHaveText('Workflow default · First model');
    await select.selectOption('local:m2');
    await expect(select).toHaveValue('local:m2');
    const peer = await context.newPage();
    await peer.goto(origin);
    await peer.evaluate(() => { window.changeChat('parallel'); window.changeWorkflow('Other'); });
    const peerSelect = peer.getByRole('combobox', {name:'AI model'});
    await peerSelect.selectOption('local:m1');
    await expect(peerSelect).toHaveValue('local:m1');
    await expect(select).toHaveValue('local:m2');
    await peer.evaluate(() => window.changeChat('fixture-chat'));
    await expect(peerSelect).toHaveValue('local:m2');
    await peerSelect.selectOption('local:m1');
    await expect(peerSelect).toHaveValue('local:m1');
    await page.bringToFront();
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await expect(select).toHaveValue('local:m1');
    await select.selectOption('local:m2');
    await expect(select).toHaveValue('local:m2');
    await peer.close();
    await page.evaluate(() => window.changeChat('sibling'));
    await expect(select).toHaveValue('');
    await page.evaluate(() => window.changeChat('fixture-chat'));
    await expect(select).toHaveValue('local:m2');
    const modelPath = '**/api/chats/model/app/Other/fixture-chat';
    let completeSave;
    let saveStarted;
    const started = new Promise(resolve => { saveStarted = resolve; });
    const paused = new Promise(resolve => { completeSave = resolve; });
    await page.route(modelPath, async route => {
      if (route.request().method() === 'PUT') {
        saveStarted();
        await paused;
      }
      await route.continue();
    });
    await page.locator('textarea').fill('A new question');
    await select.selectOption('');
    await started;
    await expect(page.getByRole('button',{name:'Send message'})).toBeDisabled();
    await page.locator('textarea').press('Enter');
    assert.equal(await page.evaluate(() => window.sent || 0),0);
    await expect(page.locator('textarea')).toHaveValue('A new question');
    completeSave();
    await expect(select).toBeEnabled();
    await expect(select).toHaveValue('');
    await page.unroute(modelPath);
    const pendingMessage = 'Finish the current workflow interaction before changing models.';
    await page.route(modelPath, route => route.fulfill({status:409,json:{detail:pendingMessage}}));
    await select.selectOption('local:m2');
    await expect(page.getByRole('status').filter({hasText:pendingMessage})).toBeVisible();
    await expect(page.getByRole('button',{name:'Send message'})).toBeEnabled();
    await expect(page.locator('textarea')).toHaveValue('A new question');
    await page.unroute(modelPath);
    await page.getByRole('button',{name:'Reload models'}).click();
    await expect(select).toHaveValue('');
    await page.route(modelPath, route => route.fulfill({status:503,body:'Temporary gateway error'}));
    await select.selectOption('local:m2');
    await expect(page.getByText('Couldn’t confirm the model change.')).toBeVisible();
    await expect(page.getByRole('button',{name:'Send message'})).toBeDisabled();
    await page.unroute(modelPath);
    await page.getByRole('button',{name:'Reload models'}).click();
    await expect(select).toHaveValue('');
    await expect(page.getByRole('button',{name:'Send message'})).toBeEnabled();
    await page.evaluate(() => window.setReconnecting(true));
    await expect(page.getByRole('button',{name:'Send message'})).toBeDisabled();
    const draft = await page.locator('textarea').inputValue();
    await page.locator('textarea').press('Enter');
    await expect(page.locator('textarea')).toHaveValue(draft);
    await page.evaluate(() => window.setReconnecting(false));
    await expect(page.getByRole('button',{name:'Send message'})).toBeEnabled();
    await page.locator('textarea').fill('');
    const retiredModelPath = '**/api/chats/model/app/Other/retired-chat';
    let retiredChoice = 'retired:old-model';
    await page.route(retiredModelPath, route => {
      const request = route.request();
      if (request.method() === 'PUT') retiredChoice = request.postDataJSON().model;
      return route.fulfill({json:{default:null, models:[], selected:retiredChoice, locked:false}});
    });
    await page.evaluate(() => window.changeChat('retired-chat'));
    await expect(select.locator('option:checked')).toHaveText('retired:old-model (unavailable)');
    await select.selectOption('');
    await expect(select).toHaveCount(0);
    assert.equal(retiredChoice,null);
    await page.evaluate(() => window.changeChat('fixture-chat'));
    await page.evaluate(() => window.changeWorkflow('AppReview'));
    await expect(select).toHaveValue('chosen');
    await select.focus();
    await page.keyboard.press('d');
    await expect(select).toBeFocused();
    await expect(page.locator('textarea')).toHaveValue('');
    const box = await page.locator('textarea').boundingBox();
    const picker = await select.boundingBox();
    assert.ok(box && picker && picker.y >= box.y + box.height - 1, 'Model picker is below the text field');
    await expect(page.getByRole('button', {name: 'Send message'})).toBeVisible();
    await page.evaluate(() => window.showBuildModel());
    const runModel = page.getByLabel('Run model: Gemini 3.8 Flash');
    await expect(runModel).toBeVisible();
    await expect(select).toHaveCount(0);
    await page.evaluate(() => window.changeMode('ask'));
    await expect(runModel).toHaveCount(0);
    await page.evaluate(() => window.changeMode('workflow'));
    await expect(runModel).toBeVisible();
    await page.evaluate(() => window.changeChat('second-chat'));
    await expect(page.getByLabel('Run model: GPT-5 Nano')).toBeVisible();
    await page.evaluate(() => window.changeChat('fixture-chat'));
    await expect(runModel).toBeVisible();
    await page.evaluate(() => window.changeChat('other-chat'));
    await expect(runModel).toHaveCount(0);
    const fallbackRequests = [];
    page.on('request', request => {
      if (request.url().includes('/api/chats/model/app/AppReview/')) fallbackRequests.push(request.url());
    });
    await page.route('**/api/studio/refinement/models', route => route.fulfill({status:503}));
    await page.evaluate(() => window.changeWorkflow('AppReview'));
    await expect(page.getByText('No models available.')).toBeVisible();
    await expect(select).toHaveCount(0);
    assert.equal(await page.evaluate(() => window.selectedModel()), '');
    assert.equal(fallbackRequests.length, 0);
    await page.unroute('**/api/studio/refinement/models');
    await page.getByRole('button', {name:'Reload models'}).click();
    await expect(select).toHaveValue('default');
    await page.route('**/api/studio/refinement/models', route => route.fulfill({json:{default:null, models:[]}}));
    await page.evaluate(() => window.changeWorkflow('Other'));
    await expect(select).toHaveCount(1);
    await page.evaluate(() => window.changeWorkflow('AppReview'));
    await expect(page.getByText('No models available.')).toBeVisible();
    await expect(select).toHaveCount(0);
    assert.equal(await page.evaluate(() => window.selectedModel()), '');
    assert.equal(fallbackRequests.length, 0);
    assert.deepEqual(errors, []);
  }
});
