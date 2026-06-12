from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
STRUCTURED_OUTPUTS = ROOT / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml"
PLATFORM_SOURCE = ROOT / "mozaiksai" / "hosts" / "platform.py"
ROUTE_RENDERER = ROOT / "chat-ui" / "src" / "components" / "RouteRenderer.jsx"
WORKSPACE_LAYOUT = ROOT / "chat-ui" / "src" / "workspace" / "WorkspaceLayout.jsx"


def _load_structured_outputs() -> dict:
    return yaml.safe_load(STRUCTURED_OUTPUTS.read_text(encoding="utf-8")) or {}


def _make_app_root(
    tmp_path: Path,
    *,
    route_manifest_pages: list[dict] | None = None,
    page_schemas: list[tuple[str, dict]] | None = None,
) -> Path:
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "ui" / "pages").mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Test App", "startup": {"landing_spot": "/home"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": "Chat"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "shell.json").write_text(json.dumps({}), encoding="utf-8")
    (app_root / "ui" / "route_manifest.json").write_text(
        json.dumps({"pages": route_manifest_pages or []}),
        encoding="utf-8",
    )
    for page_name, page_schema in page_schemas or []:
        (app_root / "ui" / "pages" / f"{page_name}.yaml").write_text(
            yaml.safe_dump(page_schema, sort_keys=False),
            encoding="utf-8",
        )
    return app_root


def test_appcustomroutemeta_accepts_requiresrole() -> None:
    models = _load_structured_outputs()["models"]
    route_meta = models["AppCustomRouteMeta"]["fields"]

    assert "requiresRole" in route_meta
    assert route_meta["requiresRole"]["type"] == "union"
    assert route_meta["requiresRole"]["variants"] == ["str", "null"]
    description = route_meta["requiresRole"]["description"].lower()
    assert "declaration" in description
    assert "module authorization" in description


def test_route_schema_does_not_introduce_requirespermission() -> None:
    models = _load_structured_outputs()["models"]
    route_meta = models["AppCustomRouteMeta"]["fields"]

    assert "requiresPermission" not in route_meta


def test_load_page_schema_routes_normalizes_meta_roles_to_requiresrole(tmp_path: Path) -> None:
    from mozaiksai.hosts import platform as platform_app

    app_root = _make_app_root(
        tmp_path,
        page_schemas=[
            (
                "ReviewQueue",
                {
                    "name": "ReviewQueue",
                    "route": "/review",
                    "title": "Review Queue",
                    "meta": {"roles": ["admin"]},
                    "sections": [],
                },
            )
        ],
    )

    pages = platform_app._load_page_schema_routes(app_root)

    assert len(pages) == 1
    assert pages[0]["path"] == "/review"
    assert pages[0]["meta"]["requiresRole"] == "admin"
    assert pages[0]["meta"]["requiresAuth"] is True
    assert "roles" not in pages[0]["meta"]


def test_build_shell_config_preserves_requiresrole_route_metadata(
    monkeypatch, tmp_path: Path
) -> None:
    from mozaiksai.hosts import platform as platform_app

    app_root = _make_app_root(
        tmp_path,
        route_manifest_pages=[
            {
                "id": "workspace-studio",
                "label": "Apps",
                "path": "/apps",
                "component": "StudioPage",
                "order": 1,
                "navigation": {"scope": "local", "group": "workspace-studio", "icon": "apps", "order": 0},
                "meta": {"requiresAuth": True, "requiresRole": "admin"},
            }
        ],
    )
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [page for page in shell["pages"] if page["path"] == "/apps"]
    assert matching
    assert matching[0]["meta"]["requiresRole"] == "admin"


def test_build_shell_config_normalizes_page_schema_roles_in_shell_output(
    monkeypatch, tmp_path: Path
) -> None:
    from mozaiksai.hosts import platform as platform_app

    app_root = _make_app_root(
        tmp_path,
        page_schemas=[
            (
                "AdminOnly",
                {
                    "name": "AdminOnly",
                    "route": "/admin-only",
                    "title": "Admin Only",
                    "meta": {"roles": ["admin"]},
                    "sections": [],
                },
            )
        ],
    )
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [page for page in shell["pages"] if page["path"] == "/admin-only"]
    assert matching
    assert matching[0]["meta"]["requiresRole"] == "admin"
    assert "roles" not in matching[0]["meta"]


def test_route_renderer_enforces_requiresauth_only_today() -> None:
    source = ROUTE_RENDERER.read_text(encoding="utf-8")

    assert "meta.requiresAuth && !isAuthenticated" in source
    assert "requiresRole" not in source


def test_workspace_layout_does_not_filter_sidebar_by_role_yet() -> None:
    source = WORKSPACE_LAYOUT.read_text(encoding="utf-8")

    assert "requiresRole" not in source


def test_platform_comments_distinguish_route_metadata_from_module_authorization() -> None:
    source = PLATFORM_SOURCE.read_text(encoding="utf-8").lower()

    assert "declaration and visibility intent only" in source
    assert "module policy remains the" in source
    assert "authoritative security boundary" in source
