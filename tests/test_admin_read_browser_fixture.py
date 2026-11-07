"""Keep the rendered-browser fixture tied to real compilation and registry loading."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "admin_read_generation"
OUTPUT = ROOT / "web_shell" / "tests" / "fixtures" / "adminRead.json"


def build_browser_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    from mozaiksai.core.admin import router as admin_router
    from mozaiksai.core.auth import UserPrincipal
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader
    from mozaiksai.core.runtime.composition.module_executor import ModuleExecutor
    from mozaiksai.core.workflow.generator_support.page_plan_utils import (
        compile_authored_page_files,
    )
    from mozaiksai.hosts import shell_config

    files = {
        path.relative_to(SOURCE).as_posix(): path.read_text(encoding="utf-8")
        for path in SOURCE.rglob("*") if path.is_file() and "__pycache__" not in path.parts
    }
    files = compile_authored_page_files(files, payload={}, context={})
    panel_path = "modules/catalog/contracts/admin.yaml"
    panel = yaml.safe_load(files[panel_path])["panels"][0]
    config = panel["sections"][0]["config"]
    assert config["api_endpoint"] == "/api/admin/modules/catalog/list_inventory/read"
    assert "data_source" not in config
    assert config["actions"][0]["href"] == "/api/admin/modules/catalog/add_entry"
    assert config["actions"][1]["href"] == "/api/admin/modules/catalog/delete_entry"

    app_root = tmp_path / "app"
    for name, content in files.items():
        target = app_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    loaded = ModuleLoader(str(app_root)).load("catalog")
    executor = ModuleExecutor()
    executor.register_loaded_module(loaded)
    assert executor.is_read_only_action("catalog", "list_inventory")
    assert not executor.is_read_only_action("catalog", "add_entry")
    assert not executor.is_read_only_action("catalog", "delete_entry")

    # Select the temporary app; leave actual registry loading, route projection,
    # panel validation and module registration in the acceptance path.
    monkeypatch.setattr(shell_config, "resolve_active_app_root", lambda: app_root)
    monkeypatch.setattr(shell_config, "resolve_app_root", lambda: app_root)
    monkeypatch.setattr(admin_router, "_resolve_admin_app_root", lambda: app_root)
    monkeypatch.setenv("AUTH_ENABLED", "false")
    shell = asyncio.run(shell_config.build_shell_config(surface="platform"))
    route = next(page for page in shell["pages"] if page["path"] == "/admin/catalog")
    principal = UserPrincipal(
        user_id="fixture-admin", email=None, name=None, roles=["admin"], scopes=[],
        raw_claims={}, auth_provenance="token_validated",
    )
    admin_config = asyncio.run(admin_router.get_admin_config(user=principal))
    assert route["meta"]["adminPage"] == "catalog_inventory"
    assert admin_config["module_panels"][0]["page"] == "catalog_inventory"
    return {"route": route, "config": admin_config}


def test_browser_fixture_matches_generated_registry_and_loaded_module(tmp_path, monkeypatch):
    assert build_browser_fixture(tmp_path, monkeypatch) == json.loads(OUTPUT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory(prefix="mozaiks-admin-read-") as temporary, pytest.MonkeyPatch.context() as patch:
        value = build_browser_fixture(Path(temporary), patch)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(OUTPUT)
