import assert from 'node:assert/strict';
import http from 'node:http';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import { chromium } from '@playwright/test';
import { submitToolCallResponse } from '../../chat-ui/src/adapters/uiToolResponse.js';
import { normalizePrimitiveActions, sendPrimitiveResponse } from '../../chat-ui/src/core/ui/workflowPrimitiveUtils.js';

const shell = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('review action availability and labels may vary without changing declared decision semantics', () => {
  const schema = { type: 'object', required: ['action'], properties: { action: { const: 'request_changes' } } };
  const actions = normalizePrimitiveActions({
    ui_contract: { actions_schema: [
      { id: 'approve', approved: true },
      { id: 'request_changes', approved: false, payload_schema: schema },
    ] },
    actions: [{ id: 'request_changes', label: 'Revise this draft', approved: true,
      payload_schema: {}, payload: { review_id: 'review-17' } },
      { id: 'delete_everything', label: 'Delete' }],
  });

  assert.deepEqual(actions.map((action) => action.id), ['request_changes']);
  assert.equal(actions[0].label, 'Revise this draft');
  assert.equal(actions[0].approved, false);
  assert.deepEqual(actions[0].payload_schema, schema);
  assert.deepEqual(actions[0].payload, { review_id: 'review-17' });
});

test('review response cannot replace the declared action or approval with per-review data', async () => {
  let response;
  await sendPrimitiveResponse(async (reply) => { response = reply; }, {
    id: 'request_changes', approved: false, payload: { action: 'approve', approved: true, review_id: 'review-1' },
  }, { action: 'approve', approved: true, rationale: 'Revise the scope' });

  assert.deepEqual(response, {
    status: 'submitted', action: 'request_changes', approved: false,
    review_id: 'review-1', rationale: 'Revise the scope',
  });
});

test('the actionless UserInputRequest renderer submits text without an action', async (t) => {
  const component = path.resolve(shell, '../chat-ui/src/core/ui/UserInputRequest.js');
  const entry = `
    import React from 'react';
    import { createRoot } from 'react-dom/client';
    import UserInputRequest from ${JSON.stringify(component)};
    window.responses = [];
    createRoot(document.getElementById('root')).render(
      <UserInputRequest payload={{ input_request_id: 'input-1', prompt: 'Describe the app' }}
        onResponse={(response) => window.responses.push(response)} />
    );
  `;
  const bundle = await build({
    stdin: { contents: entry, resolveDir: shell, loader: 'jsx' }, bundle: true, write: false,
    jsx: 'automatic', loader: { '.js': 'jsx' }, nodePaths: [path.join(shell, 'node_modules')],
  });
  const server = http.createServer((req, res) => {
    res.setHeader('Content-Type', req.url === '/fixture.js' ? 'text/javascript' : 'text/html');
    res.end(req.url === '/fixture.js' ? bundle.outputFiles[0].text
      : '<html><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise((resolve) => server.close(resolve)));
  const browser = await chromium.launch({ headless: true });
  t.after(() => browser.close());
  const page = await browser.newPage();
  await page.goto(`http://127.0.0.1:${server.address().port}`);
  await page.getByPlaceholder('Type your response or press Enter to skip...').fill('A planning board');
  await page.getByPlaceholder('Type your response or press Enter to skip...').press('Enter');
  await page.waitForFunction(() => window.responses.length === 1);
  const [reply] = await page.evaluate(() => window.responses);
  assert.equal(reply.text, 'A planning board');
  assert.equal(reply.user_input, 'A planning board');
  assert.equal(Object.hasOwn(reply, 'action'), false);
});

test('review decisions require a confirmed response and carry only decision data', async () => {
  let request;
  const accepted = await submitToolCallResponse('review-1', { action: 'approve' }, {
    baseUrl: 'http://localhost:8000', token: 'test-token',
    fetchImpl: async (...args) => { request = args; return Response.json({ status: 'success' }); },
  });
  assert.equal(accepted, true);
  assert.equal(request[0], 'http://localhost:8000/api/tool-call/respond');
  assert.equal(request[1].headers.Authorization, 'Bearer test-token');
  assert.deepEqual(JSON.parse(request[1].body), { event_id: 'review-1', response_data: { action: 'approve' } });
});

for (const status of [401, 403, 404, 500, 200]) {
  test(`unaccepted review (${status}) cannot report success`, async () => {
    await assert.rejects(submitToolCallResponse('review-1', { action: 'approve' }, {
      fetchImpl: async () => Response.json({ detail: 'not accepted' }, { status }),
    }), /not accepted|no longer active|not confirm/);
  });
}

test('disconnection and malformed acknowledgement preserve review failure', async () => {
  for (const fetchImpl of [async () => { throw Error('offline'); }, async () => new Response('not-json')]) {
    await assert.rejects(submitToolCallResponse('review-1', { action: 'approve' }, { fetchImpl }), /not confirm/);
  }
  await assert.rejects(submitToolCallResponse(null, {}, {
    fetchImpl: () => assert.fail('missing review must not submit'),
  }), /no longer active/);
});
