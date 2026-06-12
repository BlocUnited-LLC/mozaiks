"""
Pure helper unit tests for mozaiksai/control_plane/app_context_policy.py.

Covers:
  _normalize_path:
    - empty string → ""
    - backslashes normalized
    - leading "./" stripped
    - parts lowercased
    - trailing slash removed
    - "." parts dropped

  _dedupe:
    - empty → []
    - duplicates removed, order preserved
    - empty/whitespace strings excluded
    - None coerced and excluded

  _paths_overlap:
    - empty path or boundary → False
    - equal paths → True
    - path starts with boundary+"/" → True
    - boundary starts with path+"/" → True
    - no overlap → False

  _touches_module_or_backend:
    - "modules/..." → True
    - "/backend/" in path → True
    - "services/..." → True
    - unrelated path → False
    - empty list → False

  _touches_sensitive_boundary:
    - path containing "auth" → True
    - path containing "secret" → True
    - path containing ".env" → True
    - path containing "token" → True
    - path containing "vault" → True
    - unrelated path → False
    - empty list → False

  _context_state:
    - None summary → "missing"
    - summary.available=False → "missing"
    - stale_status="stale" → "stale"
    - stale_status="unknown" → "stale"
    - stale_status="partially_stale" → "stale"
    - stale_status="unsafe" → "stale"
    - stale_status="" → "stale"
    - stale_status=None → "stale"
    - stale_status="fresh" → "fresh"

  _context_warnings:
    - None summary → [APP_CONTEXT_MISSING_WARNING]
    - summary.available=False → [APP_CONTEXT_MISSING_WARNING]
    - summary with explicit warnings → those warnings
    - stale summary → [APP_CONTEXT_STALE_WARNING]
    - fresh summary → []

  _is_brownfield_source_affecting:
    - None summary → False
    - summary.mode != "brownfield" → False
    - brownfield + paths present → True
    - brownfield + data_model_migration lane → True
    - brownfield + ui_patch lane + no backend paths → False
    - brownfield + ui_patch lane + backend paths → True
    - brownfield + empty paths + low lane → False

  _touches_read_only_discovered_boundary:
    - None summary → False
    - summary with no read_only_discovered boundaries → False
    - path overlaps read_only_discovered boundary → True
    - path does not overlap → False
"""
from __future__ import annotations

from mozaiksai.control_plane.app_context import (
    APP_CONTEXT_MISSING_WARNING,
    APP_CONTEXT_STALE_WARNING,
    AppContextOwnershipBoundarySummary,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_policy import (
    _context_state,
    _context_warnings,
    _dedupe,
    _is_brownfield_source_affecting,
    _normalize_path,
    _paths_overlap,
    _touches_module_or_backend,
    _touches_read_only_discovered_boundary,
    _touches_sensitive_boundary,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_summary(**kwargs) -> AppContextSummary:
    defaults = dict(available=True, stale_status="fresh")
    defaults.update(kwargs)
    return AppContextSummary(**defaults)


def _stale_summary(**kwargs) -> AppContextSummary:
    defaults = dict(available=True, stale_status="stale")
    defaults.update(kwargs)
    return AppContextSummary(**defaults)


def _brownfield_summary(**kwargs) -> AppContextSummary:
    defaults = dict(available=True, stale_status="fresh", mode="brownfield")
    defaults.update(kwargs)
    return AppContextSummary(**defaults)


def _boundary(path: str, ownership: str = "read_only_discovered") -> AppContextOwnershipBoundarySummary:
    return AppContextOwnershipBoundarySummary(path_or_artifact=path, ownership=ownership)


# ---------------------------------------------------------------------------
# 1. _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_empty_returns_empty(self):
        assert _normalize_path("") == ""

    def test_none_coerced_to_empty(self):
        assert _normalize_path(None) == ""  # type: ignore[arg-type]

    def test_backslashes_normalized(self):
        assert _normalize_path("modules\\billing\\handler.py") == "modules/billing/handler.py"

    def test_leading_dot_slash_stripped(self):
        assert _normalize_path("./modules/billing") == "modules/billing"

    def test_multiple_leading_dot_slash_stripped(self):
        assert _normalize_path("././modules/billing") == "modules/billing"

    def test_lowercased(self):
        assert _normalize_path("Modules/Billing/Handler.py") == "modules/billing/handler.py"

    def test_trailing_slash_stripped(self):
        assert _normalize_path("modules/billing/") == "modules/billing"

    def test_dot_parts_dropped(self):
        # PurePosixPath("./foo/./bar") drops "." parts
        result = _normalize_path("foo/./bar")
        assert result == "foo/bar"

    def test_clean_path_unchanged(self):
        assert _normalize_path("modules/billing/handler.py") == "modules/billing/handler.py"


# ---------------------------------------------------------------------------
# 2. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list(self):
        assert _dedupe([]) == []

    def test_duplicates_removed(self):
        result = _dedupe(["a", "b", "a"])
        assert result.count("a") == 1

    def test_order_preserved(self):
        result = _dedupe(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_empty_strings_excluded(self):
        result = _dedupe(["a", "", "b"])
        assert "" not in result

    def test_whitespace_strings_excluded(self):
        result = _dedupe(["a", "   ", "b"])
        assert len(result) == 2

    def test_none_excluded(self):
        result = _dedupe(["a", None, "b"])  # type: ignore[list-item]
        assert None not in result

    def test_single_element(self):
        assert _dedupe(["x"]) == ["x"]


# ---------------------------------------------------------------------------
# 3. _paths_overlap
# ---------------------------------------------------------------------------

class TestPathsOverlap:
    def test_empty_path_false(self):
        assert _paths_overlap("", "modules/billing") is False

    def test_empty_boundary_false(self):
        assert _paths_overlap("modules/billing", "") is False

    def test_both_empty_false(self):
        assert _paths_overlap("", "") is False

    def test_equal_paths_true(self):
        assert _paths_overlap("modules/billing", "modules/billing") is True

    def test_path_starts_with_boundary(self):
        assert _paths_overlap("modules/billing/handler.py", "modules/billing") is True

    def test_boundary_starts_with_path(self):
        assert _paths_overlap("modules", "modules/billing/handler.py") is True

    def test_no_overlap(self):
        assert _paths_overlap("modules/billing", "modules/auth") is False

    def test_prefix_without_slash_no_overlap(self):
        # "modules/billingextra" should NOT overlap with "modules/billing"
        assert _paths_overlap("modules/billingextra", "modules/billing") is False


# ---------------------------------------------------------------------------
# 4. _touches_module_or_backend
# ---------------------------------------------------------------------------

class TestTouchesModuleOrBackend:
    def test_modules_prefix(self):
        assert _touches_module_or_backend(["modules/billing/handler.py"]) is True

    def test_backend_in_path(self):
        assert _touches_module_or_backend(["app/modules/auth/backend/service.py"]) is True

    def test_services_prefix(self):
        assert _touches_module_or_backend(["services/payment/client.py"]) is True

    def test_unrelated_path(self):
        assert _touches_module_or_backend(["ui/pages/home.yaml"]) is False

    def test_empty_list(self):
        assert _touches_module_or_backend([]) is False

    def test_mixed_list_true(self):
        assert _touches_module_or_backend(["ui/page.yaml", "modules/billing/handler.py"]) is True

    def test_multiple_unrelated(self):
        assert _touches_module_or_backend(["ui/page.yaml", "docs/guide.md"]) is False


# ---------------------------------------------------------------------------
# 5. _touches_sensitive_boundary
# ---------------------------------------------------------------------------

class TestTouchesSensitiveBoundary:
    def test_auth_path(self):
        assert _touches_sensitive_boundary(["modules/auth/handler.py"]) is True

    def test_secret_path(self):
        assert _touches_sensitive_boundary(["config/secret.yaml"]) is True

    def test_token_path(self):
        assert _touches_sensitive_boundary(["services/token/client.py"]) is True

    def test_vault_path(self):
        assert _touches_sensitive_boundary(["app/vault/keys.yaml"]) is True

    def test_env_file(self):
        assert _touches_sensitive_boundary([".env"]) is True

    def test_github_workflows(self):
        assert _touches_sensitive_boundary([".github/workflows/release.yml"]) is True

    def test_deploy_path(self):
        assert _touches_sensitive_boundary(["infra/deploy/staging.tf"]) is True

    def test_credential_path(self):
        assert _touches_sensitive_boundary(["config/credentials.yaml"]) is True

    def test_policy_path(self):
        assert _touches_sensitive_boundary(["app/policy/rules.py"]) is True

    def test_permission_path(self):
        assert _touches_sensitive_boundary(["modules/permissions/handler.py"]) is True

    def test_unrelated_path(self):
        assert _touches_sensitive_boundary(["modules/billing/handler.py"]) is False

    def test_empty_list(self):
        assert _touches_sensitive_boundary([]) is False

    def test_docker_path(self):
        assert _touches_sensitive_boundary(["infra/docker/compose.yaml"]) is True


# ---------------------------------------------------------------------------
# 6. _context_state
# ---------------------------------------------------------------------------

class TestContextState:
    def test_none_summary_missing(self):
        assert _context_state(None) == "missing"

    def test_not_available_missing(self):
        summary = AppContextSummary(available=False)
        assert _context_state(summary) == "missing"

    def test_stale_status_stale(self):
        assert _context_state(_stale_summary()) == "stale"

    def test_stale_status_unknown(self):
        assert _context_state(_fresh_summary(stale_status="unknown")) == "stale"

    def test_stale_status_partially_stale(self):
        assert _context_state(_fresh_summary(stale_status="partially_stale")) == "stale"

    def test_stale_status_unsafe(self):
        assert _context_state(_fresh_summary(stale_status="unsafe")) == "stale"

    def test_stale_status_empty_string(self):
        assert _context_state(_fresh_summary(stale_status="")) == "stale"

    def test_stale_status_none(self):
        assert _context_state(_fresh_summary(stale_status=None)) == "stale"

    def test_fresh_status(self):
        assert _context_state(_fresh_summary()) == "fresh"

    def test_fresh_status_case_insensitive(self):
        # stale_status normalized via .strip().lower()
        assert _context_state(_fresh_summary(stale_status="  STALE  ")) == "stale"


# ---------------------------------------------------------------------------
# 7. _context_warnings
# ---------------------------------------------------------------------------

class TestContextWarnings:
    def test_none_summary_returns_missing_warning(self):
        warnings = _context_warnings(None)
        assert APP_CONTEXT_MISSING_WARNING in warnings

    def test_not_available_returns_missing_warning(self):
        summary = AppContextSummary(available=False)
        warnings = _context_warnings(summary)
        assert APP_CONTEXT_MISSING_WARNING in warnings

    def test_explicit_warnings_returned(self):
        summary = _fresh_summary(warnings=["custom warning"])
        warnings = _context_warnings(summary)
        assert "custom warning" in warnings

    def test_stale_summary_returns_stale_warning(self):
        warnings = _context_warnings(_stale_summary())
        assert APP_CONTEXT_STALE_WARNING in warnings

    def test_fresh_summary_returns_empty(self):
        assert _context_warnings(_fresh_summary()) == []


# ---------------------------------------------------------------------------
# 8. _is_brownfield_source_affecting
# ---------------------------------------------------------------------------

class TestIsBrownfieldSourceAffecting:
    def test_none_summary_false(self):
        assert _is_brownfield_source_affecting(None, ["modules/billing/handler.py"], "feature_addition") is False

    def test_non_brownfield_mode_false(self):
        summary = _fresh_summary(mode="greenfield")
        assert _is_brownfield_source_affecting(summary, ["modules/billing/handler.py"], "feature_addition") is False

    def test_brownfield_with_paths_true(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, ["modules/billing/handler.py"], "feature_addition") is True

    def test_brownfield_data_model_migration_true(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, [], "data_model_migration") is True

    def test_brownfield_integration_lane_true(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, [], "integration") is True

    def test_brownfield_ui_patch_no_backend_paths_false(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, ["ui/page.yaml"], "ui_patch") is False

    def test_brownfield_ui_patch_with_backend_paths_true(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, ["modules/auth/backend/service.py"], "ui_patch") is True

    def test_brownfield_empty_paths_and_low_lane_false(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, [], "ui_patch") is False

    def test_brownfield_feature_addition_empty_paths_true(self):
        # lane is "feature_addition" so it returns True regardless of paths
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, [], "feature_addition") is True

    def test_brownfield_experience_design_no_backend_false(self):
        summary = _brownfield_summary()
        assert _is_brownfield_source_affecting(summary, ["ui/components/hero.jsx"], "experience_design") is False


# ---------------------------------------------------------------------------
# 9. _touches_read_only_discovered_boundary
# ---------------------------------------------------------------------------

class TestTouchesReadOnlyDiscoveredBoundary:
    def test_none_summary_false(self):
        assert _touches_read_only_discovered_boundary(None, ["modules/billing"]) is False

    def test_no_read_only_boundaries_false(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/billing", "full")]
        )
        assert _touches_read_only_discovered_boundary(summary, ["modules/billing/handler.py"]) is False

    def test_overlapping_read_only_boundary_true(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/discovered")]
        )
        assert _touches_read_only_discovered_boundary(summary, ["modules/discovered/handler.py"]) is True

    def test_non_overlapping_read_only_boundary_false(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/discovered")]
        )
        assert _touches_read_only_discovered_boundary(summary, ["modules/billing/handler.py"]) is False

    def test_exact_path_match_true(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/existing/handler.py")]
        )
        assert _touches_read_only_discovered_boundary(summary, ["modules/existing/handler.py"]) is True

    def test_empty_paths_false(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/discovered")]
        )
        assert _touches_read_only_discovered_boundary(summary, []) is False
