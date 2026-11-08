import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyChatProgressEvent,
  beginChatStatusProgressTurn,
  clearChatStatusProgress,
  emptyChatStatusProgress,
  getChatStatusProgress,
} from '../../chat-ui/src/pages/chatStatusProgress.js';

const scope = { mode: 'workflow', chatId: 'chat-1', workflowName: 'SourceImport' };
const event = (patch = {}) => ({
  type: 'chat.tool_progress', chat_id: 'chat-1', workflow_name: 'SourceImport',
  stage_id: 'read', stage_label: 'Reading sources', completed: 12, total: 40, unit: 'files',
  sequence: 4, ...patch,
});
const start = () => applyChatProgressEvent(emptyChatStatusProgress(), scope, event());

test('displays measured counts and indeterminate stages without guessing percentages', () => {
  let state = start();
  assert.deepEqual(getChatStatusProgress(state, scope), {
    stageId: 'read', label: 'Reading sources', completed: 12, total: 40, unit: 'files',
  });
  state = applyChatProgressEvent(state, scope, event({ sequence: 5, stage_id: 'analyze', stage_label: 'Comparing findings', completed: null, total: null, unit: null }));
  assert.equal(state.progress.label, 'Comparing findings');
  assert.equal(state.progress.completed, null);
  assert.equal(state.progress.total, null);
});

test('rejects replay, duplicate and out-of-order progress', () => {
  const state = start();
  for (const patch of [{}, { sequence: 3 }, { sequence: 8, replay: true }]) {
    assert.equal(applyChatProgressEvent(state, scope, event(patch)), state);
  }
  assert.equal(applyChatProgressEvent(state, scope, event({ sequence: 6 })).sequence, 6);
});

test('progress remains scoped to the active workflow chat and never appears in Ask', () => {
  const state = start();
  for (const other of [{ ...scope, mode: 'ask' }, { ...scope, chatId: 'chat-2' }, { ...scope, workflowName: 'Other' }]) {
    assert.equal(getChatStatusProgress(state, other), null);
    assert.equal(applyChatProgressEvent(state, other, event({ sequence: 8 })), state);
  }
  const switched = clearChatStatusProgress(state, { ...scope, chatId: 'chat-2' });
  assert.equal(getChatStatusProgress(switched, scope), null, 'returning to a chat must not revive its old stage');
});

for (const boundary of ['run_start', 'run_complete', 'error', 'awaiting_reply', 'resume_boundary', 'tool_call']) {
  test(`${boundary} clears the stage and advances the existing transport sequence`, () => {
    const state = applyChatProgressEvent(start(), scope, { type: `chat.${boundary}`, chat_id: 'chat-1', sequence: 8 });
    assert.equal(state.progress, null);
    assert.equal(state.sequence, 8);
    assert.equal(applyChatProgressEvent(state, scope, event({ sequence: 7 })), state);
    const next = applyChatProgressEvent(state, scope, event({ sequence: 9 }));
    if (['run_complete', 'error', 'awaiting_reply'].includes(boundary)) {
      assert.equal(next, state, 'later progress must not reopen a stopped or waiting run');
    } else {
      assert.equal(next.progress.completed, 12);
    }
  });
}

test('clearing a stage keeps ordering protection within the same connection', () => {
  const state = clearChatStatusProgress(start(), scope);
  assert.equal(state.progress, null);
  assert.equal(state.sequence, 4);
  assert.equal(applyChatProgressEvent(state, scope, event()), state);
  assert.equal(applyChatProgressEvent(state, scope, event({ sequence: 5, replay: true })), state);
  assert.equal(applyChatProgressEvent(state, scope, event({ sequence: 6 })).progress.label, 'Reading sources');
});

test('a new connection accepts restarted transport ordering while rejecting replay', () => {
  const oldConnection = start();
  const disconnected = clearChatStatusProgress(oldConnection, scope);
  assert.equal(disconnected.progress, null);
  const reconnected = emptyChatStatusProgress();
  assert.equal(applyChatProgressEvent(reconnected, scope, event({ sequence: 1, replay: true })), reconnected);
  assert.equal(applyChatProgressEvent(reconnected, scope, event({ sequence: 2 })).progress.completed, 12);
});

test('completion, failure and waiting reject late progress until work explicitly resumes', () => {
  for (const boundary of ['run_complete', 'error', 'awaiting_reply']) {
    const stopped = applyChatProgressEvent(start(), scope, {
      type: `chat.${boundary}`, chat_id: scope.chatId, sequence: 8,
    });
    assert.equal(applyChatProgressEvent(stopped, scope, event({ sequence: 9 })), stopped);
    const replayComplete = applyChatProgressEvent(stopped, scope, {
      type: 'chat.resume_boundary', chat_id: scope.chatId, sequence: 10,
    });
    assert.equal(replayComplete.blocked, true, 'history replay is not new work');
    assert.equal(applyChatProgressEvent(replayComplete, scope, event({ sequence: 11 })), replayComplete);
    for (const resumedBy of ['run_start', 'tool_call']) {
      const resumed = applyChatProgressEvent(stopped, scope, {
        type: `chat.${resumedBy}`, chat_id: scope.chatId, sequence: 12,
      });
      assert.equal(applyChatProgressEvent(resumed, scope, event({ sequence: 13 })).progress.completed, 12);
    }
    const userTurn = beginChatStatusProgressTurn(stopped, scope);
    assert.equal(userTurn.sequence, 8);
    assert.equal(userTurn.progress, null);
    assert.equal(applyChatProgressEvent(userTurn, scope, event({ sequence: 8 })), userTurn);
    assert.equal(applyChatProgressEvent(userTurn, scope, event({ sequence: 14 })).progress.completed, 12);
  }
});

test('rejects malformed or undeclared progress and unrelated lifecycle events', () => {
  const state = start();
  for (const patch of [
    { stage_id: '' }, { stage_label: '' }, { workflow_name: '' }, { chat_id: '' },
    { sequence: null }, { sequence: 9.2 }, { completed: 41 }, { total: null },
    { completed: -1 }, { completed: '12' }, { total: 40.2 }, { type: 'chat.text' },
    { completed: 0, total: 0 },
    { type: 'chat.run_complete', chat_id: 'other' },
  ]) assert.equal(applyChatProgressEvent(state, scope, event({ sequence: 9, ...patch })), state);
});

test('accepts zero completed items and both transport envelope shapes', () => {
  const empty = applyChatProgressEvent(emptyChatStatusProgress(), scope, event({ completed: 0, total: 40 }));
  assert.equal(empty.progress.completed, 0);
  const nested = applyChatProgressEvent(emptyChatStatusProgress(), scope, { type: 'chat.tool_progress', sequence: 7, data: event() });
  assert.equal(nested.sequence, 7, 'the transport envelope owns ordering');
  assert.equal(nested.progress.completed, 12);
});
