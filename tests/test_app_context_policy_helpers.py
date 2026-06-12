"""
App context policy pure helper unit tests.

Covers:
  _paths_overlap:
    - empty path → False
    - empty boundary → False
    - exact match → True
    - path starts with boundary/ → True
    - boundary starts with path/ → True
    - disjoint paths → False
    - partial prefix not a full segment → False

  _normalize_path:
    - None/empty → empty string
    - leading ./ stripped
    - backslashes → forward slashes
    - multiple leading ./ stripped
    - lowercased
    - dot components removed

  _dedupe:
    - empty list → empty
    - unique items preserved in order
    - duplicates removed keeping first
    - whitespace-only items filtered
    - None-ish items skipped

  _touches_module_or_backend:
    - path starting with modules/ → True
    - path containing /backend/ → True
    - path starting with services/ → True
    - UI path → False
    - empty list → False

  _touches_sensitive_boundary:
    - path containing "secret" → True
    - path containing "auth" → True
    - path containing ".github/workflows" → True
    - path containing "token" → True
    - safe path → False
    - empty list → False

  _context_state:
    - None summary → "missing"
    - unavailable summary → "missing"
    - stale_status "stale" → "stale"
    - stale_status "unknown" → "stale"
    - stale_status "" → "stale"
    - stale_status "fresh" → "fresh"

  _context_warnings:
    - None summary → [MISSING_WARNING]
    - unavailable summary without explicit warnings → [MISSING_WARNING]
    - summary with explicit warnings → those warnings
    - available but stale → [STALE_WARNING]
    - available and fresh → empty list
"""
from __future__ import annotations

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    APP_CONTEXT_STALE_WARNING,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_policy import (
    _context_state,
    _context_warnings,
    _dedupe,
    _normalize_path,
    _paths_overlap,
    _touches_module_or_backend,
    _touches_sensitive_boundary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _summary(*, available: bool = True, stale_status: str | None = None, mode: str = "greenfield", warnings: list[str] | None = None) -> AppContextSummary:
    return AppContextSummary(
        app_id="test-app",
        available=available,
        stale_status=stale_status,
        mode=mode,
        warnings=warnings or [],
    )


# ---------------------------------------------------------------------------
# 1. _paths_overlap
# ---------------------------------------------------------------------------

class TestPathsOverlap:
    def test_empty_path_returns_false(self):
        assert _paths_overlap("", "modules/wallet") is False

    def test_empty_boundary_returns_false(self):
        assert _paths_overlap("modules/wallet", "") is False

    def test_exact_match_returns_true(self):
        assert _paths_overlap("modules/wallet", "modules/wallet") is True

    def test_path_starts_with_boundary_prefix(self):
        assert _paths_overlap("modules/wallet/backend/handler.py", "modules/wallet") is True

    def test_boundary_starts_with_path_prefix(self):
        assert _paths_overlap("modules", "modules/wallet") is True

    def test_disjoint_paths_returns_false(self):
        assert _paths_overlap("modules/payments", "modules/wallet") is False

    def test_partial_prefix_not_full_segment(self):
        # "modules/wallet_v2" should NOT match "modules/wallet"
        assert _paths_overlap("modules/wallet_v2", "modules/wallet") is False


# ---------------------------------------------------------------------------
# 2. _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_empty_string_returns_empty(self):
        assert _normalize_path("") == ""

    def test_none_returns_empty(self):
        assert _normalize_path(None) == ""

    def test_leading_dot_slash_stripped(self):
        assert _normalize_path("./app/module.py") == "app/module.py"

    def test_multiple_leading_dot_slash_stripped(self):
        assert _normalize_path("././app/module.py") == "app/module.py"

    def test_backslashes_normalized(self):
        result = _normalize_path("app\\modules\\wallet")
        assert result == "app/modules/wallet"

    def test_lowercased(self):
        assert _normalize_path("App/Modules/Wallet") == "app/modules/wallet"

    def test_plain_path_unchanged(self):
        assert _normalize_path("app/modules/wallet") == "app/modules/wallet"

    def test_dot_components_removed(self):
        result = _normalize_path("app/./modules")
        assert result == "app/modules"


# ---------------------------------------------------------------------------
# 3. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list(self):
        assert _dedupe([]) == []

    def test_unique_items_preserved(self):
        assert _dedupe(["a", "b", "c"]) == ["a", "b", "c"]

    def test_duplicates_removed_first_kept(self):
        result = _dedupe(["b", "a", "b", "c", "a"])
        assert result == ["b", "a", "c"]

    def test_whitespace_only_filtered(self):
        result = _dedupe(["  ", "a", ""])
        assert result == ["a"]

    def test_none_in_list_filtered(self):
        result = _dedupe([None, "a", None])
        assert result == ["a"]

    def test_preserves_insertion_order(self):
        result = _dedupe(["c", "a", "b"])
        assert result == ["c", "a", "b"]


# ---------------------------------------------------------------------------
# 4. _touches_module_or_backend
# ---------------------------------------------------------------------------

class TestTouchesModuleOrBackend:
    def test_empty_list_false(self):
        assert _touches_module_or_backend([]) is False

    def test_modules_prefix(self):
        assert _touches_module_or_backend(["modules/wallet/handler.py"]) is True

    def test_backend_segment(self):
        assert _touches_module_or_backend(["app/modules/wallet/backend/service.py"]) is True

    def test_services_prefix(self):
        assert _touches_module_or_backend(["services/payment_adapter.py"]) is True

    def test_ui_path_false(self):
        assert _touches_module_or_backend(["app/ui/pages/home.jsx"]) is False

    def test_workflows_false(self):
        assert _touches_module_or_backend(["workflows/AppGenerator/agents.yaml"]) is False

    def test_mixed_returns_true_if_any_matches(self):
        assert _touches_module_or_backend(["docs/readme.md", "modules/wallet/handler.py"]) is True


# ---------------------------------------------------------------------------
# 5. _touches_sensitive_boundary
# ---------------------------------------------------------------------------

class TestTouchesSensitiveBoundary:
    def test_empty_list_false(self):
        assert _touches_sensitive_boundary([]) is False

    def test_secret_in_path(self):
        assert _touches_sensitive_boundary(["app/secrets/config.yaml"]) is True

    def test_auth_in_path(self):
        assert _touches_sensitive_boundary(["app/auth/middleware.py"]) is True

    def test_github_workflows(self):
        assert _touches_sensitive_boundary([".github/workflows/deploy.yml"]) is True

    def test_token_in_path(self):
        assert _touches_sensitive_boundary(["app/utils/token_utils.py"]) is True

    def test_env_file(self):
        assert _touches_sensitive_boundary([".env"]) is True

    def test_safe_path_false(self):
        assert _touches_sensitive_boundary(["app/modules/wallet/handler.py"]) is False

    def test_deploy_in_path(self):
        assert _touches_sensitive_boundary(["scripts/deploy.sh"]) is True

    def test_vault_in_path(self):
        assert _touches_sensitive_boundary(["config/vault/settings.yaml"]) is True


# ---------------------------------------------------------------------------
# 6. _context_state
# ---------------------------------------------------------------------------

class TestContextState:
    def test_none_summary_returns_missing(self):
        assert _context_state(None) == "missing"

    def test_unavailable_returns_missing(self):
        assert _context_state(_summary(available=False)) == "missing"

    def test_stale_status_stale(self):
        assert _context_state(_summary(stale_status="stale")) == "stale"

    def test_stale_status_unknown(self):
        assert _context_state(_summary(stale_status="unknown")) == "stale"

    def test_stale_status_empty_string(self):
        assert _context_state(_summary(stale_status="")) == "stale"

    def test_stale_status_none(self):
        # available=True but no stale_status set → "" → "stale"
        assert _context_state(_summary(available=True, stale_status=None)) == "stale"

    def test_stale_status_fresh(self):
        assert _context_state(_summary(available=True, stale_status="fresh")) == "fresh"

    def test_stale_status_current(self):
        assert _context_state(_summary(available=True, stale_status="current")) == "fresh"


# ---------------------------------------------------------------------------
# 7. _context_warnings
# ---------------------------------------------------------------------------

class TestContextWarnings:
    def test_none_summary_returns_missing_warning(self):
        assert APP_CONTEXT_MISSING_WARNING in _context_warnings(None)

    def test_unavailable_no_explicit_warnings_returns_missing(self):
        warnings = _context_warnings(_summary(available=False))
        assert APP_CONTEXT_MISSING_WARNING in warnings

    def test_explicit_warnings_returned(self):
        warnings = _context_warnings(_summary(available=True, warnings=["custom warning"]))
        assert "custom warning" in warnings

    def test_available_stale_returns_stale_warning(self):
        warnings = _context_warnings(_summary(available=True, stale_status="stale"))
        assert APP_CONTEXT_STALE_WARNING in warnings

    def test_available_fresh_returns_empty(self):
        warnings = _context_warnings(_summary(available=True, stale_status="fresh"))
        assert warnings == []
