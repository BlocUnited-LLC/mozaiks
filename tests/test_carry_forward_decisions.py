"""
Tests for carry_forward_decisions in AppBuildPlan (Phase 6).

Validates:
- AppBuildPlan schema includes carry_forward_decisions with default [].
- Valid CarryForwardDecision entries parse and persist.
- Invalid decision enum fails validation.
- Missing reason fails validation.
- affected_build_tasks must reference existing task ids when provided.
- Non-conceptual build with empty/absent carry_forward_decisions passes.
- AppPlanAgent guidance mentions reuse/adapt/regenerate/drop.
- Guidance states reuse does not copy files.
- Guidance states AssemblyAgent merge not implemented.
- Existing AppBuildPlan tests still pass (smoke).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _Context:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value


def _load_app_build_plan_module():
    path = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools" / "app_build_plan.py"
    spec = importlib.util.spec_from_file_location("tests.app_build_plan_phase6", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _minimal_plan(**overrides) -> dict[str, Any]:
    base: dict[str, Any] = {
        "agent_message": "Conceptual replan plan.",
        "app_kind": "community_platform",
        "pages": [{"name": "Home", "route": "/", "purpose": "Landing"}],
        "entities": [],
        "roles": ["user"],
        "backend_scope": [],
        "frontend_scope": [],
        "capability_packs": [],
        "external_integrations": [],
        "agent_backend_required": False,
        "build_tasks": [],
        "generation_order": [],
    }
    base.update(overrides)
    return base


def _minimal_task(task_id: str = "t1", task_type: str = "module_contract") -> dict[str, Any]:
    agents = {
        "module_contract": "ConfigMiddlewareAgent",
        "persistence_contract": "DatabaseAgent",
        "page_bundle": "AppSchemaAgent",
        "business_services": "ServiceAgent",
        "data_models": "ServiceAgent",
        "api_surface": "ControllerAgent",
    }
    return {
        "task_id": task_id,
        "task_type": task_type,
        "capability_pack_id": None,
        "surface_id": "notifications",
        "surface_kind": None,
        "execution_target": "app",
        "initial_agent": agents.get(task_type, "ConfigMiddlewareAgent"),
        "description": f"Task {task_id}",
        "initial_message": f"Generate {task_id}",
        "owned_paths": [f"modules/{task_id}/module.yaml"],
        "depends_on": [],
        "acceptance_criteria": [],
    }


def _decision(
    module_id: str = "notifications",
    decision: str = "reuse",
    reason: str = "Fits new concept.",
    source: str = "carry_forward_candidate",
    affected_build_tasks: list[str] | None = None,
) -> dict[str, Any]:
    d: dict[str, Any] = {
        "module_id": module_id,
        "decision": decision,
        "reason": reason,
        "source": source,
    }
    if affected_build_tasks is not None:
        d["affected_build_tasks"] = affected_build_tasks
    return d


# ---------------------------------------------------------------------------
# 1. Schema includes carry_forward_decisions with default []
# ---------------------------------------------------------------------------

class TestSchema:
    def _so(self) -> dict:
        path = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml"
        return yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_carry_forward_decisions_field_in_appbuildplan(self) -> None:
        so = self._so()
        plan_fields = so["models"]["AppBuildPlan"]["fields"]
        assert "carry_forward_decisions" in plan_fields

    def test_carry_forward_decisions_is_optional_list(self) -> None:
        so = self._so()
        field = so["models"]["AppBuildPlan"]["fields"]["carry_forward_decisions"]
        assert field["type"] == "optional_list"
        assert field["items"] == "CarryForwardDecision"

    def test_carry_forward_decision_model_exists(self) -> None:
        so = self._so()
        assert "CarryForwardDecision" in so["models"]

    def test_carry_forward_decision_has_decision_literal(self) -> None:
        so = self._so()
        field = so["models"]["CarryForwardDecision"]["fields"]["decision"]
        assert field["type"] == "literal"
        assert set(field["values"]) == {"reuse", "adapt", "regenerate", "drop"}

    def test_carry_forward_decision_has_source_literal(self) -> None:
        so = self._so()
        field = so["models"]["CarryForwardDecision"]["fields"]["source"]
        assert field["type"] == "literal"
        assert set(field["values"]) == {
            "carry_forward_candidate", "human_override", "planner"
        }

    def test_carry_forward_decision_has_affected_build_tasks(self) -> None:
        so = self._so()
        field = so["models"]["CarryForwardDecision"]["fields"]["affected_build_tasks"]
        assert field["type"] == "optional_list"


# ---------------------------------------------------------------------------
# 2. Valid carry_forward_decisions parse and persist
# ---------------------------------------------------------------------------

class TestValidDecisions:
    def test_single_reuse_decision_persisted(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        task = _minimal_task("notifications_contract")
        plan = _minimal_plan(
            build_tasks=[task],
            carry_forward_decisions=[
                _decision(
                    module_id="notifications",
                    decision="reuse",
                    reason="Notification delivery is concept-generic.",
                    source="carry_forward_candidate",
                    affected_build_tasks=["notifications_contract"],
                )
            ],
        )
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        decisions = ctx.data["app_build_plan"]["carry_forward_decisions"]
        assert len(decisions) == 1
        assert decisions[0]["module_id"] == "notifications"
        assert decisions[0]["decision"] == "reuse"

    def test_all_decision_values_accepted(self) -> None:
        mod = _load_app_build_plan_module()
        for decision_value in ("reuse", "adapt", "regenerate", "drop"):
            ctx = _Context()
            plan = _minimal_plan(
                carry_forward_decisions=[
                    _decision(decision=decision_value)
                ]
            )
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
            persisted = ctx.data["app_build_plan"]["carry_forward_decisions"][0]["decision"]
            assert persisted == decision_value

    def test_all_source_values_accepted(self) -> None:
        mod = _load_app_build_plan_module()
        for source in ("carry_forward_candidate", "human_override", "planner"):
            ctx = _Context()
            plan = _minimal_plan(
                carry_forward_decisions=[_decision(source=source)]
            )
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
            persisted = ctx.data["app_build_plan"]["carry_forward_decisions"][0]["source"]
            assert persisted == source

    def test_multiple_decisions_all_persisted(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        task1 = _minimal_task("notifications_contract")
        plan = _minimal_plan(
            build_tasks=[task1],
            carry_forward_decisions=[
                _decision("notifications", "reuse", "Generic.", "carry_forward_candidate",
                          ["notifications_contract"]),
                _decision("projects", "drop", "Domain-specific to old concept.", "planner"),
                _decision("billing_portal", "adapt", "Billing still applies, schema updated.", "planner"),
            ],
        )
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        decisions = ctx.data["app_build_plan"]["carry_forward_decisions"]
        assert len(decisions) == 3

    def test_empty_carry_forward_decisions_persisted(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(carry_forward_decisions=[])
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_build_plan"]["carry_forward_decisions"] == []

    def test_absent_carry_forward_decisions_defaults_to_empty(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan()
        # No carry_forward_decisions key at all
        plan.pop("carry_forward_decisions", None)
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_build_plan"]["carry_forward_decisions"] == []

    def test_affected_build_tasks_empty_list_ok(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(
            carry_forward_decisions=[_decision(decision="drop", affected_build_tasks=[])]
        )
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_build_plan"]["carry_forward_decisions"][0]["affected_build_tasks"] == []


# ---------------------------------------------------------------------------
# 3. Invalid decision enum fails
# ---------------------------------------------------------------------------

class TestInvalidDecision:
    def test_invalid_decision_value_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(
            carry_forward_decisions=[_decision(decision="keep")]
        )
        with pytest.raises(ValueError, match="decision must be one of"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_empty_decision_value_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(
            carry_forward_decisions=[_decision(decision="")]
        )
        with pytest.raises(ValueError, match="decision must be one of"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_invalid_source_value_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(
            carry_forward_decisions=[_decision(source="llm_output")]
        )
        with pytest.raises(ValueError, match="source must be one of"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_non_dict_entry_silently_dropped(self) -> None:
        """Non-dict entries in carry_forward_decisions are silently dropped
        by _normalize_object_list (consistent with all other list fields)."""
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(
            carry_forward_decisions=["not_a_dict"]
        )
        # Should not raise — the string is dropped, resulting in []
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_build_plan"]["carry_forward_decisions"] == []


# ---------------------------------------------------------------------------
# 4. Missing reason fails
# ---------------------------------------------------------------------------

class TestMissingReason:
    def test_empty_reason_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan(
            carry_forward_decisions=[_decision(reason="")]
        )
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_null_reason_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        d = _decision()
        d["reason"] = None
        plan = _minimal_plan(carry_forward_decisions=[d])
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_missing_reason_key_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        d = _decision()
        del d["reason"]
        plan = _minimal_plan(carry_forward_decisions=[d])
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_missing_module_id_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        d = _decision()
        d["module_id"] = ""
        plan = _minimal_plan(carry_forward_decisions=[d])
        with pytest.raises(ValueError, match="module_id must be a non-empty string"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)


# ---------------------------------------------------------------------------
# 5. affected_build_tasks must reference existing task ids when provided
# ---------------------------------------------------------------------------

class TestAffectedBuildTasksValidation:
    def test_existing_task_id_accepted(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        task = _minimal_task("notifications_task")
        plan = _minimal_plan(
            build_tasks=[task],
            carry_forward_decisions=[
                _decision(affected_build_tasks=["notifications_task"])
            ],
        )
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_plan_ready"] is True

    def test_unknown_task_id_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        task = _minimal_task("notifications_task")
        plan = _minimal_plan(
            build_tasks=[task],
            carry_forward_decisions=[
                _decision(affected_build_tasks=["nonexistent_task"])
            ],
        )
        with pytest.raises(ValueError, match="unknown task ids"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_mixed_valid_invalid_task_ids_raises(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        task = _minimal_task("notifications_task")
        plan = _minimal_plan(
            build_tasks=[task],
            carry_forward_decisions=[
                _decision(affected_build_tasks=["notifications_task", "ghost_task"])
            ],
        )
        with pytest.raises(ValueError, match="unknown task ids"):
            mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)

    def test_absent_affected_build_tasks_ok(self) -> None:
        """Omitting affected_build_tasks entirely is valid (optional field)."""
        mod = _load_app_build_plan_module()
        ctx = _Context()
        d = _decision()
        d.pop("affected_build_tasks", None)
        plan = _minimal_plan(carry_forward_decisions=[d])
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_plan_ready"] is True


# ---------------------------------------------------------------------------
# 6. Non-conceptual build with empty/absent carry_forward_decisions passes
# ---------------------------------------------------------------------------

class TestNonConceptualBuild:
    def test_no_carry_forward_decisions_passes(self) -> None:
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan()
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert ctx.data["app_plan_ready"] is True
        assert ctx.data["app_build_plan"]["carry_forward_decisions"] == []

    def test_carry_forward_decisions_not_required(self) -> None:
        """Plans that previously had no carry_forward_decisions key still pass."""
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = {
            "agent_message": "Fresh greenfield build.",
            "app_kind": "analytics_dashboard",
            "pages": [{"name": "Dashboard", "route": "/", "purpose": "Stats"}],
            "entities": [],
            "roles": [],
            "capability_packs": [],
            "external_integrations": [],
            "agent_backend_required": False,
            "build_tasks": [],
            "generation_order": [],
        }
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        # carry_forward_decisions defaults to [] without error
        assert ctx.data["app_build_plan"]["carry_forward_decisions"] == []


# ---------------------------------------------------------------------------
# 7-9. AppPlanAgent guidance tests
# ---------------------------------------------------------------------------

class TestAppPlanAgentGuidance:
    def _load_agents_yaml(self) -> str:
        path = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
        return path.read_text(encoding="utf-8")

    def test_guidance_mentions_reuse_adapt_regenerate_drop(self) -> None:
        content = self._load_agents_yaml()
        for term in ("reuse", "adapt", "regenerate", "drop"):
            assert term in content, f"agents.yaml missing guidance term: {term!r}"

    def test_guidance_states_reuse_does_not_copy_files(self) -> None:
        content = self._load_agents_yaml()
        # Guidance must say reuse does not copy old files
        assert "copy" in content.lower()
        # And must say "does not copy" or "no file copy"
        assert (
            "does not copy" in content.lower()
            or "no file copy" in content.lower()
            or "not copy" in content.lower()
        )

    def test_guidance_states_assemblyadgent_merge_not_implemented(self) -> None:
        content = self._load_agents_yaml()
        assert "AssemblyAgent" in content
        # AssemblyAgent merge behavior must be stated as not implemented
        assert (
            "not implemented" in content.lower()
            or "no merge" in content.lower()
            or "merge behavior is not" in content.lower()
        )

    def test_guidance_mentions_carry_forward_decisions(self) -> None:
        content = self._load_agents_yaml()
        assert "carry_forward_decisions" in content

    def test_guidance_mentions_affected_build_tasks(self) -> None:
        content = self._load_agents_yaml()
        assert "affected_build_tasks" in content


# ---------------------------------------------------------------------------
# 10. Existing AppBuildPlan tests smoke
# ---------------------------------------------------------------------------

class TestExistingBehaviorUnaffected:
    def test_basic_plan_without_new_field_still_passes(self) -> None:
        """Plans without carry_forward_decisions still work exactly as before."""
        mod = _load_app_build_plan_module()
        ctx = _Context()
        task = _minimal_task("projects_contract")
        plan = _minimal_plan(build_tasks=[task])
        result = mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert "App kind" in result
        assert ctx.data["app_plan_ready"] is True

    def test_normalized_plan_has_carry_forward_decisions_key(self) -> None:
        """Normalized plan always includes carry_forward_decisions (default [])."""
        mod = _load_app_build_plan_module()
        ctx = _Context()
        plan = _minimal_plan()
        mod.app_build_plan(AppBuildPlan=plan, context_variables=ctx)
        assert "carry_forward_decisions" in ctx.data["app_build_plan"]

    def test_validator_constants_correct(self) -> None:
        """Enum constants in app_build_plan.py match the structured output schema."""
        path = WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools" / "app_build_plan.py"
        source = path.read_text(encoding="utf-8")
        for value in ("reuse", "adapt", "regenerate", "drop"):
            assert f'"{value}"' in source or f"'{value}'" in source
        for value in ("carry_forward_candidate", "human_override", "planner"):
            assert f'"{value}"' in source or f"'{value}'" in source
