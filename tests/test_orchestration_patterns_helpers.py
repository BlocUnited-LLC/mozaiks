"""
Pure helper unit tests for:
  mozaiksai/core/workflow/orchestration_patterns.py

Covers sync pure helpers (no IO/async):

  _messages_to_network_prompt:
    - empty list → "."
    - message with content → "name (role): content"
    - message with no role → default role "user"
    - message with no name → uses role as name
    - message with no content → skipped
    - non-dict entry → skipped
    - multiple messages → joined with "\n\n"
    - all messages empty → "."

  _next_agent_after_trigger:
    - empty transition_rules → None
    - no matching source → None
    - matching source with valid target → target returned
    - matching source with "terminate" target → None (skip terminate)
    - matching source with empty target → None
    - multiple rules, first match returned
"""
from __future__ import annotations

from mozaiksai.core.workflow.orchestration_patterns import (
    _messages_to_network_prompt,
    _next_agent_after_trigger,
)

# ---------------------------------------------------------------------------
# 1. _messages_to_network_prompt
# ---------------------------------------------------------------------------

class TestMessagesToNetworkPrompt:
    def test_empty_list_returns_dot(self):
        assert _messages_to_network_prompt([]) == "."

    def test_single_message_formatted(self):
        messages = [{"role": "user", "content": "Hello"}]
        result = _messages_to_network_prompt(messages)
        assert "user (user): Hello" == result

    def test_name_takes_priority_over_role(self):
        messages = [{"role": "assistant", "name": "PlannerAgent", "content": "Let me plan"}]
        result = _messages_to_network_prompt(messages)
        assert "PlannerAgent (assistant): Let me plan" == result

    def test_no_role_defaults_to_user(self):
        messages = [{"content": "A message"}]
        result = _messages_to_network_prompt(messages)
        assert "user (user): A message" == result

    def test_no_name_uses_role_as_name(self):
        messages = [{"role": "assistant", "content": "Response here"}]
        result = _messages_to_network_prompt(messages)
        assert "assistant (assistant): Response here" == result

    def test_empty_content_skipped(self):
        messages = [{"role": "user", "content": ""}]
        result = _messages_to_network_prompt(messages)
        assert result == "."

    def test_whitespace_content_skipped(self):
        messages = [{"role": "user", "content": "   "}]
        result = _messages_to_network_prompt(messages)
        assert result == "."

    def test_non_dict_entry_skipped(self):
        messages = ["not-a-dict", {"role": "user", "content": "Hello"}]
        result = _messages_to_network_prompt(messages)
        assert "user (user): Hello" == result

    def test_multiple_messages_joined_with_double_newline(self):
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Second"},
        ]
        result = _messages_to_network_prompt(messages)
        parts = result.split("\n\n")
        assert len(parts) == 2
        assert "First" in parts[0]
        assert "Second" in parts[1]

    def test_all_empty_content_messages_returns_dot(self):
        messages = [{"role": "user", "content": ""}, {"role": "user"}]
        result = _messages_to_network_prompt(messages)
        assert result == "."

    def test_none_content_skipped(self):
        messages = [{"role": "user", "content": None}]
        result = _messages_to_network_prompt(messages)
        assert result == "."


# ---------------------------------------------------------------------------
# 2. _next_agent_after_trigger
# ---------------------------------------------------------------------------

class TestNextAgentAfterTrigger:
    def test_empty_rules_returns_none(self):
        assert _next_agent_after_trigger(transition_rules=[], trigger_agent="ClassifierAgent") is None

    def test_no_matching_source_returns_none(self):
        rules = [{"source_agent": "OtherAgent", "target_agent": "PlannerAgent"}]
        assert _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent") is None

    def test_matching_source_returns_target(self):
        rules = [{"source_agent": "ClassifierAgent", "target_agent": "PlannerAgent"}]
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result == "PlannerAgent"

    def test_terminate_target_returns_none(self):
        rules = [{"source_agent": "ClassifierAgent", "target_agent": "terminate"}]
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result is None

    def test_empty_target_returns_none(self):
        rules = [{"source_agent": "ClassifierAgent", "target_agent": ""}]
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result is None

    def test_missing_target_key_returns_none(self):
        rules = [{"source_agent": "ClassifierAgent"}]
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result is None

    def test_first_matching_rule_returned(self):
        rules = [
            {"source_agent": "ClassifierAgent", "target_agent": "PlannerAgent"},
            {"source_agent": "ClassifierAgent", "target_agent": "CoderAgent"},
        ]
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result == "PlannerAgent"

    def test_whitespace_in_source_handled(self):
        rules = [{"source_agent": "  ClassifierAgent  ", "target_agent": "PlannerAgent"}]
        # source_agent is stripped via str(...).strip()
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result == "PlannerAgent"

    def test_first_and_second_rule_both_match_returns_first(self):
        rules = [
            {"source_agent": "ClassifierAgent", "target_agent": "PlannerAgent"},
            {"source_agent": "ClassifierAgent", "target_agent": "CoderAgent"},
        ]
        # Already covered by test_first_matching_rule_returned; keep for explicitness
        result = _next_agent_after_trigger(transition_rules=rules, trigger_agent="ClassifierAgent")
        assert result == "PlannerAgent"
