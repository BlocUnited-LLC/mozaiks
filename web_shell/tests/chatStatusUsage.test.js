import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyChatUsageDelta,
  emptyChatStatusUsage,
  resetChatStatusRun,
} from '../../chat-ui/src/pages/chatStatusUsage.js';

const workflow = { mode: 'workflow', chatId: 'run-1' };
const ask = { mode: 'ask', chatId: 'ask-1' };

test('counts measured model calls once for the active chat', () => {
  const first = { event_id: 'call-1', chat_id: 'run-1', total_tokens: 18 };
  const second = { event_id: 'call-2', chat_id: 'run-1', total_tokens: 32 };
  let usage = applyChatUsageDelta(emptyChatStatusUsage(), workflow, first);
  assert.equal(usage.totalTokens, 18);
  assert.equal(applyChatUsageDelta(usage, workflow, first), usage);
  usage = applyChatUsageDelta(usage, workflow, second);
  assert.equal(usage.totalTokens, 50);
  assert.equal(applyChatUsageDelta(usage, workflow, { ...second, chat_id: 'other' }), usage);
  assert.equal(applyChatUsageDelta(usage, workflow, { ...second, event_id: '', total_tokens: 99 }), usage);
});

test('a new turn clears the displayed count but cannot replay an old call', () => {
  const first = { event_id: 'call-1', chat_id: 'run-1', total_tokens: 18 };
  let usage = applyChatUsageDelta(emptyChatStatusUsage(), workflow, first);
  usage = resetChatStatusRun(usage, workflow);
  assert.equal(usage.totalTokens, null);
  assert.equal(applyChatUsageDelta(usage, workflow, first), usage);
  usage = applyChatUsageDelta(usage, workflow, { event_id: 'call-2', chat_id: 'run-1', total_tokens: 7 });
  assert.equal(usage.totalTokens, 7);
});

test('Ask and workflow counts stay isolated even when chat IDs match', () => {
  const first = { event_id: 'call-1', chat_id: 'ask-1', total_tokens: 18 };
  let usage = applyChatUsageDelta(emptyChatStatusUsage(), ask, first);
  assert.equal(usage.totalTokens, 18);
  const workflowWithSameId = { mode: 'workflow', chatId: 'ask-1' };
  usage = applyChatUsageDelta(usage, workflowWithSameId, { ...first, event_id: 'call-2' });
  assert.equal(usage.totalTokens, 18);
  assert.equal(usage.scopeKey, 'workflow:ask-1');
  assert.equal(applyChatUsageDelta(usage, ask, { ...first, event_id: 'call-3' }).totalTokens, 18);
});
