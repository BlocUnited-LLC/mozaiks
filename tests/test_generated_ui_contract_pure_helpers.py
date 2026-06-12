"""
Pure helper unit tests for:
  factory_app/workflows/_shared/generated_ui_contract.py

Covers helpers NOT already tested in test_generated_ui_contract.py:

  dedupe:
    - empty iterable → []
    - duplicates removed (first occurrence kept)
    - order preserved
    - distinct items all included

  _parse_public_imports:
    - no @mozaiks/chat-ui/ui import → []
    - single named specifier returned
    - multiple specifiers returned
    - "as" alias resolved to original name
    - whitespace around specifiers stripped
    - import from different source skipped

  _class_literals:
    - no className → []
    - double-quoted className → value returned
    - single-quoted className → value returned
    - backtick className → value returned
    - brace-backtick className → value returned
    - multiple className attributes → all returned

  _looks_like_local_card_shell:
    - missing rounded → False
    - missing border → False
    - missing surface background → False
    - all three present → True
    - rounded[ syntax → True
    - bg-background variant → True
    - bg-muted variant → True

  _file_name:
    - "filename" key → returned
    - "path" key → returned when no filename
    - missing both → empty string
    - whitespace stripped

  _bundle_file_map:
    - empty list → {}
    - non-dict item → skipped
    - missing filename → skipped
    - missing content → skipped
    - non-string content → skipped
    - docs/ directory skipped
    - tests/ directory skipped
    - backslash paths normalized
    - valid item → included
    - filename is key, content is value

  _section_children:
    - no config key → []
    - config not a dict → []
    - config with no children/sections/items → []
    - children list → items with primitive included
    - sections list → items with primitive included
    - items list → items with primitive included
    - dict without primitive → excluded
    - non-dict items in list → excluded

  _strings_from_value:
    - string value with valid key_name → yielded
    - string value with invalid key_name → not yielded
    - string value with empty key_name → not yielded
    - dict recursed (each value with its key)
    - list recursed (key_name propagated)
    - non-string, non-dict, non-list → nothing yielded

  _resolve_relative_import:
    - simple relative import → resolved path
    - ../ navigates up one directory
    - no suffix → .jsx appended
    - path with existing suffix → suffix preserved

  _import_path_exists:
    - exact path in file_names → True
    - path not found → False
    - no suffix → .jsx variant checked
    - no suffix → .js variant checked
    - no suffix → .tsx variant checked
    - no suffix → .ts variant checked
    - path with suffix: only exact match checked

  _non_empty_string:
    - non-string → False
    - empty string → False
    - whitespace only → False
    - valid string → True

  _parse_registered_components:
    - no registerComponent → empty set
    - single call → component name returned
    - multiple calls → all names returned
    - double-quoted name → included
    - backtick-quoted name → included
"""
from __future__ import annotations

from pathlib import PurePosixPath

from factory_app.workflows._shared.generated_ui_contract import (
    _bundle_file_map,
    _class_literals,
    _file_name,
    _import_path_exists,
    _looks_like_local_card_shell,
    _non_empty_string,
    _parse_public_imports,
    _parse_registered_components,
    _resolve_relative_import,
    _section_children,
    _strings_from_value,
    dedupe,
)

# ---------------------------------------------------------------------------
# 1. dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_iterable_returns_empty(self):
        assert dedupe([]) == []

    def test_duplicates_removed_first_occurrence_kept(self):
        result = dedupe(["a", "b", "a"])
        assert result == ["a", "b"]

    def test_order_preserved(self):
        result = dedupe(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_distinct_items_all_included(self):
        result = dedupe(["x", "y", "z"])
        assert result == ["x", "y", "z"]

    def test_single_item(self):
        assert dedupe(["only"]) == ["only"]

    def test_multiple_duplicates(self):
        result = dedupe(["a", "a", "a"])
        assert result == ["a"]


# ---------------------------------------------------------------------------
# 2. _parse_public_imports
# ---------------------------------------------------------------------------

class TestParsePublicImports:
    def test_no_import_returns_empty(self):
        assert _parse_public_imports("const x = 1;") == []

    def test_single_specifier(self):
        code = "import { Button } from '@mozaiks/chat-ui/ui';"
        result = _parse_public_imports(code)
        assert "Button" in result

    def test_multiple_specifiers(self):
        code = "import { Button, Panel, StatusPill } from '@mozaiks/chat-ui/ui';"
        result = _parse_public_imports(code)
        assert "Button" in result
        assert "Panel" in result
        assert "StatusPill" in result

    def test_as_alias_resolved_to_original_name(self):
        code = "import { Button as Btn } from '@mozaiks/chat-ui/ui';"
        result = _parse_public_imports(code)
        assert "Button" in result
        assert "Btn" not in result

    def test_whitespace_around_specifiers_stripped(self):
        code = "import {  Button  ,  Panel  } from '@mozaiks/chat-ui/ui';"
        result = _parse_public_imports(code)
        assert "Button" in result
        assert "Panel" in result

    def test_different_source_skipped(self):
        code = "import { Button } from '@other/library';"
        result = _parse_public_imports(code)
        assert result == []

    def test_double_quoted_source(self):
        code = 'import { Card } from "@mozaiks/chat-ui/ui";'
        result = _parse_public_imports(code)
        assert "Card" in result


# ---------------------------------------------------------------------------
# 3. _class_literals
# ---------------------------------------------------------------------------

class TestClassLiterals:
    def test_no_classname_returns_empty(self):
        assert _class_literals("const x = 1;") == []

    def test_double_quoted_classname(self):
        result = _class_literals('className="flex items-center"')
        assert "flex items-center" in result

    def test_single_quoted_classname(self):
        result = _class_literals("className='text-sm font-bold'")
        assert "text-sm font-bold" in result

    def test_backtick_classname(self):
        result = _class_literals("className=`rounded-lg border`")
        assert "rounded-lg border" in result

    def test_brace_backtick_classname(self):
        result = _class_literals("className={`bg-card rounded`}")
        assert "bg-card rounded" in result

    def test_multiple_classnames_all_returned(self):
        code = 'className="flex" className="text-sm"'
        result = _class_literals(code)
        assert len(result) == 2

    def test_empty_classname_skipped(self):
        # Empty string is falsy — the if-value guard excludes it
        result = _class_literals('className=""')
        assert result == []


# ---------------------------------------------------------------------------
# 4. _looks_like_local_card_shell
# ---------------------------------------------------------------------------

class TestLooksLikeLocalCardShell:
    def test_missing_rounded_returns_false(self):
        assert _looks_like_local_card_shell("border bg-card") is False

    def test_missing_border_returns_false(self):
        assert _looks_like_local_card_shell("rounded-lg bg-card") is False

    def test_missing_surface_background_returns_false(self):
        assert _looks_like_local_card_shell("rounded-lg border text-primary") is False

    def test_all_three_present_returns_true(self):
        assert _looks_like_local_card_shell("rounded-lg border bg-card") is True

    def test_rounded_bracket_syntax_true(self):
        assert _looks_like_local_card_shell("rounded[md] border bg-card") is True

    def test_bg_background_variant(self):
        assert _looks_like_local_card_shell("rounded-md border bg-background") is True

    def test_bg_muted_variant(self):
        assert _looks_like_local_card_shell("rounded-sm border bg-muted") is True

    def test_empty_string_returns_false(self):
        assert _looks_like_local_card_shell("") is False


# ---------------------------------------------------------------------------
# 5. _file_name
# ---------------------------------------------------------------------------

class TestFileName:
    def test_filename_key_returned(self):
        assert _file_name({"filename": "ui/MyComponent.jsx"}) == "ui/MyComponent.jsx"

    def test_path_key_used_when_no_filename(self):
        assert _file_name({"path": "src/app.js"}) == "src/app.js"

    def test_filename_takes_priority_over_path(self):
        result = _file_name({"filename": "a.jsx", "path": "b.jsx"})
        assert result == "a.jsx"

    def test_missing_both_returns_empty_string(self):
        assert _file_name({}) == ""

    def test_whitespace_stripped(self):
        assert _file_name({"filename": "  app.jsx  "}) == "app.jsx"

    def test_none_filename_falls_to_path(self):
        assert _file_name({"filename": None, "path": "x.js"}) == "x.js"


# ---------------------------------------------------------------------------
# 6. _bundle_file_map
# ---------------------------------------------------------------------------

class TestBundleFileMap:
    def test_empty_list_returns_empty(self):
        assert _bundle_file_map([]) == {}

    def test_non_dict_item_skipped(self):
        result = _bundle_file_map(["not-a-dict"])
        assert result == {}

    def test_missing_filename_skipped(self):
        result = _bundle_file_map([{"content": "const x = 1;"}])
        assert result == {}

    def test_missing_content_skipped(self):
        result = _bundle_file_map([{"filename": "app.jsx"}])
        assert result == {}

    def test_non_string_content_skipped(self):
        result = _bundle_file_map([{"filename": "app.jsx", "content": 123}])
        assert result == {}

    def test_valid_item_included(self):
        result = _bundle_file_map([{"filename": "ui/App.jsx", "content": "export default () => null;"}])
        assert "ui/App.jsx" in result
        assert result["ui/App.jsx"] == "export default () => null;"

    def test_docs_directory_skipped(self):
        result = _bundle_file_map([{"filename": "docs/component.jsx", "content": "x"}])
        assert result == {}

    def test_tests_directory_skipped(self):
        result = _bundle_file_map([{"filename": "tests/component.jsx", "content": "x"}])
        assert result == {}

    def test_backslash_paths_normalized(self):
        result = _bundle_file_map([{"filename": "ui\\App.jsx", "content": "x"}])
        assert "ui/App.jsx" in result

    def test_filename_is_key(self):
        result = _bundle_file_map([{"filename": "page.jsx", "content": "hello"}])
        assert list(result.keys()) == ["page.jsx"]

    def test_path_key_also_works(self):
        result = _bundle_file_map([{"path": "ui/Page.jsx", "content": "hello"}])
        assert "ui/Page.jsx" in result


# ---------------------------------------------------------------------------
# 7. _section_children
# ---------------------------------------------------------------------------

class TestSectionChildren:
    def test_no_config_key_returns_empty(self):
        assert _section_children({}) == []

    def test_config_not_dict_returns_empty(self):
        assert _section_children({"config": "not-a-dict"}) == []

    def test_config_with_no_children_returns_empty(self):
        assert _section_children({"config": {}}) == []

    def test_children_list_with_primitive_included(self):
        section = {"config": {"children": [{"primitive": "Panel", "id": "p1"}]}}
        result = _section_children(section)
        assert len(result) == 1
        assert result[0]["primitive"] == "Panel"

    def test_sections_list_included(self):
        section = {"config": {"sections": [{"primitive": "SurfaceCard"}]}}
        result = _section_children(section)
        assert len(result) == 1

    def test_items_list_included(self):
        section = {"config": {"items": [{"primitive": "DataTable"}]}}
        result = _section_children(section)
        assert len(result) == 1

    def test_dict_without_primitive_excluded(self):
        section = {"config": {"children": [{"id": "no-primitive"}]}}
        result = _section_children(section)
        assert result == []

    def test_non_dict_in_list_excluded(self):
        section = {"config": {"children": ["string-item", 42]}}
        result = _section_children(section)
        assert result == []

    def test_multiple_children(self):
        section = {"config": {"children": [
            {"primitive": "Panel"},
            {"primitive": "SurfaceCard"},
        ]}}
        result = _section_children(section)
        assert len(result) == 2


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

    def test_string_with_description_key_yielded(self):
        result = list(_strings_from_value("Some desc", key_name="description"))
        assert result == ["Some desc"]

    def test_string_with_message_key_yielded(self):
        result = list(_strings_from_value("msg", key_name="message"))
        assert result == ["msg"]

    def test_string_with_subtitle_key_yielded(self):
        result = list(_strings_from_value("sub", key_name="subtitle"))
        assert result == ["sub"]

    def test_string_with_placeholder_key_yielded(self):
        result = list(_strings_from_value("Enter...", key_name="placeholder"))
        assert result == ["Enter..."]

    def test_string_with_empty_key_name_not_yielded(self):
        result = list(_strings_from_value("text", key_name=""))
        assert result == []

    def test_string_with_invalid_key_name_not_yielded(self):
        result = list(_strings_from_value("text", key_name="action"))
        assert result == []

    def test_dict_recursed_with_correct_keys(self):
        result = list(_strings_from_value({"title": "My Title", "action": "click"}))
        assert "My Title" in result
        assert "click" not in result

    def test_list_recursed_with_key_name(self):
        result = list(_strings_from_value(["Item 1", "Item 2"], key_name="title"))
        assert "Item 1" in result
        assert "Item 2" in result

    def test_integer_yields_nothing(self):
        result = list(_strings_from_value(42, key_name="title"))
        assert result == []

    def test_none_yields_nothing(self):
        result = list(_strings_from_value(None, key_name="title"))
        assert result == []

    def test_nested_dict_in_list_recursed(self):
        data = [{"title": "Page Title"}]
        result = list(_strings_from_value(data, key_name=""))
        assert "Page Title" in result


# ---------------------------------------------------------------------------
# 9. _resolve_relative_import
# ---------------------------------------------------------------------------

class TestResolveRelativeImport:
    def test_simple_relative_import(self):
        base = PurePosixPath("ui/pages")
        result = _resolve_relative_import(base, "./MyComponent")
        assert "ui/pages/MyComponent" in result

    def test_parent_dir_navigation(self):
        base = PurePosixPath("ui/pages")
        result = _resolve_relative_import(base, "../components/Button")
        assert "ui/components/Button" in result

    def test_no_suffix_adds_jsx(self):
        base = PurePosixPath("ui/pages")
        result = _resolve_relative_import(base, "./MyComponent")
        assert result.endswith(".jsx")

    def test_existing_suffix_preserved(self):
        base = PurePosixPath("ui/pages")
        result = _resolve_relative_import(base, "./utils.js")
        assert result.endswith(".js")

    def test_double_parent_navigation(self):
        base = PurePosixPath("ui/pages/nested")
        result = _resolve_relative_import(base, "../../components/Card")
        assert "ui/components/Card" in result

    def test_posix_separator_in_result(self):
        base = PurePosixPath("src/components")
        result = _resolve_relative_import(base, "./Button")
        assert "\\" not in result


# ---------------------------------------------------------------------------
# 10. _import_path_exists
# ---------------------------------------------------------------------------

class TestImportPathExists:
    def test_exact_path_found(self):
        assert _import_path_exists("ui/App.jsx", {"ui/App.jsx"}) is True

    def test_path_not_found(self):
        assert _import_path_exists("ui/Missing.jsx", {"ui/App.jsx"}) is False

    def test_no_suffix_jsx_variant_checked(self):
        assert _import_path_exists("ui/App", {"ui/App.jsx"}) is True

    def test_no_suffix_js_variant_checked(self):
        assert _import_path_exists("ui/App", {"ui/App.js"}) is True

    def test_no_suffix_tsx_variant_checked(self):
        assert _import_path_exists("ui/App", {"ui/App.tsx"}) is True

    def test_no_suffix_ts_variant_checked(self):
        assert _import_path_exists("ui/App", {"ui/App.ts"}) is True

    def test_path_with_suffix_only_exact_match(self):
        # Has .jsx suffix but not in file_names — no fallback variants checked
        assert _import_path_exists("ui/App.jsx", {"ui/App.js"}) is False

    def test_empty_file_names_returns_false(self):
        assert _import_path_exists("ui/App", set()) is False


# ---------------------------------------------------------------------------
# 11. _non_empty_string
# ---------------------------------------------------------------------------

class TestNonEmptyString:
    def test_non_string_returns_false(self):
        assert _non_empty_string(42) is False
        assert _non_empty_string(None) is False
        assert _non_empty_string([]) is False

    def test_empty_string_returns_false(self):
        assert _non_empty_string("") is False

    def test_whitespace_only_returns_false(self):
        assert _non_empty_string("   ") is False

    def test_valid_string_returns_true(self):
        assert _non_empty_string("hello") is True

    def test_single_char_returns_true(self):
        assert _non_empty_string("x") is True

    def test_string_with_content_and_spaces_returns_true(self):
        assert _non_empty_string("  hello  ") is True


# ---------------------------------------------------------------------------
# 12. _parse_registered_components
# ---------------------------------------------------------------------------

class TestParseRegisteredComponents:
    def test_no_register_call_returns_empty_set(self):
        assert _parse_registered_components("const x = 1;") == set()

    def test_single_quoted_name(self):
        result = _parse_registered_components("registerComponent('MyWidget', MyWidget);")
        assert "MyWidget" in result

    def test_double_quoted_name(self):
        result = _parse_registered_components('registerComponent("DashboardCard", DashboardCard);')
        assert "DashboardCard" in result

    def test_backtick_quoted_name(self):
        result = _parse_registered_components("registerComponent(`StatusPanel`, StatusPanel);")
        assert "StatusPanel" in result

    def test_multiple_calls_all_returned(self):
        code = (
            "registerComponent('Comp1', Comp1);\n"
            "registerComponent('Comp2', Comp2);\n"
        )
        result = _parse_registered_components(code)
        assert "Comp1" in result
        assert "Comp2" in result

    def test_returns_set(self):
        result = _parse_registered_components("registerComponent('X', X);")
        assert isinstance(result, set)
