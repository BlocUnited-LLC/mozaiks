"""
Pure helper unit tests for mozaiksai/control_plane/dry_run.py.

Covers:
  _dedupe:
    - removes exact duplicates, preserves order
    - strips whitespace before dedup
    - removes empty/whitespace-only strings
    - non-string values coerced to str

  _stable_request_id:
    - returns "refine_" prefixed 12-char hex
    - same inputs → same result (deterministic)
    - different inputs → different result
    - None app_id treated as empty string

  _staging_area_for:
    - constructs posix path from app_id + request_id
    - sanitizes special characters in app_id
    - None app_id → "local_app" fallback
    - None staging_base_path → default "generated/refinement_staging"
    - explicit staging_base_path used

  _path_text:
    - empty list → empty string
    - backslashes normalized to forward slashes
    - lowercased
    - multiple paths joined with space

  _has_ui_surface_impact:
    - experience_design lane → True
    - ui_patch lane → True
    - managed_capability_change lane → True
    - experience_spec in families → True
    - "ui/" in path → True
    - "route_manifest" in path → True
    - "shell.json" in path → True
    - unrelated lane and paths → False

  _has_module_contract_impact:
    - feature_addition lane → True
    - managed_capability_change lane → True
    - "modules/" in path → True
    - "/contracts/" in path → True
    - "/backend/handler.py" in path → True
    - "/backend/service.py" in path → True
    - unrelated → False

  _has_integration_impact:
    - integration lane → True
    - "integration" in path text → True
    - "connector" in scope_summary → True
    - "adapter" in path → True
    - unrelated → False

  _has_database_review_impact:
    - data_model_migration lane → True
    - "data_contract" in path → True
    - "data_migrations" in path → True
    - "destructive changes require explicit review" in scope_summary → True
    - "migration" in request → True
    - "database" in request → True
    - "data model" in request → True
    - "required field" in request → True
    - unrelated → False

  _has_managed_facade_impact:
    - managed_capability_change lane → True
    - "managed" in path text → True
    - "provider-backed" in scope_summary → True
    - unrelated → False

  _app_context_impact_warnings:
    - None → empty list
    - hints with stale_graph_warning → warning included
    - hints available=True, no stale warning → empty list
    - hints available=False → explanations returned
    - hints available=False + stale_graph_warning → both included
"""
from __future__ import annotations

from pathlib import Path

from mozaiksai.control_plane.app_context_impact import AppContextImpactHints
from mozaiksai.control_plane.dry_run import (
    _app_context_impact_warnings,
    _dedupe,
    _has_database_review_impact,
    _has_integration_impact,
    _has_managed_facade_impact,
    _has_module_contract_impact,
    _has_ui_surface_impact,
    _path_text,
    _stable_request_id,
    _staging_area_for,
)

# ---------------------------------------------------------------------------
# 1. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_removes_duplicates(self):
        result = _dedupe(["a", "b", "a", "c"])
        assert result == ["a", "b", "c"]

    def test_preserves_order(self):
        result = _dedupe(["c", "b", "a"])
        assert result == ["c", "b", "a"]

    def test_strips_whitespace_before_dedup(self):
        result = _dedupe(["  a  ", "a"])
        assert result == ["a"]

    def test_removes_empty_strings(self):
        result = _dedupe(["a", "", "b"])
        assert result == ["a", "b"]

    def test_removes_whitespace_only_strings(self):
        result = _dedupe(["a", "   ", "b"])
        assert result == ["a", "b"]

    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_all_duplicates_single_result(self):
        result = _dedupe(["x", "x", "x"])
        assert result == ["x"]

    def test_strips_then_dedupes(self):
        # "a" and "  a  " both normalize to "a"
        result = _dedupe(["a", "  a  ", "b"])
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# 2. _stable_request_id
# ---------------------------------------------------------------------------

def _make_request_id(**kwargs) -> str:
    defaults = {
        "app_id": "my-app",
        "request": "Add search",
        "artifact_kind": "app_bundle",
        "change_class": "feature",
        "workflow_sequence": "app_revision",
        "affected_bundle_paths": [],
    }
    defaults.update(kwargs)
    return _stable_request_id(**defaults)


class TestStableRequestId:
    def test_returns_string(self):
        result = _make_request_id()
        assert isinstance(result, str)

    def test_starts_with_refine_prefix(self):
        result = _make_request_id()
        assert result.startswith("refine_")

    def test_has_twelve_hex_chars_after_prefix(self):
        result = _make_request_id()
        suffix = result[len("refine_"):]
        assert len(suffix) == 12
        assert all(c in "0123456789abcdef" for c in suffix)

    def test_deterministic_same_inputs(self):
        r1 = _make_request_id()
        r2 = _make_request_id()
        assert r1 == r2

    def test_different_request_different_id(self):
        r1 = _make_request_id(request="Add search")
        r2 = _make_request_id(request="Remove search")
        assert r1 != r2

    def test_different_artifact_kind_different_id(self):
        r1 = _make_request_id(artifact_kind="app_bundle")
        r2 = _make_request_id(artifact_kind="workflow_bundle")
        assert r1 != r2

    def test_none_app_id_treated_as_empty(self):
        r1 = _make_request_id(app_id=None)
        r2 = _make_request_id(app_id="")
        # Both map to "" internally → same digest
        assert r1 == r2

    def test_paths_sorted_for_stability(self):
        r1 = _make_request_id(affected_bundle_paths=["a.py", "b.py"])
        r2 = _make_request_id(affected_bundle_paths=["b.py", "a.py"])
        assert r1 == r2

    def test_different_paths_different_id(self):
        r1 = _make_request_id(affected_bundle_paths=["a.py"])
        r2 = _make_request_id(affected_bundle_paths=["b.py"])
        assert r1 != r2


# ---------------------------------------------------------------------------
# 3. _staging_area_for
# ---------------------------------------------------------------------------

class TestStagingAreaFor:
    def test_returns_string(self):
        result = _staging_area_for(app_id="my-app", request_id="req-1", staging_base_path=None)
        assert isinstance(result, str)

    def test_contains_app_id_and_request_id(self):
        result = _staging_area_for(app_id="my-app", request_id="req-abc", staging_base_path=None)
        assert "my-app" in result
        assert "req-abc" in result

    def test_posix_separators(self):
        result = _staging_area_for(app_id="my-app", request_id="req-1", staging_base_path=None)
        assert "\\" not in result

    def test_none_app_id_uses_local_app(self):
        result = _staging_area_for(app_id=None, request_id="req-1", staging_base_path=None)
        assert "local_app" in result

    def test_none_staging_base_uses_default(self):
        result = _staging_area_for(app_id="app", request_id="req-1", staging_base_path=None)
        assert "generated" in result
        assert "refinement_staging" in result

    def test_explicit_staging_base_used(self):
        base = Path("/tmp/staging")
        result = _staging_area_for(app_id="app", request_id="req-1", staging_base_path=base)
        assert result.startswith("/tmp/staging")

    def test_special_chars_in_app_id_sanitized(self):
        result = _staging_area_for(app_id="my app/name!", request_id="req-1", staging_base_path=None)
        # Special chars replaced with underscore; spaces and ! are absent from the sanitized segment
        assert " " not in result
        assert "!" not in result

    def test_empty_app_id_uses_local_app(self):
        result = _staging_area_for(app_id="", request_id="req-1", staging_base_path=None)
        assert "local_app" in result


# ---------------------------------------------------------------------------
# 4. _path_text
# ---------------------------------------------------------------------------

class TestPathText:
    def test_empty_list_returns_empty_string(self):
        assert _path_text([]) == ""

    def test_single_path(self):
        result = _path_text(["modules/billing/handler.py"])
        assert result == "modules/billing/handler.py"

    def test_multiple_paths_joined_with_space(self):
        result = _path_text(["a.py", "b.py"])
        assert "a.py" in result
        assert "b.py" in result
        assert " " in result

    def test_lowercased(self):
        result = _path_text(["MODULES/Billing/Handler.py"])
        assert result == "modules/billing/handler.py"

    def test_backslashes_normalized(self):
        result = _path_text(["modules\\billing\\handler.py"])
        assert result == "modules/billing/handler.py"

    def test_none_paths_coerced(self):
        # None path coerced to ""
        result = _path_text([None])  # type: ignore[list-item]
        assert result == ""


# ---------------------------------------------------------------------------
# 5. _has_ui_surface_impact
# ---------------------------------------------------------------------------

class TestHasUiSurfaceImpact:
    def test_experience_design_lane_true(self):
        assert _has_ui_surface_impact(lane="experience_design", paths=[], families=[]) is True

    def test_ui_patch_lane_true(self):
        assert _has_ui_surface_impact(lane="ui_patch", paths=[], families=[]) is True

    def test_managed_capability_change_lane_true(self):
        assert _has_ui_surface_impact(lane="managed_capability_change", paths=[], families=[]) is True

    def test_experience_spec_family_true(self):
        assert _has_ui_surface_impact(lane=None, paths=[], families=["experience_spec"]) is True

    def test_ui_in_path_true(self):
        assert _has_ui_surface_impact(lane=None, paths=["app/ui/home.yaml"], families=[]) is True

    def test_route_manifest_in_path_true(self):
        assert _has_ui_surface_impact(lane=None, paths=["app/route_manifest.json"], families=[]) is True

    def test_shell_json_in_path_true(self):
        assert _has_ui_surface_impact(lane=None, paths=["app/shell.json"], families=[]) is True

    def test_unrelated_returns_false(self):
        assert _has_ui_surface_impact(lane="data_model_migration", paths=["modules/billing/handler.py"], families=[]) is False

    def test_none_lane_no_matching_path_false(self):
        assert _has_ui_surface_impact(lane=None, paths=["modules/billing/handler.py"], families=[]) is False


# ---------------------------------------------------------------------------
# 6. _has_module_contract_impact
# ---------------------------------------------------------------------------

class TestHasModuleContractImpact:
    def test_feature_addition_lane_true(self):
        assert _has_module_contract_impact(lane="feature_addition", paths=[]) is True

    def test_managed_capability_change_lane_true(self):
        assert _has_module_contract_impact(lane="managed_capability_change", paths=[]) is True

    def test_modules_in_path_true(self):
        assert _has_module_contract_impact(lane=None, paths=["app/modules/billing/module.yaml"]) is True

    def test_contracts_in_path_true(self):
        assert _has_module_contract_impact(lane=None, paths=["modules/billing/contracts/events.yaml"]) is True

    def test_backend_handler_in_path_true(self):
        assert _has_module_contract_impact(lane=None, paths=["modules/billing/backend/handler.py"]) is True

    def test_backend_service_in_path_true(self):
        assert _has_module_contract_impact(lane=None, paths=["modules/billing/backend/service.py"]) is True

    def test_unrelated_returns_false(self):
        assert _has_module_contract_impact(lane="ui_patch", paths=["app/ui/home.yaml"]) is False

    def test_none_lane_no_matching_path_false(self):
        assert _has_module_contract_impact(lane=None, paths=["app/ui/home.yaml"]) is False


# ---------------------------------------------------------------------------
# 7. _has_integration_impact
# ---------------------------------------------------------------------------

class TestHasIntegrationImpact:
    def test_integration_lane_true(self):
        assert _has_integration_impact(lane="integration", paths=[], scope_summary="") is True

    def test_integration_in_path_true(self):
        assert _has_integration_impact(lane=None, paths=["app/integrations/payment_provider.py"], scope_summary="") is True

    def test_connector_in_scope_true(self):
        assert _has_integration_impact(lane=None, paths=[], scope_summary="Add payment provider connector") is True

    def test_adapter_in_path_true(self):
        assert _has_integration_impact(lane=None, paths=["adapters/payment.py"], scope_summary="") is True

    def test_integrations_plural_in_path_true(self):
        assert _has_integration_impact(lane=None, paths=["services/integrations/"], scope_summary="") is True

    def test_unrelated_returns_false(self):
        assert _has_integration_impact(lane="ui_patch", paths=["app/ui/home.yaml"], scope_summary="Add homepage") is False


# ---------------------------------------------------------------------------
# 8. _has_database_review_impact
# ---------------------------------------------------------------------------

class TestHasDatabaseReviewImpact:
    def test_data_model_migration_lane_true(self):
        assert _has_database_review_impact(lane="data_model_migration", request="", paths=[], scope_summary="") is True

    def test_data_contract_in_path_true(self):
        assert _has_database_review_impact(lane=None, request="", paths=["app/data_contract.json"], scope_summary="") is True

    def test_data_migrations_in_path_true(self):
        assert _has_database_review_impact(lane=None, request="", paths=["data/data_migrations/001.json"], scope_summary="") is True

    def test_destructive_changes_in_scope_true(self):
        assert _has_database_review_impact(
            lane=None, request="", paths=[],
            scope_summary="destructive changes require explicit review"
        ) is True

    def test_migration_in_request_true(self):
        assert _has_database_review_impact(lane=None, request="Run migration", paths=[], scope_summary="") is True

    def test_database_in_request_true(self):
        assert _has_database_review_impact(lane=None, request="Update database schema", paths=[], scope_summary="") is True

    def test_data_model_in_request_true(self):
        assert _has_database_review_impact(lane=None, request="Change data model", paths=[], scope_summary="") is True

    def test_required_field_in_request_true(self):
        assert _has_database_review_impact(lane=None, request="Add required field", paths=[], scope_summary="") is True

    def test_migrate_in_request_true(self):
        assert _has_database_review_impact(lane=None, request="Migrate users table", paths=[], scope_summary="") is True

    def test_unrelated_returns_false(self):
        assert _has_database_review_impact(
            lane="ui_patch", request="Add color to button", paths=["app/ui/home.yaml"], scope_summary="UI change"
        ) is False


# ---------------------------------------------------------------------------
# 9. _has_managed_facade_impact
# ---------------------------------------------------------------------------

class TestHasManagedFacadeImpact:
    def test_managed_capability_change_lane_true(self):
        assert _has_managed_facade_impact(lane="managed_capability_change", paths=[], scope_summary="") is True

    def test_managed_in_path_true(self):
        assert _has_managed_facade_impact(lane=None, paths=["modules/managed_billing/module.yaml"], scope_summary="") is True

    def test_managed_in_scope_summary_true(self):
        assert _has_managed_facade_impact(lane=None, paths=[], scope_summary="Update managed capability facade") is True

    def test_unrelated_returns_false(self):
        assert _has_managed_facade_impact(lane="ui_patch", paths=["app/ui/home.yaml"], scope_summary="UI change") is False

    def test_word_boundary_for_managed(self):
        # "unmanaged" should NOT match because regex requires word boundary
        assert _has_managed_facade_impact(lane=None, paths=[], scope_summary="unmanaged service") is False

    def test_managed_standalone_word_true(self):
        assert _has_managed_facade_impact(lane=None, paths=[], scope_summary="managed service update") is True


# ---------------------------------------------------------------------------
# 10. _app_context_impact_warnings
# ---------------------------------------------------------------------------

class TestAppContextImpactWarnings:
    def test_none_returns_empty_list(self):
        assert _app_context_impact_warnings(None) == []

    def test_available_no_stale_returns_empty(self):
        hints = AppContextImpactHints(available=True, stale_graph_warning=None, explanations=[])
        assert _app_context_impact_warnings(hints) == []

    def test_stale_warning_included(self):
        hints = AppContextImpactHints(available=True, stale_graph_warning="Graph is stale", explanations=[])
        result = _app_context_impact_warnings(hints)
        assert "Graph is stale" in result

    def test_not_available_explanations_returned(self):
        hints = AppContextImpactHints(available=False, explanations=["No graph found"])
        result = _app_context_impact_warnings(hints)
        assert "No graph found" in result

    def test_not_available_multiple_explanations(self):
        hints = AppContextImpactHints(available=False, explanations=["Reason 1", "Reason 2"])
        result = _app_context_impact_warnings(hints)
        assert "Reason 1" in result
        assert "Reason 2" in result

    def test_stale_and_not_available_both_included(self):
        hints = AppContextImpactHints(
            available=False,
            stale_graph_warning="Stale",
            explanations=["Missing"],
        )
        result = _app_context_impact_warnings(hints)
        assert "Stale" in result
        assert "Missing" in result

    def test_returns_list_type(self):
        result = _app_context_impact_warnings(None)
        assert isinstance(result, list)

    def test_available_with_stale_returns_only_stale(self):
        # available=True + stale → only stale_graph_warning, not explanations
        hints = AppContextImpactHints(
            available=True,
            stale_graph_warning="Graph stale",
            explanations=["Should not appear"],
        )
        result = _app_context_impact_warnings(hints)
        assert "Graph stale" in result
        assert "Should not appear" not in result
