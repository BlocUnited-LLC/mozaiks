"""
Export agent workflow pure helper unit tests.

Covers:
  _find_zip_entry:
    - suffix match at end of name → returned
    - suffix not found → None
    - non-string items skipped
    - empty list → None
    - first match returned

  _extract_agent_names:
    - None → []
    - non-dict/non-list → []
    - dict with nested "agents" key (dict) → sorted key list
    - dict without "agents" key → flat key list (sorted)
    - list of dicts with name key → sorted deduplicated names
    - list of dicts without name → skipped
    - whitespace-only names excluded
    - names sorted alphabetically

  _extract_tool_names:
    - non-dict input → []
    - no "tools" key → []
    - tools not a list → []
    - list items with name key → sorted names
    - list items with function fallback → function names
    - non-dict items skipped
    - duplicates deduplicated (sorted, last-write-wins via dict.fromkeys)
    - whitespace-only names excluded
"""
from __future__ import annotations

from factory_app.workflows.AgentGenerator.tools.export_agent_workflow import (
    _extract_agent_names,
    _extract_tool_names,
    _find_zip_entry,
)

# ---------------------------------------------------------------------------
# 1. _find_zip_entry
# ---------------------------------------------------------------------------

class TestFindZipEntry:
    def test_suffix_match_returned(self):
        names = ["workflow/agents.yaml", "workflow/tools.yaml"]
        assert _find_zip_entry(names, "/agents.yaml") == "workflow/agents.yaml"

    def test_no_match_returns_none(self):
        names = ["workflow/tools.yaml"]
        assert _find_zip_entry(names, "/agents.yaml") is None

    def test_empty_list_returns_none(self):
        assert _find_zip_entry([], "/agents.yaml") is None

    def test_non_string_items_skipped(self):
        names = [42, None, "workflow/agents.yaml"]
        assert _find_zip_entry(names, "/agents.yaml") == "workflow/agents.yaml"

    def test_first_match_returned(self):
        names = ["a/agents.yaml", "b/agents.yaml"]
        assert _find_zip_entry(names, "/agents.yaml") == "a/agents.yaml"

    def test_partial_suffix_not_matched(self):
        # "gents.yaml" is not "/agents.yaml"
        names = ["workflow/gents.yaml"]
        assert _find_zip_entry(names, "/agents.yaml") is None


# ---------------------------------------------------------------------------
# 2. _extract_agent_names
# ---------------------------------------------------------------------------

class TestExtractAgentNames:
    def test_none_returns_empty(self):
        assert _extract_agent_names(None) == []

    def test_string_returns_empty(self):
        assert _extract_agent_names("not_a_collection") == []

    def test_int_returns_empty(self):
        assert _extract_agent_names(42) == []

    def test_dict_with_agents_key_dict(self):
        payload = {"agents": {"PlannerAgent": {}, "BuilderAgent": {}}}
        result = _extract_agent_names(payload)
        assert result == ["BuilderAgent", "PlannerAgent"]

    def test_dict_without_agents_key(self):
        payload = {"AgentA": {}, "AgentB": {}}
        result = _extract_agent_names(payload)
        assert result == ["AgentA", "AgentB"]

    def test_list_of_dicts_with_name(self):
        payload = [{"name": "PlannerAgent"}, {"name": "BuilderAgent"}]
        result = _extract_agent_names(payload)
        assert result == ["BuilderAgent", "PlannerAgent"]

    def test_list_items_without_name_skipped(self):
        payload = [{"id": "no_name"}, {"name": "ValidAgent"}]
        result = _extract_agent_names(payload)
        assert result == ["ValidAgent"]

    def test_whitespace_only_names_excluded(self):
        payload = {"  ": {}, "ValidAgent": {}}
        result = _extract_agent_names(payload)
        assert result == ["ValidAgent"]

    def test_sorted_alphabetically(self):
        payload = {"ZAgent": {}, "AAgent": {}, "MAgent": {}}
        result = _extract_agent_names(payload)
        assert result == ["AAgent", "MAgent", "ZAgent"]

    def test_list_deduplicates_names(self):
        payload = [{"name": "Agent1"}, {"name": "Agent1"}, {"name": "Agent2"}]
        result = _extract_agent_names(payload)
        assert result == ["Agent1", "Agent2"]

    def test_empty_dict_returns_empty(self):
        assert _extract_agent_names({}) == []

    def test_empty_list_returns_empty(self):
        assert _extract_agent_names([]) == []


# ---------------------------------------------------------------------------
# 3. _extract_tool_names
# ---------------------------------------------------------------------------

class TestExtractToolNames:
    def test_non_dict_returns_empty(self):
        assert _extract_tool_names("not_a_dict") == []
        assert _extract_tool_names(None) == []

    def test_no_tools_key_returns_empty(self):
        assert _extract_tool_names({"other": []}) == []

    def test_tools_not_list_returns_empty(self):
        assert _extract_tool_names({"tools": "not_a_list"}) == []

    def test_name_key_extracted(self):
        payload = {"tools": [{"name": "save_output"}, {"name": "load_config"}]}
        result = _extract_tool_names(payload)
        assert result == ["load_config", "save_output"]

    def test_function_fallback_when_no_name(self):
        payload = {"tools": [{"function": "do_something"}]}
        result = _extract_tool_names(payload)
        assert result == ["do_something"]

    def test_non_dict_items_skipped(self):
        payload = {"tools": ["string_item", {"name": "valid_tool"}]}
        result = _extract_tool_names(payload)
        assert result == ["valid_tool"]

    def test_sorted_alphabetically(self):
        payload = {"tools": [{"name": "z_tool"}, {"name": "a_tool"}]}
        result = _extract_tool_names(payload)
        assert result == ["a_tool", "z_tool"]

    def test_whitespace_only_names_excluded(self):
        payload = {"tools": [{"name": "  "}, {"name": "valid"}]}
        result = _extract_tool_names(payload)
        assert result == ["valid"]

    def test_empty_tools_list_returns_empty(self):
        assert _extract_tool_names({"tools": []}) == []

    def test_duplicates_removed(self):
        payload = {"tools": [{"name": "save_output"}, {"name": "save_output"}]}
        result = _extract_tool_names(payload)
        assert result == ["save_output"]
