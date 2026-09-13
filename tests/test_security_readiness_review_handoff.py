from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.AppReview.tools.review_context import build_review_summary_payload
from factory_app.workflows.SecurityReadiness.tools.inspect_generated_app_security import (
    inspect_generated_app_security,
)
from factory_app.workflows.SecurityReadiness.tools.record_security_findings import (
    record_security_findings,
)
from mozaiksai.core.session.model import JourneyAdvanceDecision
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.pack import journey_orchestrator
from tests.test_security_readiness_target_binding import (
    RUN,
    invoke,
    security_bridge,
    security_build_fixture,  # noqa: F401
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("project", ["project_a", "project_b"])
@pytest.mark.parametrize("select_artifact", [True, False])
async def test_security_assessment_reaches_app_review_through_journey_transition(
    monkeypatch, security_build, project, select_artifact,
):
    build = security_build.add({"app/app.json": '{"authRequired": false}'}, project=project)
    if not select_artifact:
        build.bridge = security_bridge(project, initial={"artifact_version_id": None})
    await invoke(inspect_generated_app_security, build.bridge)
    await invoke(record_security_findings, build.bridge)
    summary = detach(build.bridge.get("security_readiness_summary"))
    binding = detach(build.bridge.get("run_build_binding"))
    assert build.bridge.get("security_readiness_recorded") is True
    source = {
        "_id": RUN[2], "app_id": RUN[1], "user_id": "owner_1", "workflow_name": RUN[0],
        "run_build_binding": binding,
        "artifact_version_id": build.bridge.get("artifact_version_id"),
        "security_readiness_summary": summary,
        "security_readiness_recorded": True,
        "security_readiness_findings": [],
        "unrelated_context": "not review evidence",
    }
    collection = SimpleNamespace(find_one=AsyncMock(return_value=source))
    persistence = SimpleNamespace(_coll=AsyncMock(return_value=collection))
    transport = SimpleNamespace(
        _get_or_create_persistence_manager=lambda: persistence,
        send_event_to_ui=AsyncMock(),
    )
    router = SimpleNamespace(advance_journey_after_run_complete=AsyncMock(return_value=JourneyAdvanceDecision(
        journey_instance_id="journey_1", journey_key="build", current_group_index=9,
        journey_total_steps=11, next_group_index=10, next_transition_id="app_review", completed=False,
    )))
    monkeypatch.setattr(journey_orchestrator, "get_session_router_for_chat", AsyncMock(return_value=router))
    orchestrator = journey_orchestrator.JourneyOrchestrator()
    monkeypatch.setattr(orchestrator, "_get_transport_conn", AsyncMock(return_value=(
        {"websocket": object(), "ws_id": 77}, transport,
    )))
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    review_config = {"context_variables": yaml.safe_load(
        (ROOT / "factory_app/workflows/AppReview/context_variables.yaml").read_text(encoding="utf-8")
    )}
    monkeypatch.setattr(workflow_manager, "get_config", lambda name: review_config if name == "AppReview" else None)
    projected = journey_orchestrator._project_launch_context(source, "AppReview")
    assert projected["security_readiness_summary"] == summary
    assert "run_build_binding" not in projected
    assert "security_readiness_recorded" not in projected

    await orchestrator.handle_run_complete({
        "chat_id": RUN[2], "app_id": RUN[1], "user_id": "owner_1", "workflow_name": RUN[0],
        "status": "completed", "run_completed": True,
    })

    transport.send_event_to_ui.assert_awaited_once()
    event, chat_id = transport.send_event_to_ui.call_args.args
    assert chat_id == RUN[2]
    assert event["type"] == "chat.transition_requested"
    assert event["data"]["transition_id"] == "app_review"
    assert event["data"]["from_chat_id"] == RUN[2]
    # ChatPage submits this context to /api/transitions/resolve for AppReview.
    handoff = event["data"].get("context_variables", {})
    assert handoff == projected
    # The server resolves the binding again; it must not travel as caller context.
    review = build_review_summary_payload({**handoff, "run_build_binding": binding})
    assert review["security_readiness_summary"] == summary
    assert review["artifact_version_id"] == build.artifact.id
    assert summary["artifact_version_id"] == review["artifact_version_id"]
    assert summary["build_registry_id"] == project
    assert summary["build_id"] == binding["build_id"]
    assert review["can_promote"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [
    {"status": "failed", "run_completed": False},
    {"status": "completed", "run_completed": False},
])
async def test_failed_or_incomplete_security_run_does_not_request_review(monkeypatch, outcome):
    orchestrator = journey_orchestrator.JourneyOrchestrator()
    connection = AsyncMock(side_effect=AssertionError("An unsuccessful assessment must not advance"))
    monkeypatch.setattr(orchestrator, "_get_transport_conn", connection)

    await orchestrator.handle_run_complete({
        "chat_id": RUN[2], "app_id": RUN[1], "user_id": "owner_1", "workflow_name": RUN[0], **outcome,
    })

    connection.assert_not_awaited()
