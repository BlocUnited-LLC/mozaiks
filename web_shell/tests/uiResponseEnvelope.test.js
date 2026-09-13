import assert from 'node:assert/strict';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const bundle = await build({
  entryPoints: [path.resolve(shell, '../chat-ui/src/core/dynamicUIHandler.js')],
  bundle: true, write: false, format: 'esm', platform: 'node',
  plugins: [{ name: 'external-boundaries', setup(build) {
    build.onResolve({ filter: /(?:adapters\/api|toolsLogger)$/ }, args => ({ path: args.path, namespace: 'test-boundary' }));
    build.onLoad({ filter: /.*/, namespace: 'test-boundary' }, args => ({ contents: args.path.includes('toolsLogger')
      ? 'export const createToolsLogger = () => new Proxy({}, {get: () => () => {}});'
      : 'export const appApi = {};',
    }));
  } }],
});
const { DynamicUIHandler } = await import('data:text/javascript;base64,' + Buffer.from(bundle.outputFiles[0].text).toString('base64'));

for (const method of ['handleToolCall', 'handleUIRender']) {
  test(`${method} returns only the user response, not the displayed artifact`, async () => {
    const handler = new DynamicUIHandler();
    let update;
    let submitted;
    handler.uiUpdateCallbacks.add(value => { update = value; });
    await handler[method]({
      tool_name: 'AppWorkbench', component: 'AppWorkbench', component_type: 'AppWorkbench',
      tool_call_id: 'event-1', workflow_name: 'AppGenerator', display: 'artifact',
      display_mode: 'artifact', awaiting_response: true, interaction_type: 'ui_tool',
      payload: { generated_files: { 'huge.txt': 'x'.repeat(100000) } },
    }, response => { submitted = response; });
    await update.onResponse({ status: 'submitted', action: 'download_complete' });
    assert.equal(submitted.tool_call_id, 'event-1');
    assert.deepEqual(submitted.response, { status: 'submitted', action: 'download_complete' });
    assert.equal(submitted.payload, undefined);
    assert.ok(JSON.stringify(submitted).length < 1000);
  });
}
