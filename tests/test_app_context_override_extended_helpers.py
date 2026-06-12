"""
Pure helper unit tests for:
  mozaiksai/control_plane/app_context_override.py

Covers private helpers NOT exercised by integration tests in
test_app_context_policy_override.py:

  _normalize_path:
    - empty string → ""
    - backslashes → forward slashes
    - leading slash stripped
    - lowercased

  _dedupe:
    - empty list → []
    - duplicates removed in order
    - empty/whitespace values excluded

  _override_warnings:
    - ALLOW_WITH_WARNING → override + allow warnings
    - REQUIRE_REFRESH_FIRST → override + refresh warnings
    - REJECT → override + reject warnings
    - general override warning always present

  _stable_override_id:
    - same payload → same ID (deterministic)
    - different payload → different ID
    - starts with "ctx_override_"
    - 12-char hex suffix

  _normalize_policy:
    - AppContextPolicyResult instance → returned unchanged
    - dict → model_validate returns AppContextPolicyResult

  _normalize_override:
    - AppContextPolicyOverride instance → returned unchanged
    - dict → model_validate returns AppContextPolicyOverride

  _plan_policy_result:
    - plan with AppContextPolicyResult context_policy_decision → returned
    - plan with dict context_policy_decision → model_validate
    - plan with no context_policy_decision → fallback with original_decision

  _apply_decision_to_policy:
    - ALLOW_WITH_WARNING → decision changed to WARN, allowed=True, blocking=False
    - ALLOW_WITH_WARNING → requires_context_refresh=False, requires_human_override=False
    - REQUIRE_REFRESH_FIRST → decision unchanged from original
    - REJECT → decision unchanged from original
    - warnings from override appended in all cases

  _validate_override_scope:
    - mismatched app_id → ValueError
    - mismatched request_id → ValueError
    - matching IDs → no error
    - mismatched applies_to_change_class → ValueError
    - mismatched applies_to_refinement_lane → ValueError
"""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from mozaiksai.control_plane.app_context_override import (
    APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING,
    APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING,
    APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING,
    APP_CONTEXT_POLICY_OVERRIDE_WARNING,
    AppContextPolicyOverride,
    AppContextPolicyOverrideDecision,
    _apply_decision_to_policy,
    _dedupe,
    _normalize_override,
    _normalize_path,
    _normalize_policy,
    _override_warnings,
    _plan_policy_result,
    _stable_override_id,
    _validate_override_scope,
)
from mozaiksai.control_plane.app_context_policy import (
    AppContextPolicyDecision,
    AppContextPolicyResult,
)

# ---------------------------------------------------------------------------
# Helpers to build test fixtures
# ---------------------------------------------------------------------------

_REVIEWED_AT = datetime(2026, 6, 12, 10, 0, 0, tzinfo=UTC)


def _blocked_policy(**kwargs) -> AppContextPolicyResult:
    defaults: dict[str, Any] = {
        "decision": AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH,
        "allowed": False,
        "blocking": True,
        "risk_level": "high",
        "requires_context_refresh": True,
    }
    defaults.update(kwargs)
    return AppContextPolicyResult(**defaults)


def _override(
    override_decision: AppContextPolicyOverrideDecision = AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING,
    **kwargs,
) -> AppContextPolicyOverride:
    defaults: dict[str, Any] = {
        "override_id": "ctx_override_abc123456789",
        "app_id": "my-app",
        "request_id": "req-001",
        "original_policy_decision": AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH,
        "override_decision": override_decision,
        "reason": "Manual review approved",
        "reviewer": "mbari",
        "reviewed_at": _REVIEWED_AT,
        "warnings": [APP_CONTEXT_POLICY_OVERRIDE_WARNING, APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING],
    }
    defaults.update(kwargs)
    return AppContextPolicyOverride(**defaults)


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

    def test_leading_slash_stripped(self):
        assert _normalize_path("/modules/billing/handler.py") == "modules/billing/handler.py"

    def test_lowercased(self):
        assert _normalize_path("Modules/BILLING/Handler.py") == "modules/billing/handler.py"

    def test_valid_path_unchanged(self):
        assert _normalize_path("modules/billing/handler.py") == "modules/billing/handler.py"


# ---------------------------------------------------------------------------
# 2. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_unique_values_preserved(self):
        assert _dedupe(["a", "b", "c"]) == ["a", "b", "c"]

    def test_duplicates_removed_first_kept(self):
        assert _dedupe(["a", "b", "a"]) == ["a", "b"]

    def test_order_preserved(self):
        assert _dedupe(["z", "a", "m"]) == ["z", "a", "m"]

    def test_empty_strings_excluded(self):
        result = _dedupe(["a", "", "b"])
        assert result == ["a", "b"]

    def test_whitespace_strings_excluded(self):
        result = _dedupe(["a", "  ", "b"])
        assert result == ["a", "b"]


# ---------------------------------------------------------------------------
# 3. _override_warnings
# ---------------------------------------------------------------------------

class TestOverrideWarnings:
    def test_allow_with_warning_includes_general_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        assert APP_CONTEXT_POLICY_OVERRIDE_WARNING in warnings

    def test_allow_with_warning_includes_allow_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        assert APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING in warnings

    def test_allow_with_warning_has_two_entries(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        assert len(warnings) == 2

    def test_require_refresh_includes_general_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.REQUIRE_REFRESH_FIRST)
        assert APP_CONTEXT_POLICY_OVERRIDE_WARNING in warnings

    def test_require_refresh_includes_refresh_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.REQUIRE_REFRESH_FIRST)
        assert APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING in warnings

    def test_reject_includes_general_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.REJECT)
        assert APP_CONTEXT_POLICY_OVERRIDE_WARNING in warnings

    def test_reject_includes_reject_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.REJECT)
        assert APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING in warnings

    def test_allow_does_not_include_refresh_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        assert APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING not in warnings

    def test_reject_does_not_include_allow_warning(self):
        warnings = _override_warnings(AppContextPolicyOverrideDecision.REJECT)
        assert APP_CONTEXT_POLICY_OVERRIDE_ALLOW_WARNING not in warnings


# ---------------------------------------------------------------------------
# 4. _stable_override_id
# ---------------------------------------------------------------------------

class TestStableOverrideId:
    def test_same_payload_returns_same_id(self):
        payload = {"app_id": "my-app", "request_id": "req-1"}
        assert _stable_override_id(payload) == _stable_override_id(payload)

    def test_different_payload_returns_different_id(self):
        id1 = _stable_override_id({"app_id": "my-app"})
        id2 = _stable_override_id({"app_id": "other-app"})
        assert id1 != id2

    def test_starts_with_ctx_override_prefix(self):
        result = _stable_override_id({"app_id": "x"})
        assert result.startswith("ctx_override_")

    def test_hex_suffix_is_12_chars(self):
        result = _stable_override_id({"app_id": "x"})
        suffix = result[len("ctx_override_"):]
        assert len(suffix) == 12

    def test_payload_key_order_does_not_affect_id(self):
        id1 = _stable_override_id({"b": "2", "a": "1"})
        id2 = _stable_override_id({"a": "1", "b": "2"})
        assert id1 == id2


# ---------------------------------------------------------------------------
# 5. _normalize_policy
# ---------------------------------------------------------------------------

class TestNormalizePolicy:
    def test_instance_returned_unchanged(self):
        policy = _blocked_policy()
        result = _normalize_policy(policy)
        assert result is policy

    def test_dict_returns_policy_result(self):
        d = {
            "decision": "block_requires_context_refresh",
            "allowed": False,
            "blocking": True,
        }
        result = _normalize_policy(d)
        assert isinstance(result, AppContextPolicyResult)
        assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH

    def test_empty_dict_returns_default_policy(self):
        result = _normalize_policy({})
        assert isinstance(result, AppContextPolicyResult)
        assert result.allowed is True


# ---------------------------------------------------------------------------
# 6. _normalize_override
# ---------------------------------------------------------------------------

class TestNormalizeOverride:
    def test_instance_returned_unchanged(self):
        ov = _override()
        result = _normalize_override(ov)
        assert result is ov

    def test_dict_returns_override(self):
        d = {
            "override_id": "ctx_override_abc",
            "app_id": "my-app",
            "request_id": "req-001",
            "original_policy_decision": "block_requires_context_refresh",
            "override_decision": "allow_with_warning",
            "reason": "Approved",
            "reviewer": "mbari",
            "reviewed_at": "2026-06-12T10:00:00+00:00",
        }
        result = _normalize_override(d)
        assert isinstance(result, AppContextPolicyOverride)
        assert result.app_id == "my-app"


# ---------------------------------------------------------------------------
# 7. _plan_policy_result
# ---------------------------------------------------------------------------

class TestPlanPolicyResult:
    def test_context_policy_decision_is_result_returned_directly(self):
        policy = _blocked_policy()
        plan = SimpleNamespace(context_policy_decision=policy)
        result = _plan_policy_result(plan, AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH)
        assert result is policy

    def test_context_policy_decision_is_dict_model_validate(self):
        plan = SimpleNamespace(
            context_policy_decision={
                "decision": "block_requires_context_refresh",
                "allowed": False,
                "blocking": True,
            }
        )
        result = _plan_policy_result(plan, AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH)
        assert isinstance(result, AppContextPolicyResult)
        assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH

    def test_missing_context_policy_decision_returns_fallback(self):
        plan = SimpleNamespace()
        result = _plan_policy_result(plan, AppContextPolicyDecision.BLOCK_REQUIRES_HUMAN_OVERRIDE)
        assert isinstance(result, AppContextPolicyResult)
        assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_HUMAN_OVERRIDE
        assert result.allowed is False
        assert result.blocking is True

    def test_none_context_policy_decision_returns_fallback(self):
        plan = SimpleNamespace(context_policy_decision=None)
        result = _plan_policy_result(plan, AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH)
        assert isinstance(result, AppContextPolicyResult)
        assert result.allowed is False


# ---------------------------------------------------------------------------
# 8. _apply_decision_to_policy
# ---------------------------------------------------------------------------

class TestApplyDecisionToPolicy:
    def test_allow_with_warning_sets_decision_to_warn(self):
        policy = _blocked_policy()
        ov = _override(override_decision=AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.decision is AppContextPolicyDecision.WARN

    def test_allow_with_warning_sets_allowed_true(self):
        policy = _blocked_policy()
        ov = _override(override_decision=AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.allowed is True

    def test_allow_with_warning_sets_blocking_false(self):
        policy = _blocked_policy()
        ov = _override(override_decision=AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.blocking is False

    def test_allow_with_warning_clears_requires_context_refresh(self):
        policy = _blocked_policy(requires_context_refresh=True)
        ov = _override(override_decision=AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.requires_context_refresh is False

    def test_allow_with_warning_clears_requires_human_override(self):
        policy = _blocked_policy(requires_human_override=True)
        ov = _override(override_decision=AppContextPolicyOverrideDecision.ALLOW_WITH_WARNING)
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.requires_human_override is False

    def test_require_refresh_keeps_original_decision(self):
        policy = _blocked_policy()
        ov = _override(
            override_decision=AppContextPolicyOverrideDecision.REQUIRE_REFRESH_FIRST,
            warnings=[APP_CONTEXT_POLICY_OVERRIDE_WARNING, APP_CONTEXT_POLICY_OVERRIDE_REFRESH_WARNING],
        )
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH

    def test_reject_keeps_original_decision(self):
        policy = _blocked_policy()
        ov = _override(
            override_decision=AppContextPolicyOverrideDecision.REJECT,
            warnings=[APP_CONTEXT_POLICY_OVERRIDE_WARNING, APP_CONTEXT_POLICY_OVERRIDE_REJECT_WARNING],
        )
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert result.decision is AppContextPolicyDecision.BLOCK_REQUIRES_CONTEXT_REFRESH

    def test_override_warnings_appended(self):
        policy = _blocked_policy()
        ov = _override(
            override_decision=AppContextPolicyOverrideDecision.REQUIRE_REFRESH_FIRST,
            warnings=[APP_CONTEXT_POLICY_OVERRIDE_WARNING],
        )
        result = _apply_decision_to_policy(policy_result=policy, override=ov)
        assert APP_CONTEXT_POLICY_OVERRIDE_WARNING in result.warnings


# ---------------------------------------------------------------------------
# 9. _validate_override_scope
# ---------------------------------------------------------------------------

class TestValidateOverrideScope:
    def test_matching_ids_no_error(self):
        plan = SimpleNamespace(
            app_id="my-app",
            request_id="req-001",
            affected_bundle_paths=[],
        )
        ov = _override(applies_to_paths=[])
        # should not raise
        _validate_override_scope(plan=plan, override=ov)

    def test_mismatched_app_id_raises(self):
        plan = SimpleNamespace(app_id="other-app", request_id="req-001", affected_bundle_paths=[])
        ov = _override()
        with pytest.raises(ValueError, match="app_id"):
            _validate_override_scope(plan=plan, override=ov)

    def test_mismatched_request_id_raises(self):
        plan = SimpleNamespace(app_id="my-app", request_id="req-999", affected_bundle_paths=[])
        ov = _override()
        with pytest.raises(ValueError, match="request_id"):
            _validate_override_scope(plan=plan, override=ov)

    def test_empty_plan_app_id_no_check(self):
        # empty app_id on plan means no check is performed
        plan = SimpleNamespace(app_id="", request_id="", affected_bundle_paths=[])
        ov = _override(applies_to_paths=[])
        _validate_override_scope(plan=plan, override=ov)

    def test_mismatched_change_class_raises(self):
        plan = SimpleNamespace(
            app_id="my-app",
            request_id="req-001",
            affected_bundle_paths=[],
            change_class="feature",
        )
        ov = _override(applies_to_change_class="patch")
        with pytest.raises(ValueError, match="change_class"):
            _validate_override_scope(plan=plan, override=ov)

    def test_mismatched_refinement_lane_raises(self):
        plan = SimpleNamespace(
            app_id="my-app",
            request_id="req-001",
            affected_bundle_paths=[],
            refinement_lane="ui_patch",
        )
        ov = _override(applies_to_refinement_lane="integration")
        with pytest.raises(ValueError, match="refinement_lane"):
            _validate_override_scope(plan=plan, override=ov)

    def test_matching_change_class_no_error(self):
        plan = SimpleNamespace(
            app_id="my-app",
            request_id="req-001",
            affected_bundle_paths=[],
            change_class="patch",
        )
        ov = _override(applies_to_change_class="patch")
        _validate_override_scope(plan=plan, override=ov)
