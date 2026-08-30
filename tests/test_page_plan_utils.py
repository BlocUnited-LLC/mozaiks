"""
Page plan utility unit tests.

Covers:
  _slug:
    - special chars replaced with underscore
    - lowercased
    - leading/trailing underscores stripped
    - empty input returns empty
    - spaces and slashes

  _page_stem_from_path:
    - valid ui/pages/foo.yaml → "foo"
    - valid ui/pages/foo.yml → "foo"
    - path not in ui/pages → None
    - too few or too many parts → None
    - non-.yaml extension → None
    - path with leading ./ normalized

  _page_stems:
    - route-based stem
    - name/id/surface_id stems
    - root "/" route excluded
    - empty values excluded
    - multiple stems from multiple fields

  _decode_config_hint:
    - dict returned as-is
    - JSON string decoded to dict
    - invalid JSON returns {}
    - non-dict JSON returns {}
    - None returns {}
    - number returns {}

  _page_from_plan:
    - title from title field
    - title falls back to name then slug
    - route from route field
    - route defaults to /{stem}
    - sections from sections_hint
    - no sections_hint adds default PageHeader
    - page_type from page_type field
    - layout defaults to "stack"
    - navigation uses stem as id
    - config_hint decoded from JSON string
"""
from __future__ import annotations

from mozaiksai.core.workflow.generator_support.page_plan_utils import (
    _decode_config_hint,
    _page_from_plan,
    _page_stem_from_path,
    _page_stems,
    _slug,
)

# ---------------------------------------------------------------------------
# 1. _slug
# ---------------------------------------------------------------------------

class TestSlug:
    def test_lowercase(self):
        assert _slug("MyPage") == "mypage"

    def test_spaces_replaced(self):
        assert _slug("My Page") == "my_page"

    def test_special_chars_replaced(self):
        assert _slug("foo/bar-baz") == "foo_bar_baz"

    def test_leading_trailing_underscores_stripped(self):
        assert _slug("_foo_") == "foo"

    def test_empty_string_returns_empty(self):
        assert _slug("") == ""

    def test_none_returns_empty(self):
        assert _slug(None) == ""

    def test_digits_preserved(self):
        assert _slug("page1") == "page1"

    def test_consecutive_specials_collapsed(self):
        result = _slug("foo--bar__baz")
        assert result == "foo_bar_baz"


# ---------------------------------------------------------------------------
# 2. _page_stem_from_path
# ---------------------------------------------------------------------------

class TestPageStemFromPath:
    def test_valid_yaml_path(self):
        assert _page_stem_from_path("ui/pages/dashboard.yaml") == "dashboard"

    def test_valid_yml_path(self):
        assert _page_stem_from_path("ui/pages/settings.yml") == "settings"

    def test_non_pages_path_returns_none(self):
        assert _page_stem_from_path("config/ai.json") is None

    def test_not_in_ui_pages_returns_none(self):
        assert _page_stem_from_path("modules/wallet/module.yaml") is None

    def test_too_deep_returns_none(self):
        assert _page_stem_from_path("ui/pages/sub/page.yaml") is None

    def test_non_yaml_extension_returns_none(self):
        assert _page_stem_from_path("ui/pages/page.json") is None

    def test_empty_path_returns_none(self):
        assert _page_stem_from_path("") is None

    def test_dot_slash_normalized(self):
        result = _page_stem_from_path("./ui/pages/overview.yaml")
        assert result == "overview"

    def test_slugifies_stem(self):
        result = _page_stem_from_path("ui/pages/My-Page.yaml")
        assert result == "my_page"


# ---------------------------------------------------------------------------
# 3. _page_stems
# ---------------------------------------------------------------------------

class TestPageStems:
    def test_route_based_stem(self):
        page = {"route": "/dashboard"}
        stems = _page_stems(page)
        assert "dashboard" in stems

    def test_nested_route_uses_last_segment(self):
        page = {"route": "/admin/settings"}
        stems = _page_stems(page)
        assert "settings" in stems

    def test_root_route_excluded(self):
        page = {"route": "/"}
        stems = _page_stems(page)
        assert "dashboard" not in stems  # no slug from "/"

    def test_name_field_included(self):
        page = {"name": "My Dashboard"}
        stems = _page_stems(page)
        assert "my_dashboard" in stems

    def test_id_field_included(self):
        page = {"id": "analytics"}
        stems = _page_stems(page)
        assert "analytics" in stems

    def test_surface_id_field_included(self):
        page = {"surface_id": "profile"}
        stems = _page_stems(page)
        assert "profile" in stems

    def test_multiple_fields_all_included(self):
        page = {"route": "/dash", "name": "Dash", "id": "dash_page"}
        stems = _page_stems(page)
        assert len(stems) >= 2

    def test_empty_page_returns_empty_set(self):
        assert _page_stems({}) == set()


# ---------------------------------------------------------------------------
# 4. _decode_config_hint
# ---------------------------------------------------------------------------

class TestDecodeConfigHint:
    def test_dict_returned_as_is(self):
        d = {"key": "value"}
        assert _decode_config_hint(d) == d

    def test_json_string_decoded(self):
        result = _decode_config_hint('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json_returns_empty(self):
        assert _decode_config_hint("not json{") == {}

    def test_json_list_returns_empty(self):
        assert _decode_config_hint("[1, 2, 3]") == {}

    def test_none_returns_empty(self):
        assert _decode_config_hint(None) == {}

    def test_integer_returns_empty(self):
        assert _decode_config_hint(42) == {}

    def test_empty_string_returns_empty(self):
        assert _decode_config_hint("") == {}

    def test_nested_dict_decoded(self):
        result = _decode_config_hint('{"nested": {"a": 1}}')
        assert result["nested"]["a"] == 1


# ---------------------------------------------------------------------------
# 5. _page_from_plan
# ---------------------------------------------------------------------------

class TestPageFromPlan:
    def test_title_from_title_field(self):
        page = {"title": "My Page"}
        result = _page_from_plan(page, "my_page")
        assert result["title"] == "My Page"

    def test_title_falls_back_to_name(self):
        page = {"name": "My Page"}
        result = _page_from_plan(page, "my_page")
        assert result["title"] == "My Page"

    def test_title_falls_back_to_slug_titlecase(self):
        result = _page_from_plan({}, "my_page")
        assert result["title"] == "My Page"

    def test_route_from_route_field(self):
        page = {"route": "/custom-route"}
        result = _page_from_plan(page, "custom_route")
        assert result["route"] == "/custom-route"

    def test_route_defaults_to_slug(self):
        result = _page_from_plan({}, "my_page")
        assert result["route"] == "/my-page"

    def test_sections_from_sections_hint(self):
        page = {
            "sections_hint": [
                {"primitive": "ResourceTable", "section_id_hint": "s1"},
            ]
        }
        result = _page_from_plan(page, "items")
        assert len(result["sections"]) == 1
        assert result["sections"][0]["primitive"] == "ResourceTable"

    def test_no_sections_hint_adds_default_header(self):
        result = _page_from_plan({"title": "Overview"}, "overview")
        assert len(result["sections"]) == 1
        assert result["sections"][0]["primitive"] == "PageHeader"

    def test_page_type_from_field(self):
        page = {"page_type": "detail"}
        result = _page_from_plan(page, "detail_page")
        assert result["page_type"] == "detail"

    def test_layout_defaults_to_full_width(self):
        result = _page_from_plan({}, "p")
        assert result["layout"] == "full-width"

    def test_navigation_id_is_stem(self):
        result = _page_from_plan({}, "my_page")
        assert result["navigation"]["id"] == "my_page"

    def test_navigation_label_is_title(self):
        page = {"title": "My Page"}
        result = _page_from_plan(page, "my_page")
        assert result["navigation"]["label"] == "My Page"

    def test_config_hint_decoded_in_section(self):
        page = {
            "sections_hint": [
                {
                    "primitive": "Form",
                    "section_id_hint": "form-1",
                    "config_hint": '{"fields": ["name"]}',
                }
            ]
        }
        result = _page_from_plan(page, "form_page")
        section = result["sections"][0]
        assert section["config"] == {"fields": ["name"]}

    def test_shell_mode_defaults_to_workspace(self):
        result = _page_from_plan({}, "p")
        assert result["shell_mode"] == "workspace"

    def test_section_id_from_hint(self):
        page = {
            "sections_hint": [{"primitive": "Form", "section_id_hint": "my-form"}]
        }
        result = _page_from_plan(page, "page")
        assert result["sections"][0]["id"] == "my-form"

    def test_section_id_defaults_to_stem_index(self):
        page = {"sections_hint": [{"primitive": "Form"}]}
        result = _page_from_plan(page, "page")
        assert "page-1" in result["sections"][0]["id"]

    def test_non_dict_hint_entry_skipped(self):
        page = {"sections_hint": ["not_a_dict", {"primitive": "Form", "section_id_hint": "f"}]}
        result = _page_from_plan(page, "page")
        assert len(result["sections"]) == 1
        assert result["sections"][0]["primitive"] == "Form"
