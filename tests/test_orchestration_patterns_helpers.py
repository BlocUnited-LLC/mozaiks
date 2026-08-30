"""
Pure helper unit tests for:
  mozaiksai/core/workflow/orchestration_patterns.py

Covers sync pure helpers (no IO/async):

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
    _next_agent_after_trigger,
)

# ---------------------------------------------------------------------------
# _next_agent_after_trigger
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
