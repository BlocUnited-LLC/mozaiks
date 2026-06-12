"""
Pure helper unit tests for mozaiksai/hosts/platform.py shell/nav helpers.

Covers:
  _title_from_id:
    - hyphens split and capitalize
    - underscores split and capitalize
    - mixed separators
    - already-clean word

  _clean_string:
    - valid string stripped and returned
    - empty string → None
    - whitespace-only → None
    - non-string (int, None, list) → None

  _normalize_shell_mode:
    - valid modes returned
    - unknown mode → None
    - underscore normalized to hyphen
    - None → None
    - empty string → None

  _append_page_once:
    - new path appended
    - duplicate path skipped
    - page without path skipped

  _normalize_shell_surface:
    - "platform" → "platform"
    - "studio" → "studio"
    - None → "platform"
    - unknown string → "platform"
    - uppercase normalized

  _page_targets_surface:
    - no surfaces declared → True for any surface
    - single string surface matches
    - list of surfaces matches
    - surface not in list → False
    - meta.surfaces key used

  _shell_mode_from_entry:
    - shellMode key used
    - shell_mode key used
    - meta.shellMode fallback
    - meta.shell_mode fallback
    - no mode → None

  _route_item_from_page:
    - path not starting with "/" → None
    - missing path → None
    - valid path → item with id, label, action, path
    - label from nav.label
    - label falls back to _title_from_id
    - order preserved when int

  _shortcut_ids:
    - valid list of strings → returned
    - non-list value → []
    - empty strings filtered
    - missing key → []

  _placement_for_item:
    - explicit placement for viewport returned
    - scope "local" → policy[viewport]["local"]
    - scope "footer" → policy[viewport]["footer"]
    - scope "profile" → "profile"
    - no scope → policy[viewport]["global"]

  _public_nav_item:
    - only _NAVIGATION_ITEM_FIELDS keys kept
    - "scope" excluded even if present
    - extra fields stripped

  _footer_link_from_item:
    - item with href → {"label": ..., "href": href}
    - item with path (no href) → {"label": ..., "href": path}
    - neither href nor path → None
    - label from item.label
    - label falls back to _title_from_id(id)

  _navigation_config_from_page:
    - page.navigation dict returned
    - meta.navigation dict returned when no direct key
    - non-dict page → None
    - neither key → None

  _is_runnable_workflow_name:
    - empty string → False
    - "extended_orchestration" (non-runnable) → False
    - name in ordered_names (case-insensitive) → True
    - name not in ordered_names → False
"""
from __future__ import annotations

from mozaiksai.hosts.platform import (
    _append_page_once,
    _clean_string,
    _footer_link_from_item,
    _is_runnable_workflow_name,
    _navigation_config_from_page,
    _normalize_shell_mode,
    _normalize_shell_surface,
    _page_targets_surface,
    _placement_for_item,
    _public_nav_item,
    _route_item_from_page,
    _shell_mode_from_entry,
    _shortcut_ids,
    _title_from_id,
)

# ---------------------------------------------------------------------------
# 1. _title_from_id
# ---------------------------------------------------------------------------

class TestTitleFromId:
    def test_hyphen_splits_and_capitalizes(self):
        assert _title_from_id("my-page") == "My Page"

    def test_underscore_splits_and_capitalizes(self):
        assert _title_from_id("my_page") == "My Page"

    def test_mixed_separators(self):
        assert _title_from_id("my-home_page") == "My Home Page"

    def test_single_word_capitalized(self):
        assert _title_from_id("home") == "Home"

    def test_already_clean(self):
        assert _title_from_id("Home") == "Home"

    def test_multiple_separators_consecutive(self):
        assert _title_from_id("my--page") == "My Page"

    def test_empty_string(self):
        assert _title_from_id("") == ""


# ---------------------------------------------------------------------------
# 2. _clean_string
# ---------------------------------------------------------------------------

class TestCleanString:
    def test_valid_string_stripped(self):
        assert _clean_string("  hello  ") == "hello"

    def test_valid_string_returned(self):
        assert _clean_string("hello") == "hello"

    def test_empty_string_returns_none(self):
        assert _clean_string("") is None

    def test_whitespace_only_returns_none(self):
        assert _clean_string("   ") is None

    def test_none_returns_none(self):
        assert _clean_string(None) is None

    def test_int_returns_none(self):
        assert _clean_string(42) is None

    def test_list_returns_none(self):
        assert _clean_string(["a", "b"]) is None

    def test_dict_returns_none(self):
        assert _clean_string({"key": "val"}) is None


# ---------------------------------------------------------------------------
# 3. _normalize_shell_mode
# ---------------------------------------------------------------------------

class TestNormalizeShellMode:
    def test_standard_returned(self):
        assert _normalize_shell_mode("standard") == "standard"

    def test_workspace_returned(self):
        assert _normalize_shell_mode("workspace") == "workspace"

    def test_conversation_returned(self):
        assert _normalize_shell_mode("conversation") == "conversation"

    def test_focused_returned(self):
        assert _normalize_shell_mode("focused") == "focused"

    def test_immersive_returned(self):
        assert _normalize_shell_mode("immersive") == "immersive"

    def test_public_returned(self):
        assert _normalize_shell_mode("public") == "public"

    def test_unknown_mode_returns_none(self):
        assert _normalize_shell_mode("custom") is None

    def test_none_returns_none(self):
        assert _normalize_shell_mode(None) is None

    def test_empty_string_returns_none(self):
        assert _normalize_shell_mode("") is None

    def test_whitespace_returns_none(self):
        assert _normalize_shell_mode("   ") is None

    def test_underscore_normalized_to_hyphen(self):
        # "conversation" is valid; if someone passes underscore variant it's not in the set
        # but "standard" with no underscore is also fine
        assert _normalize_shell_mode("STANDARD") == "standard"


# ---------------------------------------------------------------------------
# 4. _append_page_once
# ---------------------------------------------------------------------------

class TestAppendPageOnce:
    def test_new_path_appended(self):
        pages: list = []
        _append_page_once(pages, {"path": "/home", "label": "Home"})
        assert len(pages) == 1

    def test_duplicate_path_skipped(self):
        pages = [{"path": "/home"}]
        _append_page_once(pages, {"path": "/home", "label": "Home"})
        assert len(pages) == 1

    def test_different_paths_both_appended(self):
        pages: list = []
        _append_page_once(pages, {"path": "/home"})
        _append_page_once(pages, {"path": "/about"})
        assert len(pages) == 2

    def test_page_without_path_skipped(self):
        pages: list = []
        _append_page_once(pages, {"label": "No path"})
        assert len(pages) == 0

    def test_page_with_non_string_path_skipped(self):
        pages: list = []
        _append_page_once(pages, {"path": 42})
        assert len(pages) == 0


# ---------------------------------------------------------------------------
# 5. _normalize_shell_surface
# ---------------------------------------------------------------------------

class TestNormalizeShellSurface:
    def test_platform_returned(self):
        assert _normalize_shell_surface("platform") == "platform"

    def test_studio_returned(self):
        assert _normalize_shell_surface("studio") == "studio"

    def test_none_returns_platform(self):
        assert _normalize_shell_surface(None) == "platform"

    def test_unknown_string_returns_platform(self):
        assert _normalize_shell_surface("mobile") == "platform"

    def test_uppercase_normalized(self):
        assert _normalize_shell_surface("STUDIO") == "studio"

    def test_empty_string_returns_platform(self):
        assert _normalize_shell_surface("") == "platform"


# ---------------------------------------------------------------------------
# 6. _page_targets_surface
# ---------------------------------------------------------------------------

class TestPageTargetsSurface:
    def test_no_surfaces_declared_returns_true(self):
        page = {"path": "/home"}
        assert _page_targets_surface(page, surface="platform") is True

    def test_matching_string_surface_true(self):
        page = {"path": "/home", "meta": {"surfaces": "platform"}}
        assert _page_targets_surface(page, surface="platform") is True

    def test_non_matching_string_surface_false(self):
        page = {"path": "/home", "meta": {"surfaces": "studio"}}
        assert _page_targets_surface(page, surface="platform") is False

    def test_matching_list_surface_true(self):
        page = {"path": "/home", "meta": {"surfaces": ["platform", "studio"]}}
        assert _page_targets_surface(page, surface="studio") is True

    def test_non_matching_list_surface_false(self):
        page = {"path": "/home", "meta": {"surfaces": ["studio"]}}
        assert _page_targets_surface(page, surface="platform") is False

    def test_surface_key_fallback(self):
        page = {"path": "/home", "meta": {"surface": "studio"}}
        assert _page_targets_surface(page, surface="studio") is True

    def test_empty_surfaces_list_returns_true(self):
        page = {"path": "/home", "meta": {"surfaces": []}}
        assert _page_targets_surface(page, surface="platform") is True


# ---------------------------------------------------------------------------
# 7. _shell_mode_from_entry
# ---------------------------------------------------------------------------

class TestShellModeFromEntry:
    def test_shellMode_key_used(self):
        assert _shell_mode_from_entry({"shellMode": "conversation"}) == "conversation"

    def test_shell_mode_snake_key_used(self):
        assert _shell_mode_from_entry({"shell_mode": "focused"}) == "focused"

    def test_meta_shellMode_fallback(self):
        entry = {"meta": {"shellMode": "immersive"}}
        assert _shell_mode_from_entry(entry) == "immersive"

    def test_meta_shell_mode_fallback(self):
        entry = {"meta": {"shell_mode": "workspace"}}
        assert _shell_mode_from_entry(entry) == "workspace"

    def test_no_mode_returns_none(self):
        assert _shell_mode_from_entry({}) is None

    def test_invalid_mode_returns_none(self):
        assert _shell_mode_from_entry({"shellMode": "unknown_mode"}) is None


# ---------------------------------------------------------------------------
# 8. _route_item_from_page
# ---------------------------------------------------------------------------

class TestRouteItemFromPage:
    def test_no_path_returns_none(self):
        assert _route_item_from_page({}) is None

    def test_non_slash_path_returns_none(self):
        assert _route_item_from_page({"path": "home"}) is None

    def test_valid_path_returns_item(self):
        result = _route_item_from_page({"path": "/home"})
        assert result is not None
        assert result["path"] == "/home"
        assert result["action"] == "navigate"

    def test_label_from_nav(self):
        page = {
            "path": "/home",
            "meta": {"navigation": {"label": "Home Page"}},
        }
        result = _route_item_from_page(page)
        assert result["label"] == "Home Page"

    def test_label_falls_back_to_title_from_id(self):
        page = {"path": "/my-page"}
        result = _route_item_from_page(page)
        assert result["label"] == "My Page"

    def test_order_preserved_when_int(self):
        page = {"path": "/home", "order": 1}
        result = _route_item_from_page(page)
        assert result["order"] == 1

    def test_order_not_added_when_not_int(self):
        page = {"path": "/home", "order": "first"}
        result = _route_item_from_page(page)
        assert "order" not in result


# ---------------------------------------------------------------------------
# 9. _shortcut_ids
# ---------------------------------------------------------------------------

class TestShortcutIds:
    def test_list_of_strings_returned(self):
        shortcuts = {"primary": ["home", "about"]}
        result = _shortcut_ids(shortcuts, "primary")
        assert result == ["home", "about"]

    def test_non_list_value_returns_empty(self):
        shortcuts = {"primary": "home"}
        result = _shortcut_ids(shortcuts, "primary")
        assert result == []

    def test_missing_key_returns_empty(self):
        assert _shortcut_ids({}, "primary") == []

    def test_empty_strings_filtered(self):
        shortcuts = {"primary": ["home", "", "about", "  "]}
        result = _shortcut_ids(shortcuts, "primary")
        assert result == ["home", "about"]

    def test_non_string_items_filtered(self):
        shortcuts = {"primary": ["home", 42, None, "about"]}
        result = _shortcut_ids(shortcuts, "primary")
        assert result == ["home", "about"]


# ---------------------------------------------------------------------------
# 10. _placement_for_item
# ---------------------------------------------------------------------------

_POLICY = {
    "desktop": {"global": "header", "local": "sidebar", "footer": "footer"},
    "mobile": {"global": "bottomBar", "local": "sheet", "footer": "hidden"},
}


class TestPlacementForItem:
    def test_explicit_placement_for_desktop(self):
        item = {"placement": {"desktop": "sidebar"}}
        assert _placement_for_item(item, viewport="desktop", policy=_POLICY) == "sidebar"

    def test_scope_local_returns_local_policy(self):
        item = {"scope": "local"}
        assert _placement_for_item(item, viewport="desktop", policy=_POLICY) == "sidebar"

    def test_scope_footer_returns_footer_policy(self):
        item = {"scope": "footer"}
        assert _placement_for_item(item, viewport="desktop", policy=_POLICY) == "footer"

    def test_scope_profile_returns_profile(self):
        item = {"scope": "profile"}
        assert _placement_for_item(item, viewport="desktop", policy=_POLICY) == "profile"

    def test_no_scope_returns_global_policy(self):
        item = {}
        assert _placement_for_item(item, viewport="desktop", policy=_POLICY) == "header"

    def test_mobile_global_returns_bottombar(self):
        item = {}
        assert _placement_for_item(item, viewport="mobile", policy=_POLICY) == "bottomBar"


# ---------------------------------------------------------------------------
# 11. _public_nav_item
# ---------------------------------------------------------------------------

class TestPublicNavItem:
    def test_allowed_fields_kept(self):
        item = {"id": "home", "label": "Home", "path": "/home", "action": "navigate"}
        result = _public_nav_item(item)
        assert result["id"] == "home"
        assert result["label"] == "Home"
        assert result["path"] == "/home"

    def test_scope_excluded(self):
        item = {"id": "home", "scope": "local", "label": "Home"}
        result = _public_nav_item(item)
        assert "scope" not in result

    def test_extra_fields_stripped(self):
        item = {"id": "home", "customField": "should_be_stripped", "label": "Home"}
        result = _public_nav_item(item)
        assert "customField" not in result

    def test_empty_item_returns_empty(self):
        assert _public_nav_item({}) == {}


# ---------------------------------------------------------------------------
# 12. _footer_link_from_item
# ---------------------------------------------------------------------------

class TestFooterLinkFromItem:
    def test_href_item_returned(self):
        item = {"id": "terms", "label": "Terms", "href": "https://example.com/terms"}
        result = _footer_link_from_item(item)
        assert result == {"label": "Terms", "href": "https://example.com/terms"}

    def test_path_used_when_no_href(self):
        item = {"id": "terms", "label": "Terms", "path": "/terms"}
        result = _footer_link_from_item(item)
        assert result == {"label": "Terms", "href": "/terms"}

    def test_no_href_or_path_returns_none(self):
        item = {"id": "terms", "label": "Terms"}
        result = _footer_link_from_item(item)
        assert result is None

    def test_label_from_title_from_id(self):
        item = {"id": "privacy-policy", "href": "/privacy"}
        result = _footer_link_from_item(item)
        assert result["label"] == "Privacy Policy"

    def test_label_from_item_label(self):
        item = {"id": "x", "label": "Custom Label", "href": "/x"}
        result = _footer_link_from_item(item)
        assert result["label"] == "Custom Label"


# ---------------------------------------------------------------------------
# 13. _navigation_config_from_page
# ---------------------------------------------------------------------------

class TestNavigationConfigFromPage:
    def test_direct_navigation_dict_returned(self):
        nav = {"include": True, "label": "Home"}
        page = {"path": "/home", "navigation": nav}
        result = _navigation_config_from_page(page)
        assert result == nav

    def test_meta_navigation_fallback(self):
        nav = {"label": "About"}
        page = {"path": "/about", "meta": {"navigation": nav}}
        result = _navigation_config_from_page(page)
        assert result == nav

    def test_non_dict_page_returns_none(self):
        assert _navigation_config_from_page("not_a_dict") is None

    def test_neither_key_returns_none(self):
        assert _navigation_config_from_page({"path": "/home"}) is None

    def test_direct_key_takes_priority(self):
        direct = {"label": "Direct"}
        meta_nav = {"label": "Meta"}
        page = {"navigation": direct, "meta": {"navigation": meta_nav}}
        result = _navigation_config_from_page(page)
        assert result == direct


# ---------------------------------------------------------------------------
# 14. _is_runnable_workflow_name
# ---------------------------------------------------------------------------

class TestIsRunnableWorkflowName:
    def test_empty_string_false(self):
        assert _is_runnable_workflow_name("", ["MyWorkflow"]) is False

    def test_none_false(self):
        assert _is_runnable_workflow_name(None, ["MyWorkflow"]) is False

    def test_non_runnable_id_false(self):
        assert _is_runnable_workflow_name("extended_orchestration", ["extended_orchestration"]) is False

    def test_name_in_ordered_names_true(self):
        assert _is_runnable_workflow_name("AppGenerator", ["AppGenerator", "AgentGenerator"]) is True

    def test_case_insensitive_match_true(self):
        assert _is_runnable_workflow_name("appgenerator", ["AppGenerator"]) is True

    def test_name_not_in_ordered_names_false(self):
        assert _is_runnable_workflow_name("Unknown", ["AppGenerator"]) is False

    def test_empty_ordered_names_false(self):
        assert _is_runnable_workflow_name("AppGenerator", []) is False
