from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("case", ["plain", "wrapped", "duplicate", "blank", "internal", "not_auto"])
def test_componentless_auto_tool_preserves_explicit_agent_message(case):
    source = (ROOT / "chat-ui/src/pages/ChatPage.js").read_text(encoding="utf-8")
    branch = source.split("case 'tool_call': {", 1)[1].split(
        "// ── ui.* typed event contract", 1,
    )[0]
    callback = "data => { switch ('tool_call') { case 'tool_call': {" + branch + "} }"
    script = r"""
const assert = require('node:assert/strict');
const { callback, case: scenario } = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
let messages = [];
const setMessagesWithLogging = update => { messages = update(messages); };
const showSystemMessages = false;
const payload = { interaction_type: 'auto_tool', agent_message: ' What is your brand color? ' };
if (scenario === 'blank') payload.agent_message = '  ';
if (scenario === 'internal') delete payload.agent_message;
if (scenario === 'not_auto') payload.interaction_type = 'ui_tool';
const event = { tool_call_id: 'turn-1-tool-1', agent_name: 'ThemeInterviewAgent', payload };
const handle = eval('(' + callback + ')');
handle(scenario === 'wrapped' ? { data: event } : event);
if (scenario === 'duplicate') handle(event);
if (['plain', 'wrapped', 'duplicate'].includes(scenario)) {
  assert.equal(messages.length, 1);
  assert.equal(messages[0].sender, 'agent');
  assert.equal(messages[0].agentName, 'ThemeInterviewAgent');
  assert.equal(messages[0].content, 'What is your brand color?');
  assert.equal(messages[0].id, 'turn-1-tool-1-agent-message');
  assert.equal(messages[0].metadata.type, 'tool_call_agent_message');
  assert.equal(messages[0].toolCall, undefined);
} else {
  assert.deepEqual(messages, []);
}
"""
    result = subprocess.run(
        ["node", "--eval", script], input=json.dumps({"callback": callback, "case": case}),
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
