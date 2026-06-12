"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_app_ui_quality_gate.py

Covers:
  _parse_json_object:
    - dict input → returned as-is
    - non-string non-dict → None
    - empty string → None
    - whitespace string → None
    - valid JSON string → parsed dict
    - JSON array string → None (not a dict)
    - fenced JSON block (```json {...} ```) → parsed
    - fenced block without "json" language tag → parsed
    - JSON embedded in prose → extracted from first { to last }
    - invalid JSON → None

  _latest_app_schema_output:
    - empty list → None
    - None → None
    - message with structured_output containing "manifest" and "pages" → returned
    - message with non-AppSchemaAgent sender → skipped
    - message with AppSchemaAgent structured_output → returned
    - message with content JSON string having manifest+pages → returned
    - reversed order — most recent first
    - message missing both structured_output and matching content → skipped
"""
from __future__ import annotations

import json

from factory_app.workflows.AppGenerator.tools.hook_app_ui_quality_gate import (
    _latest_app_schema_output,
    _parse_json_object,
)

# ---------------------------------------------------------------------------
# 1. _parse_json_object
# ---------------------------------------------------------------------------

class TestParseJsonObject:
    def test_dict_returned_as_is(self):
        data = {"manifest": {}, "pages": []}
        result = _parse_json_object(data)
        assert result is data

    def test_none_returns_none(self):
        assert _parse_json_object(None) is None

    def test_integer_returns_none(self):
        assert _parse_json_object(42) is None

    def test_list_returns_none(self):
        assert _parse_json_object([]) is None

    def test_empty_string_returns_none(self):
        assert _parse_json_object("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_json_object("   ") is None

    def test_valid_json_object_string_parsed(self):
        result = _parse_json_object('{"manifest": {}, "pages": []}')
        assert isinstance(result, dict)
        assert "manifest" in result

    def test_json_array_string_returns_none(self):
        assert _parse_json_object('["a", "b"]') is None

    def test_fenced_json_block_with_language_tag(self):
        content = '```json\n{"manifest": {}, "pages": []}\n```'
        result = _parse_json_object(content)
        assert isinstance(result, dict)
        assert "manifest" in result

    def test_fenced_block_without_language_tag(self):
        content = '```\n{"manifest": {}, "pages": []}\n```'
        result = _parse_json_object(content)
        assert isinstance(result, dict)

    def test_json_embedded_in_prose(self):
        content = 'Here is the output: {"manifest": {}, "pages": []} - done.'
        result = _parse_json_object(content)
        assert isinstance(result, dict)
        assert "manifest" in result

    def test_invalid_json_returns_none(self):
        assert _parse_json_object('{"invalid": json}') is None

    def test_nested_json_object_parsed(self):
        data = {"manifest": {"app_id": "test"}, "pages": [{"route": "/"}]}
        result = _parse_json_object(json.dumps(data))
        assert result == data


# ---------------------------------------------------------------------------
# 2. _latest_app_schema_output
# ---------------------------------------------------------------------------

class TestLatestAppSchemaOutput:
    def test_empty_list_returns_none(self):
        assert _latest_app_schema_output([]) is None

    def test_none_returns_none(self):
        assert _latest_app_schema_output(None) is None  # type: ignore[arg-type]

    def test_message_with_matching_structured_output(self):
        messages = [
            {
                "name": "AppSchemaAgent",
                "structured_output": {"manifest": {}, "pages": []},
            }
        ]
        result = _latest_app_schema_output(messages)
        assert result == {"manifest": {}, "pages": []}

    def test_message_with_non_appschema_sender_skipped(self):
        messages = [
            {
                "name": "OtherAgent",
                "structured_output": {"manifest": {}, "pages": []},
            }
        ]
        assert _latest_app_schema_output(messages) is None

    def test_message_with_no_sender_accepted(self):
        # No "name" field → sender is "" → accepts AppSchemaAgent-less messages
        messages = [
            {
                "structured_output": {"manifest": {}, "pages": []},
            }
        ]
        result = _latest_app_schema_output(messages)
        assert result is not None

    def test_message_with_json_content(self):
        messages = [
            {
                "name": "AppSchemaAgent",
                "content": json.dumps({"manifest": {}, "pages": []}),
            }
        ]
        result = _latest_app_schema_output(messages)
        assert isinstance(result, dict)
        assert "manifest" in result

    def test_returns_most_recent_matching_message(self):
        earlier = {"name": "AppSchemaAgent", "structured_output": {"manifest": {"v": 1}, "pages": []}}
        later = {"name": "AppSchemaAgent", "structured_output": {"manifest": {"v": 2}, "pages": []}}
        result = _latest_app_schema_output([earlier, later])
        # reversed() gives later first
        assert result["manifest"]["v"] == 2

    def test_structured_output_must_have_manifest_and_pages(self):
        # Missing "pages" → not a valid AppSchemaOutput
        messages = [
            {
                "name": "AppSchemaAgent",
                "structured_output": {"manifest": {}},
            }
        ]
        assert _latest_app_schema_output(messages) is None

    def test_structured_output_missing_manifest_skipped(self):
        messages = [
            {
                "name": "AppSchemaAgent",
                "structured_output": {"pages": []},
            }
        ]
        assert _latest_app_schema_output(messages) is None

    def test_non_dict_message_skipped(self):
        messages = ["not-a-dict", {"name": "AppSchemaAgent", "structured_output": {"manifest": {}, "pages": []}}]
        result = _latest_app_schema_output(messages)
        assert result is not None

    def test_message_without_structured_output_or_content_skipped(self):
        messages = [{"name": "AppSchemaAgent", "other_field": "data"}]
        assert _latest_app_schema_output(messages) is None
