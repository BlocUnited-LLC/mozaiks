"""Config Consolidation — validation tests.

Tests verify:
1. Config files exist in the active app workspace config/
2. theme_config.json owns brand/theme tokens and shell.json owns shell chrome
3. ai.json has workflow entry point and startup mode
4. Old brand/public config files are removed
5. themeProvider.js fetches from API
6. validateConfig.js validates new config shape
7. director.py has shell-config API route

Workspace-dependent tests (1–4) skip automatically when PLATFORM_PATH or
MOZAIKS_APP_WORKSPACE_PATH is not set.
"""

import json
import pytest

from tests.import_utils import active_app_root

ROOT_WORKSPACE = None  # resolved lazily to avoid skip at import time


def _app_root():
    return active_app_root()


def _framework_file(relpath: str) -> str:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    full = root / relpath
    assert full.is_file(), f"File not found: {relpath}"
    return full.read_text(encoding="utf-8")


def _load_app_json(relpath: str) -> dict:
    app_root = _app_root()
    full = app_root / relpath
    assert full.is_file(), f"Missing in app workspace: {relpath}"
    return json.loads(full.read_text(encoding="utf-8"))


# ── 1. Config files exist in app workspace config/ ──────────────────────────

CONFIG_FILES = [
    "config/ai.json",
    "brand/theme_config.json",
    "config/shell.json",
]


class TestConfigFilesExist:
    @pytest.mark.parametrize("relpath", CONFIG_FILES)
    def test_config_exists(self, relpath):
        app_root = _app_root()
        full = app_root / relpath
        assert full.is_file(), f"Missing: {relpath}"

    @pytest.mark.parametrize("relpath", CONFIG_FILES)
    def test_config_valid_json(self, relpath):
        data = _load_app_json(relpath)
        assert isinstance(data, dict)


class TestAIConfig:
    @pytest.fixture
    def ai(self):
        return _load_app_json("config/ai.json")

    def test_has_no_legacy_engine_block(self, ai):
        assert "engine" not in ai

    def test_has_workflow_entry_point(self, ai):
        assert "entry_point" in ai["workflows"]

    def test_has_chat_startup_mode(self, ai):
        assert ai["chat"]["chat_startup_mode"] == "ask"


# ── 2. theme_config.json — brand + theme tokens ─────────────────────────────

class TestThemeConfigMerged:
    @pytest.fixture
    def theme(self):
        return _load_app_json("brand/theme_config.json")

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

    def test_has_shell_primitives(self, theme):
        primitives = theme["primitives"]
        assert "radius" in primitives
        assert "surface" in primitives["radius"]
        assert "spacing" in primitives

    def test_has_page_layout_tokens(self, theme):
        page = theme["ui"]["page"]
        assert "maxWidth" in page
        assert "paddingX" in page
        assert "sectionGap" in page

    def test_theme_fonts_include_brand_font(self, theme):
        assert theme["fonts"]["body"]["family"] == "Rajdhani"


class TestShellConfig:
    @pytest.fixture
    def shell(self):
        return _load_app_json("config/shell.json")

    def test_has_header(self, shell):
        assert shell["header"]["logo"]["src"] == "mozaik_logo.svg"
        assert len(shell["header"]["actions"]) >= 1

    def test_first_class_shell_controls_are_not_app_config(self, shell):
        assert "profile" not in shell
        assert "notifications" not in shell

    def test_has_footer(self, shell):
        assert len(shell["footer"]["links"]) >= 3
        assert shell["footer"]["visible"] is True


# ── Framework-only tests (no app workspace needed) ──────────────────────────

class TestThemeProviderUpdated:
    @pytest.fixture
    def source(self):
        return _framework_file("chat-ui/src/styles/themeProvider.js")

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


class TestValidateConfigUpdated:
    @pytest.fixture
    def source(self):
        return _framework_file("chat-ui/src/config/validateConfig.js")

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
        return _framework_file("chat-ui/src/providers/NavigationProvider.jsx")

    def test_fetches_shell_config_api(self, source):
        assert "/api/shell-config" in source

    def test_no_routes_api(self, source):
        assert "/api/routes" not in source

    def test_no_components_api(self, source):
        assert "/api/available-components" not in source

    def test_no_adapters_reference(self, source):
        assert "/api/available-adapters" not in source
