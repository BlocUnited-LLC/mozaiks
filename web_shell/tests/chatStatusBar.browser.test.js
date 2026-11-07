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
    function Fixture() {
      const [chatStatus, setChatStatus] = useState({ phase: 'working', detail: 'Planner is working', totalTokens: 1248 });
      window.setChatStatus = setChatStatus;
      return <main style={{ height: '100dvh', maxWidth: 980, margin: '0 auto', padding: 12, display: 'flex', flexDirection: 'column' }}>
        <ChatInterface messages={[]} onSendMessage={() => {}} loading={true}
          workflowName="AppGenerator" connectionStatus="connected" chatStatus={chatStatus}
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
    await page.evaluate(() => window.setChatStatus({ phase: 'idle', detail: '', totalTokens: null }));
    await expect(bar).toContainText('Ready');
    await expect(bar).not.toContainText('Run tokens');
  }
});
