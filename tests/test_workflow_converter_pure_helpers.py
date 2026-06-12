"""
Pure helper unit tests for:
  factory_app/workflows/AgentGenerator/tools/workflow_converter.py

Covers helpers NOT already tested in test_agentgenerator_workflow_converter.py:

  _safe_path_segment:
    - empty/None → fallback used
    - special chars replaced with hyphens
    - leading/trailing ".-" stripped
    - fallback used when cleaned is empty after strip
    - valid segment preserved

  _normalize_nullable_text:
    - None → None
    - non-string → None
    - empty string → None
    - whitespace-only → None
    - "null" → None
    - "none" → None
    - "undefined" → None
    - case insensitive null detection
    - valid text → stripped and returned

  _normalize_tool_type:
    - "ui_tool" → "UI_Tool"
    - "UI_TOOL" → "UI_Tool"
    - "ui-tool" (hyphen) → "UI_Tool"
    - "ui_surface" → "UI_Surface"
    - "agent_tool" → "Agent_Tool"
    - "Agent_Tool" → "Agent_Tool"
    - empty string → "Agent_Tool"
    - None → "Agent_Tool"
    - unrecognized → "Agent_Tool"

  _default_ui_payload_schema:
    - returns dict with type, properties, additionalProperties
    - type is "object"
    - properties is empty dict
    - additionalProperties is True

  _normalize_ui_contract:
    - non-dict input → default contract
    - surface_kind always "agent_tool"
    - non-object payload_schema → replaced with default
    - empty payload_schema dict → replaced with default
    - valid payload_schema preserved
    - actions_schema: non-list → empty list
    - action without id skipped
    - action with null id skipped
    - action with valid id included
    - action label/description/variant optionally included
    - actions_schema result is list
"""
from __future__ import annotations

from factory_app.workflows.AgentGenerator.tools.workflow_converter import (
    _default_ui_payload_schema,
    _normalize_nullable_text,
    _normalize_tool_type,
    _normalize_ui_contract,
    _safe_path_segment,
)

# ---------------------------------------------------------------------------
# 1. _safe_path_segment
# ---------------------------------------------------------------------------

class TestSafePathSegment:
    def test_empty_string_uses_fallback(self):
        assert _safe_path_segment("", fallback="default") == "default"

    def test_none_uses_fallback(self):
        assert _safe_path_segment(None, fallback="default") == "default"

    def test_whitespace_only_uses_fallback(self):
        assert _safe_path_segment("   ", fallback="fb") == "fb"

    def test_valid_segment_preserved(self):
        assert _safe_path_segment("my-app", fallback="default") == "my-app"

    def test_special_chars_replaced_with_hyphen(self):
        result = _safe_path_segment("my app/v2", fallback="fb")
        assert " " not in result
        assert "/" not in result

    def test_leading_dots_stripped(self):
        result = _safe_path_segment(".hidden", fallback="fb")
        assert not result.startswith(".")

    def test_trailing_hyphens_stripped(self):
        result = _safe_path_segment("app/", fallback="fb")
        assert not result.endswith("-")

    def test_alphanumeric_underscore_preserved(self):
        result = _safe_path_segment("my_app_v2", fallback="fb")
        assert "my_app_v2" in result

    def test_empty_after_clean_uses_fallback(self):
        # All dots → stripped → empty → fallback
        assert _safe_path_segment("...", fallback="fb") == "fb"


# ---------------------------------------------------------------------------
# 2. _normalize_nullable_text
# ---------------------------------------------------------------------------

class TestNormalizeNullableText:
    def test_none_returns_none(self):
        assert _normalize_nullable_text(None) is None

    def test_non_string_returns_none(self):
        assert _normalize_nullable_text(42) is None
        assert _normalize_nullable_text([]) is None

    def test_empty_string_returns_none(self):
        assert _normalize_nullable_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _normalize_nullable_text("   ") is None

    def test_null_string_returns_none(self):
        assert _normalize_nullable_text("null") is None

    def test_none_string_returns_none(self):
        assert _normalize_nullable_text("none") is None

    def test_undefined_string_returns_none(self):
        assert _normalize_nullable_text("undefined") is None

    def test_case_insensitive_null_detection(self):
        assert _normalize_nullable_text("NULL") is None
        assert _normalize_nullable_text("None") is None
        assert _normalize_nullable_text("UNDEFINED") is None

    def test_valid_text_returned_stripped(self):
        assert _normalize_nullable_text("  hello  ") == "hello"

    def test_single_word_returned(self):
        assert _normalize_nullable_text("AppGenerator") == "AppGenerator"

    def test_whitespace_stripped(self):
        result = _normalize_nullable_text("  text  ")
        assert result == "text"


# ---------------------------------------------------------------------------
# 3. _normalize_tool_type
# ---------------------------------------------------------------------------

class TestNormalizeToolType:
    def test_ui_tool_lowercase(self):
        assert _normalize_tool_type("ui_tool") == "UI_Tool"

    def test_ui_tool_uppercase(self):
        assert _normalize_tool_type("UI_TOOL") == "UI_Tool"

    def test_ui_tool_hyphen(self):
        assert _normalize_tool_type("ui-tool") == "UI_Tool"

    def test_ui_surface_lowercase(self):
        assert _normalize_tool_type("ui_surface") == "UI_Surface"

    def test_ui_surface_uppercase(self):
        assert _normalize_tool_type("UI_SURFACE") == "UI_Surface"

    def test_agent_tool_lowercase(self):
        assert _normalize_tool_type("agent_tool") == "Agent_Tool"

    def test_agent_tool_mixed(self):
        assert _normalize_tool_type("Agent_Tool") == "Agent_Tool"

    def test_empty_string_returns_agent_tool(self):
        assert _normalize_tool_type("") == "Agent_Tool"

    def test_none_returns_agent_tool(self):
        assert _normalize_tool_type(None) == "Agent_Tool"

    def test_unrecognized_returns_agent_tool(self):
        assert _normalize_tool_type("custom_tool") == "Agent_Tool"


# ---------------------------------------------------------------------------
# 4. _default_ui_payload_schema
# ---------------------------------------------------------------------------

class TestDefaultUiPayloadSchema:
    def test_returns_dict(self):
        result = _default_ui_payload_schema()
        assert isinstance(result, dict)

    def test_type_is_object(self):
        assert _default_ui_payload_schema()["type"] == "object"

    def test_properties_is_empty_dict(self):
        assert _default_ui_payload_schema()["properties"] == {}

    def test_additional_properties_is_true(self):
        assert _default_ui_payload_schema()["additionalProperties"] is True


# ---------------------------------------------------------------------------
# 5. _normalize_ui_contract
# ---------------------------------------------------------------------------

class TestNormalizeUiContract:
    def test_non_dict_input_returns_default(self):
        result = _normalize_ui_contract("not-a-dict")
        assert result["surface_kind"] == "agent_tool"
        assert result["payload_schema"] == _default_ui_payload_schema()
        assert result["actions_schema"] == []

    def test_none_input_returns_default(self):
        result = _normalize_ui_contract(None)
        assert result["surface_kind"] == "agent_tool"

    def test_surface_kind_always_agent_tool(self):
        result = _normalize_ui_contract({"surface_kind": "custom_kind"})
        assert result["surface_kind"] == "agent_tool"

    def test_non_object_payload_schema_replaced_with_default(self):
        result = _normalize_ui_contract({"payload_schema": "not-a-dict"})
        assert result["payload_schema"] == _default_ui_payload_schema()

    def test_empty_payload_schema_replaced_with_default(self):
        result = _normalize_ui_contract({"payload_schema": {}})
        assert result["payload_schema"] == _default_ui_payload_schema()

    def test_valid_payload_schema_preserved(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        result = _normalize_ui_contract({"payload_schema": schema})
        assert result["payload_schema"]["properties"]["name"]["type"] == "string"

    def test_non_list_actions_schema_becomes_empty(self):
        result = _normalize_ui_contract({"actions_schema": "not-a-list"})
        assert result["actions_schema"] == []

    def test_action_without_id_skipped(self):
        result = _normalize_ui_contract({"actions_schema": [{"label": "Submit"}]})
        assert result["actions_schema"] == []

    def test_action_with_null_id_string_skipped(self):
        result = _normalize_ui_contract({"actions_schema": [{"id": "null"}]})
        assert result["actions_schema"] == []

    def test_action_with_valid_id_included(self):
        result = _normalize_ui_contract({"actions_schema": [{"id": "submit"}]})
        assert len(result["actions_schema"]) == 1
        assert result["actions_schema"][0]["id"] == "submit"

    def test_action_label_included_when_present(self):
        result = _normalize_ui_contract({"actions_schema": [{"id": "submit", "label": "Submit"}]})
        assert result["actions_schema"][0].get("label") == "Submit"

    def test_action_description_included_when_present(self):
        result = _normalize_ui_contract({
            "actions_schema": [{"id": "submit", "description": "Submit the form"}]
        })
        assert result["actions_schema"][0]["description"] == "Submit the form"

    def test_action_variant_included_when_present(self):
        result = _normalize_ui_contract({
            "actions_schema": [{"id": "submit", "variant": "primary"}]
        })
        assert result["actions_schema"][0]["variant"] == "primary"

    def test_action_approved_bool_preserved(self):
        result = _normalize_ui_contract({
            "actions_schema": [{"id": "submit", "approved": True}]
        })
        assert result["actions_schema"][0]["approved"] is True

    def test_actions_schema_result_is_list(self):
        result = _normalize_ui_contract({})
        assert isinstance(result["actions_schema"], list)

    def test_action_empty_payload_schema_replaced(self):
        result = _normalize_ui_contract({
            "actions_schema": [{"id": "submit", "payload_schema": {}}]
        })
        assert result["actions_schema"][0]["payload_schema"] == _default_ui_payload_schema()
