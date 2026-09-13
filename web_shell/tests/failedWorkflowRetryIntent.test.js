import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const hooks = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../chat-ui/src/hooks');
const launchSource = await fs.readFile(path.join(hooks, 'useWorkflowStart.js'), 'utf8');
const retrySource = await fs.readFile(path.join(hooks, 'useFailedWorkflowRetry.js'), 'utf8');
const launchCode = launchSource.slice(launchSource.indexOf('const CHAT_TRIGGER_SOURCE')).replace('export function', 'function');
const retryCode = retrySource.slice(retrySource.indexOf('// Persisted')).replaceAll('export ', '');

function fixture() {
  const input = { appId: 'host', userId: 'owner', chatId: 'failed', workflowName: 'Example', surface: 'studio', mode: 'workflow' };
  const scope = JSON.stringify(Object.values(input));
  const requests = [];
  const navigation = [];
  let stateIndex = 0;
  const context = {
    input, URLSearchParams, AbortController, console,
    useCallback: callback => callback, useEffect() {}, useRef: current => ({ current }),
    useState: initial => [stateIndex++ === 0 ? scope : initial, () => {}],
    useNavigate: () => url => navigation.push(url),
    useChatUI: () => ({ user: { id: 'owner', app_id: 'host' }, config: {}, auth: {} }),
    authFetch: async (url, options) => {
      requests.push({ url, body: JSON.parse(options.body) });
      return { ok: true, json: async () => ({ chat_id: 'fresh', workflow_id: 'Example' }) };
    },
  };
  const value = vm.runInNewContext(`${launchCode}\n${retryCode}\n({ retry: useFailedWorkflowRetry(input), start: useWorkflowStart() });`, context);
  return { ...value, requests, navigation };
}

test('retry hook sends explicit intent with only server selectors and navigates to the fresh chat', async () => {
  const state = fixture();
  assert.equal(state.retry.available, true);
  await state.retry.retry();
  assert.deepEqual(state.requests, [{ url: '/api/workflows/trigger', body: {
    trigger_source: 'manual', context_variables: {}, app_id: 'host', user_id: 'owner',
    source_chat_id: 'failed', retry_failed: true, workflow_id: 'Example',
  } }]);
  assert.match(state.navigation[0], /chat_id=fresh/);
});

test('ordinary source-chat launches do not opt into failed-run recovery', async () => {
  const state = fixture();
  await state.start.startWorkflow('Example', {}, { trigger_source: 'manual', source_chat_id: 'failed' });
  assert.equal(state.requests.length, 1);
  assert.equal('retry_failed' in state.requests[0].body, false);
});
