import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { chromium, expect } from '@playwright/test';
import tailwindcss from '@tailwindcss/postcss';
import { build } from 'esbuild';
import postcss from 'postcss';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ui = path.join(path.dirname(shell), 'chat-ui/src');

test('chat status stays between the feed and composer on desktop and mobile', async t => {
  const stubs = {
    'react-router-dom': 'export const useNavigate = () => () => {}; export const useParams = () => ({});',
    '../../core/ui/UIToolRenderer': 'export default function UIToolRenderer() { return null; }',
    '../../styles/brandAssets': 'export const getBrandLogoSrc = () => ""; export const applyBrandImageFallback = () => {};',
    '../../session/chatSessionStorage': 'export const logChatPersistence = () => {};',
  };
  const entry = `
    import React, { useState } from 'react';
    import { createRoot } from 'react-dom/client';
    import ChatInterface from ${JSON.stringify(path.join(ui, 'components/chat/ChatInterface.jsx'))};
    import { applyChatProgressEvent, emptyChatStatusProgress, getChatStatusProgress } from ${JSON.stringify(path.join(ui, 'pages/chatStatusProgress.js'))};
    function Fixture() {
      const [chatStatus, setChatStatus] = useState({ phase: 'working', detail: 'Planner is working', totalTokens: 1248 });
      const [connectionStatus, setConnectionStatus] = useState('connected');
      const [progress, setProgress] = useState(emptyChatStatusProgress);
      const scope = { mode: 'workflow', chatId: 'chat-1', workflowName: 'AppGenerator' };
      window.setChatStatus = setChatStatus;
      window.setConnectionStatus = setConnectionStatus;
      window.progressEvent = event => setProgress(current => applyChatProgressEvent(current, scope, event));
      window.reconnectProgress = () => {
        setProgress(emptyChatStatusProgress());
        setConnectionStatus('connected');
      };
      return <main style={{ height: '100dvh', maxWidth: 980, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column' }}>
        <ChatInterface messages={[]} onSendMessage={() => {}} loading={true}
          workflowName="AppGenerator" connectionStatus={connectionStatus} chatStatus={{ ...chatStatus, progress: getChatStatusProgress(progress, scope) }}
          conversationMode="workflow" plainContainer={true} />
      </main>;
    }
    createRoot(document.getElementById('root')).render(<Fixture />);
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' },
    bundle: true, write: false, jsx: 'automatic',
    loader: { '.js': 'jsx', '.png': 'dataurl', '.css': 'empty' },
    nodePaths: [path.join(shell, 'node_modules')],
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
    },
    define: { 'process.env.NODE_ENV': '"test"' },
    plugins: [{ name: 'mock-host-boundaries', setup(builder) {
      builder.onResolve({ filter: /.*/ }, args => (
        Object.hasOwn(stubs, args.path) ? { path: args.path, namespace: 'fixture-stub' } : undefined
      ));
      builder.onLoad({ filter: /.*/, namespace: 'fixture-stub' }, args => ({ contents: stubs[args.path], loader: 'js' }));
    } }],
  });
  const sources = ['ChatInterface.jsx', 'ChatStatusBar.jsx']
    .map(name => `@source "${path.join(ui, 'components/chat', name).replaceAll('\\', '/')}";`)
    .join('\n');
  const styles = await postcss([tailwindcss()]).process(
    `${await fs.readFile(path.join(shell, 'styles.css'), 'utf8')}\n${sources}`,
    { from: path.join(shell, 'styles.css') },
  );
  const html = `<!doctype html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
    <style>${styles.css} body { margin: 0; background: #101729; }
      :root { --color-primary-light: #68b4ff; --color-primary-light-rgb: 104,180,255;
        --color-text-primary: #f6f8fb; --color-text-secondary: #aab8cf; --chat-input-bg: #162139; }
    </style></head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`;
  const server = http.createServer((request, response) => {
    if (request.url !== '/' && request.url !== '/fixture.js') { response.writeHead(404).end(); return; }
    response.setHeader('Content-Type', request.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    response.end(request.url === '/fixture.js' ? bundle.outputFiles[0].text : html);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());

  for (const viewport of [{ width: 1280, height: 800 }, { width: 390, height: 844 }]) {
    const page = await browser.newPage({ viewport });
    t.after(() => page.close());
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    const bar = page.getByRole('group', { name: 'Chat status' });
    await expect(bar).toContainText('Working');
    await expect(bar).toContainText('1,248');
    await page.evaluate(() => window.progressEvent({
      type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'AppGenerator', sequence: 4,
      stage_id: 'read', stage_label: 'Reading the selected application source files', completed: 12, total: 40, unit: 'files',
    }));
    await expect(bar).toContainText('Reading the selected application source files');
    await expect(bar).toContainText('12 of 40 files');
    await expect(bar).not.toContainText('%');
    await expect(bar.getByLabel('Run tokens: 1,248')).toBeVisible();
    const feed = await page.locator('.chat-shell > .relative').first().boundingBox();
    const status = await bar.boundingBox();
    const composer = await page.locator('.chat-shell form').boundingBox();
    assert.ok(feed && status && composer);
    assert.ok(feed.y + feed.height <= status.y + 1, 'status must follow the message feed');
    assert.ok(status.y + status.height <= composer.y + 24, 'status must precede the composer');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    if (process.env.CHAT_STATUS_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.CHAT_STATUS_SCREENSHOT_DIR, { recursive: true });
      await page.screenshot({ path: path.join(process.env.CHAT_STATUS_SCREENSHOT_DIR, `chat-status-${viewport.width}.png`) });
    }
    await page.evaluate(() => window.setChatStatus({ phase: 'waiting', detail: 'Reply to continue', totalTokens: 1248 }));
    await expect(bar).toContainText('Waiting for you');
    await expect(bar).not.toContainText('Reading the selected');
    await expect(bar).not.toContainText('12 of 40');
    for (const [phase, label] of [['error', 'Needs attention'], ['complete', 'Complete']]) {
      await page.evaluate(phase => window.setChatStatus({ phase, totalTokens: 1248 }), phase);
      await expect(bar).toContainText(label);
      await expect(bar).not.toContainText('12 of 40');
    }
    await page.evaluate(() => {
      window.setChatStatus({ phase: 'working', detail: 'Processing your request', totalTokens: 1248 });
      window.setConnectionStatus('disconnected');
    });
    await expect(bar).toContainText('Disconnected');
    await expect(bar).not.toContainText('Reading the selected');
    await page.evaluate(() => {
      window.reconnectProgress();
      window.progressEvent({
        type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'AppGenerator', sequence: 4, replay: true,
        stage_id: 'read', stage_label: 'Old replay', completed: 12, total: 40, unit: 'files',
      });
    });
    await expect(bar).not.toContainText('Old replay');
    await page.evaluate(() => window.progressEvent({
      type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'AppGenerator', sequence: 1,
      stage_id: 'validate', stage_label: 'Validating the application', completed: null, total: null,
    }));
    await expect(bar).toContainText('Validating the application');
    await expect(bar).not.toContainText(' of ');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.evaluate(() => {
      window.progressEvent({ type: 'chat.awaiting_reply', chat_id: 'chat-1', sequence: 2 });
      window.progressEvent({
        type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'AppGenerator', sequence: 3,
        stage_id: 'validate', stage_label: 'Late status after waiting', completed: 2, total: 3,
      });
    });
    await expect(bar).not.toContainText('Late status after waiting');
    await page.evaluate(() => {
      window.progressEvent({ type: 'chat.run_start', chat_id: 'chat-1', sequence: 4 });
      window.progressEvent({
        type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'AppGenerator', sequence: 5,
        stage_id: 'validate', stage_label: 'Continuing validation', completed: 2, total: 3,
      });
    });
    await expect(bar).toContainText('Continuing validation');
    await expect(bar).toContainText('2 of 3');
    await page.evaluate(() => window.progressEvent({
      type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'AppGenerator', sequence: 6,
      stage_id: 'validate', stage_label: 'W'.repeat(120), completed: 12, total: 40, unit: 'W'.repeat(40),
    }));
    await expect(bar).toContainText('W'.repeat(40));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false,
      'valid maximum-length labels and units must wrap within the chat');
    await page.evaluate(() => window.setChatStatus({ phase: 'idle', detail: '', totalTokens: null }));
    await expect(bar).toContainText('Ready');
    await expect(bar).not.toContainText('Run tokens');
  }
});
