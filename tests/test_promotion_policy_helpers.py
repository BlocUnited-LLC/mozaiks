"""
Pure helper unit tests for mozaiksai/control_plane/promotion_policy.py.

Covers:
  _normalize_path:
    - empty string → ""
    - None → ""
    - backslashes → forward slashes
    - leading "./" stripped
    - multiple leading "./" stripped
    - lowercased via parts
    - normal path unchanged

  _normalized_path_set:
    - empty list → empty set
    - multiple paths normalized and deduped
    - empty/None paths excluded

  _matches_any:
    - path matching a glob pattern → True
    - path not matching any pattern → False
    - multiple patterns, one matches → True

  _is_ui_leaf_path:
    - ui/pages/*.yaml → True
    - ui/pages/custom/*.jsx → True
    - ui/route_manifest.json → True
    - ui/index.js → True
    - config/shell.json → True
    - modules/*/module.yaml → False (not a UI leaf)
    - other paths → False

  _is_ui_only_scope:
    - empty list → False
    - only UI leaf paths → True
    - includes non-UI path → False
    - config/shell.json allowed → True

  _is_module_internal_managed_path:
    - "modules/managed_billing/module.yaml" → True (managed_ prefix)
    - "modules/payment_provider_provider/handler.py" → True (provider in name)
    - "modules/billing/module.yaml" → False
    - path with fewer than 2 parts → False
    - non-modules prefix → False

  _required_validation_names:
    - experience_design lane → full set of experience validations
    - managed_capability_change lane + UI leaf → managed capability + route/UI validations
    - managed_capability_change lane + non-UI → only managed capability validation
    - data model backend path → data contract + migration validations
    - integration path → integration readiness
    - module generated path → module contract validation
    - UI leaf path → route + UI validations
    - unmatched → empty list

  _matches_validation_name:
    - exact match → True
    - alias match → True
    - no match → False

  _missing_validation_names:
    - all completed → empty list
    - one missing → that one returned
    - alias completed → not missing

  _required_artifacts_for:
    - experience_design lane → ["experience_spec"]
    - data model backend path → data artifacts
    - other → []

  _has_required_artifact_evidence:
    - empty required → True
    - experience_spec in artifacts → True
    - experience_spec missing → False
    - data_contract matching → True
    - data migrations matching → True
    - partial match (one missing) → False

  _build_blocked_decision / _build_allowed_decision:
    - blocked has allowed=False
    - allowed has allowed=True
    - defaults for required_artifacts/validation
"""
from __future__ import annotations

from mozaiksai.control_plane.promotion_policy import (
    _build_allowed_decision,
    _build_blocked_decision,
    _has_required_artifact_evidence,
    _is_module_internal_managed_path,
    _is_ui_leaf_path,
    _is_ui_only_scope,
    _matches_any,
    _matches_validation_name,
    _missing_validation_names,
    _normalize_path,
    _normalized_path_set,
    _required_artifacts_for,
    _required_validation_names,
)

# ---------------------------------------------------------------------------
# 1. _normalize_path
# ---------------------------------------------------------------------------

class TestNormalizePath:
    def test_empty_string_returns_empty(self):
        assert _normalize_path("") == ""

    def test_none_returns_empty(self):
        assert _normalize_path(None) == ""  # type: ignore[arg-type]

    def test_backslashes_converted(self):
        result = _normalize_path("app\\modules\\billing")
        assert result == "app/modules/billing"

    def test_leading_dot_slash_stripped(self):
        assert _normalize_path("./app/module.py") == "app/module.py"

    def test_multiple_leading_dot_slash_stripped(self):
        assert _normalize_path("././app/module.py") == "app/module.py"

    def test_normal_path_unchanged(self):
        assert _normalize_path("app/modules/billing/module.yaml") == "app/modules/billing/module.yaml"

    def test_single_filename(self):
        assert _normalize_path("app.json") == "app.json"

    def test_whitespace_stripped(self):
        assert _normalize_path("  app/module.py  ") == "app/module.py"


# ---------------------------------------------------------------------------
# 2. _normalized_path_set
# ---------------------------------------------------------------------------

class TestNormalizedPathSet:
    def test_empty_list_returns_empty_set(self):
        assert _normalized_path_set([]) == set()

    def test_normalizes_paths(self):
        result = _normalized_path_set(["./app/mod.py"])
        assert "app/mod.py" in result

    def test_deduplicates(self):
        result = _normalized_path_set(["app/mod.py", "./app/mod.py"])
        assert len(result) == 1

    def test_excludes_empty_paths(self):
        result = _normalized_path_set(["", "  ", "app/module.py"])
        assert "" not in result
        assert "app/module.py" in result

    def test_multiple_unique_paths_preserved(self):
        result = _normalized_path_set(["app/a.py", "app/b.py"])
        assert len(result) == 2


# ---------------------------------------------------------------------------
# 3. _matches_any
# ---------------------------------------------------------------------------

class TestMatchesAny:
    def test_matching_glob_returns_true(self):
        assert _matches_any("ui/pages/home.yaml", ("ui/pages/*.yaml",)) is True

    def test_no_match_returns_false(self):
        assert _matches_any("modules/billing/module.yaml", ("ui/pages/*.yaml",)) is False

    def test_multiple_patterns_one_matches(self):
        patterns = ("ui/pages/*.yaml", "modules/*/module.yaml")
        assert _matches_any("modules/billing/module.yaml", patterns) is True

    def test_exact_match(self):
        assert _matches_any("ui/route_manifest.json", ("ui/route_manifest.json",)) is True

    def test_partial_path_no_match(self):
        assert _matches_any("ui/pages", ("ui/pages/*.yaml",)) is False


# ---------------------------------------------------------------------------
# 4. _is_ui_leaf_path
# ---------------------------------------------------------------------------

class TestIsUiLeafPath:
    def test_ui_pages_yaml_true(self):
        assert _is_ui_leaf_path("ui/pages/home.yaml") is True

    def test_ui_pages_custom_jsx_true(self):
        assert _is_ui_leaf_path("ui/pages/custom/MyPage.jsx") is True

    def test_ui_route_manifest_true(self):
        assert _is_ui_leaf_path("ui/route_manifest.json") is True

    def test_ui_index_js_true(self):
        assert _is_ui_leaf_path("ui/index.js") is True

    def test_config_shell_json_true(self):
        assert _is_ui_leaf_path("config/shell.json") is True

    def test_module_yaml_false(self):
        assert _is_ui_leaf_path("modules/billing/module.yaml") is False

    def test_backend_handler_false(self):
        assert _is_ui_leaf_path("modules/billing/backend/handler.py") is False

    def test_unrelated_path_false(self):
        assert _is_ui_leaf_path("app.json") is False


# ---------------------------------------------------------------------------
# 5. _is_ui_only_scope
# ---------------------------------------------------------------------------

class TestIsUiOnlyScope:
    def test_empty_list_returns_false(self):
        assert _is_ui_only_scope([]) is False

    def test_only_ui_leaf_paths_true(self):
        assert _is_ui_only_scope(["ui/pages/home.yaml", "ui/route_manifest.json"]) is True

    def test_config_shell_json_allowed(self):
        assert _is_ui_only_scope(["config/shell.json"]) is True

    def test_non_ui_path_makes_false(self):
        assert _is_ui_only_scope(["ui/pages/home.yaml", "modules/billing/module.yaml"]) is False

    def test_single_ui_page_true(self):
        assert _is_ui_only_scope(["ui/pages/about.yaml"]) is True

    def test_backend_path_makes_false(self):
        assert _is_ui_only_scope(["modules/billing/backend/handler.py"]) is False


# ---------------------------------------------------------------------------
# 6. _is_module_internal_managed_path
# ---------------------------------------------------------------------------

class TestIsModuleInternalManagedPath:
    def test_managed_prefix_true(self):
        assert _is_module_internal_managed_path("modules/managed_billing/module.yaml") is True

    def test_provider_in_name_true(self):
        assert _is_module_internal_managed_path("modules/payment_provider_provider/handler.py") is True

    def test_regular_module_false(self):
        assert _is_module_internal_managed_path("modules/billing/module.yaml") is False

    def test_non_modules_prefix_false(self):
        assert _is_module_internal_managed_path("app/managed_billing/module.yaml") is False

    def test_too_few_parts_false(self):
        assert _is_module_internal_managed_path("modules") is False

    def test_managed_in_module_id_capitalized_true(self):
        # Parts are lowercased before check
        assert _is_module_internal_managed_path("modules/Managed_Billing/module.yaml") is True


# ---------------------------------------------------------------------------
# 7. _required_validation_names
# ---------------------------------------------------------------------------

class TestRequiredValidationNames:
    def test_experience_design_lane_full_set(self):
        result = _required_validation_names(lane="experience_design", path="")
        assert "experience_spec_update" in result
        assert "app_bundle_validation" in result
        assert "route_component_validation" in result
        assert "ui_theme_primitive_validation" in result

    def test_managed_capability_ui_leaf_path(self):
        result = _required_validation_names(
            lane="managed_capability_change", path="ui/pages/home.yaml"
        )
        assert "managed_facade_boundary_validation" in result
        assert "route_component_validation" in result

    def test_managed_capability_non_ui_path(self):
        result = _required_validation_names(
            lane="managed_capability_change", path="modules/managed_billing/module.yaml"
        )
        assert "managed_facade_boundary_validation" in result
        assert "route_component_validation" not in result

    def test_data_model_backend_path(self):
        result = _required_validation_names(lane="feature", path="modules/billing/backend/repo.py")
        assert "data_contract_validation" in result
        assert "migration_plan_validation" in result

    def test_integration_path(self):
        result = _required_validation_names(lane="feature", path="services/integrations/payment_provider_client.py")
        assert "integration_readiness_validation" in result

    def test_module_generated_path(self):
        # handler.py matches _MODULE_GENERATED_PATTERNS exclusively (not integration patterns)
        result = _required_validation_names(lane="feature", path="modules/billing/backend/handler.py")
        assert "module_contract_validation" in result

    def test_ui_leaf_path(self):
        result = _required_validation_names(lane="patch", path="ui/pages/home.yaml")
        assert "route_component_validation" in result
        assert "ui_theme_primitive_validation" in result

    def test_unmatched_path_empty(self):
        result = _required_validation_names(lane="patch", path="app.json")
        assert result == []


# ---------------------------------------------------------------------------
# 8. _matches_validation_name
# ---------------------------------------------------------------------------

class TestMatchesValidationName:
    def test_exact_match(self):
        assert _matches_validation_name("app_bundle_validation", {"app_bundle_validation"}) is True

    def test_alias_match_experience_spec(self):
        # experience_spec_update and experience_spec_validation are aliases
        assert _matches_validation_name("experience_spec_update", {"experience_spec_validation"}) is True

    def test_no_match(self):
        assert _matches_validation_name("data_contract_validation", {"other_validation"}) is False

    def test_managed_facade_alias(self):
        assert _matches_validation_name("managed_facade_boundary_validation", {"managed_facade_validation"}) is True

    def test_integration_alias(self):
        assert _matches_validation_name("integration_readiness_validation", {"integration_readiness"}) is True


# ---------------------------------------------------------------------------
# 9. _missing_validation_names
# ---------------------------------------------------------------------------

class TestMissingValidationNames:
    def test_all_completed_returns_empty(self):
        result = _missing_validation_names(
            ["route_component_validation", "app_bundle_validation"],
            {"route_component_validation", "app_bundle_validation"},
        )
        assert result == []

    def test_one_missing_returned(self):
        result = _missing_validation_names(
            ["route_component_validation", "app_bundle_validation"],
            {"route_component_validation"},
        )
        assert result == ["app_bundle_validation"]

    def test_alias_counts_as_completed(self):
        result = _missing_validation_names(
            ["experience_spec_update"],
            {"experience_spec_validation"},  # alias
        )
        assert result == []

    def test_empty_required_returns_empty(self):
        assert _missing_validation_names([], {"anything"}) == []

    def test_empty_completed_all_missing(self):
        result = _missing_validation_names(["route_component_validation"], set())
        assert result == ["route_component_validation"]


# ---------------------------------------------------------------------------
# 10. _required_artifacts_for
# ---------------------------------------------------------------------------

class TestRequiredArtifactsFor:
    def test_experience_design_lane(self):
        result = _required_artifacts_for(lane="experience_design", path="")
        assert "experience_spec" in result

    def test_data_model_path_returns_data_artifacts(self):
        result = _required_artifacts_for(lane="feature", path="modules/billing/backend/repo.py")
        assert "data/contract.json" in result

    def test_unmatched_returns_empty(self):
        result = _required_artifacts_for(lane="patch", path="ui/pages/home.yaml")
        assert result == []

    def test_returns_list(self):
        result = _required_artifacts_for(lane="patch", path="")
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# 11. _has_required_artifact_evidence
# ---------------------------------------------------------------------------

class TestHasRequiredArtifactEvidence:
    def test_empty_required_returns_true(self):
        assert _has_required_artifact_evidence([], set()) is True

    def test_experience_spec_present(self):
        assert _has_required_artifact_evidence(
            ["experience_spec"], {"experience_spec_v1", "other"}
        ) is True

    def test_experience_spec_missing(self):
        assert _has_required_artifact_evidence(
            ["experience_spec"], {"data_contract", "other"}
        ) is False

    def test_data_contract_present(self):
        assert _has_required_artifact_evidence(
            ["data/contract.json", "data/migrations/*.json"],
            {"data/contract.json", "data/migrations/001.json"},
        ) is True

    def test_data_contract_alias_matches(self):
        assert _has_required_artifact_evidence(
            ["data/contract.json"],
            {"data_contract_v2"},
        ) is True

    def test_data_contract_missing(self):
        assert _has_required_artifact_evidence(
            ["data/contract.json"],
            {"some_other_artifact"},
        ) is False

    def test_migrations_present(self):
        assert _has_required_artifact_evidence(
            ["data/migrations/*.json"],
            {"data/migrations/001.json"},
        ) is True

    def test_migrations_missing(self):
        assert _has_required_artifact_evidence(
            ["data/migrations/*.json"],
            {"data/contract.json"},
        ) is False


# ---------------------------------------------------------------------------
# 12. _build_blocked_decision / _build_allowed_decision
# ---------------------------------------------------------------------------

class TestBuildDecisions:
    def test_blocked_has_allowed_false(self):
        decision = _build_blocked_decision(
            path="app/module.yaml", mode="blocked_requires_validation", reason="missing validation"
        )
        assert decision.allowed is False

    def test_blocked_path_preserved(self):
        decision = _build_blocked_decision(
            path="app/module.yaml", mode="blocked_requires_validation", reason="test"
        )
        assert decision.path == "app/module.yaml"

    def test_blocked_required_artifacts_default_empty(self):
        decision = _build_blocked_decision(
            path="app", mode="blocked_requires_validation", reason="test"
        )
        assert decision.required_artifacts == []

    def test_blocked_required_artifacts_passed(self):
        decision = _build_blocked_decision(
            path="app", mode="blocked_requires_validation", reason="test",
            required_artifacts=["experience_spec"],
        )
        assert "experience_spec" in decision.required_artifacts

    def test_allowed_has_allowed_true(self):
        decision = _build_allowed_decision(
            path="ui/pages/home.yaml", mode="direct_leaf_patch", reason="UI-only leaf"
        )
        assert decision.allowed is True

    def test_allowed_required_artifacts_always_empty(self):
        decision = _build_allowed_decision(
            path="ui/pages/home.yaml", mode="direct_leaf_patch", reason="test"
        )
        assert decision.required_artifacts == []

    def test_allowed_required_validation_default_empty(self):
        decision = _build_allowed_decision(
            path="ui/pages/home.yaml", mode="direct_leaf_patch", reason="test"
        )
        assert decision.required_validation == []

    def test_allowed_required_validation_passed(self):
        decision = _build_allowed_decision(
            path="ui/pages/home.yaml", mode="direct_leaf_patch", reason="test",
            required_validation=["route_component_validation"],
        )
        assert "route_component_validation" in decision.required_validation
