/**
 * Reconnect only from a close we did not ask for.
 *
 * `onClose` bumps `connectionRetryNonce`, which is in the connection effect's
 * dependency array — so every close scheduled a reconnect. During a sequence
 * transition the old socket's close fires while the effect is already building
 * the connection for the new chat, so the retry added a *second* socket to a
 * chat that already had one. The server then evicted the live connection.
 *
 * A deliberate close needs no recovery: either we called close(), or the server
 * evicted us because a newer connection took over. Only an unexpected drop is
 * worth retrying. See #576.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import { shouldReconnectAfterClose as shouldReconnect } from '../../chat-ui/src/adapters/socketClose.js';
import { fileURLToPath } from 'node:url';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('a close we initiated does not trigger a reconnect', () => {
  // 1000 normal, 1005 no-status: both are our own close() call.
  for (const code of [1000, 1005]) {
    assert.equal(
      shouldReconnect({ code, wasClean: true }), false,
      `close code ${code} is ours; reconnecting duplicates the socket`,
    );
  }
});

test('being evicted by a newer connection does not trigger a reconnect', async () => {
  // The server sends 1001 when a replacement socket takes the slot. Retrying
  // here is the loop that produced the duplicate in the first place.
  assert.equal(shouldReconnect({ code: 1001, reason: '', wasClean: true }), false);
});

test('a dropped link still reconnects', async () => {
  // 1006 is the browser's abnormal-closure code: no close frame was received.
  assert.equal(shouldReconnect({ code: 1006, wasClean: false }), true);
  assert.equal(shouldReconnect({ code: 1011, wasClean: false }), true);
});

test('a transport that reports no code still reconnects', async () => {
  // Recovery must not depend on the close reason being available.
  for (const info of [undefined, null, {}, { code: undefined }]) {
    assert.equal(shouldReconnect(info), true, `absent code must be treated as unexpected: ${JSON.stringify(info)}`);
  }
});

test('the adapter forwards the close reason', async () => {
  // The predicate is useless if onClose is still invoked with no argument.
  const api = await fs.readFile(path.join(path.dirname(shell), 'chat-ui/src/adapters/api.js'), 'utf8');
  assert.match(api, /socket\.onclose = \(event\) =>/, 'the close event must be captured');
  assert.match(api, /callbacks\.onClose\(\{/, 'the close reason must be passed on');
  assert.match(api, /code: event\?\.code/);
});
