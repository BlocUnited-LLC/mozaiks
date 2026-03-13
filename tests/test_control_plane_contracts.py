from __future__ import annotations

import pytest

from tests.import_utils import import_module_directly

_contracts = import_module_directly("mozaiksai.core.control_plane.contracts")
_classifier = import_module_directly("mozaiksai.core.orchestration.change_classifier")


def test_build_and_parse_plan_created_event_round_trip() -> None:
    event = _contracts.build_control_plane_event(
        kind=_contracts.ControlPlaneEventKind.PLAN_CREATED,
        payload={
            "app_id": "app-1",
            "workflow_name": "SystemPlanner",
            "plan_id": "plan-1",
            "canonical_version": 2,
            "summary": "Create the first execution batch",
            "initial_batch_ids": [" foundation_auth ", "", "feature_feed"],
        },
        chat_id="chat-1",
    )

    assert event.kind == "control.plan_created"
    assert event.chat_id == "chat-1"
    assert event.payload["initial_batch_ids"] == ["foundation_auth", "feature_feed"]

    parsed = _contracts.parse_control_plane_event(event)
    assert parsed.plan_id == "plan-1"
    assert parsed.canonical_version == 2


def test_transfer_requested_validates_nested_change_intent() -> None:
    intent = _classifier.ChangeIntent.from_change_type(
        _classifier.ChangeType.FOUNDATIONAL,
        rationale="major pivot",
        confidence=0.9,
    )

    payload = _contracts.validate_control_plane_payload(
        _contracts.ControlPlaneEventKind.TRANSFER_REQUESTED,
        {
            "app_id": "app-1",
            "from_workflow": "BuildApp",
            "target_workflow": "ValueEngine",
            "transfer_mode": "new_iteration",
            "change_intent": intent.model_dump(mode="json"),
            "rationale": "request reopens canonical product definition",
        },
    )

    assert payload.target_workflow == "ValueEngine"
    assert payload.change_intent.change_type == _classifier.ChangeType.FOUNDATIONAL
    assert payload.change_intent.requires_new_iteration is True


def test_infer_control_plane_state_from_events() -> None:
    assert (
        _contracts.infer_control_plane_state(_contracts.ControlPlaneEventKind.PLAN_CREATED)
        == _contracts.ControlPlaneState.APPROVAL_PENDING
    )
    assert (
        _contracts.infer_control_plane_state("control.prerequisites_required")
        == _contracts.ControlPlaneState.PREREQUISITES_PENDING
    )
    assert (
        _contracts.infer_control_plane_state("control.artifact_ready")
        == _contracts.ControlPlaneState.REVIEW
    )
    assert (
        _contracts.infer_control_plane_state("control.transfer_requested")
        == _contracts.ControlPlaneState.REROUTING
    )


def test_invalid_control_plane_payload_is_rejected() -> None:
    with pytest.raises(ValueError):
        _contracts.validate_control_plane_payload(
            _contracts.ControlPlaneEventKind.PREREQUISITES_REQUIRED,
            {
                "app_id": "app-1",
                "workflow_name": "BuildApp",
                "plan_id": "plan-1",
                "requirements": [
                    {
                        "requirement_id": "",
                        "key": "stripe_secret_key",
                        "label": "Stripe Secret Key",
                        "category": "payments",
                        "requirement_class": "required_now",
                    }
                ],
            },
        )
