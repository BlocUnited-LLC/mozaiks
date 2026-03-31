"""
Phase 3 — Frontend Pages: validation tests.

Tests verify:
1. All new frontend files exist and have non-trivial content
2. CoreBridge exports are present
3. coreComponents.js only registers shell-owned pages
4. RouteRenderer declares new core routes
5. AdminPortal imports coreBridge functions
6. NavigationProvider loads shell config
"""

import json
import os
import re
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CHAT_UI_SRC = os.path.join(ROOT, "chat-ui", "src")


# ── Helpers ──────────────────────────────────────────────────────────────────

def read_file(relpath):
    """Read a file relative to project root."""
    full = os.path.join(ROOT, relpath.replace("/", os.sep))
    assert os.path.isfile(full), f"File not found: {relpath}"
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


# ── 1. File existence ────────────────────────────────────────────────────────

EXPECTED_FILES = [
    "chat-ui/src/coreBridge.js",
    "chat-ui/src/components/layout/NotificationsDropdown.jsx",
    "chat-ui/src/components/layout/SettingsOverlay.jsx",
]


class TestFileExistence:
    @pytest.mark.parametrize("relpath", EXPECTED_FILES)
    def test_file_exists(self, relpath):
        full = os.path.join(ROOT, relpath.replace("/", os.sep))
        assert os.path.isfile(full), f"Missing: {relpath}"

    @pytest.mark.parametrize("relpath", EXPECTED_FILES)
    def test_file_not_empty(self, relpath):
        content = read_file(relpath)
        assert len(content) > 200, f"{relpath} is too small ({len(content)} bytes)"


# ── 3. coreBridge.js ────────────────────────────────────────────────────────

class TestCoreBridge:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/coreBridge.js")

    def test_exports_fetch_navigation(self, source):
        assert "fetchNavigation" in source

    def test_exports_fetch_settings(self, source):
        assert "fetchSettings" in source
        assert "saveSettings" in source
        assert "resetSettings" in source

    def test_exports_fetch_notifications(self, source):
        assert "fetchNotifications" in source
        assert "markNotificationRead" in source
        assert "markAllNotificationsRead" in source

    def test_exports_admin_functions(self, source):
        assert "adminListUsers" in source
        assert "adminGetAnalytics" in source

    def test_exports_health(self, source):
        assert "checkCoreHealth" in source

    def test_uses_vite_env_for_url(self, source):
        assert "VITE_CORE_URL" in source or "VITE_CORE_PORT" in source

    def test_builds_auth_headers(self, source):
        assert "Authorization" in source
        assert "Bearer" in source

    def test_default_export(self, source):
        assert "export default" in source


# ── 4. coreComponents.js ────────────────────────────────────────────────────

class TestCoreComponents:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/registry/coreComponents.js")

    @pytest.fixture
    def header_source(self):
        return read_file("chat-ui/src/components/layout/Header.js")

    def test_imports_chat_page(self, source):
        assert "ChatPage" in source

    def test_discover_page_not_in_core_components(self, source):
        assert "DiscoverPage" not in source

    def test_admin_portal_not_in_core_components(self, source):
        # AdminPortal is now an adapter registered via @adapters auto-discovery,
        # not a hardcoded core component — no import or registerComponent call.
        assert not re.search(r"import\s+AdminPortal\b", source)
        assert not re.search(r"registerComponent\(\s*['\"]AdminPortal['\"]", source)

    def test_registers_chat_page(self, source):
        assert re.search(r"registerComponent\(\s*['\"]ChatPage['\"]", source)

    def test_no_admin_portal_registration(self, source):
        # AdminPortal registered via @adapters, not coreComponents
        assert not re.search(r"registerComponent\(\s*['\"]AdminPortal['\"]", source)

    def test_no_settings_page_registration(self, source):
        # SettingsPage removed — settings live in the header SettingsOverlay
        assert not re.search(r"registerComponent\(\s*['\"]SettingsPage['\"]", source)

    def test_no_notifications_page_registration(self, source):
        # NotificationsPage removed — notifications live in the header overlay
        assert not re.search(r"registerComponent\(\s*['\"]NotificationsPage['\"]", source)

    def test_core_components_list(self, source):
        assert "CORE_COMPONENTS" in source
        assert "'ChatPage'" in source or '"ChatPage"' in source
        assert "'DiscoverPage'" not in source and '"DiscoverPage"' not in source
        # AdminPortal is no longer in the core components list
        assert "'AdminPortal'" not in source and '"AdminPortal"' not in source

    def test_header_imports_notifications_dropdown(self, header_source):
        assert "NotificationsDropdown" in header_source

    def test_header_imports_settings_overlay(self, header_source):
        assert "SettingsOverlay" in header_source


# ── 5. RouteRenderer.jsx ────────────────────────────────────────────────────

class TestRouteRenderer:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/components/RouteRenderer.jsx")

    def test_chat_core_route(self, source):
        assert "'/chat/*'" in source or '"/chat/*"' in source

    def test_admin_not_core_route(self, source):
        # Admin portal is a first-class framework surface, not a hardcoded core route.
        assert "path: '/admin'" not in source and 'path: "/admin"' not in source

    def test_no_settings_route(self, source):
        # /settings is an overlay now, not a route
        assert "'/settings'" not in source and '"/settings"' not in source

    def test_no_notifications_route(self, source):
        # /notifications is an overlay now, not a route
        assert "'/notifications'" not in source and '"/notifications"' not in source

    def test_no_module_wildcard_route(self, source):
        # Dynamic module route removed — modules navigate to dedicated paths
        assert "'/modules/:moduleName'" not in source and '"/modules/:moduleName"' not in source


# ── 6. AdminPortal ───────────────────────────────────────────────────────────────────────────

class TestAdminPortal:
    """AdminPortal is a first-class framework surface (like chat-ui).
    Currently lives at platform/pages/admin/ui/ (should eventually move to first-class location).
    The section registry APIs (registerAdminSection etc.) are in adminPortalRegistry.js.
    """

    @pytest.fixture
    def source(self):
        return read_file("platform/pages/admin/ui/AdminPortal.jsx")

    def test_is_not_in_pages_dir(self):
        full = os.path.join(ROOT, "chat-ui", "src", "pages", "AdminPortal.js")
        assert not os.path.isfile(full), "AdminPortal.js should be moved out of chat-ui/src/pages/"

    def test_platform_ui_file_exists(self, source):
        assert len(source) > 500

    def test_imports_admin_api_functions(self, source):
        assert "adminListUsers" in source

    def test_calls_admin_list_users(self, source):
        assert "adminListUsers" in source

    def test_calls_admin_get_analytics(self, source):
        assert "adminGetAnalytics" in source

    def test_still_has_section_registration(self, source):
        # Built-in sections are still registered; registry itself is in adminPortalRegistry.js
        assert "registerAdminSection" in source


class TestAdminPortalRegistry:
    """adminPortalRegistry.js contains the public section registry and auth hooks."""

    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/adminPortalRegistry.js")

    def test_file_exists(self, source):
        assert len(source) > 200

    def test_has_section_registry(self, source):
        assert "registerAdminSection" in source
        assert "sectionRegistry" in source

    def test_has_auth_hooks(self, source):
        assert "useIsAdmin" in source
        assert "useHasRole" in source

    def test_has_ui_primitives(self, source):
        assert "Card" in source
        assert "Stat" in source
        assert "ProgressBar" in source


# ── 7. NavigationProvider ───────────────────────────────────────────────────

class TestNavigationProvider:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/providers/NavigationProvider.jsx")

    def test_accepts_core_api_url(self, source):
        assert "coreApiUrl" in source

    def test_fetches_shell_config(self, source):
        assert "/api/shell-config" in source

    def test_non_fatal_core_failure(self, source):
        # Core fetch failure should not crash the provider
        assert "unavailable" in source.lower() or "catch" in source.lower()


class TestPagesRegistry:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/@pages/index.js")

    def test_pages_loader_exists(self, source):
        assert len(source) > 200

    def test_scans_platform_pages(self, source):
        assert "platform/pages" in source

    def test_initializes_pages(self, source):
        assert "initializePages" in source


# ── 8. SettingsOverlay.jsx ─────────────────────────────────────────────────────

class TestSettingsPage:
    """SettingsPage.jsx removed — settings now live in the header
    SettingsOverlay.jsx panel (triggered by the gear icon)."""

    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/components/layout/SettingsOverlay.jsx")

    def test_page_file_deleted(self):
        full = os.path.join(ROOT, "chat-ui", "src", "pages", "SettingsPage.jsx")
        assert not os.path.isfile(full), "SettingsPage.jsx should be deleted"

    def test_fetches_settings_config(self, source):
        assert "fetchSettingsConfig" in source

    def test_fetches_settings(self, source):
        assert "fetchSettings" in source

    def test_saves_settings(self, source):
        assert "saveSettings" in source

    def test_renders_field_types(self, source):
        assert "TextField" in source
        assert "ToggleField" in source
        assert "SelectField" in source

    def test_exports_default(self, source):
        assert "export default SettingsOverlay" in source


# ── 9. NotificationsPage.jsx ────────────────────────────────────────────────

class TestNotificationsPage:
    """NotificationsPage.jsx removed — notifications now live in the header
    NotificationsDropdown.jsx panel."""

    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/components/layout/NotificationsDropdown.jsx")

    def test_page_file_deleted(self):
        full = os.path.join(ROOT, "chat-ui", "src", "pages", "NotificationsPage.jsx")
        assert not os.path.isfile(full), "NotificationsPage.jsx should be deleted"

    def test_fetches_notifications(self, source):
        assert "fetchNotifications" in source

    def test_marks_read(self, source):
        assert "markNotificationRead" in source

    def test_marks_all_read(self, source):
        assert "markAllNotificationsRead" in source

    def test_deletes_notification(self, source):
        assert "deleteNotification" in source

    def test_has_filter_tabs(self, source):
        assert "FILTERS" in source or "filter" in source

    def test_exports_default(self, source):
        assert "export default NotificationsDropdown" in source
