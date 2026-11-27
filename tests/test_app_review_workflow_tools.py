from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows.AppReview.tools.review_context import (
    build_refinement_request_payload,
    build_review_summary_payload,
)


class _Context:
    def __init__(self, **values: Any) -> None:
        self.data = dict(values)

    def get(self, key: str) -> Any:
        return self.data.get(key)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def _review_context(**overrides: Any) -> _Context:
    values = {
        "chat_id": "chat_review_1",
        "app_id": "factory",
        "user_id": "owner",
        "run_build_binding": {
            "build_id": "build_1", "build_registry_id": "appreg_1",
            "target_app_id": "app_1", "phase": "genesis",
        },
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "artifact_version_id": "av_app_bundle_1",
        "bundle_path": "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app",
        "lifecycle_state": "review",
        "app_validation_status": "passed",
        "app_validation_strategy_used": "docker",
        "app_bundle_acceptance_status": "passed",
        "integration_tests_passed": True,
    }
    values.update(overrides)
    return _Context(**values)


def test_review_summary_payload_preserves_appgenerator_handoff_metadata() -> None:
    payload = build_review_summary_payload(_review_context())

    assert payload["review_ready"] is True
    assert payload["can_promote"] is True
    assert payload["can_revise"] is True
    assert payload["artifact_kind"] == "app_bundle"
    assert payload["artifact_key"] == "app_bundle"
    assert payload["artifact_version_id"] == "av_app_bundle_1"
    assert payload["build_registry_id"] == "appreg_1"
    assert payload["bundle_path"].endswith("/generated/apps/app_1/build_1/app")
    assert payload["promotion_blockers"] == []
    assert payload["revision_blockers"] == []


def test_review_summary_blocks_promotion_when_handoff_is_incomplete() -> None:
    payload = build_review_summary_payload(
        _review_context(
            artifact_version_id=None,
            app_validation_status=None,
            integration_tests_passed=False,
            bundle_path=None,
        )
    )

    assert payload["review_ready"] is False
    assert payload["can_promote"] is False
    assert payload["can_revise"] is False
    assert payload["promotion_blockers"] == [
        "missing_artifact_version_id",
        "missing_app_validation_status",
        "integration_tests_failed",
    ]
    assert payload["revision_blockers"] == ["missing_review_bundle_path"]


@pytest.mark.parametrize("status", ["skipped", "pending", "unknown"])
def test_review_summary_requires_completed_build_validation(status: str) -> None:
    payload = build_review_summary_payload(_review_context(app_validation_status=status))
    assert payload["can_promote"] is False
    assert payload["review_ready"] is False
    assert payload["can_revise"] is True
    assert "app_validation_not_passed" in payload["promotion_blockers"]


def test_refinement_payload_matches_studio_trigger_contract() -> None:
    payload = build_refinement_request_payload(_review_context(), "Add dark mode.")

    assert payload == {
        "raw_user_request": "Add dark mode.",
        "artifact_kind": "app_bundle",
        "artifact_key": "app_bundle",
        "artifact_version_id": "av_app_bundle_1",
        "source_surface": "app_review",
        "extra": {
            "lifecycle_state": "review",
            "bundle_path": "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app",
            "build_registry_id": "appreg_1",
            "build_id": "build_1",
            "app_validation_status": "passed",
            "app_validation_strategy_used": "docker",
            "integration_tests_passed": True,
        },
    }


@pytest.mark.asyncio
async def test_present_review_summary_emits_canonical_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    present_module = importlib.import_module(
        "factory_app.workflows.AppReview.tools.present_review_summary"
    )
    emitted: dict[str, Any] = {}

    async def _fake_emit_ui_surface(tool_id: str, payload: dict[str, Any], **kwargs: Any) -> None:
        emitted["tool_id"] = tool_id
        emitted["payload"] = payload
        emitted["kwargs"] = kwargs

    monkeypatch.setattr(present_module, "emit_ui_surface", _fake_emit_ui_surface)

    result = await present_module.present_review_summary(_review_context())

    assert result == {"presented": True}
    assert emitted["tool_id"] == "present_review_summary"
    assert emitted["payload"]["artifact_version_id"] == "av_app_bundle_1"
    assert emitted["payload"]["review_ready"] is True
    assert emitted["kwargs"]["chat_id"] == "chat_review_1"
    assert emitted["kwargs"]["workflow_name"] == "AppReview"
    assert emitted["kwargs"]["agent_name"] == "ReviewAgent"


@pytest.mark.asyncio
async def test_submit_revision_request_records_and_emits_refinement_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submit_module = importlib.import_module(
        "factory_app.workflows.AppReview.tools.submit_revision_request"
    )
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    events: list[tuple[dict[str, Any], str | None]] = []

    class _Transport:
        async def send_event_to_ui(self, event: dict[str, Any], chat_id: str | None = None) -> None:
            events.append((event, chat_id))

    async def _get_instance(cls) -> _Transport:  # noqa: ANN001
        return _Transport()

    monkeypatch.setattr(SimpleTransport, "get_instance", classmethod(_get_instance))
    owner_input = AsyncMock(return_value="Add a dark mode toggle.")
    monkeypatch.setattr(submit_module.AG2PersistenceManager, "latest_workflow_user_input", owner_input)

    ctx = _review_context()
    result = await submit_module.submit_revision_request(
        revision_request="Add a dark mode toggle.",
        context_variables=ctx,
    )

    assert result["success"] is True
    assert result["action"] == "revise"
    assert result["event_emitted"] is True
    owner_input.assert_awaited_once_with(chat_id="chat_review_1", app_id="factory", user_id="owner")
    assert (await submit_module.submit_revision_request("Add a dark mode toggle.", context_variables=ctx))["status"] == "already_submitted"
    assert ctx.data["review_complete"] is True
    assert ctx.data["revision_submitted"] is True
    assert ctx.data["refinement_request"] == "Add a dark mode toggle."
    assert ctx.data["refinement_request_meta"]["artifact_version_id"] == "av_app_bundle_1"
    assert events == [
        (
            {
                "kind": "chat.revision_requested",
                "refinement_request": "Add a dark mode toggle.",
                "artifact_kind": "app_bundle",
                "artifact_key": "app_bundle",
                "artifact_version_id": "av_app_bundle_1",
                "source_surface": "app_review",
                "extra": {
                    "lifecycle_state": "review",
                    "bundle_path": "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app",
                    "build_registry_id": "appreg_1",
                    "build_id": "build_1",
                    "app_validation_status": "passed",
                    "app_validation_strategy_used": "docker",
                    "integration_tests_passed": True,
                },
            },
            "chat_review_1",
        )
    ]


@pytest.mark.asyncio
async def test_concurrent_submissions_emit_one_revision(monkeypatch) -> None:
    submit_module = importlib.import_module("factory_app.workflows.AppReview.tools.submit_revision_request")
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    text = "Use teal for the timer."
    both_reading = asyncio.Barrier(2)

    async def owner_input(self, **scope):
        await both_reading.wait()
        return text

    transport = AsyncMock()
    monkeypatch.setattr(submit_module.AG2PersistenceManager, "latest_workflow_user_input", owner_input)
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    context = _review_context()
    results = await asyncio.gather(*(
        submit_module.submit_revision_request(text, context_variables=context) for _ in range(2)
    ))
    assert sorted(result.get("status", result.get("action")) for result in results) == ["already_submitted", "revise"]
    transport.send_event_to_ui.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_revision_request_rejects_empty_revision() -> None:
    submit_module = importlib.import_module(
        "factory_app.workflows.AppReview.tools.submit_revision_request"
    )

    ctx = _review_context()
    with pytest.raises(ValueError, match="revision_request is required"):
        await submit_module.submit_revision_request(revision_request=" ", context_variables=ctx)

    assert "review_complete" not in ctx.data


@pytest.mark.asyncio
@pytest.mark.parametrize(("offered", "typed"), [(".", None), ("Please redesign it.", None), ("Redesign it.", "Rename the save button.")])
async def test_review_requires_the_owners_exact_chat_input(monkeypatch, offered, typed):
    module = importlib.import_module("factory_app.workflows.AppReview.tools.submit_revision_request")
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    monkeypatch.setattr(module.AG2PersistenceManager, "latest_workflow_user_input", AsyncMock(return_value=typed))
    transport = AsyncMock()
    monkeypatch.setattr(SimpleTransport, "get_instance", transport)
    ctx = _review_context()
    result = await module.submit_revision_request(offered, context_variables=ctx)
    assert result == {"success": False, "status": "awaiting_owner_request"}
    transport.assert_not_awaited()
    assert "revision_submitted" not in ctx.data and "review_complete" not in ctx.data


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", [None, "", "   "])
async def test_revision_without_a_chat_cannot_complete_or_broadcast(chat_id, monkeypatch):
    module = importlib.import_module("factory_app.workflows.AppReview.tools.submit_revision_request")
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    get_transport = AsyncMock()
    monkeypatch.setattr(SimpleTransport, "get_instance", get_transport)
    context = _review_context(chat_id=chat_id)
    with pytest.raises(ValueError, match="review chat session"):
        await module.submit_revision_request("Make the timer teal.", context_variables=context)
    get_transport.assert_not_awaited()
    assert "review_complete" not in context.data
    assert "revision_submitted" not in context.data


@pytest.mark.asyncio
async def test_revision_reaches_only_its_websocket_through_real_transport(monkeypatch):
    module = importlib.import_module("factory_app.workflows.AppReview.tools.submit_revision_request")
    from mozaiksai.core.transport.simple_transport import SimpleTransport

    class Socket:
        def __init__(self):
            self.messages = []

        async def send_json(self, message):
            self.messages.append(message)

    source, other = Socket(), Socket()
    transport = SimpleTransport()
    transport.connections = {
        "chat_review_1": {"websocket": source, "workflow_name": "AppReview"},
        "other_chat": {"websocket": other, "workflow_name": "AppReview"},
    }
    monkeypatch.setattr(SimpleTransport, "get_instance", AsyncMock(return_value=transport))
    monkeypatch.setattr(module.AG2PersistenceManager, "latest_workflow_user_input", AsyncMock(return_value="Make the timer teal."))
    result = await module.submit_revision_request("Make the timer teal.", context_variables=_review_context())
    assert result["event_emitted"] is True
    assert len(source.messages) == 1
    assert not other.messages
    envelope = source.messages[0]
    assert envelope["schema_version"] == "mozaiks.ui.event.v1"
    assert envelope["type"] == "chat.revision_requested"
    assert envelope["data"]["refinement_request"] == "Make the timer teal."
    assert envelope["data"]["artifact_version_id"] == "av_app_bundle_1"
    assert envelope["data"]["extra"]["build_registry_id"] == "appreg_1"


@pytest.mark.asyncio
async def test_submit_revision_request_marks_promotion_complete(monkeypatch) -> None:
    submit_module = importlib.import_module(
        "factory_app.workflows.AppReview.tools.submit_revision_request"
    )

    ctx = _review_context()
    service_module = importlib.import_module("factory_app.app.modules.app_registry.backend.service")
    monkeypatch.setattr(service_module, "AppRegistryService", lambda: SimpleNamespace(
        get_app_record=AsyncMock(return_value={"app": {
            "app_id": "app_1", "chat_app_id": "factory", "lifecycle_state": "active",
            "current_build_run": {"build_id": "build_1"},
        }}),
    ))
    result = await submit_module.submit_revision_request(action="promote", context_variables=ctx)

    assert result == {"success": True, "action": "promote", "revision_request": None}
    assert ctx.data["review_complete"] is True
    assert ctx.data["lifecycle_state"] == "active"


@pytest.mark.asyncio
async def test_agent_cannot_claim_an_unconfirmed_promotion(monkeypatch):
    module = importlib.import_module("factory_app.workflows.AppReview.tools.submit_revision_request")
    service_module = importlib.import_module("factory_app.app.modules.app_registry.backend.service")
    monkeypatch.setattr(service_module, "AppRegistryService", lambda: SimpleNamespace(
        get_app_record=AsyncMock(return_value={"app": None}),
    ))
    context = _review_context()
    with pytest.raises(ValueError, match="not been confirmed"):
        await module.submit_revision_request(action="promote", context_variables=context)
    assert "review_complete" not in context.data
