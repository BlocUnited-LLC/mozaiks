from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_studio_shell_config_injects_studio_routes():
    from mozaiksai.hosts import studio as studio_app

    shell_config = await studio_app.get_studio_shell_config()
    page_paths = {page.get("path") for page in shell_config.get("pages", [])}
    header_paths = {
        page.get("path")
        for page in (shell_config.get("header") or {}).get("pages", [])
        if isinstance(page, dict)
    }

    assert "/dashboard" in page_paths
    assert "/create" in page_paths
    assert "/admin" in page_paths
    assert "/admin/users" in page_paths
    assert "/admin/billing" in page_paths
    assert "/admin/usage" in page_paths
    assert "/profile" in page_paths
    assert "/studio" in page_paths
    assert "/studio/create" in page_paths
    assert "/studio" not in header_paths
    assert "/studio/create" not in header_paths
    assert "/profile" not in header_paths

    studio_pages = {page.get("path"): page for page in shell_config.get("pages", [])}
    assert studio_pages["/studio"]["meta"]["requiresRole"] == "admin"
    assert studio_pages["/studio/create"]["meta"]["requiresRole"] == "admin"


def test_mozaiks_app_composes_studio_host():
    from mozaiksai.hosts import mozaiks as mozaiks_app
    from mozaiksai.hosts import studio as studio_app

    assert mozaiks_app.app is studio_app.app


def test_studio_endpoints_work_without_auth_user_id(monkeypatch):
    from fastapi.testclient import TestClient
    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio as studio_app

    class _ArtifactStore:
        async def list_artifact_versions(self, **_kwargs):
            return []

        async def list_change_requests(self, **_kwargs):
            return []

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    reset_auth_adapter()

    client = TestClient(studio_app.app)
    build_response = client.get("/api/studio/create")
    history_response = client.get("/api/studio/history?limit=10")

    assert build_response.status_code == 200
    assert history_response.status_code == 200


def test_runtime_cors_uses_declared_frontend_origins(monkeypatch):
    from mozaiksai.hosts import runtime as runtime_app

    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setenv("REACT_DEV_ORIGIN", "http://localhost:3000")
    monkeypatch.setenv("CORS_ORIGINS", "")
    monkeypatch.setenv("ADDITIONAL_CORS_ORIGINS", "http://localhost:4173")

    assert runtime_app._build_cors_origins() == [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:4173",
    ]


def test_notification_count_query_uses_platform_notification_intents():
    from mozaiksai.hosts import platform as platform_app
    from mozaiksai.core.auth.dependencies import UserPrincipal

    principal = UserPrincipal(
        user_id="user_1",
        email="user@example.com",
        name="User",
        roles=["admin"],
        scopes=[],
        raw_claims={},
        app_id="app_1",
    )

    assert platform_app._notification_query_for_principal(principal) == {
        "status": "unread",
        "app_id": "app_1",
        "$or": [
            {"actor.id": "user_1"},
            {"audience.roles": {"$in": ["admin"]}},
            {"audience.roles": {"$exists": False}},
        ],
    }


def test_mozaiks_dashboard_uses_canonical_module_route():
    from conftest import active_app_root
    app_root = active_app_root()
    source = (app_root / "ui" / "pages" / "custom" / "Dashboard.jsx").read_text(encoding="utf-8")

    assert "/api/modules/investor_marketplace/list_listings" in source
    assert "/api/modules/communications/list_threads" in source
    assert "/api/modules/platform_apps/list_apps" not in source
    assert "/api/operations/" not in source


def test_platform_host_indexes_workflow_capability_routes(tmp_path):
    from mozaiksai.hosts import platform as platform_app

    workflow_dir = tmp_path / "workflows" / "ReviewWorkflow"
    workflow_dir.mkdir(parents=True)
    workflow_dir.joinpath("orchestrator.yaml").write_text(
        """
workflow_name: ReviewWorkflow
workflow_startup_mode: BackendOnly
triggers:
  - type: event
    event: domain.tasks.task_created
    capability_id: tasks.review
    context:
      task_id: payload.task_id
""".lstrip(),
        encoding="utf-8",
    )

    routes = platform_app._load_workflow_capability_routes(tmp_path)

    assert routes == {
        "tasks.review": [
            {
                "workflow_id": "ReviewWorkflow",
                "event_type": "domain.tasks.task_created",
                "trigger": {
                    "type": "event",
                    "event": "domain.tasks.task_created",
                    "capability_id": "tasks.review",
                    "context": {"task_id": "payload.task_id"},
                },
                "orchestrator_path": str((workflow_dir / "orchestrator.yaml")),
            }
        ]
    }


@pytest.mark.asyncio
async def test_platform_host_invokes_capability_route_into_workflow_session():
    from mozaiksai.hosts import platform as platform_app

    created: list[dict] = []
    emitted: list[tuple[str, dict]] = []

    async def fake_create_session(**kwargs):
        created.append(kwargs)
        return "chat_capability_1"

    async def fake_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    result = await platform_app._invoke_workflow_capability(
        capability_id="tasks.review",
        source_event={
            "id": "evt_1",
            "type": "domain.tasks.task_created",
            "tenant": {"app_id": "app_1"},
            "actor": {"type": "user", "id": "user_1"},
            "payload": {"task_id": "task_1"},
            "correlation": {"correlation_id": "corr_1"},
        },
        subscription={"id": "task_created_react", "module_id": "tasks"},
        routes={
            "tasks.review": [
                {
                    "workflow_id": "ReviewWorkflow",
                    "event_type": "domain.tasks.task_created",
                    "trigger": {
                        "type": "event",
                        "event": "domain.tasks.task_created",
                        "capability_id": "tasks.review",
                        "context": {"task_id": "payload.task_id"},
                    },
                }
            ]
        },
        event_emitter=fake_emit,
        create_session=fake_create_session,
        auto_start=False,
    )

    assert result == {
        "status": "created",
        "capability_id": "tasks.review",
        "workflow_id": "ReviewWorkflow",
        "chat_id": "chat_capability_1",
        "app_id": "app_1",
        "user_id": "user_1",
        "started": False,
        "websocket_url": "/ws/ReviewWorkflow/app_1/chat_capability_1/user_1",
    }
    assert created[0]["workflow_id"] == "ReviewWorkflow"
    assert created[0]["app_id"] == "app_1"
    assert created[0]["user_id"] == "user_1"
    assert created[0]["context_variables"]["triggered_capability_id"] == "tasks.review"
    assert created[0]["context_variables"]["task_id"] == "task_1"
    assert created[0]["trigger_meta"] == {
        "trigger_source": "module_event",
        "event_type": "domain.tasks.task_created",
        "source_event_id": "evt_1",
        "capability_id": "tasks.review",
        "workflow_id": "ReviewWorkflow",
        "subscription_id": "task_created_react",
        "module_id": "tasks",
    }
    assert emitted[0][0] == "platform.workflow_capability_started"
    assert emitted[0][1]["payload"]["chat_id"] == "chat_capability_1"


@pytest.mark.asyncio
async def test_platform_host_loads_app_zero_product_modules(monkeypatch):
    from mozaiksai.hosts import platform as platform_app
    from mozaiksai.core.runtime.app.loader import AppLoader

    from conftest import active_app_root
    monkeypatch.setenv("PLATFORM_PATH", str(active_app_root()))
    load_result = await AppLoader.load(str(platform_app.resolve_app_root()))
    loaded_modules = {module.name: type(module.handler).__name__ for module in load_result.modules}

    assert loaded_modules == {
        "communications": "CommunicationsHandler",
        "investor_marketplace": "InvestorMarketplaceHandler",
    }
