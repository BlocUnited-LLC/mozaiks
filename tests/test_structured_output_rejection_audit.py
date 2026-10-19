"""Rejected structured outputs leave an audit record without leaking content."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from mozaiksai.core.audit.audit_logger import AuditEventKind, AuditLogger
from mozaiksai.core.workflow.outputs import rejection_audit
from mozaiksai.core.workflow.outputs.rejection_audit import audit_structured_output_rejection
from mozaiksai.core.workflow.outputs.runtime_validation import validate_agent_structured_output

SECRET = "sk-super-secret-value"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class _RecordingAudit:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def log_agent_output_rejected(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> _RecordingAudit:
    recorder = _RecordingAudit()
    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: recorder)
    return recorder


def _validate(reply: Any):
    validation = validate_agent_structured_output(
        agent_name="StrictAgent", reply=reply, structured_registry={"StrictAgent": _Strict}
    )
    assert validation is not None
    return validation


def _context(**overrides: Any) -> dict[str, Any]:
    return {
        "source": "runtime_turn",
        "workflow_name": "Wf",
        "chat_id": "chat-1",
        "app_id": "app-1",
        **overrides,
    }


def _quiet_logger() -> SimpleNamespace:
    return SimpleNamespace(warning=lambda *a, **k: None, debug=lambda *a, **k: None)


def test_schema_violation_describes_failure_without_input_values() -> None:
    validation = _validate({"status": 5, "leak": SECRET})

    assert validation.validation_passed is False
    assert validation.failure_category == "schema_violation"
    assert set(validation.failure_fields) == {"status:string_type", "leak:extra_forbidden"}
    assert validation.output_digest is not None and len(validation.output_digest) == 64
    # The pre-existing ``error`` text quotes input values; the audit fields must not.
    assert SECRET in (validation.error or "")
    assert SECRET not in repr(
        (validation.failure_category, validation.failure_fields, validation.output_digest)
    )


def test_non_object_output_is_categorised() -> None:
    validation = _validate("not json at all")

    assert validation.failure_category == "not_json_object"
    assert validation.output_digest is not None


def test_digest_is_stable_across_key_order_and_differs_by_content() -> None:
    first = _validate({"status": 1, "b": 2})
    reordered = _validate({"b": 2, "status": 1})
    other = _validate({"status": 1, "b": 3})

    assert first.output_digest == reordered.output_digest
    assert first.output_digest != other.output_digest


def test_accepted_output_has_no_failure_metadata() -> None:
    validation = _validate({"status": "ok"})

    assert validation.validation_passed is True
    assert validation.failure_category is None
    assert validation.failure_fields == ()
    assert validation.output_digest is None


async def test_rejection_is_audited_with_traceable_metadata(audit: _RecordingAudit) -> None:
    validation = _validate({"status": 5, "leak": SECRET})

    await audit_structured_output_rejection(validation, **_context(user_id="u-1"))

    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert call["actor_id"] == "u-1"
    assert call["workflow_name"] == "Wf"
    assert call["agent_name"] == "StrictAgent"
    assert call["chat_id"] == "chat-1"
    assert call["app_id"] == "app-1"
    assert call["output_digest"] == validation.output_digest
    assert call["error"] == "schema_violation"
    assert call["extra"]["model_name"] == "_Strict"
    assert call["extra"]["source"] == "runtime_turn"
    assert SECRET not in repr(call)


async def test_actor_falls_back_to_system(audit: _RecordingAudit) -> None:
    await audit_structured_output_rejection(_validate({}), **_context())

    assert audit.calls[0]["actor_id"] == "system"


async def test_accepted_output_is_not_audited(audit: _RecordingAudit) -> None:
    await audit_structured_output_rejection(_validate({"status": "ok"}), **_context())

    assert audit.calls == []


async def test_audit_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Broken:
        async def log_agent_output_rejected(self, **_kwargs: Any) -> None:
            raise RuntimeError("mongo down")

    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: _Broken())

    await audit_structured_output_rejection(_validate({}), **_context())


async def test_slow_audit_does_not_hold_up_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Slow:
        async def log_agent_output_rejected(self, **_kwargs: Any) -> None:
            await asyncio.sleep(30)

    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: _Slow())
    monkeypatch.setattr(rejection_audit, "_AUDIT_TIMEOUT_SECONDS", 0.01)

    await asyncio.wait_for(
        audit_structured_output_rejection(_validate({}), **_context()), timeout=2
    )


async def test_cancellation_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Cancelled:
        async def log_agent_output_rejected(self, **_kwargs: Any) -> None:
            raise asyncio.CancelledError

    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: _Cancelled())

    with pytest.raises(asyncio.CancelledError):
        await audit_structured_output_rejection(_validate({}), **_context())


async def test_audit_logger_writes_a_rejection_document() -> None:
    inserted: list[dict[str, Any]] = []

    class _Collection:
        async def insert_one(self, document: dict[str, Any]) -> None:
            inserted.append(document)

    logger = AuditLogger()
    logger._get_collection = lambda: _Collection()  # type: ignore[method-assign]

    await logger.log_agent_output_rejected(
        actor_id="system",
        app_id="app-1",
        chat_id="chat-1",
        workflow_name="Wf",
        agent_name="StrictAgent",
        output_digest="a" * 64,
        error="schema_violation",
        extra={"source": "task_batch"},
    )

    (document,) = inserted
    assert document["kind"] == AuditEventKind.AGENT_OUTPUT_REJECTED.value == "agent.output.rejected"
    assert document["resource"] == "Wf"
    assert document["action"] == "StrictAgent"
    assert document["inputs_hash"] == "a" * 64
    assert document["outcome"] == "fail"
    assert document["extra"] == {"source": "task_batch"}


async def test_runtime_turn_rejection_is_audited_and_still_fails_closed(
    audit: _RecordingAudit,
) -> None:
    from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output

    result = await emit_validated_agent_output(
        current_agent_name="StrictAgent",
        last_reply={"status": 5},
        workflow_name="Wf",
        chat_id="chat-1",
        app_id="app-1",
        user_id="u-1",
        turn_sequence=1,
        context_vars_dict={},
        context_bridge=None,
        structured_registry={"StrictAgent": _Strict},
        auto_tool_agents=set(),
        wf_logger=_quiet_logger(),
    )

    assert result is None
    assert [call["extra"]["source"] for call in audit.calls] == ["runtime_turn"]


async def test_runtime_turn_acceptance_is_not_audited(
    audit: _RecordingAudit, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output

    class _Dispatcher:
        async def emit(self, *_args: Any) -> None:
            return None

    monkeypatch.setattr(
        "mozaiksai.core.events.unified_event_dispatcher.get_event_dispatcher",
        lambda: _Dispatcher(),
    )
    result = await emit_validated_agent_output(
        current_agent_name="StrictAgent",
        last_reply={"status": "ok"},
        workflow_name="Wf",
        chat_id="chat-1",
        app_id="app-1",
        user_id=None,
        turn_sequence=1,
        context_vars_dict={},
        context_bridge=None,
        structured_registry={"StrictAgent": _Strict},
        auto_tool_agents=set(),
        wf_logger=_quiet_logger(),
    )

    assert result == {"status": "ok"}
    assert audit.calls == []


def test_network_wal_validation_collects_rejections_and_keeps_failing_closed() -> None:
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import _validate_wal_structured_outputs

    envelope = SimpleNamespace(
        event_type=EV_PACKET, sender_id="agent-1", event_data={"body": {"status": 5}}
    )
    rejections: list = []
    outputs, error = _validate_wal_structured_outputs(
        wal=[envelope],
        agent_name_by_id={"agent-1": "StrictAgent"},
        structured_registry={"StrictAgent": _Strict},
        rejections=rejections,
    )

    assert outputs == []
    assert error is not None and error.startswith("structured output validation failed")
    assert [r.failure_category for r in rejections] == ["schema_violation"]


def test_network_wal_validation_collects_nothing_for_valid_output() -> None:
    from ag2.network import EV_PACKET

    from mozaiksai.core.adapters.ag2_network_runner import _validate_wal_structured_outputs

    envelope = SimpleNamespace(
        event_type=EV_PACKET, sender_id="agent-1", event_data={"body": {"status": "ok"}}
    )
    rejections: list = []
    outputs, error = _validate_wal_structured_outputs(
        wal=[envelope],
        agent_name_by_id={"agent-1": "StrictAgent"},
        structured_registry={"StrictAgent": _Strict},
        rejections=rejections,
    )

    assert error is None and len(outputs) == 1
    assert rejections == []
