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
    - canonical loader context and resolved replay policy are required
    - valid persisted values hydrate atomically
    - stale/non-persisted keys are skipped without exposing values
    - malformed known values and unresolved policies fail closed
    - persisted replay authority is not retained for live writes
"""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.authority import (
    RUNTIME_SYSTEM_WRITER,
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.execution.run_bootstrap import merge_persisted_extra_context
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


def _replay_policy():
    return build_context_authority_policy(
        workflow_name="ReplayFlow",
        definitions={
            "app_id": {
                "type": "string",
                "source": {"type": "state", "default": "app-canonical"},
            },
            "key": {
                "type": "string",
                "source": {"type": "state", "default": "default"},
            },
            "valid": {
                "type": "string",
                "source": {"type": "state", "default": "default"},
            },
            "review_complete": {
                "type": "boolean",
                "source": {"type": "state", "default": False},
            },
        },
        transition_rules=[],
    )


def _replay_context(
    initial: dict[str, Any] | None = None,
    *,
    policy=None,
    bind_session: bool = False,
):
    return create_context_container(
        initial=initial,
        authority_policy=policy or _replay_policy(),
        chat_id="chat-1" if bind_session else None,
        app_id="app-1" if bind_session else None,
    )


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

    def test_fenced_json_string_reply_decoded(self):
        registry = {"MyAgent": _SampleModel}
        result = validate_agent_structured_output(
            agent_name="MyAgent",
            reply='```json\n{"name": "Bob", "value": 5}\n```',
            structured_registry=registry,
        )
        assert result is not None
        assert result.validation_passed is True
        assert result.structured_data == {"name": "Bob", "value": 5}


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

    def test_hydrates_valid_state_into_canonical_loader_context(self):
        ctx = _replay_context({"key": "default"})

        merge_persisted_extra_context(ctx, {"key": "value"})

        assert ctx.snapshot() == {"key": "value"}
        assert ctx._mozaiks_context_writer_id == RUNTIME_SYSTEM_WRITER

    def test_rejects_noncanonical_context_for_nonempty_replay(self):
        ctx: dict[str, Any] = {}

        with pytest.raises(TypeError, match="canonical runtime context container"):
            merge_persisted_extra_context(ctx, {"key": "value"})

        assert ctx == {}

    def test_requires_bound_replay_policy(self):
        ctx = create_context_container(initial={"key": "default"})

        with pytest.raises(ContextAuthorityError, match="replay_policy_unavailable"):
            merge_persisted_extra_context(ctx, {"key": "value"})

        assert ctx.snapshot() == {"key": "default"}

    def test_skips_non_str_keys(self):
        ctx = _replay_context({"valid": "default"})

        merge_persisted_extra_context(ctx, {42: "value", "valid": "v"})  # type: ignore[dict-item]

        assert ctx.snapshot() == {"valid": "v"}

    def test_skips_whitespace_only_keys(self):
        ctx = _replay_context({"valid": "default"})

        merge_persisted_extra_context(ctx, {"   ": "value", "valid": "v"})

        assert ctx.snapshot() == {"valid": "v"}

    def test_non_persisted_authority_identifier_is_not_restored(self):
        ctx = _replay_context({"app_id": "app-canonical", "key": "default"})

        merge_persisted_extra_context(
            ctx,
            {"app_id": "app-attacker", "key": "restored"},
        )

        assert ctx.snapshot() == {"app_id": "app-canonical", "key": "restored"}

    def test_stale_key_is_dropped_without_losing_valid_sibling_or_logging_value(self, caplog):
        ctx = _replay_context({"key": "default"})

        with caplog.at_level(logging.DEBUG, logger="mozaiksai.core.workflow.context.adapter"):
            merge_persisted_extra_context(
                ctx,
                {"stale_historical_key": "sensitive-old-value", "key": "restored"},
            )

        assert ctx.snapshot() == {"key": "restored"}
        messages = [record.getMessage() for record in caplog.records]
        assert any("workflow=ReplayFlow key=stale_historical_key" in message for message in messages)
        assert all("sensitive-old-value" not in message for message in messages)

    def test_known_malformed_value_fails_without_partial_hydration(self):
        ctx = _replay_context({"key": "default", "review_complete": False})

        with pytest.raises(ContextAuthorityError, match="invalid_value"):
            merge_persisted_extra_context(
                ctx,
                {"key": "would-be-partial", "review_complete": "true"},
            )

        assert ctx.snapshot() == {"key": "default", "review_complete": False}

    def test_session_bound_loader_set_does_not_start_background_persistence(self, monkeypatch):
        import asyncio

        create_task = MagicMock()
        monkeypatch.setattr(asyncio, "create_task", create_task)
        ctx = _replay_context({"key": "default"}, bind_session=True)

        ctx.set("key", "live-update")

        assert ctx.snapshot() == {"key": "live-update"}
        create_task.assert_not_called()
