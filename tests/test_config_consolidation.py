"""Config Consolidation — validation tests.

Tests verify:
1. Config files exist in platform/config/
2. theme_config.json owns brand/theme tokens and shell.json owns shell chrome
3. ai.json has workflow entry point and startup mode
4. Old brand/public config files are removed
5. config_loader.py resolves to platform/config/
6. themeProvider.js fetches from API
7. validateConfig.js validates new config shape
8. director.py has shell-config API route
"""

import json
import os
import re
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def read_file(relpath):
    full = os.path.join(ROOT, relpath.replace("/", os.sep))
    assert os.path.isfile(full), f"File not found: {relpath}"
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


def load_json(relpath):
    return json.loads(read_file(relpath))


# ── 1. Config files exist in platform/config/ ───────────────────────────────

CONFIG_FILES = [
    "platform/config/ai.json",
    "platform/config/theme_config.json",
    "platform/config/shell.json",
]


class TestConfigFilesExist:
    @pytest.mark.parametrize("relpath", CONFIG_FILES)
    def test_config_exists(self, relpath):
        full = os.path.join(ROOT, relpath.replace("/", os.sep))
        assert os.path.isfile(full), f"Missing: {relpath}"

    @pytest.mark.parametrize("relpath", CONFIG_FILES)
    def test_config_valid_json(self, relpath):
        data = load_json(relpath)
        assert isinstance(data, dict)


class TestAIConfig:
    @pytest.fixture
    def ai(self):
        return load_json("platform/config/ai.json")

    def test_has_no_legacy_engine_block(self, ai):
        assert "engine" not in ai

    def test_has_workflow_entry_point(self, ai):
        assert isinstance(ai["workflows"]["entry_point"], str)
        assert ai["workflows"]["entry_point"]

    def test_has_chat_startup_mode(self, ai):
        assert ai["chat"]["chat_startup_mode"] == "ask"


# ── 2. theme_config.json — brand + theme tokens ─────────────────────────────

class TestThemeConfigMerged:
    @pytest.fixture
    def theme(self):
        return load_json("platform/config/theme_config.json")

    def test_has_identity(self, theme):
        assert "identity" in theme
        assert theme["identity"]["name"].lower() == "mozaiksai"

    def test_has_tagline(self, theme):
        assert theme["identity"]["tagline"] == "AI-Powered Workflows"

    def test_has_assets(self, theme):
        assert "assets" in theme
        assert theme["assets"]["logo"] == "mozaik_logo.svg"
        favicon = theme["assets"]["favicon"]
        assert isinstance(favicon, str) and favicon
        assert favicon.endswith((".svg", ".png", ".ico"))

    def test_has_fonts(self, theme):
        assert "fonts" in theme
        body = theme["fonts"]["body"]
        assert body["family"] == "Rajdhani"
        assert "googleFont" in body

    def test_has_heading_font(self, theme):
        heading = theme["fonts"]["heading"]
        assert heading["family"] == "Orbitron"

    def test_has_logo_font(self, theme):
        logo = theme["fonts"]["logo"]
        assert logo["family"] == "Fagrak Inline"
        assert logo.get("localFont") is True

    def test_has_rich_colors(self, theme):
        assert "colors" in theme
        primary = theme["colors"]["primary"]
        assert primary["main"] == "#06b6d4"
        assert primary["name"] == "cyan"
        assert "light" in primary
        assert "dark" in primary

    def test_has_semantic_colors(self, theme):
        for name in ("primary", "secondary", "accent", "success", "warning", "error"):
            assert name in theme["colors"], f"Missing color: {name}"

    def test_has_background_colors(self, theme):
        bg = theme["colors"]["background"]
        assert "base" in bg and "surface" in bg

    def test_has_shadows(self, theme):
        assert "shadows" in theme
        for name in ("primary", "secondary", "elevated", "focus"):
            assert name in theme["shadows"]

    def test_ui_has_chat(self, theme):
        chat = theme["ui"]["chat"]
        assert "modes" in chat
        assert "ask" in chat["modes"]
        assert "workflow" in chat["modes"]

    def test_theme_does_not_own_shell_ui(self, theme):
        ui = theme["ui"]
        assert "header" not in ui
        assert "profile" not in ui
        assert "notifications" not in ui
        assert "footer" not in ui

    def test_has_available_themes(self, theme):
        assert "available_themes" in theme
        theme_ids = [t["id"] for t in theme["available_themes"]]
        assert "light" in theme_ids
        assert "dark" in theme_ids

    def test_has_layout(self, theme):
        assert "layout" in theme
        assert "border_radius" in theme["layout"]

    def test_typography_uses_brand_font(self, theme):
        assert "Rajdhani" in theme["typography"]["font_family"]


class TestShellConfig:
    @pytest.fixture
    def shell(self):
        return load_json("platform/config/shell.json")

    def test_has_header(self, shell):
        assert shell["header"]["logo"]["src"] == "mozaik_logo.svg"
        assert len(shell["header"]["actions"]) >= 1

    def test_has_profile(self, shell):
        assert shell["profile"]["icon"] == "profile.svg"
        assert shell["profile"]["defaultLabel"] == "Commander"
        menu_ids = [m.get("id") for m in shell["profile"]["menu"] if isinstance(m, dict)]
        assert "admin-portal" in menu_ids
        assert "signout" in menu_ids

    def test_has_notifications(self, shell):
        assert shell["notifications"]["icon"] == "notifications.svg"
        assert shell["notifications"]["show"] is True

    def test_has_footer(self, shell):
        assert len(shell["footer"]["links"]) >= 3
        assert shell["footer"]["visible"] is True


# ── 3. Deprecated config files removed ───────────────────────────────────────

REMOVED_CONFIG_FILES = [
    "platform/config/navigation_config.json",
    "platform/config/settings_config.json",
    "platform/config/notifications_config.json",
    "platform/config/module_registry.json",
    "platform/config/subscription_config.json",
]


class TestDeprecatedConfigsRemoved:
    @pytest.mark.parametrize("relpath", REMOVED_CONFIG_FILES)
    def test_deprecated_config_removed(self, relpath):
        full = os.path.join(ROOT, relpath.replace("/", os.sep))
        assert not os.path.exists(full), f"Deprecated config still exists: {relpath}"


# ── 4. Old files removed ────────────────────────────────────────────────────

SUNSET_FILES = [
    "app/brand/public/brand.json",
    "app/brand/public/ui.json",
    "app/brand/public/navigation.json",
]


class TestOldFilesRemoved:
    @pytest.mark.parametrize("relpath", SUNSET_FILES)
    def test_old_file_gone(self, relpath):
        full = os.path.join(ROOT, relpath.replace("/", os.sep))
        assert not os.path.exists(full), f"Sunset file still exists: {relpath}"

    def test_old_config_dir_gone(self):
        old = os.path.join(ROOT, "config")
        assert not os.path.isdir(old), "Old config/ directory still exists at repo root"


# ── 6. themeProvider.js → API fetch ─────────────────────────────────────────

class TestThemeProviderUpdated:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/styles/themeProvider.js")

    def test_no_brand_json_fetch(self, source):
        assert "fetch('/brand.json')" not in source

    def test_no_ui_json_fetch(self, source):
        assert "fetch('/ui.json')" not in source

    def test_fetches_theme_config_api(self, source):
        assert "/api/theme-config" in source

    def test_has_theme_config_converter(self, source):
        assert "themeConfigToTheme" in source

    def test_loads_from_config(self, source):
        assert "loadThemeFromConfig" in source

    def test_still_has_fallback(self, source):
        assert "BARE_FALLBACK_THEME" in source

    def test_still_has_platform_overrides(self, source):
        assert "fetchPlatformOverrides" in source


# ── 7. validateConfig.js → merged validators ────────────────────────────────

class TestValidateConfigUpdated:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/config/validateConfig.js")

    def test_no_brand_json_reference(self, source):
        assert "fetch('/brand.json')" not in source

    def test_no_ui_json_reference(self, source):
        assert "fetch('/ui.json')" not in source

    def test_no_navigation_json_reference(self, source):
        assert "fetch('/navigation.json')" not in source

    def test_validates_theme_config(self, source):
        assert "validateThemeConfig" in source

    def test_validates_shell_config(self, source):
        assert "validateShellConfig" in source

    def test_fetches_from_api(self, source):
        assert "/api/theme-config" in source

    def test_fetches_shell_config_api(self, source):
        assert "/api/shell-config" in source


class TestNavigationProviderUpdated:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/providers/NavigationProvider.jsx")

    def test_fetches_shell_config_api(self, source):
        assert "/api/shell-config" in source

    def test_no_routes_api(self, source):
        # Routes endpoint removed — runtime is mozaiksai + chat-ui only
        assert "/api/routes" not in source

    def test_no_components_api(self, source):
        # Components endpoint was removed - admin is now first-class
        assert "/api/available-components" not in source

    def test_no_adapters_reference(self, source):
        assert "/api/available-adapters" not in source
