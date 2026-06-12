"""
Pure helper unit tests for:
  mozaiksai/control_plane/scoped_execution.py

Covers sync pure helpers with no filesystem side effects:

  _normalize_change_path:
    - empty path → skipped_unsafe
    - null byte in path → skipped_unsafe
    - absolute posix path → skipped_unsafe
    - absolute windows drive path → skipped_unsafe
    - path traversal (..) → skipped_unsafe
    - glob chars in path → skipped_unsafe
    - secret-sensitive path → skipped_secret
    - vault path → skipped_secret
    - .env path → skipped_secret
    - clean relative path → (normalized, None, None)
    - backslash path → normalized to forward slashes
    - leading "./" stripped
    - lowercased normalized path

  _safe_allowed_directories:
    - empty set → empty set
    - file with parent dir → parent dir in result
    - root-level file (no meaningful parent) → not included
    - multiple files same dir → one entry

  _is_allowed_new_file:
    - path in allowed dirs → True
    - path not in allowed dirs → False
    - root-level path → False

  _change_is_in_scope:
    - path in affected_paths → True regardless of allow_new_files
    - path not in affected_paths, allow_new_files=False → False
    - path not in affected_paths, allow_new_files=True, parent in scope → True
    - path not in affected_paths, allow_new_files=True, parent not in scope → False

  _stage_file_path:
    - returns staging_area / "workspace" / relative_path
"""
from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.scoped_execution import (
    _change_is_in_scope,
    _is_allowed_new_file,
    _normalize_change_path,
    _safe_allowed_directories,
    _stage_file_path,
)

# ---------------------------------------------------------------------------
# 1. _normalize_change_path
# ---------------------------------------------------------------------------

class TestNormalizeChangePath:
    def test_empty_path_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("")
        assert relative is None
        assert status == "skipped_unsafe"

    def test_whitespace_only_path_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("   ")
        assert status == "skipped_unsafe"

    def test_null_byte_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("modules/\x00billing.py")
        assert status == "skipped_unsafe"

    def test_absolute_posix_path_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("/etc/passwd")
        assert status == "skipped_unsafe"

    def test_absolute_windows_drive_path_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("C:\\Windows\\system32")
        assert status == "skipped_unsafe"

    def test_path_traversal_dotdot_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("modules/../etc/passwd")
        assert status == "skipped_unsafe"

    def test_glob_star_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("modules/*/handler.py")
        assert status == "skipped_unsafe"

    def test_glob_question_mark_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("modules/billing/handler?.py")
        assert status == "skipped_unsafe"

    def test_glob_bracket_skipped_unsafe(self):
        relative, status, reason = _normalize_change_path("modules/billing/handler[1].py")
        assert status == "skipped_unsafe"

    def test_secret_path_skipped_secret(self):
        relative, status, reason = _normalize_change_path("config/secret.yaml")
        assert status == "skipped_secret"
        assert relative == "config/secret.yaml"

    def test_vault_path_skipped_secret(self):
        relative, status, reason = _normalize_change_path("config/vault_config.yaml")
        assert status == "skipped_secret"

    def test_env_file_skipped_secret(self):
        relative, status, reason = _normalize_change_path(".env")
        assert status == "skipped_secret"

    def test_credential_path_skipped_secret(self):
        relative, status, reason = _normalize_change_path("auth/credentials.json")
        assert status == "skipped_secret"

    def test_private_key_path_skipped_secret(self):
        relative, status, reason = _normalize_change_path("keys/private_key.pem")
        assert status == "skipped_secret"

    def test_pem_file_skipped_secret(self):
        relative, status, reason = _normalize_change_path("certs/server.pem")
        assert status == "skipped_secret"

    def test_clean_relative_path_returns_normalized(self):
        relative, status, reason = _normalize_change_path("modules/billing/handler.py")
        assert relative == "modules/billing/handler.py"
        assert status is None
        assert reason is None

    def test_backslash_path_normalized(self):
        relative, status, reason = _normalize_change_path("modules\\billing\\handler.py")
        assert relative == "modules/billing/handler.py"
        assert status is None

    def test_leading_dot_slash_stripped(self):
        relative, status, reason = _normalize_change_path("./modules/billing/handler.py")
        assert relative == "modules/billing/handler.py"
        assert status is None

    def test_multiple_leading_dot_slash_stripped(self):
        relative, status, reason = _normalize_change_path("././modules/billing")
        assert relative == "modules/billing"
        assert status is None

    def test_case_preserved_in_result(self):
        # _normalize_change_path preserves original case in the returned path
        relative, status, reason = _normalize_change_path("Modules/BILLING/Handler.py")
        assert relative == "Modules/BILLING/Handler.py"
        assert status is None

    def test_nested_yaml_path_ok(self):
        relative, status, reason = _normalize_change_path("ui/pages/dashboard.yaml")
        assert relative == "ui/pages/dashboard.yaml"
        assert status is None


# ---------------------------------------------------------------------------
# 2. _safe_allowed_directories
# ---------------------------------------------------------------------------

class TestSafeAllowedDirectories:
    def test_empty_set_returns_empty(self):
        assert _safe_allowed_directories(set()) == set()

    def test_file_with_parent_included(self):
        result = _safe_allowed_directories({"modules/billing/handler.py"})
        assert "modules/billing" in result

    def test_root_level_file_excluded(self):
        # PurePosixPath("handler.py").parent.as_posix() == "."
        result = _safe_allowed_directories({"handler.py"})
        assert "." not in result

    def test_multiple_files_same_directory(self):
        result = _safe_allowed_directories({
            "modules/billing/handler.py",
            "modules/billing/service.py",
        })
        assert result == {"modules/billing"}

    def test_deep_nested_path_returns_parent(self):
        result = _safe_allowed_directories({"a/b/c/file.py"})
        assert "a/b/c" in result

    def test_multiple_different_dirs(self):
        result = _safe_allowed_directories({
            "modules/billing/handler.py",
            "ui/pages/home.yaml",
        })
        assert "modules/billing" in result
        assert "ui/pages" in result


# ---------------------------------------------------------------------------
# 3. _is_allowed_new_file
# ---------------------------------------------------------------------------

class TestIsAllowedNewFile:
    def test_path_in_allowed_dir_true(self):
        # parent "modules/billing" is derived from "modules/billing/handler.py"
        affected = {"modules/billing/handler.py"}
        assert _is_allowed_new_file("modules/billing/new_file.py", affected) is True

    def test_path_not_in_allowed_dir_false(self):
        affected = {"modules/billing/handler.py"}
        assert _is_allowed_new_file("modules/auth/new_file.py", affected) is False

    def test_root_level_path_false(self):
        # "new_file.py" has no meaningful parent
        affected = {"modules/billing/handler.py"}
        assert _is_allowed_new_file("new_file.py", affected) is False

    def test_empty_affected_paths_false(self):
        assert _is_allowed_new_file("modules/billing/new_file.py", set()) is False


# ---------------------------------------------------------------------------
# 4. _change_is_in_scope
# ---------------------------------------------------------------------------

class TestChangeIsInScope:
    def test_path_in_affected_true_regardless_of_allow_new(self):
        assert _change_is_in_scope(
            path="modules/billing/handler.py",
            affected_paths={"modules/billing/handler.py"},
            allow_new_files=False,
        ) is True

    def test_path_in_affected_with_allow_new_still_true(self):
        assert _change_is_in_scope(
            path="modules/billing/handler.py",
            affected_paths={"modules/billing/handler.py"},
            allow_new_files=True,
        ) is True

    def test_path_not_in_affected_allow_new_false(self):
        assert _change_is_in_scope(
            path="modules/billing/new.py",
            affected_paths={"modules/billing/handler.py"},
            allow_new_files=False,
        ) is False

    def test_path_not_in_affected_allow_new_true_parent_in_scope(self):
        assert _change_is_in_scope(
            path="modules/billing/new_file.py",
            affected_paths={"modules/billing/handler.py"},
            allow_new_files=True,
        ) is True

    def test_path_not_in_affected_allow_new_true_parent_not_in_scope(self):
        assert _change_is_in_scope(
            path="modules/auth/new_file.py",
            affected_paths={"modules/billing/handler.py"},
            allow_new_files=True,
        ) is False

    def test_empty_affected_paths_false(self):
        assert _change_is_in_scope(
            path="modules/billing/handler.py",
            affected_paths=set(),
            allow_new_files=True,
        ) is False


# ---------------------------------------------------------------------------
# 5. _stage_file_path
# ---------------------------------------------------------------------------

class TestStageFilePath:
    def test_returns_path_under_workspace(self):
        staging = Path("/tmp/staging")
        result = _stage_file_path(staging, "modules/billing/handler.py")
        assert result == staging / "workspace" / "modules/billing/handler.py"

    def test_nested_path_preserved(self):
        staging = Path("/tmp/staging")
        result = _stage_file_path(staging, "ui/pages/home.yaml")
        assert result == staging / "workspace" / "ui/pages/home.yaml"

    def test_returns_path_instance(self):
        result = _stage_file_path(Path("/tmp/staging"), "modules/auth/handler.py")
        assert isinstance(result, Path)
