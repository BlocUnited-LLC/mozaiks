"""Content pack assembly, app-owned presentation and trusted-fixture HTTP proof.

No model, browser, real MongoDB or external provider runs here. The real
AppLoader, module executor, persistence scoping and HTTP routers consume the
assembled files; only the Mongo transport, audit sink and caller identities are
local fixtures. This does not qualify contained or end-to-end generation.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    verify_pack_integrity,
)
from factory_app.workflows.AppGenerator.tools.validate_wiring import validate_wiring
from mozaiksai.core.auth import UserPrincipal, optional_user
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.runtime.composition.executor_registry import ExecutorRegistry
from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
from mozaiksai.core.runtime.composition.platform_hooks import PlatformHookRegistry
from mozaiksai.core.runtime.persistence import collection_name_for
from mozaiksai.core.validation import scan_functional_generated_app
from mozaiksai.core.workflow.context.adapter import create_context_container
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
    load_module_smoke_fixtures,
)
from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    module_action_index,
    pack_template_page_errors,
)
from mozaiksai.hosts.routers import modules, shell
from tests.content_smoke_fixtures import content_smoke_fixtures
from tests.factory_context import factory_context, planned_tasks
from tests.test_appgenerator_revision_baseline import _replace_archive
from tests.test_appgenerator_revision_baseline import baseline as revision_baseline  # noqa: F401
from tests.test_materialized_bundle_production_runtime import (
    _restore_global_runtime_state as restore_runtime_state,  # noqa: F401
)
from tests.test_runtime_database_migrations import FakeCursor

PACK = Path(__file__).resolve().parents[1] / "factory_app/build_context/content"
PAGE = "ui/pages/custom/Portfolio.jsx"
APP_ID = "customer-app"
EDITOR_ROLE = "content_editor"
FIXTURES = "modules/content/contracts/fixtures.yaml"
APPROVED_DATA = {"version": "1", "surfaces": [], "shared_collections": []}
THEMES = {
    "editorial": {"theme": {"primary": "emerald", "font": "system", "font_heading": "system", "appearance": "light", "density": "spacious", "radius": "small"}},
    "catalogue": {"theme": {"primary": "rose", "font": "system", "font_heading": "system", "appearance": "dark", "density": "compact", "radius": "none"}},
}


def _presentation(variant):
    listing = (
        '<section aria-label="Selected work">{entries.map(entry => <article key={entry.entry_id}><h2>{entry.title}</h2><p>{entry.summary}</p></article>)}</section>'
        if variant == "editorial" else
        '<ul aria-label="Project catalogue">{entries.map(entry => <li key={entry.entry_id}><strong>{entry.title}</strong><span>{entry.summary}</span></li>)}</ul>'
    )
    return '''import React, { useEffect, useState } from "react";
export default function Portfolio() {
  const [entries, setEntries] = useState([]);
  const [error, setError] = useState("");
  useEffect(() => {
    fetch("/api/modules/content/list_entries", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({params: {}})
    }).then(response => { if (!response.ok) throw new Error("Content unavailable"); return response.json(); })
      .then(result => setEntries(result.entries)).catch(error => setError(error.message));
  }, []);
  return <main><h1>Selected projects</h1>{error && <p role="alert">{error}</p>}
    ''' + listing + '''
    {!error && entries.length === 0 && <p>No published entries yet.</p>}
  </main>;
}
'''


def _selection():
    identity = verify_pack_integrity(PACK, "content")
    return {"id": "content", "capability_source": "generated_module", "pack_source_path": str(PACK),
            "version": identity["version"], "digest": identity["digest"]}


def _entries_page():
    return {"schema_version": "mozaiks.app_page.v1", "name": "entries", "title": "Published entries",
            "route": "/entries", "page_type": "record_list", "layout": "full-width", "sections": [{
                "id": "entries", "primitive": "DataTable", "config": {
                    "api_endpoint": "/api/modules/content/list_entries", "data_key": "entries",
                    "columns": [{"key": key, "label": key.title()} for key in ("title", "summary", "status")],
                },
            }]}


def _plan():
    return {"capability_packs": [{"capability_pack_id": "content", "capability_source": "generated_module"}],
            "pages": [{"name": "portfolio", "route": "/", "ui_surface": "custom_react_page"},
                      {"name": "entries", "route": "/entries", "ui_surface": "declarative_page"}]}


def _context(variant):
    auth = yaml.safe_load((PACK.parent / "webapp_builder/templates/config/auth.yaml").read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/"))
    auth["authorization"] = {"roles": [{"id": EDITOR_ROLE, "permissions": ["content.manage"]}]}
    plan = _plan()
    plan["build_tasks"] = [{"task_id": "content_smoke", "task_type": "module_contract", "owned_paths": [FIXTURES]}]
    return create_context_container(initial=factory_context({
        "app_id": APP_ID, "capability_packs": [_selection()], "app_build_plan": plan,
        "data_contract": deepcopy(APPROVED_DATA), "captured_theme_config": deepcopy(THEMES[variant]),
        "app_task_batch_items": planned_tasks("content_smoke"),
        "app_task_batch_results": {"content_smoke": {"code_files": [{"filename": FIXTURES, "content": content_smoke_fixtures(EDITOR_ROLE)}]}},
        "build_timestamp": "2026-11-13T00:00:00+00:00", "generated_files": {
            "app.json": json.dumps({"appId": APP_ID, "appName": "Selected projects", "authRequired": True}),
            "config/auth.yaml": yaml.safe_dump(auth, sort_keys=False),
            "data/contract.json": json.dumps(APPROVED_DATA), PAGE: _presentation(variant),
            "ui/pages/entries.yaml": yaml.safe_dump(_entries_page()),
            "ui/index.js": 'import Portfolio from "./pages/custom/Portfolio.jsx";\n'
                           'export function register(registerComponent) { registerComponent("Portfolio", Portfolio); }\n',
            "ui/route_manifest.json": json.dumps({"pages": [
                {"id": "portfolio", "path": "/", "component": "Portfolio", "meta": {"requiresAuth": False}},
                {"id": "entries", "path": "/entries", "component": "SchemaPage", "schema": "entries"},
            ]}),
        },
    }))


async def _assemble(context):
    result = await assemble_app_tasks(context_variables=context)
    assert result["success"], result
    return {file["filename"]: file["content"] for file in result["code_files"]}


def _scan(files, context):
    return scan_generated_bundle(files, capability_packs=detach(context.get("capability_packs")),
                                 planned_data_contract=APPROVED_DATA, context_variables=context)


class _ReadCollection:
    """Mongo wire double; the real persistence adapter adds the app filter."""

    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.queries = []

    def find(self, query, projection=None, **options):
        self.queries.append(dict(query))
        assert not any(isinstance(value, dict) for value in query.values()), query
        return FakeCursor([deepcopy(row) for row in self.rows if all(row.get(key) == value for key, value in query.items())])

    async def find_one(self, query, projection=None, **options):
        rows = await self.find(query, projection, **options).to_list(length=1)
        return rows[0] if rows else None


def _record(index, status, *, app_id=APP_ID):
    return {"app_id": app_id, "entry_id": f"ent_{index:032x}", "title": f"Entry {index}", "slug": f"entry-{index}",
            "summary": f"Summary {index}", "body": f"Body {index}", "status": status,
            "created_at": "2026-11-13T00:00:00+00:00", "updated_at": "2026-11-13T00:00:00+00:00"}


def _http_app(loaded, collection):
    hooks = PlatformHookRegistry()
    name = collection_name_for(app_id=APP_ID, module_id="content", entity_name="entries")
    executor = ModuleExecutor(data_contract=loaded.data_contract, platform_hooks=hooks,
                              audit_logger=SimpleNamespace(log_module_action=AsyncMock()),
                              persistence_database="starter-proof", persistence_client={"starter-proof": {name: collection}})
    for module in loaded.modules:
        executor.register_loaded_module(module)
    registry = ExecutorRegistry()
    registry.register(executor)
    app = FastAPI()
    app.include_router(modules.router)
    app.include_router(shell.router)
    app.state.executor_registry = registry
    app.state.loaded_app_id = APP_ID
    app.state.auth_contract = loaded.auth_contract
    app.state.module_action_surfaces = {module.name: module.action_api_surface_map for module in loaded.modules}
    app.state.page_schemas = {name: schema.model_dump(mode="json", exclude_none=True) for name, schema in loaded.page_schemas.items()}
    app.dependency_overrides[optional_user] = lambda: None
    app.dependency_overrides[modules.module_dispatch_environment] = lambda: modules.ModuleDispatchEnvironment(
        authentication_enabled=True, platform_hooks=hooks, record_invocation=lambda **kwargs: None,
        persistence_principal=lambda principal: None,
    )
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("variant", THEMES)
async def test_content_starter_materializes_and_serves_public_content(variant, tmp_path, monkeypatch):
    context = _context(variant)
    original_selection = detach(context.get("capability_packs"))
    files = await _assemble(context)
    assert await _assemble(_context(variant)) == files
    assert files[PAGE] == _presentation(variant)
    assert json.loads(files["brand/theme_config.json"]) == THEMES[variant]
    assert detach(context.get("captured_theme_config")) == THEMES[variant]
    assert detach(context.get("data_contract")) == APPROVED_DATA
    assert detach(context.get("capability_packs")) == original_selection
    assert _scan(files, context) == []
    fixtures = load_module_smoke_fixtures(files)
    assert set(fixtures) == {("content", action) for action in ("create_entry", "update_entry", "archive_entry")}
    assert all(fixture.roles == [EDITOR_ROLE] for fixture in fixtures.values())
    assert files[FIXTURES] == content_smoke_fixtures(EDITOR_ROLE)
    auth = yaml.safe_load(files["config/auth.yaml"])
    assert auth["authorization"]["roles"] == [{"id": EDITOR_ROLE, "permissions": ["content.manage"]}]
    assert "content.manage" not in auth["frontend"]["default_scopes"]
    with pytest.raises(ValueError, match="unknown role"):
        load_module_smoke_fixtures({**files, FIXTURES: content_smoke_fixtures("unapproved_editor")})
    assert scan_functional_generated_app(files, capability_packs=original_selection) == []
    assert (await validate_wiring({"generated_files": files}))["passed"]
    assert pack_template_page_errors(files["ui/pages/entries.yaml"], path="ui/pages/entries.yaml",
                                     modules=module_action_index(files), workflow_names=set()) == []
    provenance = json.loads(files[".mozaiks/pack_provenance.json"])
    assert [(row["pack_id"], row["version"], row["digest"]) for row in provenance["packs"]] == [
        ("content", original_selection[0]["version"], original_selection[0]["digest"]),
    ]
    assert {row["path"] for row in provenance["packs"][0]["materialized_owned_files"]}.isdisjoint({PAGE, "brand/theme_config.json"})
    app_root = tmp_path / "app"
    for relative, text in files.items():
        path = app_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    loaded = await AppLoader.load(str(app_root))
    assert loaded.failed_module_names == []
    assert [module.name for module in loaded.modules] == ["content"]
    assert [collection["name"] for collection in loaded.data_contract["surfaces"][0]["collections"]] == ["entries"]
    collection = _ReadCollection([_record(1, "published"), _record(2, "draft"), _record(3, "archived"),
                                  _record(4, "published", app_id="different-app")])
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(app_root))
    monkeypatch.setenv("MOZAIKS_USAGE_METRICS", "false")
    app = _http_app(loaded, collection)
    with TestClient(app) as client:
        assert client.get("/api/pages/entries").json()["sections"][0]["config"]["data_key"] == "entries"
        assert client.get("/api/theme-config").json() == THEMES[variant]
        listed = client.post("/api/modules/content/list_entries", json={"params": {}})
        assert listed.status_code == 200, listed.text
        assert [entry["entry_id"] for entry in listed.json()["entries"]] == [_record(1, "published")["entry_id"]]
        assert listed.json()["count"] == 1 and listed.json()["next_cursor"] is None
        visible = client.post("/api/modules/content/get_entry", json={"params": {"entry_id": _record(1, "published")["entry_id"]}})
        assert visible.status_code == 200 and visible.json()["entry"]["title"] == "Entry 1"
        hidden = client.post("/api/modules/content/get_entry", json={"params": {"entry_id": _record(2, "draft")["entry_id"]}})
        assert hidden.status_code == 404 and "Body 2" not in hidden.text
        denied = client.post("/api/modules/content/create_entry", json={"params": {"title": "Untrusted", "slug": "untrusted"}})
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"]["error_code"] == "PERMISSION_DENIED"
        manager = UserPrincipal(user_id="editor", email=None, name=None, roles=[EDITOR_ROLE], scopes=[],
                                raw_claims={}, app_id=APP_ID, auth_provenance="token_validated")
        app.dependency_overrides[optional_user] = lambda: manager
        draft = client.post("/api/modules/content/get_entry", json={"params": {"entry_id": _record(2, "draft")["entry_id"]}})
        assert draft.status_code == 200 and draft.json()["entry"]["status"] == "draft", draft.text
    assert collection.queries and all(query["app_id"] == APP_ID for query in collection.queries)
    assert len(collection.rows) == 4


@pytest.mark.asyncio
async def test_content_starter_scoped_ui_revision_preserves_backend_and_branding(request):
    saved = request.getfixturevalue("revision_baseline")
    original_context = _context("editorial")
    original = await _assemble(original_context)
    saved.files = original
    _replace_archive(saved, {})
    revised_page = original[PAGE].replace("Selected projects", "Research and selected projects")
    plan = _plan()
    plan["build_tasks"] = [{"task_id": "portfolio", "task_type": "custom_route_bundle", "owned_paths": [PAGE]}]
    context = create_context_container(initial={
        **saved.context, "app_build_plan": plan, "capability_packs": [_selection()],
        "data_contract": deepcopy(APPROVED_DATA), "build_timestamp": "2026-11-13T00:00:00+00:00",
        "app_task_batch_items": planned_tasks("portfolio"),
        "app_task_batch_results": {"portfolio": {"code_files": [{"filename": PAGE, "content": revised_page}]}},
    })
    refined = await _assemble(context)
    assert saved.content.get_bundle.await_count > 0
    assert all(call.args == ("immutable-archive",) for call in saved.content.get_bundle.await_args_list)
    assert refined[PAGE] == revised_page
    assert refined["brand/theme_config.json"] == original["brand/theme_config.json"]
    assert {path: text for path, text in refined.items() if path.startswith("modules/content/")} == {
        path: text for path, text in original.items() if path.startswith("modules/content/")
    }
    assert refined["data/contract.json"] == original["data/contract.json"]
    assert refined["config/auth.yaml"] == original["config/auth.yaml"]
    assert refined[FIXTURES] == original[FIXTURES]
    for path in ("ui/pages/entries.yaml", "ui/route_manifest.json", "ui/index.js"):
        assert refined[path] == original[path]
    assert _scan(refined, context) == []
    assert scan_functional_generated_app(refined, capability_packs=[_selection()]) == []
