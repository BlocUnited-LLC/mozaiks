/**
 * Finished runs and runs waiting for the user have nobody thinking.
 *
 * The "..." placeholder is appended when an agent hands off, and was only ever
 * removed when a NEXT agent spoke. A run that ends before that — the common
 * case on workflow_failed — left the bubble on screen permanently, which is
 * what a live ThemeCapture run showed above its final message.
 *
 * These tests execute the production pause event branches and terminal
 * reducers, then render their messages through the production component.
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
const reducerAt = (source, anchor, endMarker, last = false) => {
  const hit = last ? source.lastIndexOf(anchor) : source.indexOf(anchor);
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

const eventSource = (source, event) => {
  const start = source.indexOf(`      case '${event}': {`);
  assert.notEqual(start, -1, `could not locate event: ${event}`);
  const end = source.indexOf("      case '", start + 12);
  assert.notEqual(end, -1, `could not locate end of event: ${event}`);
  return source.slice(start, end);
};

const successReducer = source => reducerAt(
  eventSource(source, 'run_complete'), 'prev.some(m => m?.isThinking)', '));', true,
);

const pauseEvent = (source, event, state) => {
  const data = event === 'awaiting_reply'
    ? {data:{source_agent:'ExampleAgent', prompt:'What do you want to build?', reason:'awaiting_user_reply'}}
    : {status:'paused', agent:'ExampleAgent', prompt:'What do you want to build?', reason:'awaiting_user_reply'};
  vm.runInNewContext(`(() => { switch (event) { ${eventSource(source, event)} } })()`, {
    event, data, Date, isFailedWorkflowSession: () => false,
    setLoading: value => { state.loading = value; },
    setPendingWorkflowReply: value => { state.pending = typeof value === 'function' ? value(state.pending) : value; },
    setMessagesWithLogging: reducer => { state.messages = reducer(state.messages); },
  });
  return state;
};

const pauseSequences = [['awaiting_reply'], ['run_complete'], ['awaiting_reply', 'run_complete']];
for (const sequence of pauseSequences) {
  test(`${sequence.join(' then ')} removes thinking while preserving the question and reply state`, async () => {
    const source = await fs.readFile(chatPage, 'utf8');
    const state = {messages:withThinking(), loading:true, pending:null};
    const question = {id:'question', sender:'agent', content:'What do you want to build?'};
    state.messages.push(question);
    for (const event of sequence) pauseEvent(source, event, state);
    assert.equal(state.messages.filter(message => message.isThinking).length, 0);
    assert.equal(state.messages.at(-1), question);
    assert.equal(state.loading, false);
    assert.equal(state.pending.agent, 'ExampleAgent');
    assert.equal(state.pending.prompt, question.content);

    const cleanMessages = state.messages;
    const pending = state.pending;
    pauseEvent(source, 'run_complete', state);
    assert.equal(state.messages, cleanMessages, 'a repeated pause must leave real messages identity-stable');
    assert.equal(state.pending, pending, 'paused run completion must preserve the existing reply request');
  });
}

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
  const snippet = successReducer(source);

  const result = run(snippet, withThinking());

  assert.equal(result.filter((m) => m.isThinking).length, 0, 'placeholder survived a completed run');
  assert.equal(result.length, 1);
  assert.equal(result[0].content, 'Here is the summary.');
});

test('the success reducer leaves an untouched list identity-stable', async () => {
  // Returning a fresh array on every run_complete would re-render the whole
  // transcript for nothing.
  const source = await fs.readFile(chatPage, 'utf8');
  const snippet = successReducer(source);

  const clean = [{ id: 'a1', sender: 'agent', content: 'done' }];

  assert.equal(run(snippet, clean), clean, 'must return the same array when nothing changed');
});

test('the active thinking bubble disappears on completion and while waiting for a reply', async (t) => {
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
    messages => run(successReducer(source), messages),
    messages => run(reducerAt(source, '[...prev.filter(m => !m?.isThinking), {', '}]);'), messages),
    ...pauseSequences.map(sequence => messages => {
      const state = {messages, loading:true, pending:null};
      for (const event of sequence) pauseEvent(source, event, state);
      return state.messages;
    }),
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
      await page.evaluate(messages => window.setMessages(messages), reducer(messages));
      await expect(status).toHaveCount(0);
      await expect(page.getByText('Here is the summary.', {exact: true})).toBeVisible();
    }
    assert.deepEqual(errors, []);
    await page.close();
  }
});
