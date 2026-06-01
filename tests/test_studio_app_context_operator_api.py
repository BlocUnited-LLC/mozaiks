from __future__ import annotations

import inspect
import sys

from fastapi.testclient import TestClient

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    AppContextGraphLookupResult,
    AppContextRefSummary,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
)
from mozaiksai.control_plane.app_context_refresh_execution import (
    ContextRefreshLaunchResult,
    ContextRefreshLaunchStatus,
)
from mozaiksai.control_plane.dry_run import build_refinement_execution_plan_from_route
from mozaiksai.core.app_context.models import AppContextGraph, AppContextStaleStatus
from mozaiksai.core.app_context.refresh import (
    BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
    ContextRefreshPlan,
    ContextRefreshResult,
    ContextRefreshResultStatus,
)
from mozaiksai.core.auth import reset_auth_adapter


def _studio_app(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    sys.modules.pop("factory_app", None)
    from mozaiksai.hosts import studio as studio_app

    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: object())
    return studio_app


def _client(monkeypatch):
    studio_app = _studio_app(monkeypatch)
    return studio_app, TestClient(studio_app.app)


def _current_summary() -> AppContextSummary:
    return AppContextSummary(
        app_id="app_1",
        available=True,
        context_version_id="ctx_1",
        mode="brownfield",
        stale_status="current",
        source_refs=[
            AppContextRefSummary(
                ref_id="src_repo",
                kind="repo",
                target="repo://operations",
            )
        ],
        artifact_refs=[
            AppContextRefSummary(
                ref_id="av_graph_1",
                kind="app_context_graph",
                target="app_context_graph",
                lifecycle_status="current",
            )
        ],
    )


def _refresh_plan() -> ContextRefreshPlan:
    return ContextRefreshPlan(
        app_id="app_1",
        current_context_version_id="ctx_1",
        target_source_refs=[
            {
                "source_ref_id": "src_repo",
                "kind": "repo",
                "uri": "repo://operations",
            }
        ],
        workflow_sequence=BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
        mutation_allowed=False,
    )


def _graph() -> AppContextGraph:
    return AppContextGraph(
        graph_id="graph_app_1",
        app_id="app_1",
        stale_status=AppContextStaleStatus.CURRENT,
        graph_hash="sha256:test",
    )


def test_get_app_context_status_returns_current_summary(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)

    async def fake_summary(**kwargs):
        return _current_summary()

    async def fake_graph(**kwargs):
        return AppContextGraphLookupResult(graph=_graph())

    monkeypatch.setattr(studio_app, "get_current_app_context_summary", fake_summary)
    monkeypatch.setattr(studio_app, "get_current_app_context_graph", fake_graph)

    response = client.get("/api/studio/apps/app_1/context")

    assert response.status_code == 200
    body = response.json()
    assert body["app_id"] == "app_1"
    assert body["app_context_summary"]["context_version_id"] == "ctx_1"
    assert body["stale_status"] == "current"
    assert body["artifact_refs"][0]["kind"] == "app_context_graph"
    assert body["context_graph_status"]["available"] is True
    assert body["context_graph_status"]["graph_id"] == "graph_app_1"


def test_get_app_context_status_handles_missing_context(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)

    async def fake_summary(**kwargs):
        return AppContextSummary(
            app_id="app_1",
            available=False,
            warnings=[APP_CONTEXT_MISSING_WARNING],
        )

    async def fake_graph(**kwargs):
        return AppContextGraphLookupResult(graph=None)

    monkeypatch.setattr(studio_app, "get_current_app_context_summary", fake_summary)
    monkeypatch.setattr(studio_app, "get_current_app_context_graph", fake_graph)

    response = client.get("/api/studio/apps/app_1/context")

    assert response.status_code == 200
    body = response.json()
    assert body["app_context_summary"]["available"] is False
    assert body["context_graph_status"]["available"] is False
    assert APP_CONTEXT_MISSING_WARNING in body["warnings"]


def test_workspace_snapshot_endpoint_registers_context(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)

    async def fake_register(**kwargs):
        assert kwargs["app_id"] == "app_1"
        assert kwargs["workspace_root"] == "C:/workspace/app"
        return type(
            "Result",
            (),
            {
                "app_bundle_artifact_version_id": "av_bundle",
                "app_context_version_id": "ctx_app_1",
                "app_context_artifact_version_id": "av_context",
                "graph_artifact_version_id": "av_graph",
                "artifact_path": "generated/workspace_snapshots/app_1/artifact.zip",
                "indexed_file_count": 42,
                "scan_health": {"selected_file_count": 42},
                "health_report": {"status": "healthy", "warnings": [], "blockers": [], "coverage": {}},
                "warnings": [],
            },
        )()

    monkeypatch.setattr(studio_app, "register_workspace_snapshot", fake_register)

    response = client.post(
        "/api/studio/apps/app_1/context/workspace-snapshot",
        json={"workspace_root": "C:/workspace/app"},
    )

    assert response.status_code == 200
    snapshot = response.json()["workspace_snapshot"]
    assert snapshot["app_bundle_artifact_version_id"] == "av_bundle"
    assert snapshot["graph_artifact_version_id"] == "av_graph"
    assert snapshot["scan_health"]["selected_file_count"] == 42
    assert snapshot["health_report"]["status"] == "healthy"


def test_refresh_plan_is_non_mutating_and_does_not_launch(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)
    launch_called = False

    async def fake_summary(**kwargs):
        return _current_summary()

    async def fail_launch(*args, **kwargs):
        nonlocal launch_called
        launch_called = True
        raise AssertionError("refresh-plan must not launch workflows")

    monkeypatch.setattr(studio_app, "get_current_app_context_summary", fake_summary)
    monkeypatch.setattr(studio_app, "launch_context_refresh_plan", fail_launch)

    response = client.post(
        "/api/studio/apps/app_1/context/refresh-plan",
        json={"reason": "Refresh stale discovery context."},
    )

    assert response.status_code == 200
    body = response.json()
    plan = body["context_refresh_plan"]
    assert body["launched"] is False
    assert plan["workflow_sequence"] == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    assert plan["mutation_allowed"] is False
    assert launch_called is False


def test_refresh_launch_requires_explicit_confirmation(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)

    async def fail_launch(*args, **kwargs):
        raise AssertionError("launch should not run without confirm_launch=true")

    monkeypatch.setattr(studio_app, "launch_context_refresh_plan", fail_launch)

    response = client.post(
        "/api/studio/apps/app_1/context/refresh-launch",
        json={"plan": _refresh_plan().model_dump(mode="json"), "confirm_launch": False},
    )

    assert response.status_code == 400
    assert "confirm_launch=true" in response.json()["detail"]


def test_refresh_launch_calls_explicit_launcher_when_confirmed(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)
    captured = {}

    async def fake_launch(plan, **kwargs):
        captured["plan"] = plan
        captured["kwargs"] = kwargs
        return ContextRefreshLaunchResult(
            status=ContextRefreshLaunchStatus.LAUNCHED,
            workflow_sequence=BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE,
            workflow_id="ExistingAppDiscovery",
            chat_id="chat_refresh_1",
            app_id="app_1",
            user_id=kwargs["user_id"],
            current_context_version_id="ctx_1",
            expected_artifacts=["app_context_version"],
        )

    monkeypatch.setattr(studio_app, "launch_context_refresh_plan", fake_launch)
    monkeypatch.setattr(studio_app, "get_session_router", lambda: object())

    response = client.post(
        "/api/studio/apps/app_1/context/refresh-launch",
        json={
            "plan": _refresh_plan().model_dump(mode="json"),
            "confirm_launch": True,
            "reason": "Operator requested context refresh.",
        },
    )

    assert response.status_code == 200
    body = response.json()["context_refresh_launch"]
    assert body["status"] == "launched"
    assert body["workflow_sequence"] == BROWNFIELD_DISCOVERY_REFRESH_SEQUENCE
    assert captured["kwargs"]["app_id"] == "app_1"
    assert captured["kwargs"]["reason"] == "Operator requested context refresh."


def test_refresh_complete_returns_result_without_running_workflow(monkeypatch) -> None:
    studio_app, client = _client(monkeypatch)
    captured = {}

    async def fake_complete(plan, **kwargs):
        captured["plan"] = plan
        captured["kwargs"] = kwargs
        return ContextRefreshResult(
            app_id="app_1",
            previous_context_version_id="ctx_1",
            new_context_version_id="ctx_2",
            status=ContextRefreshResultStatus.COMPLETED,
            stale_resolved=True,
            warnings=[],
            artifacts_created=[],
        )

    monkeypatch.setattr(studio_app, "complete_context_refresh", fake_complete)

    response = client.post(
        "/api/studio/apps/app_1/context/refresh-complete",
        json={
            "plan": _refresh_plan().model_dump(mode="json"),
            "previous_context_version_id": "ctx_1",
        },
    )

    assert response.status_code == 200
    result = response.json()["context_refresh_result"]
    assert result["status"] == "completed"
    assert result["stale_resolved"] is True
    assert captured["kwargs"]["app_id"] == "app_1"
    assert captured["kwargs"]["previous_context_version_id"] == "ctx_1"


def test_override_requires_reviewer_and_reason(monkeypatch) -> None:
    _, client = _client(monkeypatch)
    base_payload = {
        "request_id": "req_1",
        "context_version_id": "ctx_1",
        "original_policy_decision": "block_requires_context_refresh",
        "override_decision": "allow_with_warning",
        "reason": "Reviewed stale context risk.",
        "reviewer": "operator@example.invalid",
    }

    missing_reviewer = dict(base_payload)
    missing_reviewer.pop("reviewer")
    assert client.post("/api/studio/apps/app_1/context/override", json=missing_reviewer).status_code == 422

    missing_reason = dict(base_payload)
    missing_reason.pop("reason")
    assert client.post("/api/studio/apps/app_1/context/override", json=missing_reason).status_code == 422


def test_override_allow_with_warning_is_non_mutating_and_preserves_plan_scope(monkeypatch) -> None:
    _, client = _client(monkeypatch)
    plan = build_refinement_execution_plan_from_route(
        request="Change the orders data model.",
        artifact_kind="app_bundle",
        change_class="feature",
        workflow_id="AppGenerator",
        workflow_sequence="app_revision",
        affected_workflows=["AppGenerator"],
        affected_declarative_families=["app_bundle"],
        affected_bundle_paths=["config/data.json"],
        scope_summary="Brownfield data model migration.",
        app_id="app_1",
        request_id="req_1",
        app_context_summary=AppContextSummary(
            app_id="app_1",
            available=True,
            context_version_id="ctx_1",
            mode="brownfield",
            stale_status="stale",
        ),
    )
    assert plan.context_policy_decision is not None
    assert plan.context_policy_decision.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH

    response = client.post(
        "/api/studio/apps/app_1/context/override",
        json={
            "request_id": "req_1",
            "context_version_id": "ctx_1",
            "original_policy_decision": "block_requires_context_refresh",
            "override_decision": "allow_with_warning",
            "reason": "Operator reviewed source ownership and will keep validation gates.",
            "reviewer": "operator@example.invalid",
            "applies_to_paths": ["config/data.json"],
            "change_class": "feature",
            "refinement_lane": plan.refinement_lane,
            "apply_to_plan": True,
            "plan": plan.model_dump(mode="json"),
        },
    )

    assert response.status_code == 200
    body = response.json()
    override = body["app_context_policy_override"]
    applied_plan = body["applied_plan"]
    assert body["mutation_allowed"] is False
    assert override["mutation_allowed"] is False
    assert applied_plan["mutation_allowed"] is False
    assert applied_plan["context_policy_decision"]["decision"] == "warn"
    assert applied_plan["workflow_sequence"] == plan.workflow_sequence
    assert applied_plan["target_workflow"] == plan.target_workflow
    assert applied_plan["affected_bundle_paths"] == plan.affected_bundle_paths
    assert applied_plan["validation_plan"] == plan.model_dump(mode="json")["validation_plan"]


def test_operator_api_source_does_not_call_generation_promotion_or_graph_database(monkeypatch) -> None:
    studio_app = _studio_app(monkeypatch)
    source = "\n".join(
        [
            inspect.getsource(studio_app.get_studio_app_context_status),
            inspect.getsource(studio_app.create_studio_app_context_refresh_plan),
            inspect.getsource(studio_app.launch_studio_app_context_refresh),
            inspect.getsource(studio_app.complete_studio_app_context_refresh),
            inspect.getsource(studio_app.create_studio_app_context_policy_override),
        ]
    )

    assert "AppGenerator" not in source
    assert "AgentGenerator" not in source
    assert "promote_build_artifact_version" not in source
    assert "revert_to_artifact_version" not in source
    assert "Falkor" not in source
    assert "mozaiks" + "-app" not in source.lower()
