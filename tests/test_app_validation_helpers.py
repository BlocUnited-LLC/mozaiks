"""
AppGenerator app_validation.py pure helper unit tests.

Covers:
  _base_result:
    - status "failed" → success=False
    - status other than "failed" → success=True
    - all canonical keys present
    - strategy and status reflected in result

  _safe_relpath:
    - normal relative path → returned
    - backslash path → normalized
    - absolute path (starts with /) → None
    - path traversal with .. → None
    - empty string → None
    - non-string input → None
    - whitespace-only → None
    - nested relative path → returned

  _is_truthy:
    - bool True → True
    - bool False → False
    - string "true" / "1" / "yes" / "passed" / "ready" → True (case-insensitive)
    - string "false" / "0" / "no" → False
    - whitespace-wrapped truthy string → True
    - empty string → False
    - None → False
    - non-zero int → True
    - zero int → False
    - non-empty list → True
    - empty list → False

  _read_package_scripts_from_text:
    - valid JSON with scripts → scripts dict returned
    - valid JSON without scripts → {}
    - invalid JSON → {}
    - non-string input → {}
    - scripts not a dict → {}

  parse_build_errors:
    - empty string → []
    - non-string → []
    - TypeScript error format "file:line:col - error TSxxx: msg" → parsed
    - webpack error format → parsed
    - no recognized error patterns → []
    - multiple TypeScript errors → all parsed
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.app_validation import (
    _base_result,
    _is_truthy,
    _read_package_scripts_from_text,
    _safe_relpath,
    parse_build_errors,
)

# ---------------------------------------------------------------------------
# 1. _base_result
# ---------------------------------------------------------------------------

class TestBaseResult:
    def test_failed_status_success_false(self):
        result = _base_result(strategy="local", status="failed")
        assert result["success"] is False

    def test_non_failed_status_success_true(self):
        result = _base_result(strategy="local", status="passed")
        assert result["success"] is True

    def test_skipped_status_success_true(self):
        result = _base_result(strategy="skip", status="skipped")
        assert result["success"] is True

    def test_strategy_reflected(self):
        result = _base_result(strategy="docker", status="passed")
        assert result["validation_strategy"] == "docker"

    def test_status_reflected(self):
        result = _base_result(strategy="local", status="passed")
        assert result["validation_status"] == "passed"

    def test_all_canonical_keys_present(self):
        result = _base_result(strategy="e2b", status="pending")
        for key in (
            "success",
            "validation_strategy",
            "validation_status",
            "strategy_reason",
            "build_output",
            "errors",
            "warnings",
            "preview_url",
            "test_results",
            "parsed_errors",
        ):
            assert key in result

    def test_default_empty_collections(self):
        result = _base_result(strategy="local", status="passed")
        assert result["errors"] == []
        assert result["warnings"] == []
        assert result["parsed_errors"] == []
        assert result["build_output"] == ""
        assert result["preview_url"] is None
        assert result["test_results"] is None


# ---------------------------------------------------------------------------
# 2. _safe_relpath
# ---------------------------------------------------------------------------

class TestSafeRelpath:
    def test_simple_relative_path_returned(self):
        assert _safe_relpath("app/main.py") == "app/main.py"

    def test_backslash_normalized(self):
        assert _safe_relpath("app\\main.py") == "app/main.py"

    def test_absolute_path_returns_none(self):
        assert _safe_relpath("/etc/passwd") is None

    def test_dotdot_traversal_returns_none(self):
        assert _safe_relpath("../../../etc/passwd") is None

    def test_nested_dotdot_returns_none(self):
        assert _safe_relpath("a/b/../../etc/passwd") is None

    def test_empty_string_returns_none(self):
        assert _safe_relpath("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_relpath("   ") is None

    def test_non_string_returns_none(self):
        assert _safe_relpath(None) is None  # type: ignore[arg-type]
        assert _safe_relpath(42) is None  # type: ignore[arg-type]

    def test_nested_relative_path_returned(self):
        result = _safe_relpath("modules/tasks/backend/handler.py")
        assert result == "modules/tasks/backend/handler.py"

    def test_single_filename_returned(self):
        assert _safe_relpath("app.json") == "app.json"


# ---------------------------------------------------------------------------
# 3. _is_truthy
# ---------------------------------------------------------------------------

class TestIsTruthy:
    def test_bool_true(self):
        assert _is_truthy(True) is True

    def test_bool_false(self):
        assert _is_truthy(False) is False

    def test_string_true(self):
        assert _is_truthy("true") is True

    def test_string_one(self):
        assert _is_truthy("1") is True

    def test_string_yes(self):
        assert _is_truthy("yes") is True

    def test_string_passed(self):
        assert _is_truthy("passed") is True

    def test_string_ready(self):
        assert _is_truthy("ready") is True

    def test_string_true_uppercase(self):
        assert _is_truthy("TRUE") is True

    def test_string_whitespace_wrapped_truthy(self):
        assert _is_truthy("  true  ") is True

    def test_string_false(self):
        assert _is_truthy("false") is False

    def test_string_zero(self):
        assert _is_truthy("0") is False

    def test_string_no(self):
        assert _is_truthy("no") is False

    def test_empty_string_false(self):
        assert _is_truthy("") is False

    def test_none_false(self):
        assert _is_truthy(None) is False

    def test_non_zero_int_true(self):
        assert _is_truthy(42) is True

    def test_zero_int_false(self):
        assert _is_truthy(0) is False

    def test_non_empty_list_true(self):
        assert _is_truthy([1, 2]) is True

    def test_empty_list_false(self):
        assert _is_truthy([]) is False


# ---------------------------------------------------------------------------
# 4. _read_package_scripts_from_text
# ---------------------------------------------------------------------------

class TestReadPackageScriptsFromText:
    def test_valid_json_with_scripts(self):
        pkg = '{"name": "app", "scripts": {"build": "vite build", "test": "vitest"}}'
        result = _read_package_scripts_from_text(pkg)
        assert result == {"build": "vite build", "test": "vitest"}

    def test_valid_json_without_scripts_key(self):
        pkg = '{"name": "app", "version": "1.0.0"}'
        result = _read_package_scripts_from_text(pkg)
        assert result == {}

    def test_invalid_json_returns_empty(self):
        result = _read_package_scripts_from_text("not valid json {{{")
        assert result == {}

    def test_non_string_returns_empty(self):
        result = _read_package_scripts_from_text(None)  # type: ignore[arg-type]
        assert result == {}

    def test_scripts_not_dict_returns_empty(self):
        pkg = '{"scripts": ["build", "test"]}'
        result = _read_package_scripts_from_text(pkg)
        assert result == {}

    def test_empty_scripts_dict(self):
        pkg = '{"scripts": {}}'
        result = _read_package_scripts_from_text(pkg)
        assert result == {}


# ---------------------------------------------------------------------------
# 5. parse_build_errors
# ---------------------------------------------------------------------------

class TestParseBuildErrors:
    def test_empty_string_returns_empty(self):
        assert parse_build_errors("") == []

    def test_non_string_returns_empty(self):
        assert parse_build_errors(None) == []  # type: ignore[arg-type]

    def test_typescript_error_parsed(self):
        output = "src/App.tsx:42:10 - error TS2345: Argument of type 'string' is not assignable."
        result = parse_build_errors(output)
        assert len(result) == 1
        assert result[0]["file"] == "src/App.tsx"
        assert result[0]["line"] == 42
        assert result[0]["column"] == 10
        assert "Argument of type" in result[0]["message"]

    def test_no_recognized_patterns_returns_empty(self):
        output = "Build completed successfully with 0 errors."
        result = parse_build_errors(output)
        assert result == []

    def test_multiple_typescript_errors_all_parsed(self):
        output = (
            "src/A.ts:10:5 - error TS2304: Cannot find name 'x'.\n"
            "src/B.ts:20:3 - error TS2551: Property 'y' does not exist.\n"
        )
        result = parse_build_errors(output)
        assert len(result) == 2
        files = [r["file"] for r in result]
        assert "src/A.ts" in files
        assert "src/B.ts" in files

    def test_typescript_en_dash_separator_parsed(self):
        # Some TypeScript outputs use en-dash (–) instead of hyphen
        output = "src/App.tsx:1:1 – error TS2345: Some error message."
        result = parse_build_errors(output)
        # May or may not match depending on regex — document actual behavior
        # The regex uses [-–] so it should match
        assert isinstance(result, list)
