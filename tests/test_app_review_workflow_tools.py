from __future__ import annotations

import importlib
from typing import Any

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
        "app_id": "app_1",
        "build_id": "build_1",
        "build_registry_id": "appreg_1",
        "build_family": "app_bundle",
        "build_key": "app_bundle",
        "build_record_id": "av_app_bundle_1",
        "bundle_path": "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app",
        "lifecycle_state": "review",
        "app_validation_status": "skipped",
        "app_validation_strategy_used": "skip",
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
    assert payload["build_family"] == "app_bundle"
    assert payload["build_key"] == "app_bundle"
    assert payload["build_record_id"] == "av_app_bundle_1"
    assert payload["build_registry_id"] == "appreg_1"
    assert payload["bundle_path"].endswith("/generated/apps/app_1/build_1/app")
    assert payload["promotion_blockers"] == []
    assert payload["revision_blockers"] == []


def test_review_summary_blocks_promotion_when_handoff_is_incomplete() -> None:
    payload = build_review_summary_payload(
        _review_context(
            build_registry_id=None,
            build_record_id=None,
            app_validation_status=None,
            integration_tests_passed=False,
            bundle_path=None,
        )
    )

    assert payload["review_ready"] is False
    assert payload["can_promote"] is False
    assert payload["can_revise"] is False
    assert payload["promotion_blockers"] == [
        "missing_build_registry_id",
        "missing_build_record_id",
        "missing_app_validation_status",
        "integration_tests_failed",
    ]
    assert payload["revision_blockers"] == ["missing_review_bundle_path"]


def test_refinement_payload_matches_studio_trigger_contract() -> None:
    payload = build_refinement_request_payload(_review_context(), "Add dark mode.")

    assert payload == {
        "raw_user_request": "Add dark mode.",
        "build_family": "app_bundle",
        "build_key": "app_bundle",
        "build_record_id": "av_app_bundle_1",
        "source_surface": "app_review",
        "extra": {
            "lifecycle_state": "review",
            "bundle_path": "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app",
            "build_registry_id": "appreg_1",
            "build_id": "build_1",
            "app_validation_status": "skipped",
            "app_validation_strategy_used": "skip",
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
    assert emitted["payload"]["build_record_id"] == "av_app_bundle_1"
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

    ctx = _review_context()
    result = await submit_module.submit_revision_request(
        revision_request="Add a dark mode toggle.",
        context_variables=ctx,
    )

    assert result["success"] is True
    assert result["action"] == "revise"
    assert result["event_emitted"] is True
    assert ctx.data["review_complete"] is True
    assert ctx.data["revision_submitted"] is True
    assert ctx.data["refinement_request"] == "Add a dark mode toggle."
    assert ctx.data["refinement_request_meta"]["build_record_id"] == "av_app_bundle_1"
    assert events == [
        (
            {
                "kind": "chat.revision_requested",
                "refinement_request": "Add a dark mode toggle.",
                "build_family": "app_bundle",
                "build_key": "app_bundle",
                "build_record_id": "av_app_bundle_1",
                "source_surface": "app_review",
                "extra": {
                    "lifecycle_state": "review",
                    "bundle_path": "C:/Repos/BlocUnitedRepo/mozaiks/generated/apps/app_1/build_1/app",
                    "build_registry_id": "appreg_1",
                    "build_id": "build_1",
                    "app_validation_status": "skipped",
                    "app_validation_strategy_used": "skip",
                    "integration_tests_passed": True,
                },
            },
            "chat_review_1",
        )
    ]


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
async def test_submit_revision_request_marks_promotion_complete() -> None:
    submit_module = importlib.import_module(
        "factory_app.workflows.AppReview.tools.submit_revision_request"
    )

    ctx = _review_context()
    result = await submit_module.submit_revision_request(action="promote", context_variables=ctx)

    assert result == {"success": True, "action": "promote", "revision_request": None}
    assert ctx.data["review_complete"] is True
    assert ctx.data["lifecycle_state"] == "active"

