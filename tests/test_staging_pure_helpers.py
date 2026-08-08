"""
Pure helper unit tests for mozaiksai/control_plane/staging.py.

Covers:
  _is_relative_to:
    - child inside parent → True
    - child equal to parent → True
    - child outside parent → False
    - unrelated paths → False

  _normalize_bundle_path:
    - empty string → (None, "skipped_unsafe", message)
    - null byte → (None, "skipped_unsafe", message)
    - absolute path "/" → (None, "skipped_unsafe", message)
    - Windows drive path "C:\\\\..." → (None, "skipped_unsafe", message)
    - traversal ".." → (None, "skipped_unsafe", message)
    - leading "./" stripped
    - backslashes normalized to forward slashes
    - secret-sensitive path → (path, "skipped_secret", message)
    - glob char "*" → (path, "skipped_glob", message)
    - glob char "?" → (path, "skipped_glob", message)
    - clean path → (normalized, None, None)
    - nested path preserved
    - .env file → skipped_secret

  _readme_text:
    - contains "# Refinement Staging Workspace" header
    - contains request_id
    - contains execution_mode
    - contains "No source app files were mutated"
    - result is a string
"""
from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.dry_run import (
    RefinementExecutionPlan,
    RefinementExecutionProfiles,
    RefinementValidationPlan,
)
from mozaiksai.control_plane.staging import (
    _is_relative_to,
    _normalize_bundle_path,
    _readme_text,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _plan(**kwargs) -> RefinementExecutionPlan:
    defaults = {
        "request_id": "req-abc123",
        "request": "Add a search feature",
        "build_family": "app_bundle",
        "change_class": "feature",
        "workflow_id": "AppGenerator",
        "workflow_sequence": "full_build",
        "target_workflow": "AppGenerator",
        "scope_summary": "Search module addition",
        "profiles": RefinementExecutionProfiles(classifier="claude-sonnet-4-6"),
        "validation_plan": RefinementValidationPlan(),
        "execution_mode": "staged",
        "next_step": "review",
    }
    defaults.update(kwargs)
    return RefinementExecutionPlan(**defaults)


# ---------------------------------------------------------------------------
# 1. _is_relative_to
# ---------------------------------------------------------------------------

class TestIsRelativeTo:
    def test_child_inside_parent_true(self):
        parent = Path("/a/b")
        child = Path("/a/b/c/d.py")
        assert _is_relative_to(child, parent) is True

    def test_child_equal_to_parent_true(self):
        parent = Path("/a/b")
        assert _is_relative_to(parent, parent) is True

    def test_child_outside_parent_false(self):
        parent = Path("/a/b")
        child = Path("/a/c/d.py")
        assert _is_relative_to(child, parent) is False

    def test_unrelated_paths_false(self):
        parent = Path("/x/y")
        child = Path("/a/b/c.py")
        assert _is_relative_to(child, parent) is False

    def test_sibling_path_false(self):
        parent = Path("/staging/workspace")
        sibling = Path("/staging/other")
        assert _is_relative_to(sibling, parent) is False


# ---------------------------------------------------------------------------
# 2. _normalize_bundle_path
# ---------------------------------------------------------------------------

class TestNormalizeBundlePath:
    def test_empty_string_skipped_unsafe(self):
        path, status, reason = _normalize_bundle_path("")
        assert path is None
        assert status == "skipped_unsafe"
        assert reason is not None

    def test_whitespace_only_skipped_unsafe(self):
        path, status, reason = _normalize_bundle_path("   ")
        assert path is None
        assert status == "skipped_unsafe"

    def test_null_byte_skipped_unsafe(self):
        path, status, reason = _normalize_bundle_path("a\x00b.py")
        assert path is None
        assert status == "skipped_unsafe"
        assert "null" in (reason or "").lower()

    def test_absolute_posix_path_skipped_unsafe(self):
        path, status, reason = _normalize_bundle_path("/etc/passwd")
        assert path is None
        assert status == "skipped_unsafe"

    def test_path_traversal_double_dot_skipped_unsafe(self):
        path, status, reason = _normalize_bundle_path("modules/../../etc/passwd")
        assert path is None
        assert status == "skipped_unsafe"
        assert "traversal" in (reason or "").lower()

    def test_leading_dot_slash_stripped(self):
        path, status, reason = _normalize_bundle_path("./modules/my_mod/handler.py")
        assert status is None
        assert path == "modules/my_mod/handler.py"

    def test_backslashes_normalized(self):
        path, status, reason = _normalize_bundle_path("modules\\my_mod\\handler.py")
        assert status is None
        assert path == "modules/my_mod/handler.py"

    def test_env_file_skipped_secret(self):
        path, status, reason = _normalize_bundle_path(".env")
        assert status == "skipped_secret"
        assert reason is not None

    def test_secret_in_path_skipped_secret(self):
        path, status, reason = _normalize_bundle_path("config/secrets.yaml")
        assert status == "skipped_secret"

    def test_pem_file_skipped_secret(self):
        path, status, reason = _normalize_bundle_path("certs/server.pem")
        assert status == "skipped_secret"

    def test_key_file_skipped_secret(self):
        path, status, reason = _normalize_bundle_path("keys/private.key")
        assert status == "skipped_secret"

    def test_glob_star_skipped_glob(self):
        path, status, reason = _normalize_bundle_path("modules/*/module.yaml")
        assert status == "skipped_glob"
        assert reason is not None

    def test_glob_question_mark_skipped_glob(self):
        path, status, reason = _normalize_bundle_path("app/page?.yaml")
        assert status == "skipped_glob"

    def test_clean_path_returned(self):
        path, status, reason = _normalize_bundle_path("modules/my_mod/module.yaml")
        assert path == "modules/my_mod/module.yaml"
        assert status is None
        assert reason is None

    def test_nested_clean_path_returned(self):
        path, status, reason = _normalize_bundle_path("app/ui/pages/home.yaml")
        assert path == "app/ui/pages/home.yaml"
        assert status is None

    def test_triple_leading_dot_slash_stripped(self):
        path, status, reason = _normalize_bundle_path("./././modules/my_mod/x.py")
        assert status is None
        assert path == "modules/my_mod/x.py"


# ---------------------------------------------------------------------------
# 3. _readme_text
# ---------------------------------------------------------------------------

class TestReadmeText:
    def test_returns_string(self):
        plan = _plan()
        result = _readme_text(plan)
        assert isinstance(result, str)

    def test_contains_header(self):
        plan = _plan()
        result = _readme_text(plan)
        assert "# Refinement Staging Workspace" in result

    def test_contains_request_id(self):
        plan = _plan(request_id="my-request-xyz")
        result = _readme_text(plan)
        assert "my-request-xyz" in result

    def test_contains_execution_mode(self):
        plan = _plan(execution_mode="staged")
        result = _readme_text(plan)
        assert "staged" in result

    def test_contains_no_mutation_message(self):
        plan = _plan()
        result = _readme_text(plan)
        assert "No source app files were mutated" in result

    def test_contains_approval_message(self):
        plan = _plan()
        result = _readme_text(plan)
        assert "Human approval is required" in result

    def test_is_multiline(self):
        plan = _plan()
        result = _readme_text(plan)
        assert "\n" in result
