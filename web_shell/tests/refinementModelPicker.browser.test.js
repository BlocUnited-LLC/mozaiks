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
      const authFetch = (url, options) => fetch(url, options);
      function Fixture() {
        const [currentWorkflowName, setCurrentWorkflowName] = useState('AppReview');
        const pendingHarnessDecision = null;
        const [currentChatId, changeChat] = useState('fixture-chat');
        const [conversationMode, changeMode] = useState('workflow');
        const loading = false;
        ${picker}
        window.changeWorkflow = setCurrentWorkflowName;
        window.showBuildModel = () => {
          setCurrentWorkflowName('ValueEngine');
          setBoundChatModels(new Map([['fixture-chat','gemini'], ['second-chat','openai']]));
        };
        window.changeChat = changeChat;
        window.changeMode = changeMode;
        window.selectedModel = () => refinementModelRef.current;
        return <ChatInterface messages={[]} onSendMessage={() => {}} loading={false}
          workflowName={currentWorkflowName} connectionStatus="connected"
          conversationMode={conversationMode} plainContainer={true} refinementModelPicker={refinementModelPicker} />;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    ` },
    bundle: true, write: false, jsx: 'automatic', loader: { '.js': 'jsx', '.png': 'dataurl', '.css': 'empty' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: { react: path.join(shell, 'node_modules/react'), 'react-dom': path.join(shell, 'node_modules/react-dom') },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'host-boundaries', setup(builder) {
      const stubs = {
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
  const server = http.createServer((request, response) => {
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
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : '<!doctype html><link rel="stylesheet" href="/styles.css"><div id="root"></div><script src="/fixture.js"></script>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { server.closeAllConnections(); server.close(resolve); }));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    t.after(() => page.close());
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const select = page.getByRole('combobox', { name: 'AI model' });
    await expect(select).toHaveValue('default');
    await select.selectOption('chosen');
    assert.equal(await page.evaluate(() => window.selectedModel()), 'chosen');
    await page.evaluate(() => window.changeWorkflow('Other'));
    await expect(select).toHaveCount(0);
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
    assert.deepEqual(errors, []);
  }
});
