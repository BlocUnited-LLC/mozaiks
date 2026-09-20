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
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

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
