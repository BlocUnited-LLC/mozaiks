"""Tests for P0 platform shell fixes.

Fix 1 – vite.config.js isProductUiJs regex (static source check)
Fix 2 – appShell auto-inference when navigation.group is present
Fix 3 – _inject_admin_portal guarantee in build_shell_config
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Fix 1 – vite.config.js: isProductUiJs covers non-*-platform workspace dirs
# ---------------------------------------------------------------------------


def test_vite_config_is_product_ui_js_uses_platform_path_prefix() -> None:
    """isProductUiJs must accept arbitrary workspace dir names, not only *-platform."""
    root = Path(__file__).resolve().parents[1]
    vite_config = (root / "web_shell" / "vite.config.js").read_text(encoding="utf-8")

    # The fix introduced a startsWith check on platformAppDirForward.
    # Confirm it is present alongside the directory-name regex path.
    assert "platformAppDirForward" in vite_config, (
        "vite.config.js must define platformAppDirForward to support non-*-platform workspace dirs"
    )
    assert "startsWith(platformAppDirForward" in vite_config, (
        "vite.config.js isProductUiJs must use startsWith(platformAppDirForward) "
        "so workspaces like 'mozaiks-app' are transformed as JSX"
    )


# ---------------------------------------------------------------------------
# Fix 2 – appShell auto-inference from navigation.group
# ---------------------------------------------------------------------------


def _make_app_root(tmp_path: Path, pages: list[dict]) -> Path:
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "ui").mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Test App", "startup": {"landing_spot": "/home"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": "Chat"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "shell.json").write_text(
        json.dumps({}),
        encoding="utf-8",
    )
    (app_root / "ui" / "route_manifest.json").write_text(
        json.dumps({"pages": pages}),
        encoding="utf-8",
    )
    return app_root


def test_appshell_auto_inferred_when_navigation_group_present(monkeypatch, tmp_path: Path) -> None:
    """A route manifest entry with navigation.group must have appShell inferred as True."""
    from mozaiksai.hosts import platform as platform_app

    pages = [
        {
            "id": "workspace-studio",
            "label": "Apps",
            "path": "/apps",
            "component": "StudioPage",
            "order": 1,
            "navigation": {
                "scope": "local",
                "group": "workspace-studio",
                "icon": "apps",
                "order": 0,
            },
            "meta": {"requiresAuth": True, "requiresRole": "admin"},
        }
    ]
    app_root = _make_app_root(tmp_path, pages)
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/apps"]
    assert matching, "Route /apps should appear in shell pages"
    assert matching[0]["meta"].get("appShell") is True, (
        "appShell must be auto-inferred as True when navigation.group is set"
    )


def test_appshell_not_inferred_when_navigation_group_absent(monkeypatch, tmp_path: Path) -> None:
    """A route with no navigation.group must not have appShell auto-set."""
    from mozaiksai.hosts import platform as platform_app

    pages = [
        {
            "id": "login",
            "label": "Sign In",
            "path": "/login",
            "component": "LoginPage",
            "order": 0,
            "meta": {"requiresAuth": False},
        }
    ]
    app_root = _make_app_root(tmp_path, pages)
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/login"]
    assert matching, "Route /login should appear in shell pages"
    # appShell should not be set at all (or explicitly False); it must not be True
    assert matching[0]["meta"].get("appShell") is not True, (
        "appShell must not be auto-inferred when navigation.group is absent"
    )


def test_explicit_appshell_false_not_overridden(monkeypatch, tmp_path: Path) -> None:
    """An explicit appShell: false must not be overridden even if navigation.group is set."""
    from mozaiksai.hosts import platform as platform_app

    pages = [
        {
            "id": "opt-out",
            "label": "Opt Out",
            "path": "/opt-out",
            "component": "OptOutPage",
            "order": 5,
            "navigation": {
                "scope": "local",
                "group": "workspace-studio",
            },
            "meta": {"requiresAuth": True, "appShell": False},
        }
    ]
    app_root = _make_app_root(tmp_path, pages)
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    matching = [p for p in shell["pages"] if p["path"] == "/opt-out"]
    assert matching, "Route /opt-out should appear in shell pages"
    # setdefault must not replace an explicit False
    assert matching[0]["meta"].get("appShell") is False, (
        "Explicit appShell: false must not be overridden by the auto-inference"
    )


# ---------------------------------------------------------------------------
# Fix 3 – _inject_admin_portal guarantee
# ---------------------------------------------------------------------------


def test_inject_admin_portal_inserts_before_signout() -> None:
    from mozaiksai.hosts.platform import _inject_admin_portal

    result: dict = {
        "profile": {
            "show": True,
            "menu": [
                {"id": "profile", "action": "navigate", "path": "/profile"},
                {"id": "signout", "action": "signout"},
            ],
        }
    }
    _inject_admin_portal(result)

    ids = [item["id"] for item in result["profile"]["menu"]]
    assert ids == ["profile", "admin-portal", "signout"], (
        "admin-portal must be inserted immediately before signout"
    )


def test_inject_admin_portal_appends_when_no_signout() -> None:
    from mozaiksai.hosts.platform import _inject_admin_portal

    result: dict = {
        "profile": {
            "show": True,
            "menu": [
                {"id": "profile", "action": "navigate", "path": "/profile"},
            ],
        }
    }
    _inject_admin_portal(result)

    ids = [item["id"] for item in result["profile"]["menu"]]
    assert "admin-portal" in ids, "admin-portal must be appended when no signout is present"
    assert ids[-1] == "admin-portal", "admin-portal should be last when signout is absent"


def test_inject_admin_portal_is_idempotent() -> None:
    from mozaiksai.hosts.platform import _inject_admin_portal

    result: dict = {
        "profile": {
            "show": True,
            "menu": [
                {"id": "profile", "action": "navigate", "path": "/profile"},
                {"id": "signout", "action": "signout"},
            ],
        }
    }
    _inject_admin_portal(result)
    _inject_admin_portal(result)

    ids = [item["id"] for item in result["profile"]["menu"]]
    assert ids.count("admin-portal") == 1, "admin-portal must not be duplicated on repeated calls"


def test_inject_admin_portal_creates_profile_when_missing() -> None:
    from mozaiksai.hosts.platform import _inject_admin_portal

    result: dict = {}
    _inject_admin_portal(result)

    assert "profile" in result, "_inject_admin_portal must create profile when absent"
    ids = [item.get("id") for item in result["profile"]["menu"]]
    assert "admin-portal" in ids


def test_build_shell_config_always_injects_admin_portal(monkeypatch, tmp_path: Path) -> None:
    """build_shell_config must inject admin-portal even with no shortcuts configured."""
    from mozaiksai.hosts import platform as platform_app

    # Minimal app with no shell.json shortcuts at all
    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "ui").mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Minimal App", "startup": {"landing_spot": "/home"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": "Chat"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "shell.json").write_text(json.dumps({}), encoding="utf-8")
    (app_root / "ui" / "route_manifest.json").write_text(
        json.dumps({"pages": []}), encoding="utf-8"
    )
    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    profile = shell.get("profile", {})
    menu = profile.get("menu", [])
    ids = [item.get("id") for item in menu if isinstance(item, dict)]
    assert "admin-portal" in ids, (
        "admin-portal must appear in profile menu even when no shortcuts are configured"
    )
