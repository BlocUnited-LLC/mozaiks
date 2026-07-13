"""
Admin registry pure helper unit tests.

Covers:
  AdminRegistryPage validation:
    - valid page constructs correctly
    - id/label/path empty or missing → ValidationError
    - scope defaults to "app"
    - scope "workspace" accepted
    - scope invalid → ValidationError
    - surfaces None → None
    - surfaces single string → list
    - surfaces list with valid values → deduped list
    - surfaces with invalid value → ValidationError
    - unknown fields ignored (extra="ignore")
    - show_in_navigation defaults to true and can be disabled

  AdminRegistry.enabled_pages:
    - no pages → []
    - all enabled → all returned, sorted by (order, id)
    - disabled pages filtered out
    - scope filter applied
    - pages sorted by order then id

  AdminRegistry.page_ids:
    - no pages → empty set
    - returns enabled page ids as set
    - disabled pages excluded

  build_admin_shell_routes:
    - no pages → []
    - enabled pages → route dicts with path, label, order, title, admin_page, scope
    - disabled pages excluded
    - surfaces field included
"""
from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from mozaiksai.core.admin.registry import (
    AdminRegistry,
    AdminRegistryPage,
    build_admin_shell_routes,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _page(**kw) -> dict[str, Any]:
    defaults = {"id": "test_page", "label": "Test Page", "path": "/admin/test"}
    return {**defaults, **kw}


# ---------------------------------------------------------------------------
# 1. AdminRegistryPage validation
# ---------------------------------------------------------------------------

class TestAdminRegistryPage:
    def test_valid_page_constructs(self):
        page = AdminRegistryPage(**_page())
        assert page.id == "test_page"
        assert page.label == "Test Page"
        assert page.path == "/admin/test"

    def test_id_empty_raises(self):
        with pytest.raises(ValidationError):
            AdminRegistryPage(**_page(id=""))

    def test_label_empty_raises(self):
        with pytest.raises(ValidationError):
            AdminRegistryPage(**_page(label=""))

    def test_path_empty_raises(self):
        with pytest.raises(ValidationError):
            AdminRegistryPage(**_page(path=""))

    def test_scope_defaults_to_app(self):
        page = AdminRegistryPage(**_page())
        assert page.scope == "app"

    def test_scope_workspace_accepted(self):
        page = AdminRegistryPage(**_page(scope="workspace"))
        assert page.scope == "workspace"

    def test_scope_invalid_raises(self):
        with pytest.raises(ValidationError):
            AdminRegistryPage(**_page(scope="global"))

    def test_enabled_defaults_true(self):
        page = AdminRegistryPage(**_page())
        assert page.enabled is True

    def test_show_in_navigation_defaults_true(self):
        page = AdminRegistryPage(**_page())
        assert page.show_in_navigation is True

    def test_show_in_navigation_can_be_disabled(self):
        page = AdminRegistryPage(**_page(show_in_navigation=False))
        assert page.show_in_navigation is False

    def test_order_defaults_zero(self):
        page = AdminRegistryPage(**_page())
        assert page.order == 0

    def test_surfaces_none(self):
        page = AdminRegistryPage(**_page(surfaces=None))
        assert page.surfaces is None

    def test_surfaces_single_string(self):
        page = AdminRegistryPage(**_page(surfaces="platform"))
        assert page.surfaces == ["platform"]

    def test_surfaces_list(self):
        page = AdminRegistryPage(**_page(surfaces=["platform", "studio"]))
        assert set(page.surfaces) == {"platform", "studio"}

    def test_surfaces_deduped(self):
        page = AdminRegistryPage(**_page(surfaces=["platform", "platform"]))
        assert page.surfaces == ["platform"]

    def test_surfaces_invalid_value_raises(self):
        with pytest.raises(ValidationError):
            AdminRegistryPage(**_page(surfaces=["invalid"]))

    def test_unknown_fields_ignored(self):
        page = AdminRegistryPage(**_page(unknown_field="x"))
        assert not hasattr(page, "unknown_field")

    def test_whitespace_stripped_from_id(self):
        page = AdminRegistryPage(**_page(id="  my_page  "))
        assert page.id == "my_page"


# ---------------------------------------------------------------------------
# 2. AdminRegistry.enabled_pages
# ---------------------------------------------------------------------------

def _make_registry(*page_data: dict) -> AdminRegistry:
    return AdminRegistry(pages=[AdminRegistryPage(**p) for p in page_data])


class TestAdminRegistryEnabledPages:
    def test_no_pages_returns_empty(self):
        registry = AdminRegistry()
        assert registry.enabled_pages() == []

    def test_all_enabled_returned(self):
        registry = _make_registry(
            _page(id="a", order=1),
            _page(id="b", order=2),
        )
        assert len(registry.enabled_pages()) == 2

    def test_disabled_pages_excluded(self):
        registry = _make_registry(
            _page(id="enabled"),
            _page(id="disabled", enabled=False),
        )
        pages = registry.enabled_pages()
        assert len(pages) == 1
        assert pages[0].id == "enabled"

    def test_sorted_by_order_then_id(self):
        registry = _make_registry(
            _page(id="b", order=1),
            _page(id="a", order=1),
            _page(id="c", order=0),
        )
        pages = registry.enabled_pages()
        assert pages[0].id == "c"  # order=0
        assert pages[1].id == "a"  # order=1, id="a"
        assert pages[2].id == "b"  # order=1, id="b"

    def test_scope_filter_app(self):
        registry = _make_registry(
            _page(id="app_page", scope="app"),
            _page(id="ws_page", scope="workspace"),
        )
        pages = registry.enabled_pages(scope="app")
        assert len(pages) == 1
        assert pages[0].id == "app_page"

    def test_scope_filter_workspace(self):
        registry = _make_registry(
            _page(id="app_page", scope="app"),
            _page(id="ws_page", scope="workspace"),
        )
        pages = registry.enabled_pages(scope="workspace")
        assert len(pages) == 1
        assert pages[0].id == "ws_page"

    def test_no_scope_filter_returns_all_enabled(self):
        registry = _make_registry(
            _page(id="app_page", scope="app"),
            _page(id="ws_page", scope="workspace"),
        )
        assert len(registry.enabled_pages()) == 2


# ---------------------------------------------------------------------------
# 3. AdminRegistry.page_ids
# ---------------------------------------------------------------------------

class TestAdminRegistryPageIds:
    def test_no_pages_returns_empty_set(self):
        registry = AdminRegistry()
        assert registry.page_ids() == set()

    def test_returns_enabled_ids(self):
        registry = _make_registry(
            _page(id="a"),
            _page(id="b"),
        )
        assert registry.page_ids() == {"a", "b"}

    def test_disabled_pages_excluded(self):
        registry = _make_registry(
            _page(id="enabled"),
            _page(id="disabled", enabled=False),
        )
        assert registry.page_ids() == {"enabled"}


# ---------------------------------------------------------------------------
# 4. build_admin_shell_routes
# ---------------------------------------------------------------------------

class TestBuildAdminShellRoutes:
    def test_empty_registry_returns_empty(self):
        registry = AdminRegistry()
        assert build_admin_shell_routes(registry) == []

    def test_enabled_page_produces_route(self):
        registry = _make_registry(_page(id="users", label="Users", path="/admin/users"))
        routes = build_admin_shell_routes(registry)
        assert len(routes) == 1
        r = routes[0]
        assert r["path"] == "/admin/users"
        assert r["label"] == "Users"
        assert r["admin_page"] == "users"
        assert r["title"] == "Users"

    def test_disabled_page_excluded(self):
        registry = _make_registry(
            _page(id="visible"),
            _page(id="hidden", enabled=False),
        )
        routes = build_admin_shell_routes(registry)
        assert len(routes) == 1
        assert routes[0]["admin_page"] == "visible"

    def test_order_field_included(self):
        registry = _make_registry(_page(id="p", order=5))
        routes = build_admin_shell_routes(registry)
        assert routes[0]["order"] == 5

    def test_scope_field_included(self):
        registry = _make_registry(_page(id="p", scope="workspace"))
        routes = build_admin_shell_routes(registry)
        assert routes[0]["scope"] == "workspace"

    def test_surfaces_field_included(self):
        registry = _make_registry(_page(id="p", surfaces=["studio"]))
        routes = build_admin_shell_routes(registry)
        assert routes[0]["surfaces"] == ["studio"]

    def test_show_in_navigation_field_included(self):
        registry = _make_registry(_page(id="p", show_in_navigation=False))
        routes = build_admin_shell_routes(registry)
        assert routes[0]["show_in_navigation"] is False

    def test_multiple_pages_ordered(self):
        registry = _make_registry(
            _page(id="b", order=2),
            _page(id="a", order=1),
        )
        routes = build_admin_shell_routes(registry)
        assert routes[0]["admin_page"] == "a"
        assert routes[1]["admin_page"] == "b"
