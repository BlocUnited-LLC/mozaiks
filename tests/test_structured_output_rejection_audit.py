"""Rejected structured outputs leave an audit record without leaking content."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from types import SimpleNamespace
from typing import Any

import pytest
from ag2 import Agent
from pydantic import BaseModel, ConfigDict

from mozaiksai.core.adapters.ag2_network_runner import AG2NetworkRunner, AG2NetworkRunnerRequest
from mozaiksai.core.adapters.ag2_task_batch_runner import (
    AG2TaskBatchRunner,
    AG2TaskBatchRunnerRequest,
    AG2TaskBatchRunnerResult,
)
from mozaiksai.core.audit.audit_logger import AuditEventKind, AuditLogger
from mozaiksai.core.ports.orchestration import RunStatus
from mozaiksai.core.workflow.outputs import rejection_audit
from mozaiksai.core.workflow.outputs.rejection_audit import (
    audit_structured_output_rejection,
    packet_turn_key,
)
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


@pytest.fixture(autouse=True)
def _fresh_dedupe_state() -> None:
    rejection_audit._recorded_turns.clear()


@pytest.fixture
def audit_docs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run the real AuditLogger over an in-memory collection."""

    documents: list[dict[str, Any]] = []

    class _Collection:
        async def insert_one(self, document: dict[str, Any]) -> None:
            documents.append(document)

    logger = AuditLogger()
    logger._get_collection = lambda: _Collection()  # type: ignore[method-assign]
    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: logger)
    return documents


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
    assert set(validation.failure_fields) == {
        "status:string_type",
        "<redacted-field>:extra_forbidden",
    }
    assert validation.output_digest is not None and len(validation.output_digest) == 64
    # The pre-existing ``error`` text quotes input values; the audit fields must not.
    assert SECRET in (validation.error or "")
    assert SECRET not in repr(
        (validation.failure_category, validation.failure_fields, validation.output_digest)
    )


class _Item(BaseModel):
    name: str


class _Nested(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[_Item]
    payload: dict[str, int]


def test_declared_field_errors_keep_their_path() -> None:
    validation = validate_agent_structured_output(
        agent_name="N",
        reply={"items": [{"name": 1}], "payload": {}},
        structured_registry={"N": _Nested},
    )

    assert validation is not None
    assert validation.failure_fields == ("items.0.name:string_type",)


@pytest.mark.parametrize(
    "key",
    [
        "sk-live-secret-in-key",
        "line one" + chr(10) + "secret-after-newline",
        "ctrl" + chr(0) + chr(27) + "[31mred",
        "x" * 500,
    ],
)
def test_model_controlled_keys_never_reach_failure_fields(key: str) -> None:
    extra_key = validate_agent_structured_output(
        agent_name="N",
        reply={"items": [], "payload": {}, key: 1},
        structured_registry={"N": _Nested},
    )
    open_dict_key = validate_agent_structured_output(
        agent_name="N",
        reply={"items": [], "payload": {key: "not an int"}},
        structured_registry={"N": _Nested},
    )

    assert extra_key is not None and open_dict_key is not None
    assert extra_key.failure_fields == ("<redacted-field>:extra_forbidden",)
    assert open_dict_key.failure_fields == ("payload.<redacted-field>:int_parsing",)
    for validation in (extra_key, open_dict_key):
        assert key not in repr(validation.failure_fields)
        assert key.strip() not in repr(validation.failure_fields)


async def test_secret_key_is_absent_from_the_audit_call_and_serialized_document(
    audit: _RecordingAudit, audit_docs: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "sk-live-secret-in-key"
    validation = validate_agent_structured_output(
        agent_name="N",
        reply={"items": [], "payload": {key: "x"}, key: 1},
        structured_registry={"N": _Nested},
    )
    assert validation is not None

    await audit_structured_output_rejection(validation, **_context())

    assert len(audit_docs) == 1
    assert key not in json.dumps(audit_docs[0], default=str)
    # ``audit`` is registered after ``audit_docs``; repoint to prove the call args too.
    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: audit)
    rejection_audit._recorded_turns.clear()
    await audit_structured_output_rejection(validation, **_context())
    assert key not in repr(audit.calls)


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
    assert [(r.failure_category, channel, causation) for r, channel, causation in rejections] == [
        ("schema_violation", "", "")
    ]


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


# ---------------------------------------------------------------------------
# Initiating identity, exactly-once, and call-site coverage
# ---------------------------------------------------------------------------


class _PlannerOutput(BaseModel):
    plan_ready: bool


class _BadPlanner(Agent):
    """Replies with JSON that does not satisfy ``_PlannerOutput``."""

    def __init__(self) -> None:
        super().__init__("PlannerAgent", prompt="deterministic test agent")

    async def ask(self, *msg: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        return SimpleNamespace(body='{"plan_ready": "nope"}')


def _network_request(**overrides: Any) -> AG2NetworkRunnerRequest:
    payload: dict[str, Any] = {
        "workflow_name": "AuditSmoke",
        "chat_id": "chat-1",
        "app_id": "app-1",
        "agents": {"PlannerAgent": _BadPlanner()},
        "transition_rules": [
            {
                "source_agent": "PlannerAgent",
                "target_agent": "terminate",
                "transition_type": "after_turn",
            }
        ],
        "initial_agent_name": "PlannerAgent",
        "initial_message": "Plan.",
        "structured_registry": {"PlannerAgent": _PlannerOutput},
        "max_turns": 2,
        "idle_timeout_seconds": 10.0,
        "user_id": "u-net",
    }
    payload.update(overrides)
    return AG2NetworkRunnerRequest(**payload)


def _kinds_and_sources(documents: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(doc["kind"], doc["extra"]["source"]) for doc in documents]


async def test_network_wal_rejection_is_audited_once_for_the_initiating_user(
    audit_docs: list[dict[str, Any]],
) -> None:
    result = await AG2NetworkRunner().run(_network_request())

    assert result.status is RunStatus.FAILED
    assert result.error is not None and "structured output validation failed" in result.error
    assert _kinds_and_sources(audit_docs) == [("agent.output.rejected", "network_wal")]
    assert audit_docs[0]["actor_id"] == "u-net"
    assert audit_docs[0]["chat_id"] == "chat-1"
    assert audit_docs[0]["app_id"] == "app-1"
    assert audit_docs[0]["action"] == "PlannerAgent"


async def test_packet_and_wal_validation_of_one_reply_write_exactly_one_record(
    audit_docs: list[dict[str, Any]],
) -> None:
    """The packet hook audits and lets the packet through; the WAL check rejects it again."""

    from mozaiksai.core.workflow.outputs.runtime_events import emit_validated_agent_output

    packet_hook_calls: list[Any] = []

    async def packet_hook(agent_name: str, packet: Any) -> None:
        packet_hook_calls.append(packet)
        await emit_validated_agent_output(
            current_agent_name=agent_name,
            last_reply=(packet.event_data or {}).get("body"),
            workflow_name="AuditSmoke",
            chat_id="chat-1",
            app_id="app-1",
            user_id="u-net",
            turn_sequence=0,
            turn_idempotency_key=packet_turn_key(
                "chat-1", packet.channel_id, agent_name, packet.causation_id
            ),
            context_vars_dict={},
            context_bridge=None,
            structured_registry={"PlannerAgent": _PlannerOutput},
            auto_tool_agents=set(),
            wf_logger=_quiet_logger(),
        )

    result = await AG2NetworkRunner().run(_network_request(agent_output_handler=packet_hook))

    # Both validation sites saw the reply...
    assert len(packet_hook_calls) == 1
    assert result.status is RunStatus.FAILED
    assert result.error is not None and "structured output validation failed" in result.error
    # ...and the append-only log still holds a single record for it.
    assert _kinds_and_sources(audit_docs) == [("agent.output.rejected", "runtime_turn")]
    assert audit_docs[0]["actor_id"] == "u-net"


async def test_orchestration_packet_rejection_is_audited_once_for_the_initiating_user(
    audit_docs: list[dict[str, Any]],
) -> None:
    from mozaiksai.core.workflow.orchestration_patterns import _dispatch_agent_packet_output

    async def packet_hook(agent_name: str, packet: Any) -> None:
        await _dispatch_agent_packet_output(
            agent_name=agent_name,
            packet=packet,
            workflow_name="AuditSmoke",
            chat_id="chat-1",
            app_id="app-1",
            user_id="u-net",
            context_bridge=None,
            structured_registry={"PlannerAgent": _PlannerOutput},
            auto_tool_agents=set(),
            wf_logger=_quiet_logger(),
        )

    result = await AG2NetworkRunner().run(_network_request(agent_output_handler=packet_hook))

    assert result.status is RunStatus.FAILED
    assert len(audit_docs) == 1
    assert audit_docs[0]["kind"] == "agent.output.rejected"
    assert audit_docs[0]["extra"]["source"] == "runtime_turn"
    assert audit_docs[0]["actor_id"] == "u-net"


async def test_one_packet_is_recorded_once_but_a_retried_turn_is_recorded_again(
    audit: _RecordingAudit,
) -> None:
    validation = _validate({"status": 5})
    first = packet_turn_key("chat-1", "chan", "StrictAgent", "cause-1")
    retry = packet_turn_key("chat-1", "chan", "StrictAgent", "cause-2")

    await audit_structured_output_rejection(validation, **_context(turn_key=first))
    await audit_structured_output_rejection(validation, **_context(turn_key=first))
    assert len(audit.calls) == 1

    await audit_structured_output_rejection(validation, **_context(turn_key=retry))
    assert len(audit.calls) == 2


async def test_rejections_without_a_turn_key_are_never_deduplicated(
    audit: _RecordingAudit,
) -> None:
    validation = _validate({"status": 5})

    await audit_structured_output_rejection(validation, **_context())
    await audit_structured_output_rejection(validation, **_context())

    assert len(audit.calls) == 2


async def test_failed_audit_write_does_not_suppress_a_later_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: Counter[str] = Counter()

    class _Flaky:
        async def log_agent_output_rejected(self, **_kwargs: Any) -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("mongo down")

    monkeypatch.setattr(rejection_audit, "get_audit_logger", lambda: _Flaky())
    validation = _validate({"status": 5})
    key = packet_turn_key("chat-1", "chan", "StrictAgent", "cause-1")

    await audit_structured_output_rejection(validation, **_context(turn_key=key))
    await audit_structured_output_rejection(validation, **_context(turn_key=key))

    assert attempts["n"] == 2


async def test_task_batch_rejection_is_audited_for_the_initiating_user(
    audit_docs: list[dict[str, Any]],
) -> None:
    class _BadWorker(Agent):
        def __init__(self) -> None:
            super().__init__("WorkerAgent", prompt="deterministic test worker")

        async def ask(self, message: Any, **kwargs: Any) -> Any:  # type: ignore[override]
            return SimpleNamespace(body='{"ok": "not-a-bool"}')

    class _WorkerOutput(BaseModel):
        ok: bool

    result = await AG2TaskBatchRunner().run(
        AG2TaskBatchRunnerRequest(
            workflow_name="TaskBatchWorkflow",
            batch_id="reviews",
            task_id="review_a",
            chat_id="chat-1",
            app_id="app-1",
            agent_name="WorkerAgent",
            agent=_BadWorker(),
            prompt="Review.",
            context_variables={"current_task_id": "review_a"},
            structured_registry={"WorkerAgent": _WorkerOutput},
            timeout_seconds=5,
            user_id="u-task",
        )
    )

    assert result.status is RunStatus.FAILED
    assert result.close_reason == "structured_output_validation_failed"
    assert _kinds_and_sources(audit_docs) == [("agent.output.rejected", "task_batch")]
    assert audit_docs[0]["actor_id"] == "u-task"
    assert audit_docs[0]["resource"] == "TaskBatchWorkflow"
    assert audit_docs[0]["action"] == "WorkerAgent"


async def test_task_batch_execution_hands_the_initiating_user_to_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow import task_batches as tb
    from tests.test_task_batch_recovery import _config
    from tests.test_task_batch_recovery import _context as batch_context

    seen_users: list[str | None] = []

    async def run(self: Any, request: Any) -> AG2TaskBatchRunnerResult:
        seen_users.append(request.user_id)
        task = request.context_variables["current_task"]
        files = [{"filename": path, "content": "{}"} for path in task["owned_paths"]]
        return AG2TaskBatchRunnerResult(status=RunStatus.COMPLETED, output={"code_files": files})

    monkeypatch.setattr(tb.AG2TaskBatchRunner, "run", run)
    config = _config()
    config.batches[0].recovery = None

    async def checkpoint(_updates: dict[str, Any]) -> None:
        return None

    await tb.execute_task_batches_for_trigger(
        workflow_name="RecoveryTest",
        trigger_agent="Plan",
        batches_config=config,
        agents={name: object() for name in ("ModelAgent", "ServiceAgent", "PageAgent", "OtherAgent")},
        context_variables=batch_context(),
        fresh_agents_per_task=False,
        checkpoint=checkpoint,
        chat_id="chat",
        app_id="app",
        user_id="u-batch",
        parent_channel_id="test-parent-channel",
    )

    assert seen_users and set(seen_users) == {"u-batch"}


async def test_network_phase_hands_the_initiating_user_to_the_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mozaiksai.core.workflow import orchestration_patterns as op

    captured: list[Any] = []

    class _Runner:
        async def run(self, request: Any) -> str:
            captured.append(request)
            return "result"

    monkeypatch.setattr(op, "AG2NetworkRunner", _Runner)

    result = await op._run_ag2_network_phase(
        workflow_name="Wf",
        chat_id="chat-1",
        app_id="app-1",
        agents={},
        transition_rules=[],
        initial_agent_name="A",
        initial_message="hi",
        context_variables={},
        structured_registry={},
        max_turns=1,
        user_id="u-phase",
    )

    assert result == "result"
    assert captured[0].user_id == "u-phase"


async def test_audit_logger_records_trusted_scope_when_supplied() -> None:
    inserted: list[dict[str, Any]] = []

    class _Collection:
        async def insert_one(self, document: dict[str, Any]) -> None:
            inserted.append(document)

    logger = AuditLogger()
    logger._get_collection = lambda: _Collection()  # type: ignore[method-assign]

    await logger.log_agent_output_rejected(
        actor_id="u-1",
        app_id="app-1",
        chat_id="chat-1",
        workflow_name="Wf",
        agent_name="StrictAgent",
        output_digest=None,
        error="schema_violation",
        tenant_id="tenant-1",
        workspace_id="ws-1",
    )

    assert inserted[0]["tenant_id"] == "tenant-1"
    assert inserted[0]["workspace_id"] == "ws-1"


# ---------------------------------------------------------------------------
# Audit paths follow schema position; request positions stay stable
# ---------------------------------------------------------------------------

CARD = 4111111111111111


class _IntKeys(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payload: dict[int, int]


class _Open(BaseModel):
    items: list[int]
    payload: dict[str, int]


class _OptionalNested(BaseModel):
    item: _Item | None
    by_name: dict[str, _Item]


class _Tuples(BaseModel):
    pair: tuple[int, str]
    rest: tuple[int, ...]


class _Ambiguous(BaseModel):
    x: int | list[int]


@pytest.mark.parametrize(
    ("model", "reply", "expected"),
    [
        (_IntKeys, {"payload": {CARD: SECRET}}, ("payload.<redacted-field>:int_parsing",)),
        (_IntKeys, {"payload": {}, CARD: SECRET}, ("<redacted-field>:invalid_key",)),
        (
            _IntKeys,
            json.dumps({"payload": {str(CARD): SECRET}}),
            ("payload.<redacted-field>:int_parsing",),
        ),
        (_Open, {"items": [], "payload": {"items": SECRET}}, ("payload.<redacted-field>:int_parsing",)),
        (_Open, {"items": [], "payload": {"payload": SECRET}}, ("payload.<redacted-field>:int_parsing",)),
        (_OptionalNested, {"item": {"name": 5}, "by_name": {}}, ("item.name:string_type",)),
        (
            _OptionalNested,
            {"item": None, "by_name": {SECRET: {"name": 5}}},
            ("by_name.<redacted-field>.name:string_type",),
        ),
        (_Tuples, {"pair": [1, 2], "rest": [1]}, ("pair.1:string_type",)),
        (_Tuples, {"pair": [1, "a"], "rest": [1, "x"]}, ("rest.1:int_parsing",)),
        (
            _Ambiguous,
            {"x": [SECRET]},
            (
                "x.<redacted-field>:int_type",
                "x.<redacted-field>.<redacted-field>:int_parsing",
            ),
        ),
    ],
    ids=[
        "int-map-key",
        "int-extra-key",
        "json-numeric-key",
        "open-map-key-equals-sibling-field",
        "open-map-key-equals-own-field",
        "optional-nested-model",
        "model-valued-map",
        "tuple-fixed",
        "tuple-variadic",
        "ambiguous-union",
    ],
)
async def test_audit_paths_follow_schema_position_in_the_serialized_record(
    audit_docs: list[dict[str, Any]],
    model: type[BaseModel],
    reply: Any,
    expected: tuple[str, ...],
) -> None:
    validation = validate_agent_structured_output(
        agent_name="PositionAgent", reply=reply, structured_registry={"PositionAgent": model}
    )
    assert validation is not None and not validation.validation_passed

    await audit_structured_output_rejection(validation, **_context(user_id="trusted-user"))

    (document,) = audit_docs
    assert validation.failure_fields == expected
    assert tuple(document["extra"]["failure_fields"]) == expected
    serialized = json.dumps(document, default=str)
    assert SECRET not in serialized
    assert str(CARD) not in serialized
    assert document["actor_id"] == "trusted-user"
    assert document["inputs_hash"] == validation.output_digest


def test_network_request_keeps_existing_positional_fields() -> None:
    request = AG2NetworkRunnerRequest(
        "Flow", "chat", "app", {}, [], "Planner", "Start", {}, {}, 7, 8.0
    )

    assert request.max_turns == 7
    assert request.idle_timeout_seconds == 8.0
    assert request.user_id is None


def test_network_request_user_id_is_keyword_identity_not_context() -> None:
    request = AG2NetworkRunnerRequest(
        workflow_name="Flow",
        chat_id="chat",
        app_id="app",
        agents={},
        transition_rules=[],
        initial_agent_name="Planner",
        initial_message="Start",
        context_variables={"user_id": "untrusted-model-context"},
        user_id="trusted-user",
    )

    assert request.user_id == "trusted-user"
    assert request.max_turns is None


def test_user_id_is_the_last_field_of_both_runner_requests() -> None:
    import dataclasses

    for request_type in (AG2NetworkRunnerRequest, AG2TaskBatchRunnerRequest):
        assert dataclasses.fields(request_type)[-1].name == "user_id"
