/**
 * A failed transition resolution must reach the screen.
 *
 * `handleNavigate` caught every error, logged it, and returned false. Nothing
 * read that boolean — every call site is fire-and-forget — so the failure was
 * swallowed. On 2026-09-17 the token-balance gate rejected with a 400 whose
 * detail said exactly what to do, and the user saw a button that did nothing:
 * the console had the message, the UI had no idea anything went wrong.
 *
 * TransitionScreen already owns an `error` state and renders TransitionError
 * with a retry. The fix routes resolution failures into it.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const chatUi = path.join(path.dirname(shell), 'chat-ui/src');
const read = (rel) => fs.readFile(path.join(chatUi, rel), 'utf8');

test('a resolution failure is re-thrown, not swallowed', async () => {
  const src = await read('components/RouteRenderer.jsx');
  const handler = src.slice(src.indexOf('const handleNavigate'));
  assert.match(handler, /throw err;/, 'handleNavigate must propagate the failure');
  assert.doesNotMatch(
    handler.slice(0, handler.indexOf('throw err;')),
    /catch \(err\) \{[\s\S]*?return false;/,
    'returning false strands the caller with no way to know it failed',
  );
});

test('the detail from the API becomes the message', async () => {
  const src = await read('components/RouteRenderer.jsx');
  // A bare "Failed to resolve transition" tells the user nothing actionable.
  assert.match(src, /err\.detail \|\| 'Failed to resolve transition'/);
});

test('every resolution path routes failures into the error state', async () => {
  const src = await read('ui/screens/TransitionScreen.jsx');
  assert.match(src, /const resolve = useCallback/, 'one funnel, so no call site can forget');
  assert.match(src, /\.catch\(\(err\) => setError\(/, 'failures must land in the error state');

  // The auto-navigating effects and the event bus must use the funnel too --
  // they are fire-and-forget, so a bare onNavigate there is an unhandled rejection.
  const body = src.slice(src.indexOf('const resolve = useCallback'));
  assert.doesNotMatch(body, /onNavigate\?\.\(null\)/, 'auto-navigation must go through resolve()');
  assert.match(src, /resolve\(option_id \?\? null/, 'the event bus must go through resolve()');
});

test('the funnel is declared before the effects that depend on it', async () => {
  const src = await read('ui/screens/TransitionScreen.jsx');
  // A dependency array is evaluated during render: a `const` declared lower is
  // still in the temporal dead zone and throws before the screen ever paints.
  assert.ok(
    src.indexOf('const resolve = useCallback') < src.indexOf('[transition, resolve]'),
    'resolve must be defined above the effect that lists it as a dependency',
  );
});

test('the error surface it feeds still exists', async () => {
  const src = await read('ui/screens/TransitionScreen.jsx');
  assert.match(src, /if \(error\) return <TransitionError/, 'setError must render something');
});
