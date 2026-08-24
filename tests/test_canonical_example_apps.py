from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from mozaiksai.core.admin.registry import AdminRegistry, build_admin_shell_routes
from mozaiksai.core.auth.adapters import registry as auth_registry
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter, UserClaims
from mozaiksai.core.auth.adapters.registry import register_adapter, reset_auth_adapter
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition import module_executor as module_executor_mod
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.validation import scan_functional_generated_app
from mozaiksai.hosts import platform

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES_ROOT = ROOT / "examples" / "canonical-apps"
FACTORY_WORKFLOWS_ROOT = ROOT / "factory_app" / "workflows"


class _ExampleAuthAdapter(BaseAuthAdapter):
    name = "canonical-example"
    app_id = "canonical-example"

    async def validate_token(self, token: str) -> UserClaims:  # noqa: ARG002
        return UserClaims(
            user_id="example-user",
            email="example@example.test",
            name="Example User",
            roles=["user", "admin"],
            scopes=["access_as_user"],
            raw_claims={},
            provider=self.name,
            app_id=self.app_id,
        )

    def is_enabled(self) -> bool:
        return True


class _UpdateResult:
    def __init__(self, modified_count: int) -> None:
        self.modified_count = modified_count


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def insert_one(self, document: dict[str, Any]) -> dict[str, Any]:
        self.rows.append(deepcopy(document))
        return {"inserted_id": document.get("id")}

    async def find_many(
        self,
        query: dict[str, Any] | None = None,
        *,
        limit: int = 50,
        sort: Any = None,  # noqa: ARG002
        projection: Any = None,  # noqa: ARG002
    ) -> list[dict[str, Any]]:
        criteria = query or {}
        return [
            deepcopy(row)
            for row in self.rows
            if all(row.get(key) == value for key, value in criteria.items())
        ][:limit]

    async def find_one(
        self,
        query: dict[str, Any],
        projection: Any = None,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        rows = await self.find_many(query, limit=1)
        return rows[0] if rows else None

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> _UpdateResult:
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(deepcopy(update.get("$set") or {}))
                return _UpdateResult(1)
        if upsert:
            self.rows.append({**deepcopy(query), **deepcopy(update.get("$set") or {})})
            return _UpdateResult(1)
        return _UpdateResult(0)


class _PersistenceContext:
    stores: dict[str, list[dict[str, Any]]] = {}

    def __init__(
        self,
        *,
        app_id: str,
        tenant_id: str | None = None,
        workspace_id: str | None = None,
        user_id: str | None = None,
        **_kwargs: Any,
    ) -> None:
        self.app_id = app_id
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        self.user_id = user_id

    def collection(self, module_id: str, entity_name: str) -> _Collection:
        return self.literal_collection(f"{module_id}.{entity_name}")

    def literal_collection(self, collection_name: str) -> _Collection:
        return _Collection(self.stores.setdefault(collection_name, []))

    @classmethod
    def reset(cls) -> None:
        cls.stores = {}


def _app_root(name: str) -> Path:
    return EXAMPLES_ROOT / name / "app"


async def _configure_app(name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    app_root = _app_root(name)
    loaded = await AppLoader.load(str(app_root))
    _PersistenceContext.reset()
    monkeypatch.setattr(module_executor_mod, "MongoPersistenceContext", _PersistenceContext)
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")

    auth_required = bool(loaded.definition.config.get("authRequired"))
    reset_auth_adapter()
    if auth_required:
        _ExampleAuthAdapter.app_id = str(loaded.definition.config["appId"])
        register_adapter("canonical-example", _ExampleAuthAdapter)
        monkeypatch.setenv("AUTH_ENABLED", "true")
        monkeypatch.setenv("AUTH_PROVIDER", "canonical-example")
    else:
        monkeypatch.setenv("AUTH_ENABLED", "false")

    entitlement_checker = None
    if loaded.subscriptions_config is not None:
        entitlement_checker = ConfiguredEntitlementAdapter(
            config=loaded.subscriptions_config,
            collection_resolver=lambda alias: _PersistenceContext(app_id="app").literal_collection(
                "entitlement_dispatch_subscriptions" if alias == "billing.subscriptions" else alias
            ),
        )

    executor = ModuleExecutor(entitlement_checker=entitlement_checker)
    for module in loaded.modules:
        executor.register(
            module.name,
            module.handler,
            action_method_map=module.action_method_map,
            action_permissions=module.action_permissions_map,
            action_schemas=module.action_schemas_map,
            action_entitlements=module.action_entitlement_map,
        )
    registry = ExecutorRegistry()
    registry.register(executor)
    monkeypatch.setattr(platform, "executor_registry", registry)
    platform.app.state.executor_registry = registry
    platform.app.state.failed_module_names = []
    platform.app.state.module_action_surfaces = {
        module.name: module.action_api_surface_map for module in loaded.modules
    }
    platform.app.state.page_schemas = {
        page_name: schema.model_dump(mode="json", exclude_none=True)
        for page_name, schema in loaded.page_schemas.items()
    }
    return loaded


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "modules", "pages", "workflows"),
    [
        ("project-hub", {"projects", "tasks"}, {"projects", "tasks"}, set()),
        ("reporting-saas", {"entitlement_dispatch", "reports"}, {"reports"}, set()),
        ("research-ops", {"incidents", "research"}, {"operations", "research"}, {"ResearchWorkflow"}),
    ],
)
async def test_canonical_example_workspace_loads_real_contracts(name, modules, pages, workflows) -> None:
    loaded = await AppLoader.load(str(_app_root(name)))

    assert loaded.failed_module_names == []
    assert {module.name for module in loaded.modules} == modules
    assert set(loaded.page_schemas) == pages
    assert {workflow.name for workflow in loaded.definition.workflows} == workflows


@pytest.mark.parametrize("name", ["project-hub", "reporting-saas", "research-ops"])
def test_canonical_example_has_no_functional_bundle_gaps(name: str) -> None:
    app_root = _app_root(name)
    files = {
        path.relative_to(app_root).as_posix(): path.read_text(encoding="utf-8")
        for path in app_root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    }

    assert scan_functional_generated_app(files) == []


@pytest.mark.asyncio
async def test_project_hub_serves_pages_and_persists_crud(monkeypatch: pytest.MonkeyPatch) -> None:
    await _configure_app("project-hub", monkeypatch)
    headers = {"Authorization": "Bearer example-token"}
    client = TestClient(platform.app, raise_server_exceptions=False)

    assert client.get("/api/pages/projects", headers=headers).status_code == 200
    created = client.post(
        "/api/modules/projects/create_project",
        json={"params": {"name": "Canonical app proof", "status": "active"}},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    listed = client.post(
        "/api/modules/projects/list_projects",
        json={"params": {}},
        headers=headers,
    )
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"][0]["name"] == "Canonical app proof"


@pytest.mark.asyncio
async def test_reporting_saas_enforces_and_grants_entitlement(monkeypatch: pytest.MonkeyPatch) -> None:
    await _configure_app("reporting-saas", monkeypatch)
    headers = {"Authorization": "Bearer example-token"}
    client = TestClient(platform.app, raise_server_exceptions=False)

    denied = client.post("/api/modules/reports/export_report", json={"params": {}}, headers=headers)
    assert denied.status_code == 402, denied.text

    assigned = client.post(
        "/api/modules/entitlement_dispatch/assign_subscription",
        json={"params": {"user_id": "example-user", "plan_id": "pro"}},
        headers=headers,
    )
    assert assigned.status_code == 200, assigned.text

    granted = client.post("/api/modules/reports/export_report", json={"params": {}}, headers=headers)
    assert granted.status_code == 200, granted.text
    assert granted.json()["exported"] is True


@pytest.mark.asyncio
async def test_research_ops_loads_workflow_admin_and_module_http(monkeypatch: pytest.MonkeyPatch) -> None:
    from mozaiksai.core.workflow.workflow_manager import initialize_workflows, workflow_manager

    loaded = await _configure_app("research-ops", monkeypatch)
    initialize_workflows(str(EXAMPLES_ROOT / "research-ops" / "workflows"))
    client = TestClient(platform.app, raise_server_exceptions=False)

    assert {workflow.name for workflow in loaded.definition.workflows} == {"ResearchWorkflow"}
    assert "ResearchWorkflow" in workflow_manager.get_all_workflow_names()
    assert (loaded.definition.config["startup"]["landing_spot"]) == "/research"
    registry = AdminRegistry.model_validate(
        yaml.safe_load((_app_root("research-ops") / "admin" / "admin_registry.yaml").read_text(encoding="utf-8"))
    )
    assert {route["path"] for route in build_admin_shell_routes(registry)} == {"/admin/operations"}
    assert client.get("/api/pages/operations").status_code == 200
    summarized = client.post(
        "/api/modules/research/summarize_source",
        json={"params": {"source_text": "A deterministic local source for acceptance."}},
    )
    assert summarized.status_code == 200, summarized.text
    assert summarized.json()["summary"] == "A deterministic local source for acceptance."


@pytest.fixture(autouse=True)
def _restore_runtime_state():
    previous_state = {
        "executor_registry": getattr(platform.app.state, "executor_registry", None),
        "failed_module_names": list(getattr(platform.app.state, "failed_module_names", [])),
        "module_action_surfaces": deepcopy(getattr(platform.app.state, "module_action_surfaces", {})),
        "page_schemas": deepcopy(getattr(platform.app.state, "page_schemas", {})),
        "subscriptions_config": getattr(platform.app.state, "subscriptions_config", None),
    }
    yield
    from mozaiksai.core.workflow.workflow_manager import initialize_workflows

    initialize_workflows(str(FACTORY_WORKFLOWS_ROOT))
    platform.app.state.executor_registry = previous_state["executor_registry"]
    platform.app.state.failed_module_names = previous_state["failed_module_names"]
    platform.app.state.module_action_surfaces = previous_state["module_action_surfaces"]
    platform.app.state.page_schemas = previous_state["page_schemas"]
    platform.app.state.subscriptions_config = previous_state["subscriptions_config"]
    auth_registry._adapter_registry.clear()
    reset_auth_adapter()
