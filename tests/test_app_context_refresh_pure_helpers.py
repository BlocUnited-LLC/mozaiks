"""
Pure helper unit tests for:
  mozaiksai/control_plane/app_context_refresh.py

Covers sync pure helpers (no IO/async):

  _normalize_summary:
    - AppContextSummary instance → returned unchanged
    - None → None
    - dict → model_validate returns AppContextSummary

  _normalize_policy:
    - AppContextPolicyResult instance → returned unchanged
    - None → None
    - dict → model_validate returns AppContextPolicyResult

  _refresh_reason:
    - explicit reason → returned as-is (stripped)
    - no reason, policy with reasons → joined with space
    - no reason, policy with empty reasons → default fallback
    - no reason, None policy → default fallback
    - whitespace-only reason → falls through to policy reasons

  _dedupe:
    - empty list → []
    - list with unique values → all preserved in order
    - list with duplicate values → first occurrence kept
    - list with empty/whitespace strings → filtered
    - None entries → filtered
    - mixed valid and invalid → only valid unique kept
"""
from __future__ import annotations

from mozaiksai.control_plane.app_context import AppContextSummary
from mozaiksai.control_plane.app_context_policy import AppContextPolicyResult
from mozaiksai.control_plane.app_context_refresh import (
    _dedupe,
    _normalize_policy,
    _normalize_summary,
    _refresh_reason,
)

# ---------------------------------------------------------------------------
# 1. _normalize_summary
# ---------------------------------------------------------------------------

class TestNormalizeSummary:
    def test_instance_returned_unchanged(self):
        summary = AppContextSummary(app_id="my-app")
        result = _normalize_summary(summary)
        assert result is summary

    def test_none_returns_none(self):
        assert _normalize_summary(None) is None

    def test_dict_returns_app_context_summary(self):
        result = _normalize_summary({"app_id": "my-app", "available": True})
        assert isinstance(result, AppContextSummary)
        assert result.app_id == "my-app"
        assert result.available is True

    def test_empty_dict_returns_default_summary(self):
        result = _normalize_summary({})
        assert isinstance(result, AppContextSummary)
        assert result.app_id is None
        assert result.available is False


# ---------------------------------------------------------------------------
# 2. _normalize_policy
# ---------------------------------------------------------------------------

class TestNormalizePolicy:
    def test_instance_returned_unchanged(self):
        policy = AppContextPolicyResult()
        result = _normalize_policy(policy)
        assert result is policy

    def test_none_returns_none(self):
        assert _normalize_policy(None) is None

    def test_dict_returns_app_context_policy_result(self):
        result = _normalize_policy({"decision": "allow", "allowed": True})
        assert isinstance(result, AppContextPolicyResult)
        assert result.allowed is True

    def test_empty_dict_returns_default_policy(self):
        result = _normalize_policy({})
        assert isinstance(result, AppContextPolicyResult)
        assert result.allowed is True  # default


# ---------------------------------------------------------------------------
# 3. _refresh_reason
# ---------------------------------------------------------------------------

class TestRefreshReason:
    def test_explicit_reason_returned(self):
        policy = AppContextPolicyResult(reasons=["some policy reason"])
        result = _refresh_reason(reason="Custom reason", policy_result=policy)
        assert result == "Custom reason"

    def test_explicit_reason_stripped(self):
        result = _refresh_reason(reason="  Custom reason  ", policy_result=None)
        assert result == "Custom reason"

    def test_no_reason_uses_policy_reasons(self):
        policy = AppContextPolicyResult(reasons=["Context is stale", "Data model changed"])
        result = _refresh_reason(reason=None, policy_result=policy)
        assert result == "Context is stale Data model changed"

    def test_no_reason_empty_policy_reasons_returns_default(self):
        policy = AppContextPolicyResult(reasons=[])
        result = _refresh_reason(reason=None, policy_result=policy)
        assert result == "Refresh app context before retrying high-risk refinement."

    def test_no_reason_none_policy_returns_default(self):
        result = _refresh_reason(reason=None, policy_result=None)
        assert result == "Refresh app context before retrying high-risk refinement."

    def test_whitespace_only_reason_falls_through_to_policy(self):
        policy = AppContextPolicyResult(reasons=["Stale context"])
        result = _refresh_reason(reason="   ", policy_result=policy)
        assert result == "Stale context"

    def test_empty_reason_falls_through_to_default(self):
        result = _refresh_reason(reason="", policy_result=None)
        assert result == "Refresh app context before retrying high-risk refinement."


# ---------------------------------------------------------------------------
# 4. _dedupe
# ---------------------------------------------------------------------------

class TestDedupe:
    def test_empty_list_returns_empty(self):
        assert _dedupe([]) == []

    def test_unique_values_preserved_in_order(self):
        result = _dedupe(["alpha", "beta", "gamma"])
        assert result == ["alpha", "beta", "gamma"]

    def test_duplicate_values_first_occurrence_kept(self):
        result = _dedupe(["alpha", "beta", "alpha"])
        assert result == ["alpha", "beta"]

    def test_empty_strings_filtered(self):
        result = _dedupe(["alpha", "", "beta"])
        assert result == ["alpha", "beta"]

    def test_whitespace_only_strings_filtered(self):
        result = _dedupe(["alpha", "   ", "beta"])
        assert result == ["alpha", "beta"]

    def test_none_entries_filtered(self):
        result = _dedupe(["alpha", None, "beta"])  # type: ignore
        assert result == ["alpha", "beta"]

    def test_all_duplicates_returns_single(self):
        result = _dedupe(["same", "same", "same"])
        assert result == ["same"]

    def test_mixed_valid_and_invalid(self):
        result = _dedupe(["valid", "", "valid", "also_valid", None, "also_valid"])  # type: ignore
        assert result == ["valid", "also_valid"]
