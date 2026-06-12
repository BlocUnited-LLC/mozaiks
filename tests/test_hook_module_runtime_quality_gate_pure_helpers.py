"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_module_runtime_quality_gate.py

Covers:

  _parse_json_object:
    - dict input → returned directly
    - non-string non-dict → None
    - empty string → None
    - whitespace-only string → None
    - valid JSON object string → parsed dict returned
    - valid JSON non-object (list) → None
    - JSON in ```json fence → parsed
    - JSON in plain ``` fence → parsed
    - JSON embedded in surrounding text → extracted by { … }
    - invalid JSON → None
    - nested JSON objects parsed correctly
    - fence without closing backticks → falls back to brace extraction

  _latest_service_output:
    - empty list → None
    - None input → None
    - ServiceAgent message with python_files in structured_output → returned
    - ServiceAgent message with code_files in structured_output → returned
    - non-ServiceAgent message skipped
    - ServiceAgent message without relevant keys → skipped
    - messages scanned in reverse (most recent first)
    - JSON content string parsed
    - agent with name=None treated as non-ServiceAgent (skipped only if sender != 'ServiceAgent')
    - structured_output dict preferred over content string
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_module_runtime_quality_gate import (
    _latest_service_output,
    _parse_json_object,
)

# ---------------------------------------------------------------------------
# 1. _parse_json_object
# ---------------------------------------------------------------------------

class TestParseJsonObject:
    def test_dict_returned_directly(self):
        d = {"key": "value"}
        assert _parse_json_object(d) is d

    def test_non_string_non_dict_returns_none(self):
        assert _parse_json_object(42) is None
        assert _parse_json_object([1, 2]) is None
        assert _parse_json_object(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_json_object("") is None

    def test_whitespace_only_returns_none(self):
        assert _parse_json_object("   ") is None

    def test_valid_json_object_parsed(self):
        result = _parse_json_object('{"python_files": []}')
        assert isinstance(result, dict)
        assert result["python_files"] == []

    def test_valid_json_list_returns_none(self):
        result = _parse_json_object('[1, 2, 3]')
        assert result is None

    def test_json_in_json_fence(self):
        content = '```json\n{"code_files": ["a.py"]}\n```'
        result = _parse_json_object(content)
        assert isinstance(result, dict)
        assert "code_files" in result

    def test_json_in_plain_fence(self):
        content = '```\n{"python_files": ["b.py"]}\n```'
        result = _parse_json_object(content)
        assert isinstance(result, dict)
        assert "python_files" in result

    def test_json_embedded_in_text(self):
        content = 'Here is the output:\n{"python_files": ["c.py"]}\nEnd.'
        result = _parse_json_object(content)
        assert isinstance(result, dict)
        assert "python_files" in result

    def test_invalid_json_returns_none(self):
        assert _parse_json_object('{"unclosed": "bracket"') is None

    def test_nested_json_parsed(self):
        content = '{"outer": {"inner": "value"}}'
        result = _parse_json_object(content)
        assert result is not None
        assert result["outer"]["inner"] == "value"

    def test_no_braces_returns_none(self):
        assert _parse_json_object("no braces here") is None

    def test_empty_json_object(self):
        result = _parse_json_object("{}")
        assert result == {}


# ---------------------------------------------------------------------------
# 2. _latest_service_output
# ---------------------------------------------------------------------------

def _make_msg(name: str, structured: dict | None = None, content: str | None = None) -> dict:
    msg: dict = {"name": name}
    if structured is not None:
        msg["structured_output"] = structured
    if content is not None:
        msg["content"] = content
    return msg


class TestLatestServiceOutput:
    def test_empty_list_returns_none(self):
        assert _latest_service_output([]) is None

    def test_none_input_returns_none(self):
        assert _latest_service_output(None) is None

    def test_service_agent_with_python_files(self):
        msg = _make_msg("ServiceAgent", structured={"python_files": ["service.py"]})
        result = _latest_service_output([msg])
        assert result is not None
        assert "python_files" in result

    def test_service_agent_with_code_files(self):
        msg = _make_msg("ServiceAgent", structured={"code_files": ["handler.py"]})
        result = _latest_service_output([msg])
        assert result is not None
        assert "code_files" in result

    def test_non_service_agent_skipped(self):
        msg = _make_msg("ModelAgent", structured={"python_files": ["model.py"]})
        assert _latest_service_output([msg]) is None

    def test_service_agent_without_relevant_keys_skipped(self):
        msg = _make_msg("ServiceAgent", structured={"other_key": "value"})
        assert _latest_service_output([msg]) is None

    def test_most_recent_message_returned(self):
        older = _make_msg("ServiceAgent", structured={"python_files": ["old.py"]})
        newer = _make_msg("ServiceAgent", structured={"python_files": ["new.py"]})
        result = _latest_service_output([older, newer])
        assert result is not None
        assert result["python_files"] == ["new.py"]

    def test_json_content_string_parsed(self):
        msg = _make_msg("ServiceAgent", content='{"python_files": ["svc.py"]}')
        result = _latest_service_output([msg])
        assert result is not None
        assert "python_files" in result

    def test_structured_output_preferred_over_content(self):
        msg = {
            "name": "ServiceAgent",
            "structured_output": {"python_files": ["from_structured.py"]},
            "content": '{"code_files": ["from_content.py"]}',
        }
        result = _latest_service_output([msg])
        assert result is not None
        assert "python_files" in result  # structured_output wins

    def test_non_dict_message_skipped(self):
        messages = ["not-a-dict", _make_msg("ServiceAgent", structured={"python_files": []})]
        result = _latest_service_output(messages)
        assert result is not None

    def test_message_with_sender_key(self):
        msg = {"sender": "ServiceAgent", "structured_output": {"code_files": ["a.py"]}}
        result = _latest_service_output([msg])
        assert result is not None
        assert "code_files" in result

    def test_mixed_agents_only_service_agent_returned(self):
        messages = [
            _make_msg("AppPlanAgent", structured={"python_files": ["plan.py"]}),
            _make_msg("ServiceAgent", structured={"python_files": ["service.py"]}),
            _make_msg("ModelAgent", structured={"python_files": ["model.py"]}),
        ]
        result = _latest_service_output(messages)
        assert result is not None
        assert result["python_files"] == ["service.py"]
