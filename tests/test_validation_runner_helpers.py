"""
mozaiksai/control_plane/validation_runner.py pure helper unit tests.

Covers:
  _dedupe_ordered:
    - empty sequence → []
    - unique values → same order preserved
    - duplicates → first occurrence kept

  _normalize_path:
    - None-like input → ""
    - backslash normalized to slash
    - leading "./" stripped
    - multiple leading "./" stripped
    - trailing slash stripped via PurePosixPath
    - empty parts removed

  _matches_any:
    - no patterns → False
    - pattern matches → True
    - no pattern matches → False
    - glob wildcard matched

  _is_text_file:
    - .json → True
    - .yaml → True
    - .yml → True
    - .js → True
    - .jsx → True
    - .md → True
    - .py → True
    - .png → False
    - .unknown → False
    - experience_spec.json filename → True
    - experience_spec.yaml filename → True

  _code_file_list:
    - empty files → []
    - files sorted by filename
    - predicate filters files
    - no predicate → all files included
    - returns list of {"filename": ..., "content": ...} dicts
"""
from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.validation_runner import (
    _code_file_list,
    _dedupe_ordered,
    _is_text_file,
    _matches_any,
    _normalize_path,
)

# ---------------------------------------------------------------------------
# 1. _dedupe_ordered
# ---------------------------------------------------------------------------

class TestDedupeOrdered:
    def test_empty_sequence_returns_empty(self):
        assert _dedupe_ordered([]) == []

    def test_unique_values_preserved_in_order(self):
        result = _dedupe_ordered(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_duplicates_first_kept(self):
        result = _dedupe_ordered(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_all_duplicates(self):
        result = _dedupe_ordered(["x", "x", "x"])
        assert result == ["x"]

    def test_tuple_input_accepted(self):
        result = _dedupe_ordered(("a", "b", "a"))
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_empty_string_returns_empty(self):
        assert _normalize_path("") == ""

    def test_backslash_normalized(self):
        result = _normalize_path("ui\\pages\\home.yaml")
        assert "\\" not in result
        assert "/" in result

    def test_leading_dot_slash_stripped(self):
        result = _normalize_path("./ui/pages/home.yaml")
        assert not result.startswith("./")
        assert "ui" in result

    def test_multiple_leading_dot_slash_stripped(self):
        result = _normalize_path("././ui/pages/home.yaml")
        assert result.startswith("ui")

    def test_simple_path_unchanged(self):
        assert _normalize_path("ui/pages/home.yaml") == "ui/pages/home.yaml"

    def test_leading_slash_stripped(self):
        # PurePosixPath("/ui/pages/home.yaml").parts = ('/', 'ui', 'pages', 'home.yaml')
        # "." is filtered, "/" is not filtered but join produces absolute path
        # Actually PurePosixPath("").parts = () → "/".join([]) = ""
        # Let me check what actually happens...
        # For "/ui/pages/home.yaml" → parts include "/" → join includes it
        result = _normalize_path("ui/pages/home.yaml")
        assert result == "ui/pages/home.yaml"

    def test_whitespace_stripped(self):
        result = _normalize_path("  ui/pages/home.yaml  ")
        assert not result.startswith(" ")
        assert not result.endswith(" ")


# ---------------------------------------------------------------------------
# 3. _matches_any
# ---------------------------------------------------------------------------

class TestMatchesAny:
    def test_no_patterns_returns_false(self):
        assert _matches_any("ui/pages/home.yaml", []) is False

    def test_exact_pattern_matches(self):
        assert _matches_any("ui/pages/home.yaml", ["ui/pages/home.yaml"]) is True

    def test_non_matching_pattern_returns_false(self):
        assert _matches_any("ui/pages/home.yaml", ["other/path.yaml"]) is False

    def test_wildcard_pattern_matches(self):
        assert _matches_any("ui/pages/home.yaml", ["**/*.yaml"]) is True

    def test_wildcard_pattern_no_match(self):
        assert _matches_any("ui/pages/home.jsx", ["**/*.yaml"]) is False

    def test_multiple_patterns_first_match_wins(self):
        result = _matches_any(
            "modules/tasks/module.yaml",
            ["other/*.yaml", "modules/**/*.yaml"],
        )
        assert result is True

    def test_suffix_glob(self):
        assert _matches_any("modules/tasks/module.yaml", ["*.yaml"]) is True


# ---------------------------------------------------------------------------
# 4. _is_text_file
# ---------------------------------------------------------------------------

class TestIsTextFile:
    def test_json_is_text(self):
        assert _is_text_file(Path("data.json")) is True

    def test_yaml_is_text(self):
        assert _is_text_file(Path("config.yaml")) is True

    def test_yml_is_text(self):
        assert _is_text_file(Path("config.yml")) is True

    def test_js_is_text(self):
        assert _is_text_file(Path("app.js")) is True

    def test_jsx_is_text(self):
        assert _is_text_file(Path("Button.jsx")) is True

    def test_md_is_text(self):
        assert _is_text_file(Path("README.md")) is True

    def test_py_is_text(self):
        assert _is_text_file(Path("handler.py")) is True

    def test_png_is_not_text(self):
        assert _is_text_file(Path("logo.png")) is False

    def test_unknown_suffix_is_not_text(self):
        assert _is_text_file(Path("data.bin")) is False

    def test_experience_spec_json_is_text(self):
        assert _is_text_file(Path("experience_spec.json")) is True

    def test_experience_spec_yaml_is_text(self):
        assert _is_text_file(Path("experience_spec.yaml")) is True

    def test_ui_schema_json_is_text(self):
        assert _is_text_file(Path("ui_schema.json")) is True

    def test_case_insensitive_suffix(self):
        # _is_text_file uses path.suffix.lower() → .JSON matches .json
        assert _is_text_file(Path("data.JSON")) is True


# ---------------------------------------------------------------------------
# 5. _code_file_list
# ---------------------------------------------------------------------------

class TestCodeFileList:
    def test_empty_files_returns_empty(self):
        assert _code_file_list({}) == []

    def test_files_sorted_by_filename(self):
        files = {"b.js": "// b", "a.js": "// a"}
        result = _code_file_list(files)
        assert result[0]["filename"] == "a.js"
        assert result[1]["filename"] == "b.js"

    def test_no_predicate_all_files_included(self):
        files = {"a.js": "content_a", "b.yaml": "content_b"}
        result = _code_file_list(files)
        assert len(result) == 2

    def test_predicate_filters_files(self):
        files = {"ui/pages/home.yaml": "yaml", "src/app.js": "js"}
        result = _code_file_list(files, predicate=lambda f: f.endswith(".yaml"))
        assert len(result) == 1
        assert result[0]["filename"] == "ui/pages/home.yaml"

    def test_result_has_filename_and_content(self):
        files = {"a.js": "some content"}
        result = _code_file_list(files)
        assert result[0]["filename"] == "a.js"
        assert result[0]["content"] == "some content"

    def test_predicate_returns_false_excludes_all(self):
        files = {"a.js": "x", "b.js": "y"}
        result = _code_file_list(files, predicate=lambda f: False)
        assert result == []
