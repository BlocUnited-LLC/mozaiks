"""
generated_ui_contract.py pure helper unit tests.

Covers:
  _non_empty_string:
    - non-string → False
    - empty string → False
    - whitespace only → False
    - valid string → True

  _file_name:
    - dict with "filename" key → returned
    - dict with "path" key → returned (fallback)
    - dict with neither → ""
    - whitespace value stripped

  _looks_like_local_card_shell:
    - has rounded + border + bg-card → True
    - has rounded + border + bg-background → True
    - has rounded + border + bg-muted → True
    - missing border → False
    - missing rounded → False
    - missing background → False
    - partial match → False

  _resolve_relative_import:
    - simple relative path → resolved
    - ".." traversal → resolved correctly
    - path without extension → ".jsx" appended
    - path with extension → extension preserved

  _import_path_exists:
    - exact match → True
    - path not in file_names → False
    - path without extension + .jsx variant → True
    - path without extension + .js variant → True
    - path without extension and no match → False

  _route_manifest_pages_from_content:
    - valid JSON with pages list → pages list returned
    - valid JSON with routes fallback → routes list returned
    - valid JSON without pages/routes → empty list, no errors
    - invalid JSON → empty list, error message
    - non-dict JSON → empty list, error message
    - non-list pages → empty list, error message

  _section_children:
    - no config → []
    - config without children/sections/items → []
    - config.children with primitive items → returned
    - config.sections with primitive items → returned
    - non-dict items excluded
    - items without primitive excluded

  _strings_from_value:
    - string with text key → yielded
    - string with non-text key → not yielded
    - dict → recurses into values
    - list → recurses into items
    - other types → nothing yielded
"""
from __future__ import annotations

from pathlib import PurePosixPath

from factory_app.workflows._shared.generated_ui_contract import (
    _file_name,
    _import_path_exists,
    _looks_like_local_card_shell,
    _non_empty_string,
    _resolve_relative_import,
    _route_manifest_pages_from_content,
    _section_children,
    _strings_from_value,
)

# ---------------------------------------------------------------------------
# 1. _non_empty_string
# ---------------------------------------------------------------------------

class TestNonEmptyString:
    def test_non_string_returns_false(self):
        assert _non_empty_string(None) is False
        assert _non_empty_string(42) is False
        assert _non_empty_string([]) is False

    def test_empty_string_returns_false(self):
        assert _non_empty_string("") is False

    def test_whitespace_only_returns_false(self):
        assert _non_empty_string("   ") is False

    def test_valid_string_returns_true(self):
        assert _non_empty_string("hello") is True

    def test_single_char_returns_true(self):
        assert _non_empty_string("x") is True


# ---------------------------------------------------------------------------
# 2. _file_name
# ---------------------------------------------------------------------------

class TestFileName:
    def test_filename_key(self):
        assert _file_name({"filename": "Button.jsx"}) == "Button.jsx"

    def test_path_key_fallback(self):
        assert _file_name({"path": "components/Button.jsx"}) == "components/Button.jsx"

    def test_neither_key_returns_empty(self):
        assert _file_name({}) == ""

    def test_whitespace_stripped(self):
        assert _file_name({"filename": "  Button.jsx  "}) == "Button.jsx"

    def test_filename_takes_priority_over_path(self):
        assert _file_name({"filename": "Button.jsx", "path": "other.jsx"}) == "Button.jsx"


# ---------------------------------------------------------------------------
# 3. _looks_like_local_card_shell
# ---------------------------------------------------------------------------

class TestLooksLikeLocalCardShell:
    def test_rounded_border_bg_card_returns_true(self):
        assert _looks_like_local_card_shell("rounded-lg border bg-card") is True

    def test_rounded_border_bg_background_returns_true(self):
        assert _looks_like_local_card_shell("rounded-md border bg-background p-4") is True

    def test_rounded_border_bg_muted_returns_true(self):
        assert _looks_like_local_card_shell("rounded-sm border-2 bg-muted") is True

    def test_missing_border_returns_false(self):
        assert _looks_like_local_card_shell("rounded-lg bg-card") is False

    def test_missing_rounded_returns_false(self):
        assert _looks_like_local_card_shell("border bg-card") is False

    def test_missing_background_returns_false(self):
        assert _looks_like_local_card_shell("rounded-lg border text-sm") is False

    def test_empty_string_returns_false(self):
        assert _looks_like_local_card_shell("") is False

    def test_rounded_bracket_syntax_accepted(self):
        # rounded[...] form is also accepted
        assert _looks_like_local_card_shell("rounded[4px] border bg-card") is True


# ---------------------------------------------------------------------------
# 4. _resolve_relative_import
# ---------------------------------------------------------------------------

class TestResolveRelativeImport:
    def test_simple_relative_path(self):
        base = PurePosixPath("app/ui/pages")
        result = _resolve_relative_import(base, "./Button")
        assert "Button.jsx" in result

    def test_dotdot_traversal_resolved(self):
        base = PurePosixPath("app/ui/pages/home")
        result = _resolve_relative_import(base, "../shared/Card")
        assert "shared/Card.jsx" in result

    def test_path_with_jsx_extension_preserved(self):
        base = PurePosixPath("app/ui/pages")
        result = _resolve_relative_import(base, "./Button.jsx")
        assert result.endswith(".jsx")

    def test_path_without_extension_gets_jsx(self):
        base = PurePosixPath("app/ui/pages")
        result = _resolve_relative_import(base, "./Card")
        assert result.endswith(".jsx")

    def test_path_with_js_extension_preserved(self):
        base = PurePosixPath("app/ui/pages")
        result = _resolve_relative_import(base, "./utils.js")
        assert result.endswith(".js")


# ---------------------------------------------------------------------------
# 5. _import_path_exists
# ---------------------------------------------------------------------------

class TestImportPathExists:
    def test_exact_match_returns_true(self):
        assert _import_path_exists("app/ui/Button.jsx", {"app/ui/Button.jsx"}) is True

    def test_no_match_returns_false(self):
        assert _import_path_exists("app/ui/Missing.jsx", {"app/ui/Button.jsx"}) is False

    def test_path_without_extension_jsx_variant(self):
        assert _import_path_exists("app/ui/Button", {"app/ui/Button.jsx"}) is True

    def test_path_without_extension_js_variant(self):
        assert _import_path_exists("app/ui/utils", {"app/ui/utils.js"}) is True

    def test_path_without_extension_tsx_variant(self):
        assert _import_path_exists("app/ui/Button", {"app/ui/Button.tsx"}) is True

    def test_path_without_extension_ts_variant(self):
        assert _import_path_exists("app/ui/types", {"app/ui/types.ts"}) is True

    def test_path_without_extension_no_match_returns_false(self):
        assert _import_path_exists("app/ui/Missing", {"app/ui/Other.jsx"}) is False

    def test_path_with_extension_no_match_returns_false(self):
        # Path with extension but not found → False (no suffix guessing)
        assert _import_path_exists("app/ui/Button.jsx", {"app/ui/Button.tsx"}) is False


# ---------------------------------------------------------------------------
# 6. _route_manifest_pages_from_content
# ---------------------------------------------------------------------------

class TestRouteManifestPagesFromContent:
    def test_valid_json_with_pages(self):
        import json
        content = json.dumps({"pages": [{"path": "/home", "component": "Home"}]})
        pages, errors = _route_manifest_pages_from_content(content, source_label="test")
        assert len(pages) == 1
        assert errors == []

    def test_valid_json_with_routes_fallback(self):
        import json
        content = json.dumps({"routes": [{"path": "/about"}]})
        pages, errors = _route_manifest_pages_from_content(content, source_label="test")
        assert len(pages) == 1
        assert errors == []

    def test_valid_json_without_pages_or_routes(self):
        import json
        content = json.dumps({"version": "1.0"})
        pages, errors = _route_manifest_pages_from_content(content, source_label="test")
        assert pages == []
        assert errors == []

    def test_invalid_json_returns_error(self):
        pages, errors = _route_manifest_pages_from_content("{broken", source_label="app")
        assert pages == []
        assert any("not valid JSON" in e for e in errors)

    def test_non_dict_json_returns_error(self):
        import json
        content = json.dumps(["not", "a", "dict"])
        pages, errors = _route_manifest_pages_from_content(content, source_label="app")
        assert pages == []
        assert any("must be an object" in e for e in errors)

    def test_non_list_pages_returns_error(self):
        import json
        content = json.dumps({"pages": "not_a_list"})
        pages, errors = _route_manifest_pages_from_content(content, source_label="app")
        assert pages == []
        assert any("must be a list" in e for e in errors)

    def test_non_dict_page_items_excluded(self):
        import json
        content = json.dumps({"pages": [{"path": "/"}, "not_a_dict", None]})
        pages, errors = _route_manifest_pages_from_content(content, source_label="test")
        assert len(pages) == 1
        assert errors == []


# ---------------------------------------------------------------------------
# 7. _section_children
# ---------------------------------------------------------------------------

class TestSectionChildren:
    def test_no_config_returns_empty(self):
        assert _section_children({}) == []

    def test_config_not_dict_returns_empty(self):
        assert _section_children({"config": "not_a_dict"}) == []

    def test_config_without_children_keys_returns_empty(self):
        assert _section_children({"config": {"other": "value"}}) == []

    def test_config_children_with_primitive(self):
        section = {"config": {"children": [{"primitive": "Button", "label": "OK"}]}}
        result = _section_children(section)
        assert len(result) == 1
        assert result[0]["primitive"] == "Button"

    def test_config_sections_key_used(self):
        section = {"config": {"sections": [{"primitive": "Panel"}]}}
        result = _section_children(section)
        assert len(result) == 1

    def test_config_items_key_used(self):
        section = {"config": {"items": [{"primitive": "Card"}]}}
        result = _section_children(section)
        assert len(result) == 1

    def test_items_without_primitive_excluded(self):
        section = {"config": {"children": [
            {"primitive": "Button"},
            {"no_primitive": "here"},
        ]}}
        result = _section_children(section)
        assert len(result) == 1

    def test_non_dict_items_excluded(self):
        section = {"config": {"children": ["not_a_dict", {"primitive": "Button"}]}}
        result = _section_children(section)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 8. _strings_from_value
# ---------------------------------------------------------------------------

class TestStringsFromValue:
    def test_string_with_title_key_yielded(self):
        result = list(_strings_from_value("Hello World", key_name="title"))
        assert result == ["Hello World"]

    def test_string_with_label_key_yielded(self):
        result = list(_strings_from_value("Submit", key_name="label"))
        assert result == ["Submit"]

    def test_string_with_non_text_key_not_yielded(self):
        result = list(_strings_from_value("some_value", key_name="id"))
        assert result == []

    def test_dict_recurses_into_values(self):
        data = {"title": "My Page", "id": "page-1"}
        result = list(_strings_from_value(data))
        assert "My Page" in result
        # "page-1" under "id" should not be yielded
        assert "page-1" not in result

    def test_list_recurses_into_items(self):
        data = [{"title": "Item 1"}, {"title": "Item 2"}]
        result = list(_strings_from_value(data, key_name="sections"))
        assert "Item 1" in result
        assert "Item 2" in result

    def test_none_yields_nothing(self):
        result = list(_strings_from_value(None, key_name="title"))
        assert result == []

    def test_integer_yields_nothing(self):
        result = list(_strings_from_value(42, key_name="title"))
        assert result == []

    def test_description_key_yielded(self):
        result = list(_strings_from_value("A description", key_name="description"))
        assert result == ["A description"]

    def test_subtitle_key_yielded(self):
        result = list(_strings_from_value("A subtitle", key_name="subtitle"))
        assert result == ["A subtitle"]
