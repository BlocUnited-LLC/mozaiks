import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// This test bundles the public package entrypoint with the dev-only esbuild.
// It lives apart from authAdapter.test.js because the packaged-wheel smoke runs
// that file without node_modules; `npm run test:auth` runs both.
const chatUiRoot = process.env.MOZAIKS_CHAT_UI_PATH || fileURLToPath(new URL('../../chat-ui', import.meta.url));

test('the public auth entrypoint opens a socket with header credentials and an unchanged URL', async (t) => {
  const { build } = createRequire(import.meta.url)('esbuild');
  const pkg = JSON.parse(await readFile(resolve(chatUiRoot, 'package.json'), 'utf8'));
  const bundled = await build({
    stdin: {
      contents: "export { openAuthenticatedWebSocket } from '@mozaiks/chat-ui/auth';",
      resolveDir: chatUiRoot,
    },
    bundle: true, write: false, format: 'esm', jsx: 'automatic',
    loader: { '.js': 'jsx', '.css': 'empty', '.png': 'dataurl' },
    external: [...Object.keys(pkg.dependencies), ...Object.keys(pkg.peerDependencies), '@platform/*', '@studio/*', 'virtual:*'],
    plugins: [{
      name: 'transport-only-consumer',
      setup(builder) {
        // This consumer imports no auth pages; their React tree is outside this test.
        builder.onResolve({ filter: /AuthPages\.jsx$/ }, args => ({ path: args.path, external: true, sideEffects: false }));
      },
    }],
  });
  const { openAuthenticatedWebSocket } = await import(
    `data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`
  );
  const calls = [];
  // Node 20 has no global WebSocket, so a method mock has nothing to replace.
  // Install a recorder and restore the previous property, or its absence.
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'WebSocket');
  t.after(() => {
    if (previous) Object.defineProperty(globalThis, 'WebSocket', previous);
    else delete globalThis.WebSocket;
  });
  Object.defineProperty(globalThis, 'WebSocket', {
    configurable: true,
    writable: true,
    value: class {
      constructor(...args) { calls.push(args); }
    },
  });
  const url = 'wss://app.example/api/rooms/room-one/live?cursor=after-one';
  const token = 'opaque+access/token=with?reserved&bytes';
  openAuthenticatedWebSocket(url, token);
  openAuthenticatedWebSocket(url, null);
  assert.deepEqual(calls, [
    [url, ['mozaiks.bearer.v1', Buffer.from(token).toString('base64url')]],
    [url],
  ]);
});
