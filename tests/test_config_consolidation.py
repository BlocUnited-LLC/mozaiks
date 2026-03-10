"""Config Consolidation — validation tests.

Tests verify:
1. All config files exist in platform/config/
2. theme_config.json has merged brand + ui data
3. navigation_config.json has merged pages + nav data
4. Old brand/public config files are removed
5. config_loader.py resolves to platform/config/
6. themeProvider.js fetches from API
7. NavigationProvider.jsx fetches from API
8. validateConfig.js validates new config shape
9. director.py has navigation-config API route
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
    "platform/config/theme_config.json",
    "platform/config/navigation_config.json",
    "platform/config/module_registry.json",
    "platform/config/notifications_config.json",
    "platform/config/settings_config.json",
    "platform/config/subscription_config.json",
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


# ── 2. theme_config.json — merged brand + ui ────────────────────────────────

class TestThemeConfigMerged:
    @pytest.fixture
    def theme(self):
        return load_json("platform/config/theme_config.json")

    def test_has_identity(self, theme):
        assert "identity" in theme
        assert theme["identity"]["name"] == "MozaiksAI"

    def test_has_tagline(self, theme):
        assert theme["identity"]["tagline"] == "AI-Powered Workflows"

    def test_has_assets(self, theme):
        assert "assets" in theme
        assert theme["assets"]["logo"] == "mozaik_logo.svg"
        assert theme["assets"]["favicon"] == "mozaik.png"

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

    def test_has_ui_section(self, theme):
        assert "ui" in theme

    def test_ui_has_chat(self, theme):
        chat = theme["ui"]["chat"]
        assert "modes" in chat
        assert "ask" in chat["modes"]
        assert "workflow" in chat["modes"]

    def test_ui_has_header(self, theme):
        header = theme["ui"]["header"]
        assert "logo" in header
        assert header["logo"]["src"] == "mozaik_logo.svg"

    def test_ui_has_profile(self, theme):
        profile = theme["ui"]["profile"]
        assert profile["icon"] == "profile.svg"
        assert profile["defaultLabel"] == "Commander"
        menu_ids = [m.get("id") for m in profile["menu"]]
        assert "admin-portal" in menu_ids
        assert "signout" in menu_ids

    def test_ui_has_notifications(self, theme):
        notif = theme["ui"]["notifications"]
        assert notif["icon"] == "notifications.svg"
        assert notif["show"] is True

    def test_ui_has_footer(self, theme):
        footer = theme["ui"]["footer"]
        assert len(footer["links"]) >= 3
        assert footer["visible"] is True

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


# ── 3. navigation_config.json — merged pages + nav ──────────────────────────

class TestNavigationConfigMerged:
    @pytest.fixture
    def nav(self):
        return load_json("platform/config/navigation_config.json")

    def test_has_version(self, nav):
        assert nav["version"] == "1.1.0"

    def test_has_landing_spot(self, nav):
        assert nav["landing_spot"] == "/"

    def test_has_startup_mode(self, nav):
        assert nav["startup_mode"] == "ask"

    def test_has_pages(self, nav):
        # pages[] lists config-driven nav routes; core overlays (settings, notifications)
        # are now embedded in the header gear/bell and are not page routes.
        assert "pages" in nav
        assert isinstance(nav["pages"], list)

    def test_settings_not_a_page(self, nav):
        # Settings moved to SettingsOverlay in the header.
        paths = [p["path"] for p in nav["pages"]]
        assert "/settings" not in paths

    def test_notifications_not_a_page(self, nav):
        # Notifications moved to NotificationsDropdown in the header.
        paths = [p["path"] for p in nav["pages"]]
        assert "/notifications" not in paths

    def test_pages_have_component(self, nav):
        for page in nav["pages"]:
            assert "component" in page

    def test_has_default_nav(self, nav):
        assert "default" in nav
        labels = [d["label"] for d in nav["default"]]
        assert "AI" in labels

    def test_has_modules_nav(self, nav):
        assert "modules" in nav
        assert any(m.get("module_name") == "admin_portal" for m in nav["modules"])
        # admin_portal navigates to /admin (dedicated route, not the generic module dispatcher)
        admin = next(m for m in nav["modules"] if m.get("module_name") == "admin_portal")
        assert admin["path"] == "/admin"


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


# ── 5. config_loader.py → platform/config ───────────────────────────────────

class TestConfigLoader:
    @pytest.fixture
    def source(self):
        return read_file("mozaikscore/core/config_loader.py")

    def test_resolves_to_platform_config(self, source):
        assert '"platform"' in source or "'platform'" in source
        assert '"config"' in source or "'config'" in source

    def test_no_old_app_config_path(self, source):
        lines = source.split("\n")
        for line in lines:
            if "parent.parent.parent" in line and '"config"' in line:
                assert '"platform"' in line, "config_loader still resolves to old app/config/ path"


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


# ── 7. NavigationProvider.jsx → API fetch ────────────────────────────────────

class TestNavigationProviderUpdated:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/providers/NavigationProvider.jsx")

    def test_no_navigation_json_fetch(self, source):
        assert "'/navigation.json'" not in source

    def test_no_config_path_prop(self, source):
        assert "configPath" not in source

    def test_fetches_navigation_config_api(self, source):
        assert "/api/navigation-config" in source

    def test_still_merges_core_navigation(self, source):
        assert "/api/navigation" in source

    def test_still_discovers_modules(self, source):
        assert "/api/available-modules" in source


# ── 8. validateConfig.js → merged validators ────────────────────────────────

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

    def test_validates_navigation(self, source):
        assert "validateNavigation" in source

    def test_fetches_from_api(self, source):
        assert "/api/theme-config" in source
        assert "/api/navigation-config" in source


# ── 9. director.py — navigation-config route ────────────────────────────────

class TestDirectorRoutes:
    @pytest.fixture
    def source(self):
        return read_file("mozaikscore/core/director.py")

    def test_has_navigation_config_route(self, source):
        assert "/api/navigation-config" in source

    def test_has_theme_config_route(self, source):
        assert "/api/theme-config" in source

    def test_app_config_uses_identity(self, source):
        assert "identity" in source
