"""Auth route composition across app schema, auth scaffold and validation."""

import json

import pytest

from factory_app.workflows.AppGenerator.tools import save_app_schema as schema_tool
from factory_app.workflows.AppGenerator.tools.code_file_utils import (
    collect_generated_app_file_map,
    compose_bundle_auth_routes,
)
from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _scan_auth_app_contract,
    _scan_route_manifest_component_files,
)
from factory_app.workflows.AppGenerator.tools.render_infra_scaffold import save_infra_scaffold
from mozaiksai.core.runtime.app.auth_contract import AppAuthContractError
from mozaiksai.core.runtime.app.loader import AppLoader


@pytest.fixture
async def generated_auth_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(schema_tool, "_resolve_output_dir", lambda **_: tmp_path)
    schema_tool.save_app_schema(
        manifest={
            "app_name": "Auth route proof", "version": "1.0.0", "default_route": "/home",
            "pages": ["home"], "custom_routes": [], "auth_strategy": "oidc",
        },
        pages=[{
            "schema_version": "mozaiks.app_page.v1", "name": "home", "route": "/home",
            "title": "Home", "page_type": "record_list", "layout": "grid",
            "sections": [{"id": "empty", "primitive": "Empty", "config": {}}],
        }],
    )
    files = collect_generated_app_file_map(tmp_path)
    scaffold = await save_infra_scaffold(
        emit_infra=False, emit_auth_adapter=True, context_variables={"default_route": "/home"},
    )
    files.update({entry["filename"]: entry["content"] for entry in scaffold["code_files"]})
    return files, tmp_path


@pytest.mark.asyncio
async def test_app_schema_and_auth_scaffold_compose_public_routes_and_load(generated_auth_bundle):
    files, root = generated_auth_bundle
    assert _scan_auth_app_contract(files)  # Auth declaration alone is insufficient.
    compose_bundle_auth_routes(files)
    first = files["ui/route_manifest.json"]
    compose_bundle_auth_routes(files)
    assert files["ui/route_manifest.json"] == first
    assert _scan_auth_app_contract(files) == []
    assert _scan_route_manifest_component_files(files) == []
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    loaded = await AppLoader.load(str(root))
    assert loaded.auth_contract.routes.post_login_default == "/home"
    pages = json.loads(first)["pages"]
    assert {page["path"] for page in pages} == {"/login", "/auth/callback"}
    assert all(page["meta"]["requiresAuth"] is False for page in pages)
    assert (root / "ui/pages/home.yaml").exists()


@pytest.mark.asyncio
async def test_existing_custom_public_auth_routes_are_preserved(generated_auth_bundle):
    files, _ = generated_auth_bundle
    pages = [
        {"path": "/login", "component": "ProductLoginPage", "meta": {"requiresAuth": False, "appShell": False}},
        {"path": "/auth/callback", "component": "ProductCallbackPage", "meta": {"requiresAuth": False}},
        {"path": "/reports", "component": "ReportsPage", "meta": {"requiresAuth": True}},
    ]
    files["ui/route_manifest.json"] = json.dumps({"pages": pages})
    compose_bundle_auth_routes(files)
    assert json.loads(files["ui/route_manifest.json"])["pages"] == pages
    assert _scan_auth_app_contract(files, require_generated_adapter=False) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["missing", "protected", "duplicate", "role", "roles", "requiredRole", "authorization", "empty_component"])
async def test_auth_route_scanner_rejects_broken_bindings(generated_auth_bundle, defect):
    files, _ = generated_auth_bundle
    compose_bundle_auth_routes(files)
    pages = json.loads(files["ui/route_manifest.json"])["pages"]
    if defect == "missing":
        pages.pop(0)
    elif defect == "duplicate":
        pages.append(dict(pages[0]))
    elif defect == "protected":
        pages[0]["meta"]["requiresAuth"] = True
    elif defect == "role":
        pages[0]["meta"]["requiresRole"] = "admin"
    elif defect in {"roles", "requiredRole"}:
        pages[0]["meta"][defect] = ["admin"]
    elif defect == "authorization":
        pages[0]["meta"]["routeAuth"] = {"module": "access", "action": "check"}
    else:
        pages[0]["component"] = ""
    files["ui/route_manifest.json"] = json.dumps({"pages": pages})
    assert _scan_auth_app_contract(files, require_generated_adapter=False)
    if defect != "missing":
        with pytest.raises(AppAuthContractError):
            compose_bundle_auth_routes(files)


def test_public_bundle_keeps_its_own_routes_without_auth_generation():
    files = {"app.json": '{"appName":"Public","authRequired":false}', "ui/route_manifest.json": '{"pages":[]}'}
    expected = dict(files)
    compose_bundle_auth_routes(files)
    assert files == expected
    assert _scan_auth_app_contract(files) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["config/auth.yaml", "app.json", "ui/route_manifest.json"])
async def test_auth_composition_parser_errors_do_not_echo_input(generated_auth_bundle, path):
    files, _ = generated_auth_bundle
    files[path] = 'private-input: ["unterminated'
    with pytest.raises(AppAuthContractError) as caught:
        compose_bundle_auth_routes(files)
    assert "private-input" not in str(caught.value)
