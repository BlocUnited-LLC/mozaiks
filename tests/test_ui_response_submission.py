from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("status", [200, 404, 403, 500, "network", "read_only", "new_review"])
def test_review_is_only_dismissed_after_server_accepts(status):
    source = (ROOT / "chat-ui/src/pages/ChatPage.js").read_text(encoding="utf-8")
    callback = source.split("const handleAgentAction = ", 1)[1].split(
        "\n\n  const handleAppClick", 1,
    )[0].strip().removesuffix(";")
    helper = (ROOT / "chat-ui/src/adapters/uiToolResponse.js").read_text(encoding="utf-8").removeprefix("export ")
    script = r"""
const assert = require('node:assert/strict');
const { callback, helper, status } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const submitToolCallResponse = eval('(' + helper + ')');
const changes = [];
const updates = [];
let messages = [];
let release;
const api = { getHttpBaseUrl: () => 'http://backend.test' };
const getAccessToken = () => 'test-token';
const lastArtifactEventRef = { current: 'review-1' };
const artifactAutoClearRef = { current: false };
const currentChatId = 'chat-1';
const setIsSidePanelOpen = value => changes.push(['open', value]);
const dispatchSurfaceAction = value => changes.push(['surface', value]);
const setCurrentArtifactMessages = value => changes.push(['artifacts', value]);
const clearStoredArtifactState = value => changes.push(['clear', value]);
const setMessagesWithLogging = update => { messages = update(messages); };
const dynamicUIHandler = { notifyUIUpdate: update => updates.push(update) };
const fetch = async (url, options) => {
  assert.equal(url, 'http://backend.test/api/tool-call/respond');
  assert.equal(options.method, 'POST');
  assert.equal(options.headers.Authorization, 'Bearer test-token');
  assert.deepEqual(JSON.parse(options.body), {
    event_id: 'review-1', response_data: { approved: true },
  });
  await new Promise(resolve => { release = resolve; });
  if (status === 'network') throw new Error('Untrusted provider details');
  return { ok: status === 200 || status === 'new_review', status, json: async () => ({ status: 'success' }) };
};
const handle = eval('(' + callback + ')');
(async () => {
  const pending = handle({
    type: 'tool_call_response', tool_name: 'WorkflowPlanReview',
    tool_call_id: status === 'read_only' ? null : 'review-1', response: { approved: true },
  });
  if (status !== 'read_only') {
    assert.deepEqual(changes, []);
    assert.equal(artifactAutoClearRef.current, false);
    if (status === 'new_review') lastArtifactEventRef.current = 'review-2';
    release();
  }
  const accepted = await pending;
  if ([200, 'read_only', 'new_review'].includes(status)) {
    assert.equal(accepted, true);
    assert.deepEqual(updates, []);
    assert.deepEqual(messages, []);
    if (status === 'new_review') {
      assert.deepEqual(changes, []);
      assert.equal(lastArtifactEventRef.current, 'review-2');
    } else {
      assert.equal(lastArtifactEventRef.current, null);
      assert.equal(artifactAutoClearRef.current, true);
      assert.ok(changes.some(change => change[0] === 'clear' && change[1] === 'chat-1'));
    }
  } else {
    assert.equal(accepted, false);
    assert.deepEqual(changes, []);
    assert.equal(lastArtifactEventRef.current, 'review-1');
    assert.equal(artifactAutoClearRef.current, false);
    assert.equal(updates.length, 1);
    assert.equal(updates[0].type, 'ui.update');
    assert.equal(updates[0].tool_call_id, 'review-1');
    assert.equal(messages[0].content, updates[0].patch.error);
    assert.ok(!messages[0].content.includes('Untrusted provider details'));
    if (status === 404) assert.ok(messages[0].content.includes('no longer active'));
    if (status === 'network') assert.ok(messages[0].content.includes('did not confirm'));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        ["node", "--eval", script], input=json.dumps({"callback": callback, "helper": helper, "status": status}),
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
