import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import test from 'node:test';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { build, transform } from 'esbuild';
import { submitToolCallResponse } from '../../chat-ui/src/adapters/uiToolResponse.js';

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

const chatPage = await fs.readFile(path.resolve(shell, '../chat-ui/src/pages/ChatPage.js'), 'utf8');
await transform(chatPage, { loader: 'jsx' });

async function routeRevisionResult(triggerData, {
  triggerStatus = 200, bundleStatus = 200,
  workbenchUI = { component: 'ReviewWorkspace', workflow_name: 'ExampleBuilder' },
} = {}) {
  const start = chatPage.indexOf("case 'chat.revision_requested': {");
  const eventCase = chatPage.slice(start, chatPage.indexOf("case 'error': {", start));
  const body = eventCase.slice(eventCase.indexOf('{') + 1, eventCase.lastIndexOf('}'));
  const requests = [];
  const updates = [];
  const messages = [];
  const state = {};
  const handler = new DynamicUIHandler();
  handler.uiUpdateCallbacks.add(update => updates.push(update));
  const setters = Object.fromEntries([
    'CurrentChatId', 'ActiveChatId', 'CurrentWorkflowName', 'ActiveWorkflowName', 'ConversationMode',
    'WorkflowCompleted', 'PendingHarnessDecision', 'PendingHarnessDecisionError', 'Loading', 'PendingWorkflowReply',
  ].map(name => [`set${name}`, value => {state[name] = value;}]));
  await vm.runInNewContext(`(function(){${body}})();`, {
    data: {data: {refinement_request: 'Update the title', artifact_kind: 'app_bundle', artifact_key: 'app_bundle',
      artifact_version_id: 'baseline', source_surface: 'app_review', extra: {build_registry_id: 'owned-build'}}},
    appId: 'studio-host', user: {id: 'owner'}, config: {}, auth: {fixture: true}, currentChatId: 'review-chat',
    dynamicUIHandler: handler, console: {error() {}}, ...setters,
    rememberWorkflowChatSession: (chatId, workflow) => {state.remembered = [chatId, workflow];},
    buildPendingHarnessDecision: decision => decision,
    setMessagesWithLogging: update => {messages.splice(0, messages.length, ...update(messages));},
    authFetch: async (url, options, authOptions) => {
      requests.push({url, options, authOptions});
      return url === '/api/workflows/trigger'
        ? Response.json(triggerData, {status: triggerStatus})
        : Response.json({workbench_ui: workbenchUI,
          workbench: {artifact_version_id: 'baseline', generated_files: {'page.json': 'before'}}}, {status: bundleStatus});
    },
  });
  return {requests, updates, messages, state};
}

for (const [mode, resultKey, statuses] of [
  ['coding_worker', 'coding_worker', ['validated', 'planned', 'failed']],
  ['surface_regeneration', 'surface_result', ['success', 'partial', 'failed']],
]) {
  for (const status of statuses) {
    test(`revision event opens the canonical workbench for ${mode} ${status} without rerunning or promoting`, async () => {
      const response = {execution_mode: mode, refinement_session_id: 'session-1',
        [resultKey]: {status, metadata: {build_record_id: 'saved-candidate'}, applied_files: {'page.json': 'after'}}};
      const {requests, updates, messages, state} = await routeRevisionResult(response);
      assert.equal(requests.length, 2);
      const trigger = JSON.parse(requests[0].options.body);
      assert.equal(trigger.source_chat_id, 'review-chat');
      assert.equal(trigger.build_registry_id, 'owned-build');
      assert.equal(trigger.trigger_payload.refinement_request.artifact_version_id, 'baseline');
      assert.equal(requests[1].url, '/api/studio/build/artifacts/baseline/bundle?build_registry_id=owned-build');
      assert.equal(updates.length, 1);
      assert.equal(updates[0].component_type, 'ReviewWorkspace');
      assert.equal(updates[0].workflow_name, 'ExampleBuilder');
      assert.equal(updates[0].display, 'artifact');
      assert.equal(updates[0].payload.awaiting_response, false);
      assert.equal(updates[0].payload.artifact_version_id, 'baseline');
      assert.equal(updates[0].payload.build_registry_id, 'owned-build');
      assert.deepEqual(updates[0].payload.generated_files, {'page.json': 'before'});
      assert.deepEqual(updates[0].payload.refinement_result, response);
      assert.equal(state.Loading, false);
      assert.equal(state.PendingWorkflowReply, null);
      assert.equal(state.CurrentChatId, undefined);
      assert.deepEqual(messages, []);
    });
  }
}

test('revision workflow and harness decisions retain their existing routing behavior', async () => {
  const workflow = await routeRevisionResult({execution_mode: 'workflow', chat_id: 'next-chat', workflow_id: 'DesignDocs'});
  assert.equal(workflow.requests.length, 1);
  assert.deepEqual(workflow.state.remembered, ['next-chat', 'DesignDocs']);
  assert.equal(workflow.updates.length, 0);
  const decision = {decision_id: 'choose-scope'};
  const harness = await routeRevisionResult({execution_mode: 'harness_decision', harness_decision: decision});
  assert.equal(harness.requests.length, 1);
  assert.deepEqual(harness.state.PendingHarnessDecision, decision);
  assert.equal(harness.updates.length, 0);
});

for (const options of [{triggerStatus: 409}, {bundleStatus: 403}]) {
  test(`revision errors reach the user without implying saved output was activated (${JSON.stringify(options)})`, async () => {
    const {updates, messages, state} = await routeRevisionResult({execution_mode: 'coding_worker', coding_worker: {status: 'planned'}}, options);
    assert.equal(updates.length, 0);
    assert.equal(messages.length, 1);
    assert.match(messages[0].content, /could not be started|result could not be opened/);
    assert.equal(state.Loading, false);
    assert.equal(state.PendingWorkflowReply, null);
  });
}

for (const workbenchUI of [null, {}, {component: 'ReviewWorkspace', workflow_name: ' '}]) {
  test(`revision rejects an absent review surface (${JSON.stringify(workbenchUI)})`, async () => {
    const {updates, messages} = await routeRevisionResult(
      {execution_mode: 'coding_worker', coding_worker: {status: 'planned'}}, {workbenchUI},
    );
    assert.equal(updates.length, 0);
    assert.match(messages[0].content, /no registered review surface/);
  });
}

for (const eventType of ['tool_call', 'ui.render']) {
  for (const outcome of ['accepted', 'offline', 401, 403, 404, 500, 'unconfirmed']) {
    test(`live ${eventType} requires HTTP acknowledgement without a socket (${outcome})`, async () => {
      const eventCase = chatPage.slice(chatPage.indexOf(`case '${eventType}': {`));
      const callback = eventCase.slice(eventCase.indexOf('const sendResponse ='), eventCase.indexOf('dynamicUIHandler.processUIEvent('));
      let token = 'expired-test-token';
      const requests = [];
      const sendResponse = vm.runInNewContext(`${callback}\nsendResponse;`, {
        wsRef: { current: null }, console: { warn() {} },
        api: { getHttpBaseUrl: () => 'https://fixture.invalid' },
        getAccessToken: () => token,
        submitToolCallResponse: (eventId, response, options) => submitToolCallResponse(eventId, response, {
          ...options,
          fetchImpl: async (...args) => {
            requests.push(args);
            if (outcome === 'offline') throw Error('offline');
            return Response.json({ status: outcome === 'accepted' ? 'success' : 'rejected' }, {
              status: typeof outcome === 'number' ? outcome : 200,
            });
          },
        }),
      });
      const handler = new DynamicUIHandler();
      let update;
      handler.uiUpdateCallbacks.add(value => { update = value; });
      await handler.processUIEvent({
        type: eventType, tool_name: 'ExistingReview', component: 'ExistingReview',
        component_type: 'ExistingReview', tool_call_id: 'owned-event', workflow_name: 'ExampleWorkflow',
        display: 'artifact', display_mode: 'artifact', awaiting_response: true, payload: {},
      }, sendResponse);
      token = 'refreshed-test-token';
      const submitted = update.onResponse({ status: 'submitted', action: 'continue' });
      if (outcome === 'accepted') await assert.doesNotReject(submitted);
      else await assert.rejects(submitted, /not accepted|no longer active|not confirm/);
      assert.equal(requests.length, 1);
      const [url, options] = requests[0];
      assert.equal(url, 'https://fixture.invalid/api/tool-call/respond');
      assert.equal(options.headers.Authorization, 'Bearer refreshed-test-token');
      assert.deepEqual(JSON.parse(options.body), {
        event_id: 'owned-event', response_data: { status: 'submitted', action: 'continue' },
      });
    });
  }
}

for (const outcome of ['accepted', 'rejected', 'missing event']) {
  test(`reopened artifact panel requires acknowledgement (${outcome})`, async () => {
    const start = chatPage.indexOf('artifactMsg.toolCall.onResponse =');
    const callback = chatPage.slice(start, chatPage.indexOf('};', start) + 2);
    const artifactMsg = { toolCall: { tool_call_id: outcome === 'missing event' ? null : 'cached-event' } };
    let requests = 0;
    vm.runInNewContext(callback, {
      artifactMsg, console: { warn() {} }, getAccessToken: () => 'current-test-token',
      api: { getHttpBaseUrl: () => '' },
      submitToolCallResponse: (id, data, options) => submitToolCallResponse(id, data, {
        ...options, fetchImpl: async () => {
          requests += 1;
          return Response.json({ status: outcome === 'accepted' ? 'success' : 'rejected' });
        },
      }),
    });
    const submitted = artifactMsg.toolCall.onResponse({ action: 'continue' });
    assert.ok(submitted instanceof Promise);
    if (outcome === 'accepted') assert.equal(await submitted, true);
    else await assert.rejects(submitted, /no longer active|not confirm/);
    assert.equal(requests, outcome === 'missing event' ? 0 : 1);
  });
}

for (const method of ['handleToolCall', 'handleUIRender']) {
  for (const result of [false, true, 'missing']) {
    test(`${method} propagates review acceptance (${result})`, async () => {
      const handler = new DynamicUIHandler();
      let update;
      handler.uiUpdateCallbacks.add(value => { update = value; });
      await handler[method]({
        tool_name: 'ConceptBlueprint', component: 'ConceptBlueprint', component_type: 'ConceptBlueprint',
        tool_call_id: 'review-event', workflow_name: 'ValueEngine', display: 'artifact',
        display_mode: 'artifact', awaiting_response: true, interaction_type: 'ui_tool', payload: {},
      }, result === 'missing' ? undefined : async () => result);
      if (result === true) await assert.doesNotReject(update.onResponse({ action: 'approve' }));
      else await assert.rejects(update.onResponse({ action: 'approve' }), /not accepted|unavailable/);
    });
  }

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
