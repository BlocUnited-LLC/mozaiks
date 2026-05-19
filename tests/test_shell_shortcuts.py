from __future__ import annotations

import asyncio
import json
from pathlib import Path


def test_platform_shell_config_expands_shortcuts(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.hosts import platform as platform_app

    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "ui").mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Shortcut App", "startup": {"landing_spot": "/dashboard"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "ai.json").write_text(
        json.dumps({"chat": {"chat_startup_mode": "ask"}, "workflows": {"entry_point": "Chat"}}),
        encoding="utf-8",
    )
    (app_root / "ui" / "route_manifest.json").write_text(
        json.dumps(
            {
                "pages": [
                    {
                        "id": "dashboard",
                        "label": "Dashboard",
                        "path": "/dashboard",
                        "component": "DashboardPage",
                        "order": 1,
                        "meta": {"requiresAuth": True},
                    },
                    {
                        "id": "wallet",
                        "label": "Wallet",
                        "path": "/wallet",
                        "component": "WalletPage",
                        "order": 2,
                        "meta": {"requiresAuth": True},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    (app_root / "config" / "shell.json").write_text(
        json.dumps(
            {
                "header": {"logo": {"href": "/dashboard"}},
                "shortcuts": {
                    "header": ["dashboard", "wallet"],
                    "profile": ["profile", "wallet", "signout"],
                    "mobile": ["dashboard", "wallet", "profile"],
                    "footer": ["legal", "terms"],
                    "footerHideOnMobile": True,
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    assert shell["header"]["logo"]["href"] == "/dashboard"
    assert [item["path"] for item in shell["header"]["pages"]] == ["/dashboard", "/wallet"]
    assert [item["id"] for item in shell["profile"]["menu"]] == [
        "profile",
        "wallet",
        "admin-portal",
        "signout",
    ]
    assert [item["path"] for item in shell["mobile"]["bottomBar"]["items"]] == ["/dashboard", "/wallet", "/profile"]
    assert shell["footer"]["links"][0] == {"label": "Legal Notice", "href": "/legal"}
    assert shell["footer"]["hideOnMobile"] is True


def test_platform_shell_config_resolves_page_navigation_policy(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.hosts import platform as platform_app

    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    pages_dir = app_root / "ui" / "pages"
    pages_dir.mkdir(parents=True)
    (app_root / "app.json").write_text(
        json.dumps({"appName": "Navigation App", "startup": {"landing_spot": "/dashboard"}}),
        encoding="utf-8",
    )
    (app_root / "config" / "shell.json").write_text(
        json.dumps(
            {
                "navigation": {
                    "policy": {
                        "desktop": {"global": "header", "local": "sidebar", "footer": "visible"},
                        "mobile": {"global": "bottomBar", "local": "sheet", "footer": "hidden"},
                        "maxMobileItems": 2,
                        "autoFromPages": False,
                    }
                },
                "chrome": {
                    "defaultMode": "standard",
                    "modes": {
                        "conversation": {
                            "desktop": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                            "mobile": {"header": True, "footer": False, "bottomBar": False, "localNav": False},
                        }
                    },
                },
                "shortcuts": {"footer": ["privacy"], "footerHideOnMobile": True},
            }
        ),
        encoding="utf-8",
    )
    (pages_dir / "Dashboard.yaml").write_text(
        """
name: Dashboard
route: /dashboard
title: Dashboard
layout: grid
navigation:
  scope: global
  order: 10
sections:
  - id: overview
    primitive: Panel
    config: {}
""".strip(),
        encoding="utf-8",
    )
    (pages_dir / "Messages.yaml").write_text(
        """
name: Messages
route: /messages
title: Messages
layout: full-width
shell_mode: conversation
navigation:
  scope: global
  order: 20
sections:
  - id: inbox
    primitive: Panel
    config: {}
""".strip(),
        encoding="utf-8",
    )
    (pages_dir / "Billing.yaml").write_text(
        """
name: Billing
route: /billing
title: Billing
layout: full-width
navigation:
  scope: local
  order: 30
sections:
  - id: billing
    primitive: Panel
    config: {}
""".strip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(platform_app, "resolve_app_root", lambda: app_root)

    shell = asyncio.run(platform_app.build_shell_config(surface="platform"))

    assert [item["path"] for item in shell["header"]["pages"]] == ["/dashboard", "/messages"]
    assert [item["path"] for item in shell["mobile"]["bottomBar"]["items"]] == ["/dashboard", "/messages"]
    assert [item["path"] for item in shell["navigation"]["resolved"]["local"]["mobile"]] == ["/billing"]
    assert shell["navigation"]["policy"]["mobile"]["local"] == "sheet"
    assert shell["chrome"]["defaultMode"] == "standard"
    assert shell["chrome"]["modes"]["conversation"]["mobile"]["bottomBar"] is False
    assert next(item for item in shell["pages"] if item["path"] == "/messages")["meta"]["shellMode"] == "conversation"
    assert shell["footer"]["links"] == [{"label": "Privacy Policy", "href": "/privacy"}]
    assert shell["footer"]["hideOnMobile"] is True


def test_frontend_shell_normalizes_mobile_and_desktop_footer() -> None:
    root = Path(__file__).resolve().parents[1]
    provider = (root / "chat-ui" / "src" / "providers" / "NavigationProvider.jsx").read_text(encoding="utf-8")
    route_renderer = (root / "chat-ui" / "src" / "components" / "RouteRenderer.jsx").read_text(encoding="utf-8")
    footer = (root / "chat-ui" / "src" / "components" / "layout" / "Footer.js").read_text(encoding="utf-8")

    assert "normalizeMobileConfig" in provider
    assert "normalizeNavigationModel" in provider
    assert "normalizeChromeConfig" in provider
    assert "navigationModel: navigation.navigation" in provider
    assert "chrome: navigation.chrome" in provider
    assert "mobile: normalizeMobileConfig(navigation.mobile)" in provider
    assert "resolveShellMode" in route_renderer
    assert "showBottomBarMobile" in route_renderer
    assert "footerConfig.hideOnMobile" in footer
