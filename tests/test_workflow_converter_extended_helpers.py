"""
Pure helper unit tests for:
  factory_app/workflows/AgentGenerator/tools/workflow_converter.py

Covers helpers NOT tested in test_workflow_converter_pure_helpers.py:

  _normalize_workflow_extra_path:
    - None → None
    - empty string → None
    - whitespace-only → None
    - null byte → None
    - ".." component → None
    - "." component → None
    - "_shared" component → None
    - leading slash stripped, valid path returned
    - backslash separators converted
    - nested valid path returned
    - single valid segment returned

  _normalize_visual_agents:
    - None, backendonly → None
    - None, chat mode → []
    - None, no mode → []
    - string agent name → [name]
    - comma-separated string → list of names
    - "null" string, normal mode → []
    - "null" string, backendonly → None
    - "[]" string, normal → []
    - list of agents → returned
    - list with non-string items skipped
    - list with "none" strings skipped
    - duplicate agents deduped
    - non-list/non-string value, normal → []
    - non-list/non-string, backendonly → None

  _normalize_transition_rules:
    - non-list → []
    - empty list → []
    - non-dict entry → skipped
    - condition_type "llm" → raises ValueError
    - condition_type "string_llm" → raises ValueError
    - stale "condition" field present → raises ValueError
    - unsupported transition_type → raises ValueError
    - valid after_turn rule → transition_type set
    - after_turn with condition field → raises ValueError
    - context_equals with condition_key → valid
    - context_equals missing condition_key → raises ValueError
    - context_expression with expression → valid
    - context_expression missing expression → raises ValueError
    - tool_called with tool_name → valid
    - tool_called missing tool_name → raises ValueError

  _build_workflow_ui_barrel:
    - empty dict → header only with trailing newline
    - single component → correct export line
    - multiple components → sorted alphabetically
    - output contains auto-generated header comment
    - export line format: export { default as Name } from './path';

  _normalize_runtime_extensions:
    - non-list → []
    - non-dict entry → skipped
    - unknown kind → skipped
    - valid kind, no entrypoint → skipped
    - non-string entrypoint → skipped
    - entrypoint with _shared → skipped
    - entrypoint wrong prefix → skipped
    - valid entry → included

  _normalize_orchestrator_triggers:
    - non-list → []
    - non-dict entry → skipped
    - unknown trigger type → skipped
    - valid type "chat" → included
    - valid type "event" → included
    - trigger with only event key (no type) → included
    - trigger with only endpoint key → included
    - trigger with no discriminating key → skipped
    - string fields whitespace stripped

  _collect_code_files:
    - non-dict input → []
    - key absent → []
    - non-list value → []
    - non-dict entry → skipped
    - entry with unsafe path → skipped
    - entry with empty content → skipped
    - valid entry → returned
    - list_key parameter honoured
"""
from __future__ import annotations

import pytest

from factory_app.workflows.AgentGenerator.tools.workflow_converter import (
    _build_workflow_ui_barrel,
    _collect_code_files,
    _normalize_orchestrator_triggers,
    _normalize_runtime_extensions,
    _normalize_transition_rules,
    _normalize_visual_agents,
    _normalize_workflow_extra_path,
)

# ---------------------------------------------------------------------------
# Shared mock logger
# ---------------------------------------------------------------------------

class _NullLogger:
    """Drop-in logger that silently discards all messages."""

    def warning(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


_LOG = _NullLogger()


# ---------------------------------------------------------------------------
# 1. _normalize_workflow_extra_path
# ---------------------------------------------------------------------------

class TestNormalizeWorkflowExtraPath:
    def test_none_returns_none(self):
        assert _normalize_workflow_extra_path(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_workflow_extra_path("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_workflow_extra_path("   ") is None

    def test_null_byte_returns_none(self):
        assert _normalize_workflow_extra_path("tools/\x00file.py") is None

    def test_dotdot_component_returns_none(self):
        assert _normalize_workflow_extra_path("tools/../escape.py") is None

    def test_dot_component_normalized_by_posixpath(self):
        # PurePosixPath eliminates single-dot segments, so "tools/./file.py" → "tools/file.py"
        assert _normalize_workflow_extra_path("tools/./file.py") == "tools/file.py"

    def test_shared_component_returns_none(self):
        assert _normalize_workflow_extra_path("_shared/util.py") is None

    def test_shared_in_nested_path_returns_none(self):
        assert _normalize_workflow_extra_path("tools/_shared/util.py") is None

    def test_leading_slash_stripped_and_returned(self):
        result = _normalize_workflow_extra_path("/tools/my_tool.py")
        assert result == "tools/my_tool.py"

    def test_backslash_converted_to_forward_slash(self):
        result = _normalize_workflow_extra_path("tools\\my_tool.py")
        assert result == "tools/my_tool.py"

    def test_nested_valid_path_returned(self):
        assert _normalize_workflow_extra_path("tools/agents/helper.py") == "tools/agents/helper.py"

    def test_single_segment_returned(self):
        assert _normalize_workflow_extra_path("orchestrator.yaml") == "orchestrator.yaml"

    def test_non_string_coerced(self):
        # Any non-None value is str()-coerced
        result = _normalize_workflow_extra_path(42)
        assert result == "42"


# ---------------------------------------------------------------------------
# 2. _normalize_visual_agents
# ---------------------------------------------------------------------------

class TestNormalizeVisualAgents:
    def test_none_backendonly_returns_none(self):
        assert _normalize_visual_agents(None, workflow_startup_mode="BackendOnly") is None

    def test_none_chat_mode_returns_empty_list(self):
        assert _normalize_visual_agents(None, workflow_startup_mode="chat") == []

    def test_none_no_mode_returns_empty_list(self):
        assert _normalize_visual_agents(None, workflow_startup_mode=None) == []

    def test_single_string_agent_returned(self):
        assert _normalize_visual_agents("AgentA", workflow_startup_mode="chat") == ["AgentA"]

    def test_comma_separated_string_split(self):
        result = _normalize_visual_agents("AgentA, AgentB", workflow_startup_mode="chat")
        assert result == ["AgentA", "AgentB"]

    def test_null_string_normal_mode_returns_empty_list(self):
        assert _normalize_visual_agents("null", workflow_startup_mode="chat") == []

    def test_null_string_backendonly_returns_none(self):
        assert _normalize_visual_agents("null", workflow_startup_mode="BackendOnly") is None

    def test_empty_brackets_string_normal_returns_empty_list(self):
        assert _normalize_visual_agents("[]", workflow_startup_mode="chat") == []

    def test_empty_brackets_backendonly_returns_none(self):
        assert _normalize_visual_agents("[]", workflow_startup_mode="BackendOnly") is None

    def test_list_of_agents_returned(self):
        result = _normalize_visual_agents(["AgentA", "AgentB"], workflow_startup_mode="chat")
        assert result == ["AgentA", "AgentB"]

    def test_list_non_string_items_skipped(self):
        result = _normalize_visual_agents(["AgentA", 42, None], workflow_startup_mode="chat")
        assert result == ["AgentA"]

    def test_list_with_none_string_items_skipped(self):
        result = _normalize_visual_agents(["AgentA", "none", "AgentB"], workflow_startup_mode="chat")
        assert result == ["AgentA", "AgentB"]

    def test_duplicate_agents_deduped(self):
        result = _normalize_visual_agents(["AgentA", "AgentA", "AgentB"], workflow_startup_mode="chat")
        assert result == ["AgentA", "AgentB"]

    def test_non_list_non_string_normal_mode_returns_empty_list(self):
        assert _normalize_visual_agents(123, workflow_startup_mode="chat") == []

    def test_non_list_non_string_backendonly_returns_none(self):
        assert _normalize_visual_agents(123, workflow_startup_mode="BackendOnly") is None

    def test_empty_list_normal_mode_returns_empty(self):
        assert _normalize_visual_agents([], workflow_startup_mode="chat") == []

    def test_empty_list_backendonly_returns_none(self):
        assert _normalize_visual_agents([], workflow_startup_mode="BackendOnly") is None


# ---------------------------------------------------------------------------
# 3. _normalize_transition_rules
# ---------------------------------------------------------------------------

class TestNormalizeTransitionRules:
    def test_non_list_returns_empty(self):
        assert _normalize_transition_rules("not-a-list") == []

    def test_empty_list_returns_empty(self):
        assert _normalize_transition_rules([]) == []

    def test_non_dict_entry_skipped(self):
        assert _normalize_transition_rules(["string-entry"]) == []

    def test_condition_type_llm_raises(self):
        with pytest.raises(ValueError, match="LLM-evaluated"):
            _normalize_transition_rules([{"condition_type": "llm", "from_agent": "A"}])

    def test_condition_type_string_llm_raises(self):
        with pytest.raises(ValueError, match="LLM-evaluated"):
            _normalize_transition_rules([{"condition_type": "string_llm", "from_agent": "A"}])

    def test_stale_condition_field_raises(self):
        with pytest.raises(ValueError, match="expression"):
            _normalize_transition_rules([{"condition": "some expression", "from_agent": "A"}])

    def test_unsupported_transition_type_raises(self):
        with pytest.raises(ValueError, match="Unsupported transition_type"):
            _normalize_transition_rules([{"transition_type": "parallel", "from_agent": "A"}])

    def test_valid_after_turn_sets_type(self):
        result = _normalize_transition_rules([{"from_agent": "A", "to_agent": "B"}])
        assert len(result) == 1
        assert result[0]["transition_type"] == "after_turn"

    def test_after_turn_with_condition_fields_raises(self):
        rule = {
            "from_agent": "A",
            "to_agent": "B",
            "transition_type": "after_turn",
            "condition_type": "context_equals",
        }
        with pytest.raises(ValueError, match="must not declare condition"):
            _normalize_transition_rules([rule])

    def test_context_equals_valid(self):
        rule = {
            "from_agent": "A",
            "to_agent": "B",
            "condition_type": "context_equals",
            "condition_key": "status",
            "condition_value": "done",
        }
        result = _normalize_transition_rules([rule])
        assert len(result) == 1
        assert result[0]["condition_type"] == "context_equals"
        assert result[0]["condition_key"] == "status"
        assert result[0]["condition_value"] == "done"
        assert result[0]["transition_type"] == "condition"

    def test_context_equals_missing_condition_key_raises(self):
        rule = {"from_agent": "A", "condition_type": "context_equals", "condition_value": "v"}
        with pytest.raises(ValueError, match="condition_key"):
            _normalize_transition_rules([rule])

    def test_context_expression_valid(self):
        rule = {
            "from_agent": "A",
            "to_agent": "B",
            "condition_type": "context_expression",
            "context_expression": "ctx['key'] == 'val'",
        }
        result = _normalize_transition_rules([rule])
        assert result[0]["condition_type"] == "context_expression"
        assert result[0]["context_expression"] == "ctx['key'] == 'val'"

    def test_context_expression_missing_expression_raises(self):
        rule = {"from_agent": "A", "condition_type": "context_expression"}
        with pytest.raises(ValueError, match="context_expression"):
            _normalize_transition_rules([rule])

    def test_tool_called_valid(self):
        rule = {
            "from_agent": "A",
            "to_agent": "B",
            "condition_type": "tool_called",
            "tool_name": "save_output",
        }
        result = _normalize_transition_rules([rule])
        assert result[0]["condition_type"] == "tool_called"
        assert result[0]["tool_name"] == "save_output"

    def test_tool_called_missing_tool_name_raises(self):
        rule = {"from_agent": "A", "condition_type": "tool_called"}
        with pytest.raises(ValueError, match="tool_name"):
            _normalize_transition_rules([rule])

    def test_multiple_valid_rules_returned(self):
        rules = [
            {"from_agent": "A", "to_agent": "B"},
            {"from_agent": "B", "to_agent": "C"},
        ]
        result = _normalize_transition_rules(rules)
        assert len(result) == 2

    def test_none_entry_in_list_skipped(self):
        rules = [None, {"from_agent": "A", "to_agent": "B"}]
        result = _normalize_transition_rules(rules)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 4. _build_workflow_ui_barrel
# ---------------------------------------------------------------------------

class TestBuildWorkflowUiBarrel:
    def test_empty_dict_produces_header_only(self):
        result = _build_workflow_ui_barrel({})
        assert "AUTO-GENERATED FILE - workflow UI barrel." in result
        assert "export {" not in result

    def test_single_component_export_line(self):
        result = _build_workflow_ui_barrel({"MyComp": "ui/MyComp.jsx"})
        assert "export { default as MyComp } from './MyComp.jsx';" in result

    def test_nested_path_export_line(self):
        result = _build_workflow_ui_barrel({"MyComp": "ui/components/MyComp.jsx"})
        assert "export { default as MyComp } from './components/MyComp.jsx';" in result

    def test_components_sorted_alphabetically(self):
        result = _build_workflow_ui_barrel({
            "ZComp": "ui/ZComp.jsx",
            "AComp": "ui/AComp.jsx",
        })
        lines = result.splitlines()
        export_lines = [ln for ln in lines if ln.startswith("export")]
        assert export_lines[0].startswith("export { default as AComp")
        assert export_lines[1].startswith("export { default as ZComp")

    def test_output_starts_with_block_comment(self):
        result = _build_workflow_ui_barrel({})
        assert result.startswith("/**")

    def test_output_ends_with_newline(self):
        result = _build_workflow_ui_barrel({"MyComp": "ui/MyComp.jsx"})
        assert result.endswith("\n")

    def test_auto_registered_comment_present(self):
        result = _build_workflow_ui_barrel({})
        assert "@chat-workflows" in result


# ---------------------------------------------------------------------------
# 5. _normalize_runtime_extensions
# ---------------------------------------------------------------------------

class TestNormalizeRuntimeExtensions:
    def _call(self, extensions, workflow_name="MyWorkflow"):
        return _normalize_runtime_extensions(
            extensions, workflow_name=workflow_name, wf_logger=_LOG
        )

    def test_non_list_returns_empty(self):
        assert self._call("not-a-list") == []

    def test_empty_list_returns_empty(self):
        assert self._call([]) == []

    def test_non_dict_entry_skipped(self):
        assert self._call(["not-a-dict"]) == []

    def test_unknown_kind_skipped(self):
        entry = {"kind": "unknown_kind", "entrypoint": "workflows.MyWorkflow.tools.ext"}
        assert self._call([entry]) == []

    def test_valid_kind_no_entrypoint_skipped(self):
        assert self._call([{"kind": "api_router"}]) == []

    def test_non_string_entrypoint_skipped(self):
        entry = {"kind": "api_router", "entrypoint": 42}
        assert self._call([entry]) == []

    def test_entrypoint_with_shared_skipped(self):
        entry = {
            "kind": "api_router",
            "entrypoint": "workflows.MyWorkflow._shared.router",
        }
        assert self._call([entry]) == []

    def test_entrypoint_wrong_prefix_skipped(self):
        entry = {
            "kind": "api_router",
            "entrypoint": "workflows.OtherWorkflow.tools.router",
        }
        assert self._call([entry]) == []

    def test_valid_api_router_entry_included(self):
        entry = {
            "kind": "api_router",
            "entrypoint": "workflows.MyWorkflow.tools.router",
        }
        result = self._call([entry])
        assert len(result) == 1
        assert result[0]["kind"] == "api_router"
        assert result[0]["entrypoint"] == "workflows.MyWorkflow.tools.router"

    def test_valid_startup_service_included(self):
        entry = {
            "kind": "startup_service",
            "entrypoint": "workflows.MyWorkflow.tools.svc",
        }
        result = self._call([entry])
        assert len(result) == 1
        assert result[0]["kind"] == "startup_service"

    def test_extra_fields_preserved(self):
        entry = {
            "kind": "api_router",
            "entrypoint": "workflows.MyWorkflow.tools.router",
            "prefix": "/api/v1",
        }
        result = self._call([entry])
        assert result[0]["prefix"] == "/api/v1"


# ---------------------------------------------------------------------------
# 6. _normalize_orchestrator_triggers
# ---------------------------------------------------------------------------

class TestNormalizeOrchestratorTriggers:
    def _call(self, triggers):
        return _normalize_orchestrator_triggers(triggers, wf_logger=_LOG)

    def test_non_list_returns_empty(self):
        assert self._call("not-a-list") == []

    def test_empty_list_returns_empty(self):
        assert self._call([]) == []

    def test_non_dict_entry_skipped(self):
        assert self._call(["string"]) == []

    def test_unknown_trigger_type_skipped(self):
        assert self._call([{"type": "webhook"}]) == []

    def test_valid_chat_type_included(self):
        result = self._call([{"type": "chat"}])
        assert len(result) == 1
        assert result[0]["type"] == "chat"

    def test_valid_event_type_included(self):
        result = self._call([{"type": "event", "event": "app.build_completed"}])
        assert len(result) == 1
        assert result[0]["type"] == "event"
        assert result[0]["event"] == "app.build_completed"

    def test_trigger_with_only_event_key_included(self):
        result = self._call([{"event": "app.ready"}])
        assert len(result) == 1
        assert result[0]["event"] == "app.ready"

    def test_trigger_with_only_endpoint_included(self):
        result = self._call([{"endpoint": "/api/trigger"}])
        assert len(result) == 1
        assert result[0]["endpoint"] == "/api/trigger"

    def test_trigger_with_no_discriminating_key_skipped(self):
        # Has description but no type, event, or endpoint
        assert self._call([{"description": "standalone"}]) == []

    def test_string_fields_whitespace_stripped(self):
        result = self._call([{"type": "chat", "description": "  some desc  "}])
        assert result[0]["description"] == "some desc"

    def test_valid_route_type_included(self):
        result = self._call([{"type": "route", "endpoint": "/hook"}])
        assert result[0]["type"] == "route"

    def test_capability_id_included_when_present(self):
        result = self._call([{"type": "action", "capability_id": "my-capability"}])
        assert result[0]["capability_id"] == "my-capability"


# ---------------------------------------------------------------------------
# 7. _collect_code_files
# ---------------------------------------------------------------------------

class TestCollectCodeFiles:
    def _call(self, payload, list_key="tools"):
        return _collect_code_files(
            payload, list_key=list_key, source_name="TestSource", wf_logger=_LOG
        )

    def test_non_dict_input_returns_empty(self):
        assert self._call("not-a-dict") == []

    def test_missing_key_returns_empty(self):
        assert self._call({}) == []

    def test_non_list_value_returns_empty(self):
        assert self._call({"tools": "not-a-list"}) == []

    def test_non_dict_entry_skipped(self):
        assert self._call({"tools": ["string-entry"]}) == []

    def test_entry_with_unsafe_dotdot_path_skipped(self):
        entry = {"filename": "../escape.py", "content": "print('x')"}
        assert self._call({"tools": [entry]}) == []

    def test_entry_with_empty_content_skipped(self):
        entry = {"filename": "tools/helper.py", "content": ""}
        assert self._call({"tools": [entry]}) == []

    def test_entry_with_whitespace_content_skipped(self):
        entry = {"filename": "tools/helper.py", "content": "   "}
        assert self._call({"tools": [entry]}) == []

    def test_valid_entry_returned(self):
        entry = {"filename": "tools/helper.py", "content": "def helper(): pass"}
        result = self._call({"tools": [entry]})
        assert len(result) == 1
        assert result[0]["path"] == "tools/helper.py"
        assert result[0]["content"] == "def helper(): pass"

    def test_path_key_used_when_filename_absent(self):
        entry = {"path": "tools/util.py", "content": "x = 1"}
        result = self._call({"tools": [entry]})
        assert result[0]["path"] == "tools/util.py"

    def test_filename_takes_priority_over_path(self):
        entry = {"filename": "tools/a.py", "path": "tools/b.py", "content": "x = 1"}
        result = self._call({"tools": [entry]})
        assert result[0]["path"] == "tools/a.py"

    def test_list_key_parameter_respected(self):
        payload = {
            "tools": [{"filename": "tools/a.py", "content": "x = 1"}],
            "lifecycle_tools": [{"filename": "tools/b.py", "content": "y = 2"}],
        }
        result = self._call(payload, list_key="lifecycle_tools")
        assert len(result) == 1
        assert result[0]["path"] == "tools/b.py"

    def test_multiple_valid_entries_returned(self):
        payload = {
            "tools": [
                {"filename": "tools/a.py", "content": "a = 1"},
                {"filename": "tools/b.py", "content": "b = 2"},
            ]
        }
        result = self._call(payload)
        assert len(result) == 2
