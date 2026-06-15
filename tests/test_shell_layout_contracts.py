from __future__ import annotations

import json
from pathlib import Path

import pytest


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_header_has_single_nav_source_of_truth() -> None:
    source = _read("chat-ui/src/components/layout/Header.js")
    assert "shell-mobile-label" not in source
    assert "activePage?.label" not in source


def test_shell_chrome_main_column_preserves_footer() -> None:
    source = _read("chat-ui/src/components/RouteRenderer.jsx")
    assert '<main className="flex min-h-0 flex-1 flex-col">{children}</main>' in source


def test_transition_screens_use_shell_safe_height() -> None:
    launcher = _read("chat-ui/src/ui/screens/LauncherScreen.jsx")
    confirm = _read("chat-ui/src/ui/screens/ConfirmScreen.jsx")
    transition = _read("chat-ui/src/ui/screens/TransitionScreen.jsx")
    primitives = _read("chat-ui/src/platform/transitionPrimitives.jsx")

    # No screen may use min-h-screen (breaks inside overlay frames)
    assert "min-h-screen" not in launcher
    assert "min-h-screen" not in confirm
    assert "min-h-screen" not in transition
    # Shell-safe height lives in the shared primitives — screens delegate layout there
    assert "min-h-full flex-1" in primitives


def test_platform_transitions_stay_declarative() -> None:
    registry = json.loads(
        _read("factory_app/workflows/extended_orchestration/extension_registry.json")
    )
    assert any(t.get("transition_type") == "user_choice_context" for t in registry["transitions"])
    for transition in registry["transitions"]:
        assert "component" not in transition
        assert "config" not in transition
        assert "description" not in transition
        assert "context" not in transition
        assert "actions" not in transition
        assert transition["transition_type"] in {
            "user_choice",
            "user_choice_context",
            "user_choice_route",
            "confirm",
            "condition",
            "silent",
            "progress_view",
            "prerequisite_redirect",
            "chat_session",
            "workflow_complete",
        }
        if transition["transition_type"] in {"user_choice", "user_choice_context", "user_choice_route", "confirm"}:
            assert transition.get("ui", {}).get("component")
        if transition["transition_type"] in {"user_choice", "user_choice_context", "user_choice_route"}:
            assert "route_to" not in transition
        for option in transition.get("options", []):
            assert "label" not in option
            assert "description" not in option
            assert "context" not in option
            assert option.get("route_to")
            if "context_variables" in option:
                assert isinstance(option["context_variables"], dict)
    assert (_workspace() / "factory_app" / "workflows" / "extended_orchestration" / "ui").exists()
    # App workspace build context should not have a UI bundle (shared factory owns it)
    from tests.import_utils import active_app_root
    app_root = active_app_root()
    if app_root.resolve() == (_workspace() / "factory_app" / "app").resolve():
        pytest.skip("The repo-local factory bundle intentionally owns the shared transition UI bundle.")
    workflows_root = app_root.parent / "workflows" if app_root.name == "app" else app_root / "workflows"
    assert not (workflows_root / "extended_orchestration" / "ui" / "index.js").exists()


def test_build_satisfaction_transition_is_optional_and_registered() -> None:
    registry = json.loads(
        _read("factory_app/workflows/extended_orchestration/extension_registry.json")
    )
    transitions = {transition["id"]: transition for transition in registry["transitions"]}
    transition = transitions["build_satisfaction_rating"]

    assert transition["optional"] is True
    assert transition["transition_type"] == "user_choice_context"
    assert transition["ui"]["component"] == "BuildSatisfactionRating"
    assert {option["id"] for option in transition["options"]} == {"rated", "skip"}
    assert all(option["route_to"] == "workflow_complete" for option in transition["options"])

    build_sequence = next(sequence for sequence in registry["workflow_sequences"] if sequence["id"] == "build")
    assert build_sequence["steps"][-1] == {"transition": "build_satisfaction_rating"}

    transition_exports = _read("factory_app/workflows/extended_orchestration/ui/index.js")
    transition_component = _read(
        "factory_app/workflows/extended_orchestration/ui/transitions/BuildSatisfactionRating.js"
    )
    assert "BuildSatisfactionRating" in transition_exports
    assert "/api/modules/build_intelligence/record_build_satisfaction" in transition_component
    assert "onResolve('rated', { satisfaction_rating: selected })" in transition_component
    assert "onResolve('skip', {})" in transition_component


def test_app_review_revision_event_preserves_refinement_provenance() -> None:
    source = _read("chat-ui/src/pages/ChatPage.js")

    assert "const artifactKey = detail.artifact_key || artifactKind;" in source
    assert "const artifactVersionId = detail.artifact_version_id || null;" in source
    assert "const sourceSurface = detail.source_surface || 'app_review';" in source
    assert "const triggerPayload = {" in source
    assert "refinementRequest.artifact_version_id = artifactVersionId;" in source
    assert "refinementRequest.extra = requestExtra;" in source
    assert "trigger_payload: triggerPayload" in source


def test_app_review_revision_harness_decision_uses_existing_pending_decision_ui() -> None:
    source = _read("chat-ui/src/pages/ChatPage.js")

    assert "triggerData.execution_mode === 'harness_decision'" in source
    assert "buildPendingHarnessDecision(" in source
    assert "triggerData.harness_decision" in source
    assert "trigger_payload: triggerPayload" in source
    assert "setPendingHarnessDecision(nextDecision)" in source
    assert "setPendingHarnessDecisionError(" in source


def test_app_review_summary_promotes_reviewed_artifact_version() -> None:
    source = _read("factory_app/workflows/AppReview/ui/AppReview/AppReviewSummary.jsx")

    assert "/api/modules/app_registry/promote_build" not in source
    assert "No artifact version available. Cannot promote." in source
    assert "encodeURIComponent(payload.artifact_version_id)" in source
    assert "const appIdQuery = payload?.app_id ? `?app_id=${encodeURIComponent(payload.app_id)}` : '';" in source
    assert "/api/studio/build/artifacts/${encodeURIComponent(payload.artifact_version_id)}/promote${appIdQuery}" in source
    assert "body: JSON.stringify({ build_registry_id: payload.build_registry_id || null })" in source
    assert "Boolean(payload?.artifact_version_id)" in source

