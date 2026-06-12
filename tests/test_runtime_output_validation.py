"""
Runtime structured-output validation and chat resume helper unit tests.

Covers:
  runtime_validation.reply_body_to_data:
    - dict passthrough
    - list passthrough
    - Pydantic model → model_dump
    - JSON string decoded to dict
    - JSON string decoded to list
    - invalid JSON string returned as-is
    - object with .body dict attr uses body
    - object with .body BaseModel uses body.model_dump
    - empty string returned as empty string
    - non-string/dict/list scalar returned as-is

  runtime_validation.validate_agent_structured_output:
    - no model registered returns None
    - valid payload returns StructuredOutputValidation(validation_passed=True)
    - invalid payload returns validation_passed=False with error
    - non-dict raw_data returns validation_passed=False (not JSON object)
    - agent_name and model_name set correctly

  resume.merge_persisted_extra_context:
    - empty/None extra_ctx → no-op
    - non-str keys skipped
    - uses context.set() when available
    - uses context[key] = value when __setitem__ available
    - parent_chat_id sets automated_workflow_run=True
    - parent_chat_id absent → automated_workflow_run not set
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pydantic import BaseModel

from mozaiksai.core.workflow.execution.resume import merge_persisted_extra_context
from mozaiksai.core.workflow.outputs.runtime_validation import (
    reply_body_to_data,
    validate_agent_structured_output,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SampleModel(BaseModel):
    name: str
    value: int


class _ObjectWithBody:
    def __init__(self, body: Any):
        self.body = body


# ---------------------------------------------------------------------------
# 1. reply_body_to_data
# ---------------------------------------------------------------------------

class TestReplyBodyToData:
    def test_dict_passthrough(self):
        d = {"key": "val"}
        assert reply_body_to_data(d) is d

    def test_list_passthrough(self):
        lst = [1, 2, 3]
        assert reply_body_to_data(lst) is lst

    def test_pydantic_model_dumped(self):
        model = _SampleModel(name="Alice", value=42)
        result = reply_body_to_data(model)
        assert result == {"name": "Alice", "value": 42}

    def test_json_string_decoded_to_dict(self):
        result = reply_body_to_data('{"name": "Alice", "value": 42}')
        assert result == {"name": "Alice", "value": 42}

    def test_json_string_decoded_to_list(self):
        result = reply_body_to_data('[1, 2, 3]')
        assert result == [1, 2, 3]

    def test_invalid_json_returned_as_is(self):
        result = reply_body_to_data("not json{{")
        assert result == "not json{{"

    def test_object_with_body_dict_uses_body(self):
        obj = _ObjectWithBody({"key": "value"})
        result = reply_body_to_data(obj)
        assert result == {"key": "value"}

    def test_object_with_body_pydantic_uses_model_dump(self):
        model = _SampleModel(name="Bob", value=10)
        obj = _ObjectWithBody(model)
        result = reply_body_to_data(obj)
        assert result == {"name": "Bob", "value": 10}

    def test_empty_string_returned_empty(self):
        assert reply_body_to_data("") == ""

    def test_whitespace_only_string_returned(self):
        result = reply_body_to_data("   ")
        assert isinstance(result, str)

    def test_integer_passthrough(self):
        assert reply_body_to_data(42) == 42

    def test_none_passthrough(self):
        assert reply_body_to_data(None) is None


# ---------------------------------------------------------------------------
# 2. validate_agent_structured_output
# ---------------------------------------------------------------------------

class TestValidateAgentStructuredOutput:
    def test_no_model_returns_none(self):
        result = validate_agent_structured_output(
            agent_name="UnknownAgent",
            reply={"name": "Alice", "value": 1},
            structured_registry={},
        )
        assert result is None

    def test_valid_payload_returns_passed(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply={"name": "Alice", "value": 42},
            structured_registry=registry,
        )
        assert result is not None
        assert result.validation_passed is True
        assert result.structured_data == {"name": "Alice", "value": 42}

    def test_invalid_payload_returns_failed(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply={"name": "Alice"},  # missing 'value'
            structured_registry=registry,
        )
        assert result is not None
        assert result.validation_passed is False
        assert result.error is not None

    def test_non_dict_raw_data_returns_failed(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply="not a dict",
            structured_registry=registry,
        )
        assert result is not None
        assert result.validation_passed is False
        assert "not a JSON object" in (result.error or "")

    def test_agent_name_set_correctly(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply={"name": "Alice", "value": 1},
            structured_registry=registry,
        )
        assert result is not None
        assert result.agent_name == "MyAgent"

    def test_model_name_set_correctly(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply={"name": "Alice", "value": 1},
            structured_registry=registry,
        )
        assert result is not None
        assert result.model_name == "_SampleModel"

    def test_raw_data_preserved_on_failure(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply={"name": "Alice"},
            structured_registry=registry,
        )
        assert result is not None
        assert result.raw_data == {"name": "Alice"}

    def test_json_string_reply_decoded(self):
        registry = {"MyAgent": _SampleModel}
        import json
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply=json.dumps({"name": "Bob", "value": 5}),
            structured_registry=registry,
        )
        assert result is not None
        assert result.validation_passed is True


# ---------------------------------------------------------------------------
# 3. merge_persisted_extra_context
# ---------------------------------------------------------------------------

class TestMergePersistedExtraContext:
    def test_empty_extra_ctx_no_op(self):
        ctx = MagicMock()
        merge_persisted_extra_context(ctx, {})
        ctx.set.assert_not_called()

    def test_none_extra_ctx_no_op(self):
        ctx = MagicMock()
        merge_persisted_extra_context(ctx, None)  # type: ignore[arg-type]
        ctx.set.assert_not_called()

    def test_non_dict_extra_ctx_no_op(self):
        ctx = MagicMock()
        merge_persisted_extra_context(ctx, "not a dict")  # type: ignore[arg-type]
        ctx.set.assert_not_called()

    def test_uses_context_set_when_available(self):
        ctx = MagicMock()
        ctx.set = MagicMock()
        merge_persisted_extra_context(ctx, {"key": "value"})
        ctx.set.assert_any_call("key", "value")

    def test_uses_setitem_when_no_set(self):
        ctx: dict = {}
        merge_persisted_extra_context(ctx, {"key": "value"})
        assert ctx["key"] == "value"

    def test_skips_non_str_keys(self):
        ctx = MagicMock()
        ctx.set = MagicMock()
        merge_persisted_extra_context(ctx, {42: "value", "valid": "v"})
        # Only "valid" should be set, 42 is not a str
        call_keys = [call[0][0] for call in ctx.set.call_args_list]
        assert "valid" in call_keys
        assert 42 not in call_keys

    def test_skips_whitespace_only_keys(self):
        ctx = MagicMock()
        ctx.set = MagicMock()
        merge_persisted_extra_context(ctx, {"   ": "value"})
        ctx.set.assert_not_called()

    def test_parent_chat_id_sets_automated_workflow_run(self):
        ctx = MagicMock()
        ctx.set = MagicMock()
        merge_persisted_extra_context(ctx, {"parent_chat_id": "chat-123"})
        # automated_workflow_run should be set to True
        set_calls = {call[0][0]: call[0][1] for call in ctx.set.call_args_list}
        assert set_calls.get("automated_workflow_run") is True

    def test_no_parent_chat_id_no_automated_workflow_run(self):
        ctx = MagicMock()
        ctx.set = MagicMock()
        merge_persisted_extra_context(ctx, {"some_key": "value"})
        call_keys = [call[0][0] for call in ctx.set.call_args_list]
        assert "automated_workflow_run" not in call_keys
