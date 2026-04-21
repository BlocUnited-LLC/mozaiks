"""
Phase 4 — Dynamic Architecture: validation tests.

Tests verify:
1. Generic capability routing is removed
2. useCoreNotifications hook exists with correct API
3. Header.js uses real notification hook
4. RouteRenderer keeps only shell-owned core routes
5. coreComponents.js does not register generic capability routes
6. coreBridge.js exports capability/theme/profile APIs
7. NavigationProvider discovers pages and only route-enabled capabilities
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


# ── 1. Capability architecture ────────────────────────────────────────────────

class TestCapabilityPage:
    """ModulePage.jsx removed — capabilities now navigate to dedicated paths."""

    def test_module_page_file_deleted(self):
        full = os.path.join(ROOT, "chat-ui", "src", "pages", "ModulePage.jsx")
        assert not os.path.isfile(full), "ModulePage.jsx should be deleted"

    def test_admin_portal_exists_in_platform_pages(self):
        # Admin is at platform/pages/admin/ (should eventually be first-class like chat-ui)
        full = os.path.join(ROOT, "platform", "pages", "admin", "ui", "AdminPortal.jsx")
        assert os.path.isfile(full), "AdminPortal should exist at platform/pages/admin/ui/"


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
        # Admin portal is a first-class framework surface (like chat-ui),
        # not a hardcoded route in CORE_ROUTES.
        assert "path: '/admin'" not in source and 'path: "/admin"' not in source


# ── 5. coreComponents.js — core registrations ───────────────────────────────────

class TestCoreComponentsDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/registry/coreComponents.js")

    def test_no_module_page_registration(self, source):
        # ModulePage removed — modules use dedicated routes
        assert not re.search(r"registerComponent\(\s*['\"]ModulePage['\"]" , source)

    def test_capability_catalog_still_scans_platform_capabilities(self):
        # Durable first-class capabilities remain under platform/operations/*
        capabilities_index = os.path.join(ROOT, "chat-ui", "src", "@operations", "index.js")
        assert os.path.isfile(capabilities_index), "@operations/index.js should exist"
        with open(capabilities_index, "r", encoding="utf-8") as f:
            content = f.read()
        assert "platform/operations" in content, "@operations index should scan platform/operations"
        assert "initializeOperations" in content

    def test_page_catalog_still_scans_platform_pages(self):
        pages_index = os.path.join(ROOT, "chat-ui", "src", "@pages", "index.js")
        assert os.path.isfile(pages_index), "@pages/index.js should exist"
        with open(pages_index, "r", encoding="utf-8") as f:
            content = f.read()
        assert "platform/pages" in content, "@pages index should scan platform/pages"
        assert "initializePages" in content

    def test_core_components_list_no_module_page(self, source):
        assert "'ModulePage'" not in source and '"ModulePage"' not in source


# ── 6. coreBridge.js — capability/theme/profile APIs ────────────────────────

class TestCoreBridgeDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/coreBridge.js")

    # Capability APIs
    def test_fetch_available_capabilities(self, source):
        assert "fetchAvailableCapabilities" in source

    def test_execute_capability(self, source):
        assert "executeCapability" in source
        assert "/api/execute/" in source

    def test_check_capability_access(self, source):
        assert "checkCapabilityAccess" in source

    def test_fetch_capability_settings(self, source):
        assert "fetchCapabilitySettings" in source

    def test_save_capability_settings(self, source):
        assert "saveCapabilitySettings" in source

    # Theme APIs
    def test_fetch_theme_config(self, source):
        assert "fetchThemeConfig" in source

    def test_change_theme(self, source):
        assert "changeTheme" in source

    # Profile APIs
    def test_update_profile(self, source):
        assert "updateUserProfile" in source

    # Default export completeness
    def test_default_export_capabilities(self, source):
        for fn in ["fetchAvailableCapabilities", "executeCapability", "changeTheme", "updateUserProfile"]:
            assert fn in source, f"Missing from default export: {fn}"


# ── 7. NavigationProvider — shell config ────────────────────────────────────

class TestNavigationProviderDynamic:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/providers/NavigationProvider.jsx")

    def test_fetches_shell_config(self, source):
        assert "/api/shell-config" in source

    def test_non_fatal_failure(self, source):
        # Fetch failure should not crash navigation
        assert "unavailable" in source.lower() or "catch" in source.lower()
