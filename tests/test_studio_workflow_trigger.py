from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_studio_trigger_endpoint_accepts_refinement_trigger_payload(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    reset_auth_adapter()

    from mozaiksai.hosts import studio as studio_app

    captured_prepare: dict = {}
    persisted_changes: list[dict] = []

    async def fake_prepare_routed_workflow_launch(**kwargs):
        captured_prepare.update(kwargs)
        return SimpleNamespace(
            workflow_id="AppGenerator",
            routing_decision=SimpleNamespace(
                explanation="refinement reroute",
                is_full_restart=False,
                rerouted_by_dependency=False,
            ),
        )

    async def fake_launch_prepared_workflow(launch):  # noqa: ANN001
        return SimpleNamespace(
            chat_id="chat_refine_1",
            workflow_id=launch.workflow_id,
            requested_workflow_id="AppGenerator",
            websocket_url="/ws/AppGenerator/app_1/chat_refine_1/demo-user",
            trigger_source="refinement",
            routing_explanation=launch.routing_decision.explanation,
            rerouted_by_dependency=False,
        )

    class _ArtifactStore:
        async def create_change_request(self, **kwargs):
            persisted_changes.append(kwargs)

    monkeypatch.setattr(studio_app, "prepare_routed_workflow_launch", fake_prepare_routed_workflow_launch)
    monkeypatch.setattr(studio_app, "launch_prepared_workflow", fake_launch_prepared_workflow)
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "trigger_source": "refinement",
            "trigger_payload": {
                "change_class": "patch",
                "artifact_kind": "app_bundle",
                "artifact_version_id": "av_123",
                "raw_user_request": "Add an export action",
            },
            "context_variables": {"screen": "studio-create"},
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "chat_id": "chat_refine_1",
        "workflow_id": "AppGenerator",
        "requested_workflow_id": "AppGenerator",
        "websocket_url": "/ws/AppGenerator/app_1/chat_refine_1/demo-user",
        "trigger_source": "refinement",
        "routing_explanation": "refinement reroute",
        "rerouted_by_dependency": False,
    }
    assert captured_prepare["workflow_id"] is None
    assert captured_prepare["trigger_source"] == "refinement"
    assert captured_prepare["context_variables"] == {"screen": "studio-create"}
    assert captured_prepare["trigger_payload"] == {
        "change_class": "patch",
        "artifact_kind": "app_bundle",
        "artifact_version_id": "av_123",
        "raw_user_request": "Add an export action",
    }
    assert "change_class" not in captured_prepare
    assert "artifact_kind" not in captured_prepare
    assert "artifact_version_id" not in captured_prepare
    assert "raw_user_request" not in captured_prepare
    assert captured_prepare["extra_trigger_meta"] == {
        "action_id": None,
        "change_class": "patch",
        "artifact_version_id": "av_123",
        "artifact_kind": "app_bundle",
    }
    assert persisted_changes == [
        {
            "app_id": captured_prepare["app_id"],
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "artifact_version_id": "av_123",
            "raw_user_request": "Add an export action",
            "classification": studio_app.ChangeClassification.PATCH,
            "router_decision": {
                "workflow_id": "AppGenerator",
                "explanation": "refinement reroute",
                "is_full_restart": False,
                "rerouted_by_dependency": False,
            },
            "created_by_user_id": "demo-user",
        }
    ]


def test_studio_trigger_endpoint_rejects_legacy_top_level_refinement_fields(monkeypatch):
    from mozaiksai.core.auth import reset_auth_adapter

    monkeypatch.setenv("AUTH_ENABLED", "false")
    reset_auth_adapter()

    from mozaiksai.hosts import studio as studio_app

    client = TestClient(studio_app.app)
    response = client.post(
        "/api/workflows/trigger",
        json={
            "trigger_source": "refinement",
            "change_class": "patch",
            "artifact_kind": "app_bundle",
            "artifact_version_id": "av_123",
            "raw_user_request": "Add an export action",
        },
    )

    assert response.status_code == 400
    assert "workflow_id is required unless refinement routing resolves one" in response.json()["detail"]