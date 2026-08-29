from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_studio_shell_config_injects_studio_routes(monkeypatch):
    from tests.import_utils import active_app_root

    # Importing the Studio host is environment-inert; bind the workspace
    # explicitly instead of relying on an import-time side effect.
    monkeypatch.setenv("PLATFORM_PATH", str(active_app_root()))
    from mozaiksai.hosts import studio as studio_app

    shell_config = await studio_app.get_studio_shell_config()
    page_paths = {page.get("path") for page in shell_config.get("pages", [])}
    header_paths = {
        page.get("path")
        for page in (shell_config.get("header") or {}).get("pages", [])
        if isinstance(page, dict)
    }

    assert "/apps" in page_paths
    assert "/apps/:appId/overview" in page_paths
    assert "/apps/:appId/building" in page_paths
    assert "/apps/:appId/billing" not in page_paths
    assert "/apps/:appId/hosting" not in page_paths
    assert "/apps/:appId/integrations" in page_paths
    assert "/apps/:appId/access" in page_paths
    assert "/apps/:appId/users" not in page_paths
    assert "/apps/:appId/usage" in page_paths
    assert "/apps/:appId/activity" in page_paths
    assert "/billing" not in page_paths
    assert "/hosting" not in page_paths
    assert "/apps/:appId/build" not in page_paths
    assert "/apps/:appId/deploy" not in page_paths
    assert "/apps/:appId/admin" not in page_paths
    assert "/me" in page_paths
    # admin_registry.yaml is reserved for AdminPortal extension pages, so
    # unfinished operations/settings pages do not leak into Studio shell routes.
    assert "/apps/:appId/operations" not in page_paths
    assert "/apps/:appId/settings" not in page_paths
    assert "/apps" not in header_paths
    assert "/me" not in header_paths

    studio_pages = {page.get("path"): page for page in shell_config.get("pages", [])}
    assert studio_pages["/apps"]["meta"]["requiresRole"] == "admin"


def test_platform_health_endpoint_returns_200_when_healthy():
    from fastapi.testclient import TestClient

    from mozaiksai.hosts import studio as studio_app

    studio_app.app.state.startup_degraded = False
    client = TestClient(studio_app.app, raise_server_exceptions=False)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_platform_health_endpoint_returns_503_when_degraded():
    from fastapi.testclient import TestClient

    from mozaiksai.hosts import studio as studio_app

    studio_app.app.state.startup_degraded = True
    studio_app.app.state.startup_degraded_reason = "module load failed"
    try:
        client = TestClient(studio_app.app, raise_server_exceptions=False)
        response = client.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["reason"] == "module load failed"
    finally:
        studio_app.app.state.startup_degraded = False
        studio_app.app.state.startup_degraded_reason = None


def test_studio_host_composes_platform_host():
    from mozaiksai.hosts import platform as platform_app
    from mozaiksai.hosts import studio as studio_app

    assert studio_app.app is platform_app.app


def test_studio_endpoints_work_without_auth_user_id(monkeypatch):
    from fastapi.testclient import TestClient

    from mozaiksai.core.auth import reset_auth_adapter
    from mozaiksai.hosts import studio as studio_app
    from tests.import_utils import active_app_root

    # Importing the Studio host is environment-inert; bind the workspace
    # explicitly instead of relying on an import-time side effect.
    monkeypatch.setenv("PLATFORM_PATH", str(active_app_root()))

    class _ArtifactStore:
        async def list_build_records(self, **_kwargs):
            return []

        async def list_change_requests(self, **_kwargs):
            return []

    class _AppRegistryService:
        def __init__(self) -> None:
            self._app = None

        async def create_app_record(self, **kwargs):  # noqa: ANN003
            self._app = {
                "app_id": kwargs.get("app_id") or "smoke-app",
                "bundle_path": None,
                "created_at": "2025-01-01T00:00:00+00:00",
                "description": kwargs.get("description"),
                "last_status_changed_at": "2025-01-01T00:00:00+00:00",
                "lifecycle_state": kwargs.get("status", "draft"),
                "name": kwargs.get("name", "Smoke App"),
                "owner_user_id": kwargs.get("owner_user_id", "demo-user"),
                "updated_at": "2025-01-01T00:00:00+00:00",
                "build_registry_id": "appreg_smoke",
                "active_chat_id": kwargs.get("active_chat_id"),
                "active_workflow_id": kwargs.get("active_workflow_id"),
            }
            return {"success": True, "app": self._app}

        async def get_app_record(self, **_kwargs):
            return {"app": self._app}

        async def update_build_status(self, **kwargs):  # noqa: ANN003
            self._app = {
                **(self._app or {}),
                "build_registry_id": kwargs.get("build_registry_id"),
                "lifecycle_state": kwargs.get("status"),
                "bundle_path": kwargs.get("bundle_path"),
            }
            return {"success": True, "app": self._app}

    service = _AppRegistryService()

    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    monkeypatch.setattr(studio_app, "get_artifact_store", lambda: _ArtifactStore())
    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: service)
    reset_auth_adapter()

    client = TestClient(studio_app.app)
    create_response = client.post(
        "/api/studio/apps",
        json={
            "name": "Smoke App",
            "active_chat_id": "chat-smoke",
            "active_workflow_id": "ValueEngine",
        },
    )
    assert create_response.status_code == 200
    app_id = create_response.json()["app"]["app_id"]
    build_registry_id = create_response.json()["app"]["build_registry_id"]
    assert create_response.json()["app"]["active_chat_id"] == "chat-smoke"
    assert create_response.json()["app"]["active_workflow_id"] == "ValueEngine"

    build_response = client.get(f"/api/studio/build?app_id={app_id}")
    status_response = client.put(
        f"/api/studio/apps/{build_registry_id}/status",
        json={"status": "review", "bundle_path": "generated/apps/smoke"},
    )
    history_response = client.get("/api/studio/build/history?limit=10")

    assert build_response.status_code == 200
    assert status_response.status_code == 200
    assert status_response.json()["app"]["lifecycle_state"] == "review"
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
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts.routers import notifications as notifications_router

    principal = UserPrincipal(
        user_id="user_1",
        email="user@example.com",
        name="User",
        roles=["admin"],
        scopes=["notifications.read"],
        raw_claims={},
        app_id="app_1",
    )

    assert notifications_router._notification_query_for_principal(principal) == {
        "status": "unread",
        "app_id": "app_1",
        "$or": [
            {"audience.user_ids": "user_1"},
            {"audience.roles": {"$in": ["admin"]}},
            {"audience.permissions": {"$in": ["notifications.read"]}},
            {
                "$and": [
                    {"$or": [{"audience.user_ids": {"$exists": False}}, {"audience.user_ids": []}]},
                    {"$or": [{"audience.roles": {"$exists": False}}, {"audience.roles": []}]},
                    {"$or": [{"audience.permissions": {"$exists": False}}, {"audience.permissions": []}]},
                ]
            },
        ],
    }


def test_product_home_uses_canonical_module_actions():
    from tests.import_utils import active_app_root

    app_root = active_app_root()
    home_path = app_root / "ui" / "pages" / "custom" / "HomePage.jsx"
    if not home_path.exists():
        pytest.skip("Product-specific HomePage.jsx not present in active app workspace")
    source = home_path.read_text(encoding="utf-8")

    assert "moduleAction('app_registry', 'list_user_apps'" in source
    assert "moduleAction('messages', 'list_threads'" in source
    assert "moduleAction('wallet', 'get_wallet_summary'" in source
    assert "/api/modules/platform_apps/list_apps" not in source
    assert "/api/modules/" not in source
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
                "orchestrator_path": str(workflow_dir / "orchestrator.yaml"),
            }
        ]
    }


@pytest.mark.asyncio
async def test_platform_host_invokes_capability_route_into_workflow_session():
    from mozaiksai.core.runtime.composition.workflow_trigger_guard import (
        WORKFLOW_TRIGGER_TRACE_KEY,
        WorkflowTriggerDecision,
    )
    from mozaiksai.hosts import platform as platform_app

    created: list[dict] = []
    emitted: list[tuple[str, dict]] = []

    async def fake_create_session(**kwargs):
        created.append(kwargs)
        return "chat_capability_1"

    async def fake_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    class _AllowingGuard:
        async def authorize(self, **_kwargs):
            return WorkflowTriggerDecision(
                allowed=True,
                reason="allowed",
                invocation_id="wti_test",
                event_identity="evt_1",
                depth=1,
                trace={
                    "root_event_id": "evt_1",
                    "depth": 1,
                    "capability_ids": ["tasks.review"],
                    "invocation_ids": ["wti_test"],
                },
            )

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
        trigger_guard=_AllowingGuard(),
        auto_start=False,
    )

    assert result == {
        "status": "created",
        "capability_id": "tasks.review",
        "workflow_id": "ReviewWorkflow",
        "chat_id": "chat_capability_1",
        "app_id": "app_1",
        "user_id": "user_1",
        "invocation_id": "wti_test",
        "trigger_depth": 1,
        "started": False,
        "websocket_url": "/ws/ReviewWorkflow/app_1/chat_capability_1/user_1",
    }
    assert created[0]["workflow_id"] == "ReviewWorkflow"
    assert created[0]["app_id"] == "app_1"
    assert created[0]["user_id"] == "user_1"
    assert created[0]["context_variables"]["triggered_capability_id"] == "tasks.review"
    assert created[0]["context_variables"]["task_id"] == "task_1"
    assert created[0]["context_variables"][WORKFLOW_TRIGGER_TRACE_KEY]["depth"] == 1
    assert created[0]["trigger_meta"] == {
        "trigger_source": "module_event",
        "event_type": "domain.tasks.task_created",
        "source_event_id": "evt_1",
        "capability_id": "tasks.review",
        "workflow_id": "ReviewWorkflow",
        "subscription_id": "task_created_react",
        "module_id": "tasks",
        "invocation_id": "wti_test",
        "trigger_depth": 1,
    }
    assert emitted[0][0] == "platform.workflow_capability_started"
    assert emitted[0][1]["payload"]["chat_id"] == "chat_capability_1"


@pytest.mark.asyncio
async def test_platform_host_loads_app_zero_product_modules(monkeypatch):
    from mozaiksai.core.runtime.app.loader import AppLoader
    from mozaiksai.hosts import platform as platform_app
    from tests.import_utils import active_app_root

    monkeypatch.setenv("PLATFORM_PATH", str(active_app_root()))
    load_result = await AppLoader.load(str(platform_app.resolve_app_root()))
    loaded_modules = {module.name: type(module.handler).__name__ for module in load_result.modules}

    product_modules = {"communications", "investor_marketplace"}
    if "factory_control_plane" in loaded_modules:
        pytest.skip("Factory app workspace includes shared modules but is not the product workspace")
    if not product_modules.intersection(loaded_modules):
        pytest.skip("Product modules not present in active app workspace")
    assert loaded_modules == {
        "communications": "CommunicationsHandler",
        "investor_marketplace": "InvestorMarketplaceHandler",
    }

