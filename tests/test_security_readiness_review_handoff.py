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
from mozaiksai.core.session.launcher import validate_context_for_workflow
from mozaiksai.core.session.model import JourneyAdvanceDecision
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.authority import (
    CALLER_INPUT_WRITER,
    ContextAuthorityError,
    build_context_authority_policy,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.pack import journey_orchestrator
from tests.test_security_readiness_target_binding import (
    RUN,
    invoke,
    security_bridge,
    security_build_fixture,  # noqa: F401
)

ROOT = Path(__file__).resolve().parents[1]


def workflow_config(name):
    return {"context_variables": yaml.safe_load(
        (ROOT / f"factory_app/workflows/{name}/context_variables.yaml").read_text(encoding="utf-8")
    )}


@pytest.mark.asyncio
@pytest.mark.parametrize("validation,integration,acceptance,preview", [
    ("skipped", True, "passed", None),
    ("passed", True, "passed", "https://preview.example.test/build"),
    ("failed", False, "failed", None),
])
@pytest.mark.parametrize("missing", [None, "artifact_version_id", "bundle_path", "app_validation_status",
                                    "app_bundle_acceptance_status", "integration_tests_passed"])
async def test_three_workflow_review_facts_survive_declared_projection(
    monkeypatch, security_build, missing, validation, integration, acceptance, preview,
):
    from factory_app.workflows.SecurityReadiness.tools import record_security_findings as recorder
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    monkeypatch.setattr(workflow_manager, "get_config", workflow_config)
    dispatch = AsyncMock(side_effect=AssertionError("Zero findings must not dispatch a write"))
    monkeypatch.setattr(recorder, "dispatch_workflow_module_action", dispatch)
    build = security_build.add({"app.json": '{"authRequired": false}'})
    facts = {
        "artifact_version_id": build.artifact.id,
        "bundle_path": str(build.archive.parent / "app"),
        "lifecycle_state": "review",
        "app_validation_status": validation,
        "app_validation_strategy_used": "skip" if validation == "skipped" else "local",
        "app_validation_preview_url": preview,
        "app_bundle_acceptance_status": acceptance,
        "integration_tests_passed": integration,
    }
    if missing:
        facts.pop(missing)
    config = workflow_config("AppGenerator")["context_variables"]
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=config["definitions"])
    generator = ContextVariablesBridge({}, authority_policy=policy)
    generator._bind_run(("AppGenerator", RUN[1], "generator_chat"), policy)

    async def publish(context_variables=None):
        for key, value in facts.items():
            context_variables.set(key, value)

    await invoke(publish, generator)
    security_input = validate_context_for_workflow(
        "SecurityReadiness", journey_orchestrator._project_launch_context(generator.to_dict(), "SecurityReadiness"),
    )
    assert security_input == facts
    build.bridge = security_bridge(initial={**security_input, "artifact_version_id": security_input.get("artifact_version_id")})
    await invoke(inspect_generated_app_security, build.bridge)
    result = await invoke(record_security_findings, build.bridge)
    assert result["success"] is True
    assert result["persisted"] is False
    assert "persistence_error" not in result
    assert build.bridge.get("security_readiness_recorded") is True
    dispatch.assert_not_awaited()

    projected = validate_context_for_workflow(
        "AppReview", journey_orchestrator._project_launch_context(build.bridge.to_dict(), "AppReview"),
    )
    expected = {**facts, "artifact_version_id": build.artifact.id}
    assert {key: projected[key] for key in expected} == expected
    assert "run_build_binding" not in projected
    assert "security_readiness_recorded" not in projected
    if missing and missing != "artifact_version_id":
        assert missing not in projected
    review = build_review_summary_payload({
        **projected, "run_build_binding": detach(build.bridge.get("run_build_binding")),
    })
    for key, value in expected.items():
        assert review[key] == value
    assert review["security_readiness_summary"]["success"] is True
    assert review["can_promote"] is (validation != "failed" and missing in {None, "artifact_version_id", "bundle_path"})
    assert review["can_revise"] is (missing != "bundle_path")


@pytest.mark.parametrize("workflow", ["SecurityReadiness", "AppReview"])
@pytest.mark.parametrize("key", ["app_validation_status", "app_bundle_acceptance_status", "integration_tests_passed"])
def test_review_evidence_is_router_seeded_not_caller_supplied(monkeypatch, workflow, key):
    from mozaiksai.core.workflow.workflow_manager import workflow_manager

    monkeypatch.setattr(workflow_manager, "get_config", workflow_config)
    value = True if key == "integration_tests_passed" else "passed"
    assert validate_context_for_workflow(workflow, {key: value}) == {key: value}
    with pytest.raises(ContextAuthorityError):
        validate_context_for_workflow(workflow, {key: value}, writer_id=CALLER_INPUT_WRITER)


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
