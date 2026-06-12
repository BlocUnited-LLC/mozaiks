"""Route authorization metadata contract tests.

Documents current truth and the near-term contract:

- requiresAuth is enforced by the shell (RouteRenderer redirect).
- requiresRole can be declared in route metadata for navigation visibility intent.
- Page schema meta.roles normalizes to requiresRole in shell config.
- requiresRole is NOT yet enforced by RouteWrapper or sidebar filtering.
  That enforcement is a tracked follow-up.
- requiresPermission and resource-scoped authorization do not exist yet.
- Module service/policy is the authoritative boundary for fine-grained authorization.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _make_app_root(tmp_path: Path, *, pages: list[dict] | None = None, ui_pages: dict | None = None) -> Path:
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    ui_dir = app_root / "ui"
    ui_dir.mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Test App", "startup": {"landing_spot": "/home"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": "Chat"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "shell.json").write_text(json.dumps({}), encoding="utf-8")
    (ui_dir / "route_manifest.json").write_text(
        json.dumps({"pages": pages or []}),
        encoding="utf-8",
    )
    if ui_pages:
        pages_dir = ui_dir / "pages"
        pages_dir.mkdir(parents=True)
        for filename, content in ui_pages.items():
            (pages_dir / filename).write_text(
                yaml.dump(content, default_flow_style=False),
                encoding="utf-8",
            )
    return app_root


def _read(relative: str) -> str:
    return (_workspace() / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. AppCustomRouteMeta accepts requiresRole
# ---------------------------------------------------------------------------

def test_app_custom_route_meta_declares_requires_role() -> None:
    """AppCustomRouteMeta schema must include requiresRole as an optional string field."""
    so = _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    # Locate AppCustomRouteMeta block and confirm requiresRole is declared inside it
    assert "AppCustomRouteMeta:" in so
    meta_start = so.index("AppCustomRouteMeta:")
    # requiresRole must appear after the model declaration
    requires_role_pos = so.find("requiresRole:", meta_start)
    assert requires_role_pos != -1, "requiresRole must be declared in AppCustomRouteMeta"
    # Confirm it is positioned before the next top-level model definition
    next_model = so.find("\n  App", meta_start + len("AppCustomRouteMeta:"))
    assert requires_role_pos < next_model, (
        "requiresRole must be inside AppCustomRouteMeta, not a later model"
    )


def test_app_custom_route_meta_requires_role_is_nullable() -> None:
    """requiresRole in AppCustomRouteMeta must allow null (optional field)."""
    so = yaml.safe_load(_read("factory_app/workflows/AppGenerator/structured_outputs.yaml"))
    meta_model = so["models"]["AppCustomRouteMeta"]
    requires_role = meta_model["fields"]["requiresRole"]
    assert requires_role["type"] == "union", "requiresRole must be a union type"
    assert "null" in requires_role["variants"], "requiresRole must allow null"
    assert "str" in requires_role["variants"], "requiresRole must allow str"


def test_app_custom_route_entry_still_has_requires_auth() -> None:
    """AppCustomRouteEntry must retain requiresAuth as a required bool field."""
    so = yaml.safe_load(_read("factory_app/workflows/AppGenerator/structured_outputs.yaml"))
    entry = so["models"]["AppCustomRouteEntry"]
    assert "requiresAuth" in entry["fields"], "AppCustomRouteEntry must declare requiresAuth"
    assert entry["fields"]["requiresAuth"]["type"] == "bool"


# ---------------------------------------------------------------------------
# 2. meta.roles normalizes to requiresRole in _load_page_schema_routes
# ---------------------------------------------------------------------------

def test_page_schema_roles_normalizes_to_requires_role(monkeypatch, tmp_path: Path) -> None:
    """Page YAML with roles: [admin] must produce meta.requiresRole: 'admin' in shell config."""
    from mozaiksai.hosts import platform as platform_app

    app_root = _make_app_root(
        tmp_path,
        ui_pages={
            "reports.yaml": {
                "name": "reports",
                "title": "Reports",
                "route": "/reports",
                "roles": ["admin"],
            }
        },
    )
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/reports"]
    assert matching, "Reports page must appear in shell pages"
    meta = matching[0]["meta"]
    assert meta.get("requiresRole") == "admin", (
        "meta.roles: ['admin'] must normalize to meta.requiresRole: 'admin'"
    )
    assert "roles" not in meta, (
        "meta.roles list must not be present after normalization"
    )


def test_page_schema_multiple_roles_uses_first(monkeypatch, tmp_path: Path) -> None:
    """When roles has multiple values, the first entry becomes requiresRole."""
    from mozaiksai.hosts import platform as platform_app

    app_root = _make_app_root(
        tmp_path,
        ui_pages={
            "audit.yaml": {
                "name": "audit",
                "title": "Audit Log",
                "route": "/audit",
                "roles": ["admin", "auditor"],
            }
        },
    )
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/audit"]
    assert matching
    assert matching[0]["meta"].get("requiresRole") == "admin", (
        "First role in roles list must be used as requiresRole"
    )


def test_page_schema_without_roles_has_no_requires_role(monkeypatch, tmp_path: Path) -> None:
    """A page with no roles field must not have requiresRole set."""
    from mozaiksai.hosts import platform as platform_app

    app_root = _make_app_root(
        tmp_path,
        ui_pages={
            "projects.yaml": {
                "name": "projects",
                "title": "Projects",
                "route": "/projects",
            }
        },
    )
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/projects"]
    assert matching
    assert "requiresRole" not in matching[0]["meta"], (
        "Pages without roles must not have requiresRole in shell config"
    )


# ---------------------------------------------------------------------------
# 3. requiresRole on route manifest passes through shell config
# ---------------------------------------------------------------------------

def test_route_manifest_requires_role_passes_through(monkeypatch, tmp_path: Path) -> None:
    """requiresRole declared in route_manifest.json must survive into shell config pages."""
    from mozaiksai.hosts import platform as platform_app

    pages = [
        {
            "id": "support-cases",
            "label": "Support Cases",
            "path": "/support",
            "component": "SupportPage",
            "requiresAuth": True,
            "meta": {"requiresRole": "support_agent", "shellMode": "workspace"},
        }
    ]
    app_root = _make_app_root(tmp_path, pages=pages)
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/support"]
    assert matching, "Support page must appear in shell pages"
    assert matching[0]["meta"].get("requiresRole") == "support_agent", (
        "requiresRole from route_manifest must be preserved in shell config"
    )


def test_route_manifest_requires_auth_still_enforced(monkeypatch, tmp_path: Path) -> None:
    """requiresAuth from route_manifest must default to True and be present in shell config."""
    from mozaiksai.hosts import platform as platform_app

    pages = [
        {
            "id": "dashboard",
            "label": "Dashboard",
            "path": "/dashboard",
            "component": "DashboardPage",
        }
    ]
    app_root = _make_app_root(tmp_path, pages=pages)
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/dashboard"]
    assert matching
    assert matching[0]["meta"].get("requiresAuth") is True, (
        "requiresAuth must default to True when not explicitly set"
    )


# ---------------------------------------------------------------------------
# 4. requiresRole is NOT yet enforced by RouteWrapper (current truth)
# ---------------------------------------------------------------------------

def test_route_renderer_route_wrapper_does_not_check_requires_role() -> None:
    """RouteWrapper must only check requiresAuth, not requiresRole.

    This test documents the current state: requiresRole is declaration-only.
    When frontend enforcement lands (RouteWrapper + WorkspaceLayout), this test
    must be updated to reflect the new behavior.
    """
    source = _read("chat-ui/src/components/RouteRenderer.jsx")
    route_wrapper_start = source.index("const RouteWrapper")
    route_wrapper_end = source.index("\n};", route_wrapper_start) + 3
    route_wrapper_body = source[route_wrapper_start:route_wrapper_end]

    assert "meta.requiresAuth" in route_wrapper_body, (
        "RouteWrapper must check requiresAuth"
    )
    assert "requiresRole" not in route_wrapper_body, (
        "RouteWrapper must NOT check requiresRole yet — "
        "enforcement is a tracked follow-up; this field is declaration-only"
    )


# ---------------------------------------------------------------------------
# 5. requiresPermission and scope do not exist yet
# ---------------------------------------------------------------------------

def test_requires_permission_not_in_structured_outputs() -> None:
    """requiresPermission must not appear in AppCustomRouteMeta or AppCustomRouteEntry.

    This test guards against premature introduction before the contract is designed.
    """
    so = yaml.safe_load(_read("factory_app/workflows/AppGenerator/structured_outputs.yaml"))
    for model_name in ("AppCustomRouteMeta", "AppCustomRouteEntry"):
        fields = so["models"][model_name]["fields"]
        assert "requiresPermission" not in fields, (
            f"{model_name} must not declare requiresPermission yet — "
            "that contract is deferred to a future design pass"
        )


def test_route_manifest_meta_scope_auth_contract_not_introduced() -> None:
    """The scope/resource authorization contract must not be present in route metadata yet."""
    so = yaml.safe_load(_read("factory_app/workflows/AppGenerator/structured_outputs.yaml"))
    for model_name in ("AppCustomRouteMeta", "AppCustomRouteEntry"):
        fields = so["models"][model_name]["fields"]
        assert "scope" not in fields or model_name != "AppCustomRouteMeta", (
            "AppCustomRouteMeta must not declare a scope authorization field yet"
        )


# ---------------------------------------------------------------------------
# 6. Module policy is the authoritative authorization boundary (doc contract)
# ---------------------------------------------------------------------------

def test_app_custom_route_meta_requires_role_description_mentions_module_policy() -> None:
    """The requiresRole field description must mention module policy as the enforcement boundary."""
    so = _read("factory_app/workflows/AppGenerator/structured_outputs.yaml")
    meta_start = so.index("AppCustomRouteMeta:")
    next_model = so.find("\n  App", meta_start + len("AppCustomRouteMeta:"))
    meta_block = so[meta_start:next_model]
    assert "module" in meta_block.lower(), (
        "requiresRole description must reference module policy as the authoritative boundary"
    )

