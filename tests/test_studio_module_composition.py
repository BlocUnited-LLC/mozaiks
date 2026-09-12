from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.app.module_loader import ModuleLoader
from mozaiksai.core.runtime.composition.module_authority import ModuleDispatchAuthority
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor, ModuleRequest
from mozaiksai.resources import resolve_factory_app_root


@pytest.fixture(autouse=True)
def restore_module_imports(monkeypatch):
    from mozaiksai.core.account.registry import account_data_registry

    monkeypatch.setattr(account_data_registry, "_handlers", {})
    paths = list(sys.path)
    modules = dict(sys.modules)
    yield
    sys.path[:] = paths
    for key in list(sys.modules):
        if key == "services" or key.startswith(("services.", "mozaiks_runtime_module_")):
            if key in modules:
                sys.modules[key] = modules[key]
            else:
                sys.modules.pop(key, None)


def app_root(parent: Path, name: str) -> Path:
    root = parent / name / "app"
    root.mkdir(parents=True)
    (root / "app.json").write_text(json.dumps({"appName": name, "appId": name}), encoding="utf-8")
    return root


def module(root: Path, name: str, label: str, *, import_service: bool = False) -> None:
    target = root / "modules" / name
    (target / "backend").mkdir(parents=True)
    (target / "module.yaml").write_text(
        f"schema_version: mozaiks.module.v1\nmodule:\n  id: {name}\n  version: 1.0.0\n  handler: backend.handler:Handler\nactions:\n  - id: read\n    description: Read source marker.\n    handler_method: read\n",
        encoding="utf-8",
    )
    source = "from services.support import VALUE\n" if import_service else f"VALUE = {label!r}\n"
    (target / "backend/handler.py").write_text(source + "class Handler:\n    async def read(self, ctx):\n        return {'source': VALUE}\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_platform_app_loader_has_no_implicit_factory_modules(tmp_path):
    active = app_root(tmp_path, "customer")
    module(active, "product", "customer")
    result = await AppLoader.load(str(active))
    assert [item.name for item in result.modules] == ["product"]
    assert "security_readiness" not in {item.name for item in result.modules}


@pytest.mark.asyncio
async def test_explicit_defaults_compose_by_id_preserving_workspace_services(tmp_path):
    active = app_root(tmp_path, "customer")
    defaults = app_root(tmp_path, "studio_defaults")
    module(defaults, "shared", "default", import_service=True)
    module(defaults, "override", "default")
    module(active, "override", "customer")
    module(active, "product", "customer")
    for root, value in ((active, "customer-service"), (defaults, "default-service")):
        (root / "services").mkdir()
        (root / "services/support.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
    result = await AppLoader.load(str(active), module_defaults_path=str(defaults))
    loaded = {item.name: item for item in result.modules}
    assert set(loaded) == {"product", "override", "shared"}
    assert (await loaded["shared"].handler.read(None))["source"] == "customer-service"
    assert (await loaded["override"].handler.read(None))["source"] == "customer"
    assert loaded["shared"].path == defaults / "modules/shared"
    assert loaded["override"].path == active / "modules/override"
    assert list(sys.modules["services"].__path__) == [str(active / "services")]
    assert result.definition.config["appId"] == "customer"
    assert result.data_contract is None


@pytest.mark.asyncio
async def test_broken_local_override_cannot_silently_load_default(tmp_path):
    active = app_root(tmp_path, "customer")
    defaults = app_root(tmp_path, "studio_defaults")
    module(defaults, "shared", "default")
    (active / "modules/shared").mkdir(parents=True)
    result = await AppLoader.load(str(active), module_defaults_path=str(defaults))
    assert result.modules == []
    assert result.failed_module_names == ["shared"]


def test_loading_factory_as_active_root_deduplicates_defaults():
    factory = resolve_factory_app_root() / "app"
    plain = ModuleLoader(str(factory)).discover_module_names()
    composed = ModuleLoader(str(factory), module_defaults_path=str(factory)).discover_module_names()
    assert composed == plain


@pytest.mark.asyncio
async def test_packaged_security_module_and_product_module_dispatch_together(tmp_path, monkeypatch):
    active = app_root(tmp_path, "host-app")
    module(active, "security_compliance", "product")
    factory = resolve_factory_app_root() / "app"
    loaded = await AppLoader.load(str(active), module_defaults_path=str(factory))
    assert not loaded.failed_module_names
    modules = {item.name: item for item in loaded.modules}
    assert modules["security_readiness"].path == factory / "modules/security_readiness"
    assert modules["security_compliance"].path == active / "modules/security_compliance"
    surfaces = {item.name: item.action_api_surface_map for item in loaded.modules}
    assert "record_assessment" in surfaces["security_readiness"]
    assert surfaces["security_readiness"]["record_assessment"] is None
    executor = ModuleExecutor()
    monkeypatch.setattr(executor, "_emit_dispatch_audit", AsyncMock())
    for item in loaded.modules:
        executor.register(item.name, item.handler, action_method_map=item.action_method_map,
                          action_permissions=item.action_permissions_map, action_schemas=item.action_schemas_map,
                          action_emits=item.action_emits_map, event_payload_schemas=item.event_payload_schemas_map)
    documents = []
    class Collection:
        async def update_one(self, query, update, *, upsert):
            documents.append(update["$set"])

        async def find_many(self, query, *, limit, sort):
            return [row for row in documents if all(row.get(key) == value for key, value in query.items())][:limit]

    class Persistence:
        app_id = "host-app"

        def collection(self, module_id, entity_name):
            assert (module_id, entity_name) == ("security_readiness", "findings")
            return Collection()

    authority = ModuleDispatchAuthority(kind="authenticated_user", permission_mode="enforce", reason="test user", actor_id="owner", permissions=("security_readiness.manage", "security_readiness.read"))
    def request(action, params):
        return ModuleRequest(module="security_readiness", action=action, params=params, app_id="host-app", user_id="owner", authority=authority)
    substrate = executor._build_persistence_context(request("list_findings", {"app_id": "host-app"}))
    assert substrate.app_id == "host-app"
    monkeypatch.setattr(executor, "_build_persistence_context", lambda request: Persistence())
    for project in ("project-one", "project-two"):
        saved = await executor.execute(request("record_assessment", {"app_id": "host-app", "build_registry_id": project, "findings": [{"finding_id": "same-scanner-key", "title": "Review auth", "severity": "high", "control_area": "auth"}]}))
        assert saved.success
        assert saved.data["saved"] == 1
    listed = await executor.execute(request("list_findings", {"app_id": "host-app", "build_registry_id": "project-one"}))
    assert listed.success
    assert listed.data["count"] == 1
    assert listed.data["findings"][0]["build_registry_id"] == "project-one"
    assert len({row["finding_id"] for row in documents}) == 2


@pytest.mark.asyncio
async def test_studio_bootstrap_scopes_defaults_to_the_running_host(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager, nullcontext

    from fastapi import FastAPI

    from mozaiksai.hosts import bootstrap

    defaults = app_root(tmp_path, "factory")
    observed = []
    @asynccontextmanager
    async def lifespan(app):
        observed.append(getattr(app.state, "module_defaults_path", None))
        yield

    host = FastAPI(lifespan=lifespan)
    monkeypatch.setattr(bootstrap, "configure_repo_host_defaults", lambda host: None)
    monkeypatch.setattr(bootstrap, "_workflow_catalog_bound_to_host_config", nullcontext)
    monkeypatch.setattr(bootstrap, "resolve_factory_app_root", lambda: defaults.parent)
    bootstrap.register_repo_host_bootstrap(host, "platform")
    assert not hasattr(host.state, "module_defaults_path")
    async with host.router.lifespan_context(host):
        assert observed[-1] is None
    bootstrap.register_repo_host_bootstrap(host, "studio")
    assert not hasattr(host.state, "module_defaults_path")
    async with host.router.lifespan_context(host):
        assert observed[-1] == str(defaults)
    assert not hasattr(host.state, "module_defaults_path")


def test_explicit_missing_module_default_root_fails(tmp_path):
    from mozaiksai.core.runtime.app.module_loader import ModuleLoadError

    with pytest.raises(ModuleLoadError, match="Module defaults app root not found"):
        ModuleLoader(str(tmp_path), module_defaults_path=str(tmp_path / "missing"))
