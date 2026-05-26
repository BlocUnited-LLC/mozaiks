from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _load_module(module_name: str, relative_path: str):
    workspace = Path(__file__).resolve().parents[1]
    file_path = workspace / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


admin_paths = _load_module("tests.admin_paths", "mozaiksai/core/admin/paths.py")
email_promotion = _load_module("tests.admin_email_promotion", "mozaiksai/core/admin/email_promotion.py")


def test_admin_paths_resolve_app_root_from_direct_app_root(monkeypatch, tmp_path: Path) -> None:
    app_root = tmp_path / "custom-app"
    _write_json(app_root / "app.json", {"appName": "Custom App"})
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))

    assert admin_paths.resolve_admin_app_root() == app_root.resolve()


def test_admin_paths_resolve_app_root_from_workspace_root(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    app_root = workspace_root / "app"
    _write_json(app_root / "app.json", {"appName": "Workspace App"})
    monkeypatch.setenv("PLATFORM_PATH", str(workspace_root))

    assert admin_paths.resolve_admin_app_root() == app_root.resolve()


def test_admin_paths_resolve_app_root_from_workspace_env(monkeypatch, tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace-from-env"
    app_root = workspace_root / "app"
    _write_json(app_root / "app.json", {"appName": "Workspace Env App"})
    monkeypatch.delenv("PLATFORM_PATH", raising=False)
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace_root))

    assert admin_paths.resolve_admin_app_root() == app_root.resolve()


def test_email_promotion_reads_active_app_admins(monkeypatch, tmp_path: Path) -> None:
    app_root = tmp_path / "standalone-app"
    _write_json(app_root / "app.json", {"appName": "Standalone", "admins": ["Owner@Example.com", "ops@example.com"]})
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))

    assert email_promotion.get_admin_emails() == ["owner@example.com", "ops@example.com"]
    assert email_promotion.is_admin_by_email("owner@example.com") is True
    assert email_promotion.is_admin_by_email("OWNER@example.com") is True
    assert email_promotion.is_admin_by_email("missing@example.com") is False
