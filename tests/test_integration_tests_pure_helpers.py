"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/integration_tests.py

Covers:

  _safe_relpath:
    - valid relative path → returned (backslash normalized)
    - leading slash → None
    - starts with ".." → None
    - absolute Windows path → None
    - non-string input → None
    - empty string → None
    - whitespace-only → None
    - path with ".." in middle → None
    - normal path traversal is preserved

  _is_truthy:
    - True → True
    - False → False
    - "1" → True
    - "true" → True (case-insensitive)
    - "True" → True
    - "yes" → True
    - "passed" → True
    - "ready" → True
    - "0" → False
    - "false" → False
    - "no" → False
    - "" → False
    - None → False
    - 0 → False
    - 1 → True
    - non-empty list → True
    - empty list → False

  _content_contains:
    - needle in file content → True
    - needle not in any file → False
    - empty needle → False
    - empty files_map → False
    - needle spans two files: found in one → True
    - non-string content → not crashed (skipped by isinstance check)

  _parse_env_value:
    - key with value → value returned
    - key not present → None
    - empty env text → None
    - non-string env text → None
    - comment lines skipped
    - blank lines skipped
    - lines without "=" skipped
    - whitespace around key stripped
    - whitespace around value stripped
    - first matching key wins (multiple occurrences)
    - empty target key → None
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.integration_tests import (
    _content_contains,
    _is_truthy,
    _parse_env_value,
    _safe_relpath,
)

# ---------------------------------------------------------------------------
# 1. _safe_relpath
# ---------------------------------------------------------------------------

class TestSafeRelpath:
    def test_simple_relative_path(self):
        result = _safe_relpath("modules/orders/module.yaml")
        assert result == "modules/orders/module.yaml"

    def test_backslash_normalized(self):
        result = _safe_relpath("modules\\orders\\module.yaml")
        assert result is not None
        assert "\\" not in result
        assert "modules" in result

    def test_leading_slash_returns_none(self):
        assert _safe_relpath("/modules/orders/module.yaml") is None

    def test_dotdot_at_start_returns_none(self):
        assert _safe_relpath("../secret.env") is None

    def test_dotdot_in_middle_returns_none(self):
        assert _safe_relpath("modules/../etc/passwd") is None

    def test_non_string_returns_none(self):
        assert _safe_relpath(None) is None
        assert _safe_relpath(42) is None

    def test_empty_string_returns_none(self):
        assert _safe_relpath("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_relpath("   ") is None

    def test_simple_filename_returned(self):
        result = _safe_relpath("module.yaml")
        assert result == "module.yaml"

    def test_nested_path_returned(self):
        result = _safe_relpath("app/modules/orders/backend/service.py")
        assert result is not None
        assert "orders" in result

    def test_returns_string_or_none(self):
        result = _safe_relpath("valid/path.txt")
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# 2. _is_truthy
# ---------------------------------------------------------------------------

class TestIsTruthy:
    def test_true_returns_true(self):
        assert _is_truthy(True) is True

    def test_false_returns_false(self):
        assert _is_truthy(False) is False

    def test_string_1_true(self):
        assert _is_truthy("1") is True

    def test_string_true_true(self):
        assert _is_truthy("true") is True

    def test_string_True_true(self):
        assert _is_truthy("True") is True

    def test_string_yes_true(self):
        assert _is_truthy("yes") is True

    def test_string_passed_true(self):
        assert _is_truthy("passed") is True

    def test_string_ready_true(self):
        assert _is_truthy("ready") is True

    def test_string_0_false(self):
        assert _is_truthy("0") is False

    def test_string_false_false(self):
        assert _is_truthy("false") is False

    def test_string_no_false(self):
        assert _is_truthy("no") is False

    def test_empty_string_false(self):
        assert _is_truthy("") is False

    def test_whitespace_string_false(self):
        assert _is_truthy("  ") is False

    def test_none_false(self):
        assert _is_truthy(None) is False

    def test_zero_false(self):
        assert _is_truthy(0) is False

    def test_one_true(self):
        assert _is_truthy(1) is True

    def test_non_empty_list_true(self):
        assert _is_truthy(["item"]) is True

    def test_empty_list_false(self):
        assert _is_truthy([]) is False

    def test_string_with_whitespace_around(self):
        # "  true  ".strip().lower() == "true" → True
        assert _is_truthy("  true  ") is True


# ---------------------------------------------------------------------------
# 3. _content_contains
# ---------------------------------------------------------------------------

class TestContentContains:
    def test_needle_in_file_content(self):
        files = {"module.py": "from mozaiks import Agent"}
        assert _content_contains(files, "from mozaiks") is True

    def test_needle_not_in_any_file(self):
        files = {"module.py": "hello world"}
        assert _content_contains(files, "MISSING") is False

    def test_empty_needle_returns_false(self):
        files = {"module.py": "anything"}
        assert _content_contains(files, "") is False

    def test_empty_files_map_returns_false(self):
        assert _content_contains({}, "needle") is False

    def test_needle_found_in_one_of_multiple_files(self):
        files = {
            "a.py": "irrelevant",
            "b.py": "import REACT_APP_AGENT_URL",
        }
        assert _content_contains(files, "REACT_APP_AGENT_URL") is True

    def test_needle_exact_match_required(self):
        files = {"f.py": "SOME_VALUE = 1"}
        assert _content_contains(files, "OTHER_VALUE") is False

    def test_non_string_content_not_crashed(self):
        # isinstance(content, str) check guards against non-string values
        files = {"f.py": None}  # type: ignore
        # Should not raise, just skip
        result = _content_contains(files, "anything")  # type: ignore
        assert result is False


# ---------------------------------------------------------------------------
# 4. _parse_env_value
# ---------------------------------------------------------------------------

class TestParseEnvValue:
    def test_key_with_value(self):
        env = "DATABASE_URL=postgres://localhost/mydb\n"
        assert _parse_env_value(env, "DATABASE_URL") == "postgres://localhost/mydb"

    def test_key_not_present_returns_none(self):
        env = "OTHER_KEY=value\n"
        assert _parse_env_value(env, "MISSING_KEY") is None

    def test_empty_env_text_returns_none(self):
        assert _parse_env_value("", "KEY") is None

    def test_non_string_env_text_returns_none(self):
        assert _parse_env_value(None, "KEY") is None  # type: ignore
        assert _parse_env_value(42, "KEY") is None  # type: ignore

    def test_comment_lines_skipped(self):
        env = "# This is a comment\nKEY=value\n"
        assert _parse_env_value(env, "KEY") == "value"

    def test_blank_lines_skipped(self):
        env = "\n\nKEY=value\n\n"
        assert _parse_env_value(env, "KEY") == "value"

    def test_lines_without_equals_skipped(self):
        env = "INVALID_LINE\nKEY=value\n"
        assert _parse_env_value(env, "KEY") == "value"

    def test_whitespace_around_key_stripped(self):
        env = "  KEY  =value\n"
        assert _parse_env_value(env, "KEY") == "value"

    def test_whitespace_around_value_stripped(self):
        env = "KEY=  trimmed_value  \n"
        assert _parse_env_value(env, "KEY") == "trimmed_value"

    def test_first_matching_key_wins(self):
        env = "KEY=first\nKEY=second\n"
        assert _parse_env_value(env, "KEY") == "first"

    def test_empty_target_key_returns_none(self):
        env = "KEY=value\n"
        assert _parse_env_value(env, "") is None

    def test_whitespace_target_key_returns_none(self):
        assert _parse_env_value("KEY=val\n", "   ") is None

    def test_empty_value_returned(self):
        env = "KEY=\n"
        assert _parse_env_value(env, "KEY") == ""

    def test_value_with_equals_sign(self):
        env = "TOKEN=abc=def=ghi\n"
        # split("=", 1) → key="TOKEN", value="abc=def=ghi"
        assert _parse_env_value(env, "TOKEN") == "abc=def=ghi"
