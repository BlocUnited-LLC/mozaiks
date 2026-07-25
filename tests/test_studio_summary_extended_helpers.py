"""
Pure helper unit tests for:
  mozaiksai/core/runtime/app/studio_summary.py

Covers helpers NOT tested in test_studio_summary_helpers.py:

  _normalize_current_plan:
    - non-dict → default empty plan
    - empty dict → default empty plan
    - summary present → preserved
    - whitespace-only summary → treated as None
    - build_tasks normalised via _normalize_build_tasks
    - owned_paths from dict → preserved
    - owned_paths absent → flattened from task owned_paths
    - acceptance_criteria from dict → preserved
    - acceptance_criteria absent → flattened from tasks
    - approvals_required returned
    - cost_implications returned
    - runtime_implications returned

  _normalize_build_state:
    - empty dict → default plan_state "not_started"
    - current_plan with tasks → plan_state "plan_ready"
    - current_request.text set but no plan → plan_state "draft_saved"
    - explicit plan_state string preserved
    - approval_state from dict preserved
    - approval_state missing → default "not_started"
    - last_saved_at from dict preserved
    - last_saved_at absent → falls back to current_request.updated_at
    - returns all required keys

  _recommend_next_step:
    - no provider → config guidance message
    - no model → config guidance message
    - no refinement policy enabled -> enable guidance
    - no admins → admin guidance
    - zero workflows → start build guidance
    - no entry_point → entry_point guidance
    - all configured → review guidance
"""
from __future__ import annotations

from typing import Any

from mozaiksai.core.runtime.app.studio_summary import (
    _normalize_build_state,
    _normalize_current_plan,
    _recommend_next_step,
)

# ---------------------------------------------------------------------------
# Helpers to build valid refinement policy and plan state dicts
# ---------------------------------------------------------------------------

def _cp(enabled: bool = True) -> dict[str, Any]:
    return {"enabled": enabled, "classifier": {}, "coding": {}, "llm_profiles": {}}


def _valid_step_kwargs(**overrides) -> dict[str, Any]:
    """Minimal kwargs that produce the final 'review' guidance."""
    base: dict[str, Any] = {
        "provider": "openai",
        "model": "gpt-4o",
        "admins": ["admin@example.com"],
        "workflow_count": 1,
        "entry_point": "ValueEngine",
        "refinement_policy": _cp(enabled=True),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. _normalize_current_plan
# ---------------------------------------------------------------------------

class TestNormalizeCurrentPlan:
    def test_non_dict_returns_default_empty_plan(self):
        result = _normalize_current_plan(None)
        assert result["summary"] is None
        assert result["build_tasks"] == []
        assert result["owned_paths"] == []

    def test_empty_dict_returns_default(self):
        result = _normalize_current_plan({})
        assert result["summary"] is None
        assert result["build_tasks"] == []

    def test_summary_preserved(self):
        result = _normalize_current_plan({"summary": "Add wallet module"})
        assert result["summary"] == "Add wallet module"

    def test_whitespace_only_summary_is_none(self):
        result = _normalize_current_plan({"summary": "   "})
        assert result["summary"] is None

    def test_build_tasks_normalised(self):
        plan = {"build_tasks": [{"task_id": "t1", "owned_paths": ["modules/wallet/"]}]}
        result = _normalize_current_plan(plan)
        assert len(result["build_tasks"]) == 1
        assert result["build_tasks"][0]["task_id"] == "t1"

    def test_owned_paths_from_dict_preserved(self):
        plan = {"owned_paths": ["modules/orders/", "modules/wallet/"]}
        result = _normalize_current_plan(plan)
        assert "modules/orders/" in result["owned_paths"]
        assert "modules/wallet/" in result["owned_paths"]

    def test_owned_paths_absent_flattened_from_tasks(self):
        plan = {
            "build_tasks": [
                {"task_id": "t1", "owned_paths": ["modules/orders/"]},
                {"task_id": "t2", "owned_paths": ["modules/wallet/"]},
            ]
        }
        result = _normalize_current_plan(plan)
        assert "modules/orders/" in result["owned_paths"]
        assert "modules/wallet/" in result["owned_paths"]

    def test_acceptance_criteria_from_dict_preserved(self):
        plan = {"acceptance_criteria": ["Users can pay", "Receipts generated"]}
        result = _normalize_current_plan(plan)
        assert "Users can pay" in result["acceptance_criteria"]

    def test_acceptance_criteria_absent_flattened_from_tasks(self):
        plan = {
            "build_tasks": [
                {"task_id": "t1", "acceptance_criteria": ["Feature X works"]}
            ]
        }
        result = _normalize_current_plan(plan)
        assert "Feature X works" in result["acceptance_criteria"]

    def test_approvals_required_returned(self):
        plan = {"approvals_required": ["stakeholder_sign_off"]}
        result = _normalize_current_plan(plan)
        assert "stakeholder_sign_off" in result["approvals_required"]

    def test_cost_implications_returned(self):
        plan = {"cost_implications": ["Adds payment provider fee"]}
        result = _normalize_current_plan(plan)
        assert "Adds payment provider fee" in result["cost_implications"]

    def test_runtime_implications_returned(self):
        plan = {"runtime_implications": ["New webhook endpoint"]}
        result = _normalize_current_plan(plan)
        assert "New webhook endpoint" in result["runtime_implications"]


# ---------------------------------------------------------------------------
# 2. _normalize_build_state
# ---------------------------------------------------------------------------

class TestNormalizeBuildState:
    def test_empty_dict_plan_state_not_started(self):
        result = _normalize_build_state({})
        assert result["plan_state"] == "not_started"

    def test_plan_with_build_tasks_is_plan_ready(self):
        raw = {
            "current_plan": {
                "build_tasks": [{"task_id": "t1"}],
            }
        }
        result = _normalize_build_state(raw)
        assert result["plan_state"] == "plan_ready"

    def test_plan_with_summary_is_plan_ready(self):
        raw = {"current_plan": {"summary": "Do something"}}
        result = _normalize_build_state(raw)
        assert result["plan_state"] == "plan_ready"

    def test_request_text_without_plan_is_draft_saved(self):
        raw = {"current_request": {"text": "Build me a wallet"}}
        result = _normalize_build_state(raw)
        assert result["plan_state"] == "draft_saved"

    def test_explicit_plan_state_preserved(self):
        raw = {"plan_state": "awaiting_approval"}
        result = _normalize_build_state(raw)
        assert result["plan_state"] == "awaiting_approval"

    def test_approval_state_from_dict_preserved(self):
        raw = {"approval_state": "approved"}
        result = _normalize_build_state(raw)
        assert result["approval_state"] == "approved"

    def test_approval_state_absent_defaults(self):
        result = _normalize_build_state({})
        assert result["approval_state"] == "not_started"

    def test_last_saved_at_from_dict_preserved(self):
        raw = {"last_saved_at": "2026-06-12T10:00:00Z"}
        result = _normalize_build_state(raw)
        assert result["last_saved_at"] == "2026-06-12T10:00:00Z"

    def test_last_saved_at_falls_back_to_request_updated_at(self):
        raw = {"current_request": {"text": "x", "updated_at": "2026-01-01T00:00:00Z"}}
        result = _normalize_build_state(raw)
        assert result["last_saved_at"] == "2026-01-01T00:00:00Z"

    def test_returns_all_required_keys(self):
        result = _normalize_build_state({})
        for key in ("current_request", "current_plan", "recent_requests", "plan_state", "approval_state", "last_saved_at"):
            assert key in result


# ---------------------------------------------------------------------------
# 3. _recommend_next_step
# ---------------------------------------------------------------------------

class TestRecommendNextStep:
    def test_no_provider_returns_config_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs(provider=None))
        assert "provider" in result.lower() or "model" in result.lower()

    def test_no_model_returns_config_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs(model=None))
        assert "provider" in result.lower() or "model" in result.lower()

    def test_refinement_policy_disabled_returns_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs(refinement_policy=_cp(enabled=False)))
        assert "refinement_policy" in result or "enable" in result.lower()

    def test_no_admins_returns_admin_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs(admins=[]))
        assert "admin" in result.lower()

    def test_zero_workflows_returns_build_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs(workflow_count=0))
        assert "build" in result.lower() or "workflow" in result.lower() or "request" in result.lower()

    def test_no_entry_point_returns_entry_point_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs(entry_point=None))
        assert "entry_point" in result or "workflows.entry_point" in result

    def test_all_configured_returns_review_guidance(self):
        result = _recommend_next_step(**_valid_step_kwargs())
        assert "Review" in result or "review" in result.lower()

    def test_returns_non_empty_string(self):
        for kwargs in [
            _valid_step_kwargs(provider=None),
            _valid_step_kwargs(refinement_policy=_cp(enabled=False)),
            _valid_step_kwargs(admins=[]),
            _valid_step_kwargs(workflow_count=0),
            _valid_step_kwargs(entry_point=None),
            _valid_step_kwargs(),
        ]:
            result = _recommend_next_step(**kwargs)
            assert isinstance(result, str)
            assert result.strip()
