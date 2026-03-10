"""
Phase 4 — Dynamic Architecture: validation tests.

Tests verify:
1. ModulePage.jsx exists and is well-structured
2. useCoreNotifications hook exists with correct API
3. Header.js uses real notification hook
4. RouteRenderer includes dynamic module route
5. coreComponents.js registers ModulePage
6. coreBridge.js exports module/theme/profile APIs
7. NavigationProvider does module auto-discovery
"""

import os
import re
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def read_file(relpath):
    """Read a file relative to project root."""
    full = os.path.join(ROOT, relpath.replace("/", os.sep))
    assert os.path.isfile(full), f"File not found: {relpath}"
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


# ── 1. Module architecture ────────────────────────────────────────────────────

class TestModulePage:
    """ModulePage.jsx removed — modules now navigate to dedicated paths
    (e.g. admin_portal → /admin) via module.json navigation.path."""

    def test_module_page_file_deleted(self):
        full = os.path.join(ROOT, "chat-ui", "src", "pages", "ModulePage.jsx")
        assert not os.path.isfile(full), "ModulePage.jsx should be deleted"

    def test_admin_portal_module_json_has_dedicated_path(self):
        import json
        full = os.path.join(ROOT, "platform", "modules", "admin_portal", "module.json")
        assert os.path.isfile(full), "admin_portal/module.json missing"
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
        nav = data.get("navigation", {})
        assert nav.get("path") == "/admin", "admin_portal should navigate to /admin"

    def test_admin_portal_module_json_has_component(self):
        import json
        full = os.path.join(ROOT, "platform", "modules", "admin_portal", "module.json")
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
        nav = data.get("navigation", {})
        assert nav.get("component") == "AdminPortal", "admin_portal component should be AdminPortal"


# ── 2. useCoreNotifications ─────────────────────────────────────────────────

class TestUseCoreNotifications:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/hooks/useCoreNotifications.js")

    def test_file_exists(self, source):
        assert len(source) > 200

    def test_exports_hook(self, source):
        assert "export function useCoreNotifications" in source

    def test_calls_fetch_notification_count(self, source):
        assert "fetchNotificationCount" in source

    def test_has_poll_interval(self, source):
        assert "pollInterval" in source

    def test_returns_count(self, source):
        assert "count" in source

    def test_returns_refresh(self, source):
        assert "refresh" in source

    def test_uses_effect_for_polling(self, source):
        assert "useEffect" in source
        assert "setInterval" in source or "clearInterval" in source

    def test_non_fatal_on_error(self, source):
        assert "catch" in source


# ── 3. Header.js — real notifications ───────────────────────────────────────

class TestHeaderNotifications:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/components/layout/Header.js")

    def test_imports_core_notifications(self, source):
        assert "useCoreNotifications" in source

    def test_no_random_mock(self, source):
        # The old mock had Math.random() for notification count
        assert "Math.random" not in source

    def test_uses_hook_count(self, source):
        assert "coreNotificationCount" in source

    def test_no_hardcoded_initial_count(self, source):
        # Should NOT initialize to 3 anymore
        assert "useState(3)" not in source


# ── 4. RouteRenderer — module routes ───────────────────────────────────────────

class TestRouteRendererDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/components/RouteRenderer.jsx")

    def test_no_generic_module_route(self, source):
        # Generic /modules/:moduleName dispatcher removed
        assert "/modules/:moduleName" not in source

    def test_admin_not_hardcoded_route(self, source):
        # AdminPortal is now a platform module — /admin route comes from nav config,
        # not hardcoded in CORE_ROUTES
        assert "path: '/admin'" not in source and 'path: "/admin"' not in source


# ── 5. coreComponents.js — core registrations ───────────────────────────────────

class TestCoreComponentsDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/registry/coreComponents.js")

    def test_no_module_page_registration(self, source):
        # ModulePage removed — modules use dedicated routes
        assert not re.search(r"registerComponent\(\s*['\"]ModulePage['\"]" , source)

    def test_admin_portal_registered_via_modules(self):
        # AdminPortal is now registered via @modules auto-discovery, not coreComponents.
        # Verify the @modules index exists and scans platform/modules/*
        modules_index = os.path.join(ROOT, "chat-ui", "src", "@modules", "index.js")
        assert os.path.isfile(modules_index), "@modules/index.js should exist"
        with open(modules_index, "r", encoding="utf-8") as f:
            content = f.read()
        assert "platform/modules" in content, "@modules index should scan platform/modules"
        assert "initializeModules" in content

    def test_core_components_list_no_module_page(self, source):
        assert "'ModulePage'" not in source and '"ModulePage"' not in source


# ── 6. coreBridge.js — module/theme/profile APIs ────────────────────────────

class TestCoreBridgeDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/coreBridge.js")

    # Module APIs
    def test_fetch_available_modules(self, source):
        assert "fetchAvailableModules" in source

    def test_execute_module(self, source):
        assert "executeModule" in source
        assert "/api/execute/" in source

    def test_check_module_access(self, source):
        assert "checkModuleAccess" in source

    def test_fetch_module_settings(self, source):
        assert "fetchModuleSettings" in source

    def test_save_module_settings(self, source):
        assert "saveModuleSettings" in source

    # Theme APIs
    def test_fetch_theme_config(self, source):
        assert "fetchThemeConfig" in source

    def test_change_theme(self, source):
        assert "changeTheme" in source

    # Profile APIs
    def test_update_profile(self, source):
        assert "updateUserProfile" in source

    # Default export completeness
    def test_default_export_modules(self, source):
        for fn in ["fetchAvailableModules", "executeModule", "changeTheme", "updateUserProfile"]:
            assert fn in source, f"Missing from default export: {fn}"


# ── 7. NavigationProvider — module auto-discovery ───────────────────────────

class TestNavigationProviderDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/providers/NavigationProvider.jsx")

    def test_fetches_available_modules(self, source):
        assert "/api/available-modules" in source

    def test_creates_module_page_entries(self, source):
        assert "ModulePage" in source or "modulePage" in source.lower()

    def test_deduplicates_paths(self, source):
        assert "existingPaths" in source

    def test_sets_module_meta(self, source):
        assert "requiresAuth" in source

    def test_non_fatal_module_discovery(self, source):
        # Module fetch failure should not crash navigation
        assert "unavailable" in source.lower() or "catch" in source.lower()

    def test_auto_discovers_label(self, source):
        assert "display_name" in source
