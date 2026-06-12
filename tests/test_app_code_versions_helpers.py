"""
App code versions pure helper unit tests.

Covers:
  _safe_zip_path:
    - empty/None → None
    - absolute path (leading /) → None
    - ".." component → None
    - backslashes normalized to forward slashes
    - leading slash stripped
    - valid relative path returned
    - whitespace stripped

  _normalize_file:
    - missing/unsafe path → None
    - valid path with no contentBase64 → path set, sha from entry, size from entry
    - valid path with valid base64 → sha recomputed from decoded bytes, size recomputed
    - invalid base64 string → original sha/size preserved
    - content not a string → contentBase64=None in output
    - sizeBytes not int → 0 in output

  _file_map:
    - empty snapshot → empty dict
    - snapshot with no "files" key → empty dict
    - non-list "files" → empty dict
    - files list with valid entries → path → item dict
    - item without "path" string → skipped
    - item with non-string path → skipped
    - multiple files → all in map
"""
from __future__ import annotations

import base64
import hashlib

from mozaiksai.core.workflow.generator_support.app_code_versions import (
    _file_map,
    _normalize_file,
    _safe_zip_path,
)

# ---------------------------------------------------------------------------
# 1. _safe_zip_path
# ---------------------------------------------------------------------------

class TestSafeZipPath:
    def test_none_returns_none(self):
        assert _safe_zip_path(None) is None  # type: ignore[arg-type]

    def test_empty_returns_none(self):
        assert _safe_zip_path("") is None

    def test_whitespace_only_returns_none(self):
        assert _safe_zip_path("   ") is None

    def test_leading_slash_stripped_becomes_relative(self):
        # _safe_zip_path strips leading "/" via lstrip → not treated as absolute
        result = _safe_zip_path("/app/config.yaml")
        assert result == "app/config.yaml"

    def test_dotdot_returns_none(self):
        assert _safe_zip_path("../escape.py") is None
        assert _safe_zip_path("subdir/../../etc/passwd") is None

    def test_backslashes_normalized(self):
        result = _safe_zip_path("modules\\mymod\\module.yaml")
        assert result == "modules/mymod/module.yaml"

    def test_only_slash_returns_none(self):
        # After lstrip("/") → "" → None
        assert _safe_zip_path("/") is None

    def test_valid_relative_path(self):
        assert _safe_zip_path("app/module.yaml") == "app/module.yaml"

    def test_simple_filename(self):
        assert _safe_zip_path("file.py") == "file.py"

    def test_nested_path(self):
        assert _safe_zip_path("a/b/c/d.txt") == "a/b/c/d.txt"


# ---------------------------------------------------------------------------
# 2. _normalize_file
# ---------------------------------------------------------------------------

def _b64(content: bytes) -> str:
    return base64.b64encode(content).decode("ascii")


class TestNormalizeFile:
    def test_missing_path_returns_none(self):
        assert _normalize_file({}) is None

    def test_unsafe_path_returns_none(self):
        assert _normalize_file({"path": "../escape.py"}) is None

    def test_valid_path_no_content(self):
        entry = {"path": "app/module.yaml", "sha256": "abc123", "sizeBytes": 42}
        result = _normalize_file(entry)
        assert result is not None
        assert result["path"] == "app/module.yaml"
        assert result["sha256"] == "abc123"
        assert result["sizeBytes"] == 42
        assert result["contentBase64"] is None

    def test_valid_base64_content_recomputes_sha_and_size(self):
        raw = b"hello world"
        expected_sha = hashlib.sha256(raw).hexdigest()
        entry = {
            "path": "app/code.py",
            "contentBase64": _b64(raw),
            "sha256": "old_sha",
            "sizeBytes": 0,
        }
        result = _normalize_file(entry)
        assert result is not None
        assert result["sha256"] == expected_sha
        assert result["sizeBytes"] == len(raw)
        assert result["contentBase64"] == _b64(raw)

    def test_invalid_base64_preserves_original_sha_and_size(self):
        entry = {
            "path": "app/code.py",
            "contentBase64": "not-valid-base64!!!",
            "sha256": "preserved_sha",
            "sizeBytes": 99,
        }
        result = _normalize_file(entry)
        assert result is not None
        # Invalid base64 → sha/size not recomputed
        assert result["sha256"] == "preserved_sha"
        assert result["sizeBytes"] == 99

    def test_non_string_content_becomes_none(self):
        entry = {"path": "app/file.py", "contentBase64": 12345}
        result = _normalize_file(entry)
        assert result is not None
        assert result["contentBase64"] is None

    def test_non_int_size_becomes_zero(self):
        entry = {"path": "app/file.py", "sizeBytes": "not-int"}
        result = _normalize_file(entry)
        assert result is not None
        assert result["sizeBytes"] == 0

    def test_non_string_sha_becomes_none(self):
        entry = {"path": "app/file.py", "sha256": 42}
        result = _normalize_file(entry)
        assert result is not None
        assert result["sha256"] is None


# ---------------------------------------------------------------------------
# 3. _file_map
# ---------------------------------------------------------------------------

class TestFileMap:
    def test_empty_snapshot_returns_empty(self):
        assert _file_map({}) == {}

    def test_no_files_key_returns_empty(self):
        assert _file_map({"other": "data"}) == {}

    def test_non_list_files_returns_empty(self):
        assert _file_map({"files": "not_a_list"}) == {}
        assert _file_map({"files": None}) == {}

    def test_single_valid_entry(self):
        item = {"path": "app/module.yaml", "sha256": "abc"}
        result = _file_map({"files": [item]})
        assert result == {"app/module.yaml": item}

    def test_multiple_files(self):
        items = [
            {"path": "file_a.py", "sha256": "sha_a"},
            {"path": "file_b.py", "sha256": "sha_b"},
        ]
        result = _file_map({"files": items})
        assert set(result.keys()) == {"file_a.py", "file_b.py"}
        assert result["file_a.py"]["sha256"] == "sha_a"

    def test_item_without_path_skipped(self):
        items = [{"sha256": "abc"}, {"path": "valid.py", "sha256": "def"}]
        result = _file_map({"files": items})
        assert list(result.keys()) == ["valid.py"]

    def test_item_with_non_string_path_skipped(self):
        items = [{"path": 42}, {"path": "good.py"}]
        result = _file_map({"files": items})
        assert list(result.keys()) == ["good.py"]

    def test_non_dict_item_skipped(self):
        items = ["not_a_dict", {"path": "real.py"}]
        result = _file_map({"files": items})
        assert list(result.keys()) == ["real.py"]
