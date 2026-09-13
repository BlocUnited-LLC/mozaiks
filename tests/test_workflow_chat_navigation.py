import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("mode", ["refinement", "same", "fresh", "removed", "runtime_handoff"])
def test_explicit_navigation_adopts_new_chat_without_rewinding_runtime_handoffs(mode):
    source = (ROOT / "chat-ui/src/pages/ChatPage.js").read_text(encoding="utf-8")
    effect = source.split("// A routed refinement can change the chat", 1)[1].split(
        "useEffect(() => {", 1,
    )[1].split("\n  }, [", 1)[0]
    assert "if (queryFreshStart || chatNavigationPending)" in source
    assert "if (chatNavigationPending) return;" in source
    script = r"""
const assert = require('node:assert/strict');
const { effect, mode } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const queryChatId = mode === 'removed' ? null : mode === 'refinement' ? 'new-chat' : 'old-chat';
const currentChatId = mode === 'runtime_handoff' ? 'runtime-new-chat' : 'old-chat';
const queryFreshStart = mode === 'fresh';
const navigationChatIdRef = { current: 'old-chat' };
const wsRef = { current: { close: () => changes.push(['close']) } };
const connectionInProgressRef = { current: true };
const validatedChatIdRef = { current: 'old-chat' };
const urlWorkflowName = 'AppGenerator';
const changes = [];
const setWs = value => changes.push(['socket', value]);
const setConnectionInitialized = value => changes.push(['initialized', value]);
const setConnectionStatus = value => changes.push(['status', value]);
const setChatExists = value => changes.push(['exists', value]);
const setCurrentChatId = value => changes.push(['current', value]);
const setActiveChatId = value => changes.push(['active', value]);
const setCurrentWorkflowName = value => changes.push(['workflow', value]);
const setActiveWorkflowName = value => changes.push(['active-workflow', value]);
const setWorkflowCompleted = value => changes.push(['completed', value]);
const setCompletionData = value => changes.push(['completion', value]);
const setPendingWorkflowReply = value => changes.push(['reply', value]);
const setMessagesWithLogging = value => changes.push(['messages', value]);
const apply = eval('(function () {' + effect + '})');
apply();
if (mode === 'refinement') {
  assert.deepEqual(changes[0], ['close']);
  assert.ok(changes.some(([key, value]) => key === 'current' && value === 'new-chat'));
  assert.ok(changes.some(([key, value]) => key === 'active' && value === 'new-chat'));
  assert.equal(connectionInProgressRef.current, false);
  assert.equal(validatedChatIdRef.current, null);
  const count = changes.length;
  apply();
  assert.equal(changes.length, count);
} else assert.deepEqual(changes, []);
"""
    result = subprocess.run(
        ["node", "--eval", script], cwd=ROOT, capture_output=True, text=True,
        input=json.dumps({"effect": effect, "mode": mode}), timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
