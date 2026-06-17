"""
Pure helper unit tests for:
  mozaiksai/control_plane/app_context_policy.py

Covers helpers NOT tested in test_app_context_policy_pure_helpers.py:

  _normalize_summary:
    - None → None
    - AppContextSummary instance → returned unchanged
    - dict → model_validate returns AppContextSummary

  _normalize_impact_hints:
    - None → None
    - AppContextImpactHints instance → returned unchanged
    - dict → model_validate returns AppContextImpactHints

  _risky_signals:
    - no lane, no change_class, no paths, no summary → []
    - change_class="core" → conceptual_or_architecture_replan signal
    - lane="data_model_migration" → data_model_migration signal
    - lane="integration" → integration signal
    - lane="managed_capability_change" → managed_capability_change signal
    - lane="architecture_replan" → conceptual_or_architecture_replan signal
    - lane="conceptual_reframe" → conceptual_or_architecture_replan signal
    - lane="feature_addition" + backend path → module_backend_feature_addition signal
    - lane="feature_addition" + non-backend path → no module_backend_feature_addition
    - sensitive path → sensitive_boundary_change signal
    - read_only_discovered boundary overlap → read_only_discovered_boundary signal
    - brownfield mode + paths → brownfield_source_affecting_change signal
    - duplicate signals deduped (core lane also triggers conceptual signal once)
    - mixed signals returns all unique signals
"""
from __future__ import annotations

from mozaiksai.control_plane.app_context import (
    AppContextOwnershipBoundarySummary,
    AppContextSummary,
)
from mozaiksai.control_plane.app_context_impact import AppContextImpactHints
from mozaiksai.control_plane.app_context_policy import (
    _normalize_impact_hints,
    _normalize_summary,
    _risky_signals,
)

# ---------------------------------------------------------------------------
# Helpers to build test summaries
# ---------------------------------------------------------------------------

def _fresh_summary(**kwargs) -> AppContextSummary:
    defaults: dict = {"available": True, "stale_status": "fresh"}
    defaults.update(kwargs)
    return AppContextSummary(**defaults)


def _brownfield_summary(**kwargs) -> AppContextSummary:
    defaults: dict = {"available": True, "stale_status": "fresh", "mode": "brownfield"}
    defaults.update(kwargs)
    return AppContextSummary(**defaults)


def _boundary(path: str, ownership: str = "read_only_discovered") -> AppContextOwnershipBoundarySummary:
    return AppContextOwnershipBoundarySummary(path_or_artifact=path, ownership=ownership)


def _call_risky(
    *,
    summary: AppContextSummary | None = None,
    change_class: str = "patch",
    refinement_lane: str | None = None,
    affected_bundle_paths: list[str] | None = None,
) -> list[str]:
    return _risky_signals(
        summary=summary,
        change_class=change_class,
        refinement_lane=refinement_lane,
        affected_bundle_paths=affected_bundle_paths or [],
    )


# ---------------------------------------------------------------------------
# 1. _normalize_summary
# ---------------------------------------------------------------------------

class TestNormalizeSummary:
    def test_none_returns_none(self):
        assert _normalize_summary(None) is None

    def test_summary_instance_returned_unchanged(self):
        s = _fresh_summary()
        result = _normalize_summary(s)
        assert result is s

    def test_dict_returns_app_context_summary(self):
        d = {"available": True, "stale_status": "fresh"}
        result = _normalize_summary(d)
        assert isinstance(result, AppContextSummary)
        assert result.available is True
        assert result.stale_status == "fresh"

    def test_empty_dict_returns_summary_with_defaults(self):
        result = _normalize_summary({})
        assert isinstance(result, AppContextSummary)
        assert result.available is False

    def test_dict_with_mode_preserved(self):
        result = _normalize_summary({"available": True, "mode": "brownfield"})
        assert isinstance(result, AppContextSummary)
        assert result.mode == "brownfield"


# ---------------------------------------------------------------------------
# 2. _normalize_impact_hints
# ---------------------------------------------------------------------------

class TestNormalizeImpactHints:
    def test_none_returns_none(self):
        assert _normalize_impact_hints(None) is None

    def test_impact_hints_instance_returned_unchanged(self):
        hints = AppContextImpactHints(available=True)
        result = _normalize_impact_hints(hints)
        assert result is hints

    def test_dict_returns_impact_hints(self):
        d = {"available": True, "explanations": ["Changed route"]}
        result = _normalize_impact_hints(d)
        assert isinstance(result, AppContextImpactHints)
        assert result.available is True
        assert result.explanations == ["Changed route"]

    def test_empty_dict_returns_hints_with_defaults(self):
        result = _normalize_impact_hints({})
        assert isinstance(result, AppContextImpactHints)
        assert result.available is False

    def test_dict_with_ownership_warnings_preserved(self):
        d = {"ownership_warnings": ["read-only boundary touched"]}
        result = _normalize_impact_hints(d)
        assert isinstance(result, AppContextImpactHints)
        assert "read-only boundary touched" in result.ownership_warnings


# ---------------------------------------------------------------------------
# 3. _risky_signals
# ---------------------------------------------------------------------------

class TestRiskySignals:
    def test_no_lane_no_change_class_no_paths_returns_empty(self):
        assert _call_risky() == []

    def test_low_risk_patch_no_signals(self):
        assert _call_risky(change_class="patch", refinement_lane="ui_patch") == []

    # -- change_class signals --

    def test_change_class_core_triggers_conceptual_signal(self):
        signals = _call_risky(change_class="core")
        assert "conceptual_or_architecture_replan" in signals

    def test_change_class_case_insensitive(self):
        signals = _call_risky(change_class="CORE")
        assert "conceptual_or_architecture_replan" in signals

    def test_change_class_core_whitespace_stripped(self):
        signals = _call_risky(change_class="  core  ")
        assert "conceptual_or_architecture_replan" in signals

    # -- lane-based signals --

    def test_lane_data_model_migration_signal(self):
        signals = _call_risky(refinement_lane="data_model_migration")
        assert "data_model_migration" in signals

    def test_lane_integration_signal(self):
        signals = _call_risky(refinement_lane="integration")
        assert "integration" in signals

    def test_lane_managed_capability_change_signal(self):
        signals = _call_risky(refinement_lane="managed_capability_change")
        assert "managed_capability_change" in signals

    def test_lane_architecture_replan_triggers_conceptual_signal(self):
        signals = _call_risky(refinement_lane="architecture_replan")
        assert "conceptual_or_architecture_replan" in signals

    def test_lane_conceptual_reframe_triggers_conceptual_signal(self):
        signals = _call_risky(refinement_lane="conceptual_reframe")
        assert "conceptual_or_architecture_replan" in signals

    def test_lane_feature_addition_with_backend_path(self):
        signals = _call_risky(
            refinement_lane="feature_addition",
            affected_bundle_paths=["modules/billing/handler.py"],
        )
        assert "module_backend_feature_addition" in signals

    def test_lane_feature_addition_with_non_backend_path_no_signal(self):
        signals = _call_risky(
            refinement_lane="feature_addition",
            affected_bundle_paths=["ui/pages/dashboard.yaml"],
        )
        assert "module_backend_feature_addition" not in signals

    def test_lane_feature_addition_empty_paths_no_module_signal(self):
        signals = _call_risky(refinement_lane="feature_addition")
        assert "module_backend_feature_addition" not in signals

    # -- path-triggered signals --

    def test_sensitive_path_triggers_signal(self):
        signals = _call_risky(affected_bundle_paths=["modules/auth/handler.py"])
        assert "sensitive_boundary_change" in signals

    def test_secret_path_triggers_sensitive_signal(self):
        signals = _call_risky(affected_bundle_paths=["config/secrets.yaml"])
        assert "sensitive_boundary_change" in signals

    def test_unrelated_path_no_sensitive_signal(self):
        signals = _call_risky(affected_bundle_paths=["modules/billing/handler.py"])
        assert "sensitive_boundary_change" not in signals

    def test_read_only_discovered_boundary_overlap_triggers_signal(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/discovered")]
        )
        signals = _call_risky(
            summary=summary,
            affected_bundle_paths=["modules/discovered/handler.py"],
        )
        assert "read_only_discovered_boundary" in signals

    def test_non_overlapping_boundary_no_signal(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/discovered")]
        )
        signals = _call_risky(
            summary=summary,
            affected_bundle_paths=["modules/billing/handler.py"],
        )
        assert "read_only_discovered_boundary" not in signals

    def test_none_summary_no_read_only_signal(self):
        signals = _call_risky(
            summary=None,
            affected_bundle_paths=["modules/discovered/handler.py"],
        )
        assert "read_only_discovered_boundary" not in signals

    def test_brownfield_mode_with_paths_triggers_signal(self):
        summary = _brownfield_summary()
        signals = _call_risky(
            summary=summary,
            affected_bundle_paths=["modules/orders/service.py"],
        )
        assert "brownfield_source_affecting_change" in signals

    def test_non_brownfield_mode_no_brownfield_signal(self):
        summary = _fresh_summary(mode="greenfield")
        signals = _call_risky(
            summary=summary,
            affected_bundle_paths=["modules/orders/service.py"],
        )
        assert "brownfield_source_affecting_change" not in signals

    def test_none_summary_no_brownfield_signal(self):
        signals = _call_risky(
            summary=None,
            affected_bundle_paths=["modules/orders/service.py"],
        )
        assert "brownfield_source_affecting_change" not in signals

    # -- deduplication --

    def test_signals_deduped_no_duplicates(self):
        # core change_class + architecture_replan lane both trigger conceptual_or_architecture_replan
        signals = _call_risky(
            change_class="core",
            refinement_lane="architecture_replan",
        )
        assert signals.count("conceptual_or_architecture_replan") == 1

    def test_multiple_signals_returned_together(self):
        summary = _fresh_summary(
            ownership_boundaries=[_boundary("modules/discovered")]
        )
        signals = _call_risky(
            summary=summary,
            change_class="core",
            refinement_lane="data_model_migration",
            affected_bundle_paths=["modules/discovered/handler.py", "modules/auth/service.py"],
        )
        assert "data_model_migration" in signals
        assert "conceptual_or_architecture_replan" in signals
        assert "sensitive_boundary_change" in signals
        assert "read_only_discovered_boundary" in signals
