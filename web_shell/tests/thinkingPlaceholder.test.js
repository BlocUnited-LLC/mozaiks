/**
 * A finished run has nobody thinking.
 *
 * The "..." placeholder is appended when an agent hands off, and was only ever
 * removed when a NEXT agent spoke. A run that ends before that — the common
 * case on workflow_failed — left the bubble on screen permanently, which is
 * what a live ThemeCapture run showed above its final message.
 *
 * These tests pull the two terminal reducers out of ChatPage.js and run them,
 * so they assert behaviour rather than the presence of a string.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium, expect } from '@playwright/test';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const chatPage = path.join(path.dirname(shell), 'chat-ui/src/pages/ChatPage.js');

// Anchor on a single-line fragment (the checkout is CRLF, so multi-line
// literals will not match), then walk back to the start of the call.
const reducerAt = (source, anchor, endMarker) => {
  const hit = source.indexOf(anchor);
  assert.notEqual(hit, -1, `could not locate anchor: ${anchor}`);
  const start = source.lastIndexOf('setMessagesWithLogging(', hit);
  assert.notEqual(start, -1, 'could not find the enclosing setMessagesWithLogging call');
  const end = source.indexOf(endMarker, hit);
  assert.notEqual(end, -1, `could not locate end marker: ${endMarker}`);
  return source.slice(start, end + endMarker.length);
};

const withThinking = () => ([
  { id: 'a1', sender: 'agent', content: 'Here is the summary.' },
  { id: 'thinking-1', sender: 'agent', content: '', isThinking: true },
]);

const run = (snippet, messages) => {
  let captured = null;
  vm.runInNewContext(snippet, {
    setMessagesWithLogging: (fn) => { captured = fn(messages); },
    errorMessage: 'Workflow failed (attempts_exhausted)',
    Date,
  });
  assert.ok(captured, 'reducer never ran');
  return captured;
};

test('the failure reducer drops the thinking placeholder and reports the error', async () => {
  const source = await fs.readFile(chatPage, 'utf8');
  const snippet = reducerAt(source, '[...prev.filter(m => !m?.isThinking), {', '}]);');

  const result = run(snippet, withThinking());

  assert.equal(result.filter((m) => m.isThinking).length, 0, 'placeholder survived a failed run');
  assert.equal(result.at(-1).sender, 'system');
  assert.match(result.at(-1).content, /attempts_exhausted/);
  assert.equal(result[0].content, 'Here is the summary.', 'real messages must be preserved');
});

test('the success reducer drops the thinking placeholder', async () => {
  const source = await fs.readFile(chatPage, 'utf8');
  const snippet = reducerAt(source, 'prev.some(m => m?.isThinking)', '));');

  const result = run(snippet, withThinking());

  assert.equal(result.filter((m) => m.isThinking).length, 0, 'placeholder survived a completed run');
  assert.equal(result.length, 1);
  assert.equal(result[0].content, 'Here is the summary.');
});

test('the success reducer leaves an untouched list identity-stable', async () => {
  // Returning a fresh array on every run_complete would re-render the whole
  // transcript for nothing.
  const source = await fs.readFile(chatPage, 'utf8');
  const snippet = reducerAt(source, 'prev.some(m => m?.isThinking)', '));');

  const clean = [{ id: 'a1', sender: 'agent', content: 'done' }];

  assert.equal(run(snippet, clean), clean, 'must return the same array when nothing changed');
});

test('the active thinking bubble has readable activity and disappears with the real terminal reducers', async (t) => {
  const component = path.resolve(shell, '../chat-ui/src/components/chat/ChatMessage.jsx');
  const bundle = await build({
    stdin: {resolveDir: shell, loader: 'jsx', contents: `
      import React, {useState} from 'react';
      import {createRoot} from 'react-dom/client';
      import ChatMessage from ${JSON.stringify(component)};
      function Fixture() {
        const [messages, setMessages] = useState([]);
        window.setMessages = setMessages;
        return <main>{messages.map(message => <ChatMessage key={message.id}
          message={message.content} message_from={message.sender} agentName={message.agentName}
          isThinking={message.isThinking} metadata={message.metadata} />)}</main>;
      }
      createRoot(document.getElementById('root')).render(<Fixture />);
    `},
    bundle: true, write: false, jsx: 'automatic', loader: {'.css': 'empty'},
    alias: {
      react: path.join(shell, 'node_modules/react'),
      'react-dom': path.join(shell, 'node_modules/react-dom'),
    },
    nodePaths: [path.join(shell, 'node_modules')],
  });
  const styles = await fs.readFile(path.resolve(shell, '../chat-ui/src/components/chat/ChatMessage.css'), 'utf8');
  const server = http.createServer((req, res) => {
    const script = req.url === '/fixture.js';
    res.setHeader('Content-Type', script ? 'text/javascript' : 'text/html');
    res.end(script ? bundle.outputFiles[0].text : `<!doctype html><html><head>
      <meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles}</style>
      </head><body><div id="root"></div><script src="/fixture.js"></script></body></html>`);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => server.close(resolve)));
  const browser = await chromium.launch({headless: true});
  t.after(() => browser.close());
  const source = await fs.readFile(chatPage, 'utf8');
  const reducers = [
    reducerAt(source, 'prev.some(m => m?.isThinking)', '));'),
    reducerAt(source, '[...prev.filter(m => !m?.isThinking), {', '}]);'),
  ];
  for (const width of [1440, 390]) {
    const page = await browser.newPage({viewport: {width, height: 844}});
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.waitForFunction(() => typeof window.setMessages === 'function');
    for (const reducer of reducers) {
      const messages = withThinking();
      messages[1].agentName = 'ExampleAgent';
      await page.evaluate(messages => window.setMessages(messages), messages);
      const status = page.getByRole('status', {name: 'Assistant activity'});
      await expect(status).toHaveText('Working on this step…', {timeout: 1000});
      await expect(status).toBeVisible();
      await expect(status.locator('[aria-hidden="true"]')).toHaveCount(1);
      await page.evaluate(messages => window.setMessages(messages), run(reducer, messages));
      await expect(status).toHaveCount(0);
      await expect(page.getByText('Here is the summary.', {exact: true})).toBeVisible();
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
});
