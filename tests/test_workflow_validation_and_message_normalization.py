"""
Workflow validation tool helpers and message normalization unit tests.

Covers:
  workflow.validation.tools:
    - _filter_payload:
        - drops runtime arg keys (context_variables, runtime, self)
        - keeps only model_fields keys when model has fields
        - passes unknown keys through when model has no model_fields
    - validate_tool_call:
        - no model registered returns is_valid=True with raw payload
        - valid payload returns is_valid=True with normalized_payload
        - invalid payload returns is_valid=False with error_payload
        - sentinel flag present on error_payload
        - agent_name and tool_name injected into error_payload
        - runtime keys stripped before validation

  workflow.messages.utils.normalize_to_strict_ag2:
    - None/empty input returns empty list
    - non-dict entries dropped
    - strict message (role+name+content) passes through unchanged
    - agent_name used when name missing
    - role=user with no name sets name to default_user_name
    - no role when name=user sets role to "user"
    - assistant without name is dropped
    - message with no content is dropped
    - custom default_user_name honored
    - multiple messages: valid ones kept, invalid dropped
    - deduplication/order preserved
"""
from __future__ import annotations

from unittest.mock import patch

from pydantic import BaseModel

from mozaiksai.core.workflow.messages.utils import normalize_to_strict_ag2
from mozaiksai.core.workflow.validation.tools import (
    RUNTIME_ARG_KEYS,
    SENTINEL_AGENT_KEY,
    SENTINEL_FLAG,
    SENTINEL_TOOL_KEY,
    ValidationOutcome,
    _filter_payload,
    validate_tool_call,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _StrictModel(BaseModel):
    name: str
    score: int


class _NoFieldsModel:
    """Simulate a model that lacks model_fields (non-Pydantic)."""
    pass


# ---------------------------------------------------------------------------
# 1. _filter_payload
# ---------------------------------------------------------------------------

class TestFilterPayload:
    def test_drops_context_variables(self):
        payload = {"name": "Alice", "context_variables": {"foo": "bar"}}
        result = _filter_payload(payload, _StrictModel)
        assert "context_variables" not in result

    def test_drops_runtime_key(self):
        payload = {"name": "Alice", "score": 1, "runtime": object()}
        result = _filter_payload(payload, _StrictModel)
        assert "runtime" not in result

    def test_drops_self_key(self):
        payload = {"name": "Alice", "score": 1, "self": object()}
        result = _filter_payload(payload, _StrictModel)
        assert "self" not in result

    def test_keeps_only_model_fields(self):
        payload = {"name": "Alice", "score": 42, "extra_field": "ignored"}
        result = _filter_payload(payload, _StrictModel)
        assert "extra_field" not in result
        assert result == {"name": "Alice", "score": 42}

    def test_preserves_model_field_values(self):
        payload = {"name": "Bob", "score": 100}
        result = _filter_payload(payload, _StrictModel)
        assert result["name"] == "Bob"
        assert result["score"] == 100

    def test_empty_payload_returns_empty(self):
        result = _filter_payload({}, _StrictModel)
        assert result == {}

    def test_all_runtime_keys_dropped(self):
        payload = {k: "x" for k in RUNTIME_ARG_KEYS}
        result = _filter_payload(payload, _StrictModel)
        assert result == {}

    def test_no_model_fields_passes_non_runtime_keys(self):
        # _NoFieldsModel has no model_fields attribute → only runtime keys dropped
        payload = {"anything": 1, "context_variables": "cv", "extra": 2}
        result = _filter_payload(payload, _NoFieldsModel)
        assert "anything" in result
        assert "extra" in result
        assert "context_variables" not in result


# ---------------------------------------------------------------------------
# 2. validate_tool_call
# ---------------------------------------------------------------------------

class TestValidateToolCall:
    def test_no_model_registered_returns_valid(self):
        with patch(
            "mozaiksai.core.workflow.validation.tools.get_structured_outputs_for_workflow",
            return_value={},
        ):
            outcome = validate_tool_call(
                workflow_name="MyWorkflow",
                agent_name="UnknownAgent",
                tool_name="my_tool",
                raw_payload={"any": "data"},
            )
        assert outcome.is_valid is True
        assert outcome.normalized_payload == {"any": "data"}
        assert outcome.error_payload is None

    def test_valid_payload_returns_valid_with_normalized(self):
        registry = {"MyAgent": _StrictModel}
        with patch(
            "mozaiksai.core.workflow.validation.tools.get_structured_outputs_for_workflow",
            return_value=registry,
        ):
            outcome = validate_tool_call(
                workflow_name="MyWorkflow",
                agent_name="MyAgent",
                tool_name="save_output",
                raw_payload={"name": "Alice", "score": 99},
            )
        assert outcome.is_valid is True
        assert outcome.normalized_payload == {"name": "Alice", "score": 99}
        assert outcome.error_payload is None

    def test_invalid_payload_returns_invalid(self):
        registry = {"MyAgent": _StrictModel}
        with patch(
            "mozaiksai.core.workflow.validation.tools.get_structured_outputs_for_workflow",
            return_value=registry,
        ):
            outcome = validate_tool_call(
                workflow_name="MyWorkflow",
                agent_name="MyAgent",
                tool_name="save_output",
                raw_payload={"name": "Alice"},  # missing required 'score'
            )
        assert outcome.is_valid is False
        assert outcome.error_payload is not None

    def test_error_payload_has_sentinel_flag(self):
        registry = {"MyAgent": _StrictModel}
        with patch(
            "mozaiksai.core.workflow.validation.tools.get_structured_outputs_for_workflow",
            return_value=registry,
        ):
            outcome = validate_tool_call(
                workflow_name="MyWorkflow",
                agent_name="MyAgent",
                tool_name="save_output",
                raw_payload={"name": "Alice"},
            )
        assert outcome.error_payload is not None
        assert outcome.error_payload.get(SENTINEL_FLAG) is True

    def test_error_payload_contains_agent_and_tool_name(self):
        registry = {"MyAgent": _StrictModel}
        with patch(
            "mozaiksai.core.workflow.validation.tools.get_structured_outputs_for_workflow",
            return_value=registry,
        ):
            outcome = validate_tool_call(
                workflow_name="MyWorkflow",
                agent_name="MyAgent",
                tool_name="my_tool_name",
                raw_payload={},
            )
        assert outcome.error_payload is not None
        assert outcome.error_payload.get(SENTINEL_AGENT_KEY) == "MyAgent"
        assert outcome.error_payload.get(SENTINEL_TOOL_KEY) == "my_tool_name"

    def test_runtime_keys_stripped_before_validation(self):
        # Adding context_variables should not cause validation to fail
        registry = {"MyAgent": _StrictModel}
        with patch(
            "mozaiksai.core.workflow.validation.tools.get_structured_outputs_for_workflow",
            return_value=registry,
        ):
            outcome = validate_tool_call(
                workflow_name="MyWorkflow",
                agent_name="MyAgent",
                tool_name="save_output",
                raw_payload={
                    "name": "Alice",
                    "score": 10,
                    "context_variables": {"session": "x"},
                    "runtime": object(),
                },
            )
        assert outcome.is_valid is True

    def test_validation_outcome_is_valid_field(self):
        outcome_ok = ValidationOutcome(is_valid=True, normalized_payload={})
        outcome_err = ValidationOutcome(is_valid=False, error_payload={"err": True})
        assert outcome_ok.is_valid is True
        assert outcome_err.is_valid is False


# ---------------------------------------------------------------------------
# 3. normalize_to_strict_ag2
# ---------------------------------------------------------------------------

class TestNormalizeToStrictAg2:
    def test_none_returns_empty(self):
        assert normalize_to_strict_ag2(None) == []

    def test_empty_list_returns_empty(self):
        assert normalize_to_strict_ag2([]) == []

    def test_non_dict_entry_dropped(self):
        result = normalize_to_strict_ag2(["not a dict", 42, None])
        assert result == []

    def test_strict_message_passes_through(self):
        msg = {"role": "user", "name": "user", "content": "hello"}
        result = normalize_to_strict_ag2([msg])
        assert result == [{"role": "user", "name": "user", "content": "hello"}]

    def test_assistant_strict_message_passes_through(self):
        msg = {"role": "assistant", "name": "PlannerAgent", "content": "plan"}
        result = normalize_to_strict_ag2([msg])
        assert result == [{"role": "assistant", "name": "PlannerAgent", "content": "plan"}]

    def test_agent_name_used_as_name_when_name_missing(self):
        msg = {
            "role": "assistant",
            "agent_name": "ResumeRouterAgent",
            "content": "[RESUME]",
        }
        result = normalize_to_strict_ag2([msg])
        assert result[0]["name"] == "ResumeRouterAgent"

    def test_role_user_without_name_gets_default(self):
        msg = {"role": "user", "content": "hello"}
        result = normalize_to_strict_ag2([msg])
        assert result[0]["name"] == "user"
        assert result[0]["role"] == "user"

    def test_custom_default_user_name_honored(self):
        msg = {"role": "user", "content": "hi"}
        result = normalize_to_strict_ag2([msg], default_user_name="customer")
        assert result[0]["name"] == "customer"

    def test_name_user_without_role_gets_user_role(self):
        msg = {"name": "user", "content": "hi"}
        result = normalize_to_strict_ag2([msg])
        assert result[0]["role"] == "user"

    def test_assistant_without_name_dropped(self):
        msg = {"role": "assistant", "content": "some response"}
        result = normalize_to_strict_ag2([msg])
        assert result == []

    def test_message_without_content_dropped(self):
        msg = {"role": "user", "name": "user"}
        result = normalize_to_strict_ag2([msg])
        assert result == []

    def test_mixed_valid_and_invalid(self):
        msgs = [
            {"role": "user", "name": "user", "content": "hi"},
            "not a dict",
            {"role": "assistant", "name": "Agent", "content": "resp"},
            {"role": "assistant"},  # no content, no name → dropped
        ]
        result = normalize_to_strict_ag2(msgs)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"

    def test_order_preserved(self):
        msgs = [
            {"role": "user", "name": "user", "content": "first"},
            {"role": "assistant", "name": "AgentA", "content": "second"},
            {"role": "assistant", "name": "AgentB", "content": "third"},
        ]
        result = normalize_to_strict_ag2(msgs)
        names = [m["name"] for m in result]
        assert names == ["user", "AgentA", "AgentB"]

    def test_output_contains_exactly_three_keys(self):
        msg = {"role": "user", "name": "user", "content": "hi", "extra": "noise"}
        result = normalize_to_strict_ag2([msg])
        assert set(result[0].keys()) == {"role", "name", "content"}

    def test_content_none_dropped(self):
        msg = {"role": "user", "name": "user", "content": None}
        result = normalize_to_strict_ag2([msg])
        assert result == []

    def test_content_empty_string_kept(self):
        # content="" is not None so it should pass
        msg = {"role": "user", "name": "user", "content": ""}
        result = normalize_to_strict_ag2([msg])
        assert len(result) == 1
        assert result[0]["content"] == ""
