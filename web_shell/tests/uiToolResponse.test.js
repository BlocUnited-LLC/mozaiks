import assert from 'node:assert/strict';
import test from 'node:test';
import { submitToolCallResponse } from '../../chat-ui/src/adapters/uiToolResponse.js';

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
