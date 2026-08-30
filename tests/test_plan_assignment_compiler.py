"""Tests for the deterministic approved-plan-to-WorkAssignment compiler."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.assignment_kinds import REGISTERED_ASSIGNMENT_KINDS
from mozaiksai.core.workflow.plan_assignment_compiler import (
    ApprovedAssignmentSpec,
    ApprovedPlan,
    CompiledAssignmentSet,
    compile_approved_plan,
)
from mozaiksai.core.workflow.work_contracts import WorkAssignment

_PLAN_ID = "plan-alpha"
_PLAN_DIGEST = "a" * 64
_BASELINE_SHA = "b" * 40


def _spec(
    logical_id: str = "module",
    *,
    kind: str = "module_contract",
    owned_paths: list[str] | None = None,
    depends_on: list[str] | None = None,
    **kwargs: object,
) -> ApprovedAssignmentSpec:
    return ApprovedAssignmentSpec(
        logical_id=logical_id,
        assignment_kind=kind,
        owned_paths=owned_paths or [f"app/modules/{logical_id}/module.yaml"],
        depends_on=depends_on or [],
        **kwargs,
    )


def _plan(
    *specs: ApprovedAssignmentSpec,
    plan_id: str = _PLAN_ID,
    plan_digest: str = _PLAN_DIGEST,
    baseline_sha: str = _BASELINE_SHA,
) -> ApprovedPlan:
    return ApprovedPlan(
        plan_id=plan_id,
        plan_digest=plan_digest,
        baseline_sha=baseline_sha,
        assignments=list(specs),
    )


def _compile(*specs: ApprovedAssignmentSpec, **plan_kwargs: str) -> CompiledAssignmentSet:
    return compile_approved_plan(_plan(*specs, **plan_kwargs))


def _only_assignment(result: CompiledAssignmentSet) -> WorkAssignment:
    assert result.assignment_count == 1
    return result.ordered_assignments[0]


class TestDerivedAssignmentIdentity:
    def test_assignment_id_is_derived_not_caller_supplied(self) -> None:
        result = _compile(_spec("human-readable-ref"))

        assignment = _only_assignment(result)

        assert assignment.assignment_id.startswith("wa_")
        assert assignment.assignment_id != "human-readable-ref"

    def test_repeat_compile_produces_same_ids_digests_and_set_digest(self) -> None:
        first = _compile(
            _spec("base", kind="service_foundation", owned_paths=["app/services/config.py"]),
            _spec("orders", owned_paths=["app/modules/orders/module.yaml"], depends_on=["base"]),
        )
        second = _compile(
            _spec("base", kind="service_foundation", owned_paths=["app/services/config.py"]),
            _spec("orders", owned_paths=["app/modules/orders/module.yaml"], depends_on=["base"]),
        )

        assert first.assignment_ids_in_order == second.assignment_ids_in_order
        assert [a.assignment_digest for a in first.ordered_assignments] == [
            a.assignment_digest for a in second.ordered_assignments
        ]
        assert first.assignment_set_digest == second.assignment_set_digest

    def test_input_order_does_not_change_order_ids_digests_or_set_digest(self) -> None:
        base = _spec("base", kind="service_foundation", owned_paths=["app/services/config.py"])
        orders = _spec("orders", owned_paths=["app/modules/orders/module.yaml"], depends_on=["base"])
        billing = _spec("billing", owned_paths=["app/modules/billing/module.yaml"], depends_on=["base"])

        forward = compile_approved_plan(_plan(base, orders, billing))
        reversed_input = compile_approved_plan(_plan(billing, orders, base))

        assert forward.assignment_ids_in_order == reversed_input.assignment_ids_in_order
        assert [a.assignment_digest for a in forward.ordered_assignments] == [
            a.assignment_digest for a in reversed_input.ordered_assignments
        ]
        assert forward.assignment_set_digest == reversed_input.assignment_set_digest

    def test_logical_id_rename_does_not_change_identity_when_semantics_match(self) -> None:
        first = _compile(_spec("orders", owned_paths=["app/modules/orders/module.yaml"]))
        second = _compile(_spec("renamed-ref", owned_paths=["app/modules/orders/module.yaml"]))

        assert _only_assignment(first).assignment_id == _only_assignment(second).assignment_id
        assert _only_assignment(first).assignment_digest == _only_assignment(second).assignment_digest
        assert first.assignment_set_digest == second.assignment_set_digest

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            (_spec("a", kind="module_contract"), _spec("a", kind="page_bundle")),
            (
                _spec("a", owned_paths=["app/modules/orders/module.yaml"]),
                _spec("a", owned_paths=["app/modules/payments/module.yaml"]),
            ),
            (
                _spec("a", allowed_agent_ids=["module-agent"]),
                _spec("a", allowed_agent_ids=["other-agent"]),
            ),
            (
                _spec("a", dependency_context_refs=["ctx.a"]),
                _spec("a", dependency_context_refs=["ctx.b"]),
            ),
            (
                _spec("a", required_structured_output_id="module.contract"),
                _spec("a", required_structured_output_id="module.other"),
            ),
            (
                _spec("a", required_validators=["module-contract"]),
                _spec("a", required_validators=["other-validator"]),
            ),
            (_spec("a", assignment_retry_limit=0), _spec("a", assignment_retry_limit=1)),
        ],
    )
    def test_execution_relevant_semantics_change_identity(
        self,
        left: ApprovedAssignmentSpec,
        right: ApprovedAssignmentSpec,
    ) -> None:
        first = _compile(left)
        second = _compile(right)

        assert _only_assignment(first).assignment_id != _only_assignment(second).assignment_id
        assert _only_assignment(first).assignment_digest != _only_assignment(second).assignment_digest
        assert first.assignment_set_digest != second.assignment_set_digest

    def test_plan_id_plan_digest_and_baseline_affect_identity(self) -> None:
        spec = _spec("a")

        base = _compile(spec)
        different_plan_id = _compile(spec, plan_id="other-plan")
        different_plan_digest = _compile(spec, plan_digest="c" * 64)
        different_baseline = _compile(spec, baseline_sha="d" * 40)

        assert _only_assignment(base).assignment_id != _only_assignment(different_plan_id).assignment_id
        assert _only_assignment(base).assignment_id != _only_assignment(different_plan_digest).assignment_id
        assert _only_assignment(base).assignment_id != _only_assignment(different_baseline).assignment_id

    def test_dependencies_affect_dependent_identity_and_resolve_to_derived_ids(self) -> None:
        base = _spec("base", kind="service_foundation", owned_paths=["app/services/config.py"])
        orders = _spec("orders", owned_paths=["app/modules/orders/module.yaml"], depends_on=["base"])
        independent_orders = _spec("orders", owned_paths=["app/modules/orders/module.yaml"])

        with_dep = _compile(base, orders)
        without_dep = _compile(independent_orders)
        base_assignment = with_dep.ordered_assignments[0]
        dependent_assignment = with_dep.ordered_assignments[1]

        assert dependent_assignment.depends_on == (base_assignment.assignment_id,)
        assert "base" not in dependent_assignment.depends_on
        assert dependent_assignment.assignment_id != _only_assignment(without_dep).assignment_id


class TestDependencyValidation:
    def test_dependencies_are_dependency_first(self) -> None:
        service = _spec("service", kind="service_foundation", owned_paths=["app/services/config.py"])
        api = _spec("api", kind="api_surface", owned_paths=["app/routes/api.py"], depends_on=["service"])
        page = _spec(
            "page",
            kind="page_bundle",
            owned_paths=["app/ui/pages/home.yaml"],
            depends_on=["api"],
        )

        result = _compile(page, api, service)

        service_id = result.ordered_assignments[0].assignment_id
        api_id = result.ordered_assignments[1].assignment_id
        page_id = result.ordered_assignments[2].assignment_id
        assert result.assignment_ids_in_order == (service_id, api_id, page_id)
        assert result.ordered_assignments[1].depends_on == (service_id,)
        assert result.ordered_assignments[2].depends_on == (api_id,)

    def test_missing_dependency_rejected_before_assignment_construction(self) -> None:
        with pytest.raises(ValidationError, match="undeclared logical IDs"):
            _plan(_spec("orders", depends_on=["outside-plan"]))

    def test_cycle_rejected(self) -> None:
        first = _spec("first", owned_paths=["app/modules/first/module.yaml"], depends_on=["second"])
        second = _spec("second", owned_paths=["app/modules/second/module.yaml"], depends_on=["first"])

        with pytest.raises(ValueError, match="cycle"):
            compile_approved_plan(_plan(first, second))

    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot depend on itself|cycle"):
            compile_approved_plan(_plan(_spec("self", depends_on=["self"])))

    def test_dependency_order_never_authorizes_overwrite(self) -> None:
        owner = _spec("owner", owned_paths=["app/modules/shared/module.yaml"])
        overwriter = _spec(
            "overwriter",
            owned_paths=["app/modules/shared/module.yaml"],
            depends_on=["owner"],
        )

        with pytest.raises(ValueError, match="collision"):
            compile_approved_plan(_plan(owner, overwriter))


class TestPathAndCollisionRejection:
    def test_direct_collision_rejected(self) -> None:
        left = _spec("left", owned_paths=["app/modules/orders/module.yaml"])
        right = _spec("right", owned_paths=["app/modules/orders/module.yaml"])

        with pytest.raises(ValueError, match="collision"):
            compile_approved_plan(_plan(left, right))

    def test_parent_child_collision_rejected(self) -> None:
        parent = _spec("parent", owned_paths=["app/modules/orders"])
        child = _spec("child", owned_paths=["app/modules/orders/contracts/events.yaml"])

        with pytest.raises(ValueError, match="collision"):
            compile_approved_plan(_plan(parent, child))

    def test_case_collision_rejected(self) -> None:
        upper = _spec("upper", owned_paths=["app/modules/Orders/module.yaml"])
        lower = _spec("lower", owned_paths=["app/modules/orders/module.yaml"])

        with pytest.raises(ValueError, match="collision|case"):
            compile_approved_plan(_plan(upper, lower))

    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "C:/Repos/BlocUnitedRepo/mozaiks/app/modules/a/module.yaml",
            "app/modules/../secrets.yaml",
            "app/modules/**/*.yaml",
            "app/security/secrets.yaml",
            "app/modules/orders/private_key.pem",
        ],
    )
    def test_unsafe_or_ambiguous_owned_paths_rejected(self, path: str) -> None:
        with pytest.raises((ValueError, ValidationError)):
            compile_approved_plan(_plan(_spec("bad", owned_paths=[path])))

    def test_duplicate_owned_paths_inside_one_spec_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate"):
            compile_approved_plan(
                _plan(
                    _spec(
                        "bad",
                        owned_paths=[
                            "app/modules/orders/module.yaml",
                            "app/modules/orders/module.yaml",
                        ],
                    )
                )
            )

    def test_operation_fields_are_not_part_of_the_compiler_contract(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ApprovedAssignmentSpec(
                logical_id="ops",
                assignment_kind="module_contract",
                owned_paths=["app/modules/orders/module.yaml"],
                operation="update",  # type: ignore[call-arg]
            )


class TestRetrySeparation:
    def test_assignment_retry_limit_is_preserved(self) -> None:
        result = _compile(_spec("module", assignment_retry_limit=3))

        assert _only_assignment(result).assignment_retry_limit == 3

    @pytest.mark.parametrize("retry_limit", [-1, 6, True])
    def test_assignment_retry_limit_bounds(self, retry_limit: object) -> None:
        with pytest.raises(ValidationError):
            _spec("module", assignment_retry_limit=retry_limit)  # type: ignore[arg-type]

    @pytest.mark.parametrize("field_name", ["retry_policy_ref", "queue_delivery_attempts", "ag2_retries"])
    def test_non_assignment_retry_fields_are_rejected(self, field_name: str) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ApprovedAssignmentSpec(
                logical_id="module",
                assignment_kind="module_contract",
                owned_paths=["app/modules/orders/module.yaml"],
                **{field_name: "opaque"},
            )


class TestContractStrictness:
    def test_unknown_assignment_kind_rejected(self) -> None:
        with pytest.raises(ValidationError, match="not a registered"):
            _spec("module", kind="arbitrary_executor_kind")

    def test_all_registered_assignment_kinds_are_accepted(self) -> None:
        for index, kind in enumerate(sorted(REGISTERED_ASSIGNMENT_KINDS, key=lambda item: item.value)):
            spec = _spec(
                f"item-{index}",
                kind=kind.value,
                owned_paths=[f"app/generated/item-{index}.txt"],
            )
            assert spec.assignment_kind == kind.value

    def test_duplicate_logical_ids_rejected(self) -> None:
        first = _spec("same", owned_paths=["app/modules/a/module.yaml"])
        second = _spec("same", owned_paths=["app/modules/b/module.yaml"])

        with pytest.raises(ValidationError, match="duplicate logical_id"):
            _plan(first, second)

    @pytest.mark.parametrize("field", ["assignment_id", "description", "rationale", "timestamp"])
    def test_caller_identity_and_presentation_fields_rejected(self, field: str) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ApprovedAssignmentSpec(
                logical_id="module",
                assignment_kind="module_contract",
                owned_paths=["app/modules/orders/module.yaml"],
                **{field: "ignored"},
            )

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_logical_id_rejected(self, value: str) -> None:
        with pytest.raises(ValidationError):
            _spec(value)

    @pytest.mark.parametrize("field", ["plan_id", "plan_digest", "baseline_sha"])
    def test_empty_plan_identity_fields_rejected(self, field: str) -> None:
        values = {
            "plan_id": _PLAN_ID,
            "plan_digest": _PLAN_DIGEST,
            "baseline_sha": _BASELINE_SHA,
            field: "   ",
        }

        with pytest.raises(ValidationError):
            ApprovedPlan(assignments=[_spec("module")], **values)

    def test_empty_plan_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ApprovedPlan(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                baseline_sha=_BASELINE_SHA,
                assignments=[],
            )

    def test_unknown_plan_fields_rejected(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ApprovedPlan(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                baseline_sha=_BASELINE_SHA,
                assignments=[_spec("module")],
                tenant_approval="outside-oss",  # type: ignore[call-arg]
            )


class TestCompiledOutput:
    def test_output_is_immutable(self) -> None:
        result = _compile(_spec("module"))

        with pytest.raises((TypeError, AttributeError, ValidationError)):
            result.plan_id = "mutated"  # type: ignore[misc]

    def test_properties_return_assignments_by_derived_id(self) -> None:
        result = _compile(_spec("module"))
        assignment = _only_assignment(result)

        assert result.assignment_ids_in_order == (assignment.assignment_id,)
        assert result.assignment_by_id == {assignment.assignment_id: assignment}

    def test_serialization_round_trip_revalidates_digests(self) -> None:
        result = _compile(
            _spec("service", kind="service_foundation", owned_paths=["app/services/config.py"]),
            _spec("module", owned_paths=["app/modules/orders/module.yaml"], depends_on=["service"]),
        )

        restored = CompiledAssignmentSet.model_validate(result.model_dump(mode="json"))

        assert restored.assignment_set_digest == result.assignment_set_digest
        assert restored.assignment_ids_in_order == result.assignment_ids_in_order
        assert [a.assignment_digest for a in restored.ordered_assignments] == [
            a.assignment_digest for a in result.ordered_assignments
        ]

    def test_tampered_assignment_set_digest_rejected(self) -> None:
        dumped = _compile(_spec("module")).model_dump(mode="json")
        dumped["assignment_set_digest"] = "0" * 64

        with pytest.raises(ValidationError, match="assignment_set_digest"):
            CompiledAssignmentSet.model_validate(dumped)

    def test_tampered_assignment_digest_rejected(self) -> None:
        dumped = _compile(_spec("module")).model_dump(mode="json")
        dumped["ordered_assignments"][0]["assignment_digest"] = "0" * 64

        with pytest.raises(ValidationError, match="assignment_digest"):
            CompiledAssignmentSet.model_validate(dumped)

    def test_plan_identity_preserved_in_every_assignment(self) -> None:
        result = _compile(
            _spec("module"),
            plan_id="plan-X",
            plan_digest="d" * 64,
            baseline_sha="e" * 40,
        )

        assignment = _only_assignment(result)
        assert assignment.plan_id == "plan-X"
        assert assignment.plan_digest == "d" * 64
        assert assignment.baseline_sha == "e" * 40


class TestCompilerPurity:
    def test_compiler_does_not_mutate_input_plan(self) -> None:
        plan = _plan(_spec("module"))
        before = plan.model_dump(mode="json")

        compile_approved_plan(plan)

        assert plan.model_dump(mode="json") == before

    def test_compiler_imports_no_runtime_side_effect_systems(self) -> None:
        source = Path("mozaiksai/core/workflow/plan_assignment_compiler.py").read_text()
        import_lines = [
            line.lower()
            for line in source.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        import_block = "\n".join(import_lines)

        legacy_ag2_import_name = "auto" + "gen"
        assert legacy_ag2_import_name not in import_block
        assert "openai" not in import_block
        assert "requests" not in import_block
        assert "httpx" not in import_block
        assert "github" not in import_block
        assert "workflow_queue" not in import_block
        assert "queue" not in import_block
        assert "database" not in import_block
        assert "importlib" not in import_block
        assert "subprocess" not in source
        assert "exec(" not in source
        assert "eval(" not in source
        assert "__import__" not in source

    def test_multiple_compilations_are_distinct_objects_with_same_identity(self) -> None:
        plan = _plan(_spec("module"))

        first = compile_approved_plan(plan)
        second = compile_approved_plan(plan)

        assert first is not second
        assert first.assignment_set_digest == second.assignment_set_digest


class TestRepresentativePlan:
    def test_representative_plan_compiles_to_dependency_order(self) -> None:
        specs = [
            _spec("svc", kind="service_foundation", owned_paths=["app/services/__init__.py"]),
            _spec("sub", kind="subscription_config", owned_paths=["app/config/subscriptions.yaml"]),
            _spec("mod", owned_paths=["app/modules/orders/module.yaml"], depends_on=["svc"]),
            _spec(
                "pers",
                kind="persistence_contract",
                owned_paths=["app/data/contract.json"],
                depends_on=["mod"],
            ),
            _spec(
                "mig",
                kind="data_migrations",
                owned_paths=["app/data/migrations/001.json"],
                depends_on=["pers"],
            ),
            _spec(
                "mdl",
                kind="data_models",
                owned_paths=["app/modules/orders/backend/schemas.py"],
                depends_on=["mod"],
            ),
            _spec(
                "biz",
                kind="business_services",
                owned_paths=["app/modules/orders/backend/service.py"],
                depends_on=["mdl"],
            ),
            _spec(
                "api",
                kind="api_surface",
                owned_paths=["app/modules/orders/backend/handler.py"],
                depends_on=["biz"],
            ),
            _spec("pg", kind="page_bundle", owned_paths=["app/ui/pages/orders.yaml"], depends_on=["api"]),
            _spec(
                "abk",
                kind="agent_backend_integration",
                owned_paths=["app/workflows/orders/tools/handler.py"],
                depends_on=["api"],
            ),
            _spec("ref", kind="refinement_harness", owned_paths=["tests/test_orders_harness.py"], depends_on=["mod"]),
            _spec("intg", kind="integration", owned_paths=["tests/test_orders_integration.py"], depends_on=["api"]),
            _spec("val", kind="validation", owned_paths=["tests/test_orders_e2e.py"], depends_on=["intg"]),
        ]

        result = compile_approved_plan(_plan(*specs))
        by_path = {assignment.owned_paths[0]: assignment for assignment in result.ordered_assignments}

        assert result.assignment_count == 13
        assert by_path["app/services/__init__.py"].assignment_id in by_path[
            "app/modules/orders/module.yaml"
        ].depends_on
        assert by_path["app/modules/orders/module.yaml"].assignment_id in by_path[
            "app/data/contract.json"
        ].depends_on
        assert by_path["app/modules/orders/backend/service.py"].assignment_id in by_path[
            "app/modules/orders/backend/handler.py"
        ].depends_on
        assert by_path["tests/test_orders_integration.py"].assignment_id in by_path[
            "tests/test_orders_e2e.py"
        ].depends_on
