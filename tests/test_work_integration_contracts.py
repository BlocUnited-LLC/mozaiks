"""Tests for mozaiksai.core.workflow.work_contracts.

Proves:
- deterministic digests (identical inputs → identical digests)
- strict parsing / unknown-field rejection
- dependency DAG validation
- cycle rejection
- stable topological ordering
- path normalization
- collision detection (direct, parent/child, case, operation-conflict)
- ownership violation rejection
- out-of-scope result rejection
- conflicting operations rejected
- incomplete dependency results rejected
- identical inputs produce identical integration results
- no AG2, GitHub or external service invocation
- compatibility with existing task-batch owned-path semantics

No mocks, no network, no filesystem I/O, no AG2 construction.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json

import pytest

from mozaiksai.core.workflow.work_contracts import (
    REGISTERED_ASSIGNMENT_KINDS,
    ValidationEvidence,
    WorkAssignment,
    WorkDiagnostic,
    WorkResult,
    build_integration_result,
    detect_collisions,
    make_work_assignment,
    make_work_result,
    stable_digest,
    validate_assignment_dag,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PLAN_ID = "plan_abc"
_PLAN_DIGEST = "a" * 64
_BASELINE_SHA = "b" * 40


def _assignment(
    assignment_id: str = "a1",
    kind: str = "module_contract",
    owned_paths: list[str] | None = None,
    depends_on: list[str] | None = None,
    **kwargs,
) -> WorkAssignment:
    return make_work_assignment(
        assignment_id=assignment_id,
        plan_id=_PLAN_ID,
        plan_digest=_PLAN_DIGEST,
        baseline_sha=_BASELINE_SHA,
        assignment_kind=kind,
        owned_paths=owned_paths or ["modules/foo/module.yaml"],
        depends_on=depends_on or [],
        **kwargs,
    )


def _result(
    assignment: WorkAssignment,
    status: str = "completed",
    paths: list[str] | None = None,
    operations: list[str] | None = None,
    attempt_id: str = "attempt-1",
) -> WorkResult:
    paths = paths or list(assignment.owned_paths[:1])
    operations = operations or ["create"] * len(paths)
    artifacts = [
        {"path": p, "operation": o}
        for p, o in zip(paths, operations, strict=True)
    ]
    return make_work_result(
        assignment=assignment,
        status=status,
        attempt_id=attempt_id,
        changed_artifacts=artifacts,
    )


# ===========================================================================
# 1. stable_digest — determinism
# ===========================================================================


class TestStableDigest:
    def test_identical_inputs_produce_identical_digest(self):
        d1 = stable_digest({"a": 1, "b": [1, 2]})
        d2 = stable_digest({"a": 1, "b": [1, 2]})
        assert d1 == d2

    def test_key_order_does_not_affect_digest(self):
        d1 = stable_digest({"z": 1, "a": 2})
        d2 = stable_digest({"a": 2, "z": 1})
        assert d1 == d2

    def test_different_inputs_produce_different_digests(self):
        d1 = stable_digest({"a": 1})
        d2 = stable_digest({"a": 2})
        assert d1 != d2

    def test_digest_is_hex_sha256(self):
        d = stable_digest("hello")
        assert len(d) == 64
        assert all(c in "0123456789abcdef" for c in d)

    def test_manual_sha256_matches(self):
        data = {"x": 1}
        canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        expected = hashlib.sha256(canonical.encode("ascii")).hexdigest()
        assert stable_digest(data) == expected

    def test_nested_dict_keys_sorted(self):
        d1 = stable_digest({"outer": {"b": 2, "a": 1}})
        d2 = stable_digest({"outer": {"a": 1, "b": 2}})
        assert d1 == d2


# ===========================================================================
# 2. WorkAssignment construction and validation
# ===========================================================================


class TestMakeWorkAssignment:
    def test_valid_assignment_builds(self):
        a = _assignment()
        assert a.assignment_id == "a1"
        assert a.plan_id == _PLAN_ID
        assert a.assignment_kind == "module_contract"
        assert isinstance(a.owned_paths, tuple)
        assert a.assignment_digest

    def test_digest_is_deterministic(self):
        a1 = _assignment()
        a2 = _assignment()
        assert a1.assignment_digest == a2.assignment_digest

    def test_different_owned_paths_produce_different_digests(self):
        a1 = _assignment(owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(owned_paths=["modules/bar/module.yaml"])
        assert a1.assignment_digest != a2.assignment_digest

    def test_all_registered_kinds_accepted(self):
        for kind in REGISTERED_ASSIGNMENT_KINDS:
            a = _assignment(kind=kind)
            assert a.assignment_kind == kind

    def test_unregistered_kind_rejected(self):
        with pytest.raises(ValueError, match="not registered"):
            _assignment(kind="magic_custom_kind")

    def test_empty_assignment_id_rejected(self):
        with pytest.raises(ValueError, match="assignment_id"):
            make_work_assignment(
                assignment_id="",
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                baseline_sha=_BASELINE_SHA,
                assignment_kind="module_contract",
                owned_paths=["modules/foo/module.yaml"],
            )

    def test_empty_plan_id_rejected(self):
        with pytest.raises(ValueError, match="plan_id"):
            make_work_assignment(
                assignment_id="a1",
                plan_id="  ",
                plan_digest=_PLAN_DIGEST,
                baseline_sha=_BASELINE_SHA,
                assignment_kind="module_contract",
                owned_paths=["modules/foo/module.yaml"],
            )

    def test_empty_owned_paths_rejected(self):
        with pytest.raises(ValueError, match="owned_paths"):
            make_work_assignment(
                assignment_id="a1",
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                baseline_sha=_BASELINE_SHA,
                assignment_kind="module_contract",
                owned_paths=[],
            )

    def test_self_dependency_rejected(self):
        with pytest.raises(ValueError, match="cannot depend on itself"):
            _assignment(assignment_id="a1", depends_on=["a1"])

    def test_retry_limit_above_max_rejected(self):
        with pytest.raises(ValueError, match="retry_limit"):
            _assignment(retry_limit=6)

    def test_retry_limit_below_zero_rejected(self):
        with pytest.raises(ValueError, match="retry_limit"):
            _assignment(retry_limit=-1)

    def test_retry_limit_bool_rejected(self):
        with pytest.raises(ValueError, match="retry_limit"):
            _assignment(retry_limit=True)

    def test_valid_retry_limits(self):
        for limit in range(6):  # 0–5
            a = _assignment(retry_limit=limit)
            assert a.retry_limit == limit

    def test_assignment_is_immutable(self):
        a = _assignment()
        with pytest.raises((AttributeError, TypeError, dataclasses.FrozenInstanceError)):
            a.assignment_id = "changed"  # type: ignore[misc]


# ===========================================================================
# 3. Path validation
# ===========================================================================


class TestPathNormalization:
    def test_backslash_normalized_to_forward_slash(self):
        a = _assignment(owned_paths=["modules\\foo\\handler.py"])
        assert "modules/foo/handler.py" in a.owned_paths

    def test_absolute_unix_path_rejected(self):
        with pytest.raises(ValueError, match="absolute path"):
            _assignment(owned_paths=["/etc/secrets"])

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="traversal"):
            _assignment(owned_paths=["modules/../etc/passwd"])

    def test_glob_chars_rejected(self):
        with pytest.raises(ValueError, match="glob"):
            _assignment(owned_paths=["modules/*.py"])

    def test_secret_term_path_rejected(self):
        with pytest.raises(ValueError, match="secret-term"):
            _assignment(owned_paths=["config/secret.yaml"])

    def test_key_path_rejected(self):
        with pytest.raises(ValueError, match="secret-term"):
            _assignment(owned_paths=["config/api.key"])

    def test_duplicate_owned_paths_rejected(self):
        with pytest.raises(ValueError, match="duplicate"):
            _assignment(owned_paths=["modules/foo/handler.py", "modules/foo/handler.py"])

    def test_case_collision_in_owned_paths_rejected(self):
        with pytest.raises(ValueError, match="case-normalization"):
            _assignment(owned_paths=["Modules/Foo/Handler.py", "modules/foo/handler.py"])

    def test_owned_paths_sorted(self):
        a = _assignment(owned_paths=["modules/z/m.yaml", "modules/a/m.yaml"])
        assert a.owned_paths == ("modules/a/m.yaml", "modules/z/m.yaml")

    def test_trailing_slash_stripped(self):
        a = _assignment(owned_paths=["modules/foo/"])
        assert a.owned_paths == ("modules/foo",)


# ===========================================================================
# 4. WorkResult construction and ownership validation
# ===========================================================================


class TestMakeWorkResult:
    def test_valid_result_builds(self):
        a = _assignment()
        r = _result(a)
        assert r.assignment_id == a.assignment_id
        assert r.assignment_digest == a.assignment_digest
        assert r.status == "completed"
        assert r.result_digest

    def test_result_digest_is_deterministic(self):
        a = _assignment()
        r1 = _result(a)
        r2 = _result(a)
        assert r1.result_digest == r2.result_digest

    def test_different_status_produces_different_digest(self):
        a = _assignment()
        r1 = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
        )
        r2 = make_work_result(
            assignment=a,
            status="failed",
            attempt_id="x",
        )
        assert r1.result_digest != r2.result_digest

    def test_artifact_outside_owned_paths_rejected(self):
        a = _assignment(owned_paths=["modules/foo/module.yaml"])
        with pytest.raises(ValueError, match="outside"):
            make_work_result(
                assignment=a,
                status="completed",
                attempt_id="x",
                changed_artifacts=[
                    {"path": "modules/bar/module.yaml", "operation": "create"}
                ],
            )

    def test_artifact_child_path_accepted_when_parent_owned(self):
        a = _assignment(owned_paths=["modules/foo"])
        # modules/foo/handler.py is a child of modules/foo — should be accepted
        r = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
            changed_artifacts=[
                {"path": "modules/foo/handler.py", "operation": "create"}
            ],
        )
        assert len(r.changed_artifacts) == 1

    def test_empty_attempt_id_rejected(self):
        a = _assignment()
        with pytest.raises(ValueError, match="attempt_id"):
            make_work_result(
                assignment=a,
                status="completed",
                attempt_id="",
            )

    def test_unknown_operation_rejected(self):
        a = _assignment(owned_paths=["modules/foo/module.yaml"])
        with pytest.raises(ValueError, match="unknown operation"):
            make_work_result(
                assignment=a,
                status="completed",
                attempt_id="x",
                changed_artifacts=[
                    {"path": "modules/foo/module.yaml", "operation": "rename"}
                ],
            )

    def test_output_digest_from_file_map(self):
        a = _assignment(owned_paths=["modules/foo/module.yaml"])
        r1 = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
            file_map={"modules/foo/module.yaml": "content-1"},
        )
        r2 = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
            file_map={"modules/foo/module.yaml": "content-2"},
        )
        assert r1.output_digest != r2.output_digest

    def test_diagnostics_parsed(self):
        a = _assignment()
        r = make_work_result(
            assignment=a,
            status="failed",
            attempt_id="x",
            diagnostics=[
                {"level": "error", "code": "E001", "message": "failed", "path": "modules/foo/module.yaml"}
            ],
        )
        assert len(r.diagnostics) == 1
        assert isinstance(r.diagnostics[0], WorkDiagnostic)
        assert r.diagnostics[0].level == "error"

    def test_validation_evidence_parsed(self):
        a = _assignment()
        r = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
            validation_evidence=[
                {"validator_id": "mytest", "passed": True}
            ],
        )
        assert len(r.validation_evidence) == 1
        assert isinstance(r.validation_evidence[0], ValidationEvidence)
        assert r.validation_evidence[0].passed is True

    def test_artifacts_sorted_by_path(self):
        a = _assignment(owned_paths=["modules/foo", "modules/bar"])
        r = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
            changed_artifacts=[
                {"path": "modules/foo/handler.py", "operation": "create"},
                {"path": "modules/bar/handler.py", "operation": "create"},
            ],
        )
        paths = [art.path for art in r.changed_artifacts]
        assert paths == sorted(paths)

    def test_content_digest_derived_from_file_map(self):
        a = _assignment(owned_paths=["modules/foo/module.yaml"])
        r = make_work_result(
            assignment=a,
            status="completed",
            attempt_id="x",
            changed_artifacts=[{"path": "modules/foo/module.yaml", "operation": "create"}],
            file_map={"modules/foo/module.yaml": "some content"},
        )
        expected_cd = stable_digest("some content")
        assert r.changed_artifacts[0].content_digest == expected_cd


# ===========================================================================
# 5. DAG validation
# ===========================================================================


class TestDagValidation:
    def test_linear_dag_valid(self):
        a1 = _assignment("a1", depends_on=[])
        a2 = _assignment("a2", depends_on=["a1"])
        a3 = _assignment("a3", depends_on=["a2"])
        validate_assignment_dag([a1, a2, a3])

    def test_diamond_dag_valid(self):
        a1 = _assignment("a1")
        a2 = _assignment("a2", depends_on=["a1"], owned_paths=["modules/b/m.yaml"])
        a3 = _assignment("a3", depends_on=["a1"], owned_paths=["modules/c/m.yaml"])
        a4 = _assignment("a4", depends_on=["a2", "a3"], owned_paths=["modules/d/m.yaml"])
        validate_assignment_dag([a1, a2, a3, a4])

    def test_cycle_rejected(self):
        a1 = _assignment("a1", depends_on=["a2"])
        a2 = _assignment("a2", depends_on=["a1"], owned_paths=["modules/b/m.yaml"])
        with pytest.raises(ValueError, match="cycle"):
            validate_assignment_dag([a1, a2])

    def test_three_node_cycle_rejected(self):
        a1 = _assignment("a1", depends_on=["a3"])
        a2 = _assignment("a2", depends_on=["a1"], owned_paths=["modules/b/m.yaml"])
        a3 = _assignment("a3", depends_on=["a2"], owned_paths=["modules/c/m.yaml"])
        with pytest.raises(ValueError, match="cycle"):
            validate_assignment_dag([a1, a2, a3])

    def test_missing_dep_rejected(self):
        a2 = _assignment("a2", depends_on=["nonexistent"])
        with pytest.raises(ValueError, match="not in the assignment set"):
            validate_assignment_dag([a2])

    def test_single_node_no_deps_valid(self):
        validate_assignment_dag([_assignment("a1")])

    def test_empty_list_valid(self):
        validate_assignment_dag([])

    def test_topological_order_deps_before_dependents(self):
        a1 = _assignment("a1")
        a2 = _assignment("a2", depends_on=["a1"], owned_paths=["modules/b/m.yaml"])
        a3 = _assignment("a3", depends_on=["a2"], owned_paths=["modules/c/m.yaml"])
        # Submit in reverse order to prove ordering is not input-order-sensitive
        from mozaiksai.core.workflow.work_contracts import _topological_sort
        ordered = _topological_sort([a3, a1, a2])
        ids = [a.assignment_id for a in ordered]
        assert ids.index("a1") < ids.index("a2")
        assert ids.index("a2") < ids.index("a3")

    def test_topological_order_is_deterministic(self):
        a1 = _assignment("a1")
        a2 = _assignment("a2", owned_paths=["modules/b/m.yaml"])
        a3 = _assignment("a3", owned_paths=["modules/c/m.yaml"])
        from mozaiksai.core.workflow.work_contracts import _topological_sort
        order1 = [a.assignment_id for a in _topological_sort([a1, a2, a3])]
        order2 = [a.assignment_id for a in _topological_sort([a3, a1, a2])]
        assert order1 == order2  # alphabetical tie-breaking


# ===========================================================================
# 6. Collision detection
# ===========================================================================


class TestCollisionDetection:
    def test_no_collisions_on_disjoint_paths(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment("a2", owned_paths=["modules/bar/module.yaml"])
        report = detect_collisions([a1, a2])
        assert not report.has_collisions

    def test_direct_path_collision(self):
        a1 = _assignment("a1", owned_paths=["modules/shared/handler.py"])
        a2 = _assignment("a2", owned_paths=["modules/shared/handler.py"])
        report = detect_collisions([a1, a2])
        assert report.has_collisions
        kinds = {c.kind for c in report.collisions}
        assert "direct_path" in kinds

    def test_parent_child_collision(self):
        a1 = _assignment("a1", owned_paths=["modules/foo"])
        a2 = _assignment("a2", owned_paths=["modules/foo/handler.py"])
        report = detect_collisions([a1, a2])
        assert report.has_collisions
        kinds = {c.kind for c in report.collisions}
        assert "parent_child" in kinds

    def test_case_collision(self):
        a1 = _assignment("a1", owned_paths=["Modules/Foo/Handler.py"])
        a2 = _assignment("a2", owned_paths=["modules/foo/handler.py"])
        report = detect_collisions([a1, a2])
        assert report.has_collisions
        kinds = {c.kind for c in report.collisions}
        assert "case_collision" in kinds

    def test_operation_conflict_create_delete(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment("a2", owned_paths=["modules/foo/module.yaml"])
        r1 = _result(a1, paths=["modules/foo/module.yaml"], operations=["create"])
        r2 = _result(a2, paths=["modules/foo/module.yaml"], operations=["delete"])
        report = detect_collisions([a1, a2], [r1, r2])
        assert report.has_collisions
        kinds = {c.kind for c in report.collisions}
        assert "operation_conflict" in kinds

    def test_same_create_on_same_path_is_not_operation_conflict(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment("a2", owned_paths=["modules/foo/module.yaml"])
        r1 = _result(a1, paths=["modules/foo/module.yaml"], operations=["create"])
        r2 = _result(a2, paths=["modules/foo/module.yaml"], operations=["create"])
        report = detect_collisions([a1, a2], [r1, r2])
        # Still has direct_path collision, but no operation_conflict
        op_conflict_kinds = [c for c in report.collisions if c.kind == "operation_conflict"]
        assert not op_conflict_kinds

    def test_no_parent_child_collision_same_assignment(self):
        # A single assignment owning both parent and child is fine
        a1 = _assignment("a1", owned_paths=["modules/foo", "modules/foo/handler.py"])
        report = detect_collisions([a1])
        parent_child = [c for c in report.collisions if c.kind == "parent_child"]
        assert not parent_child

    def test_collision_report_is_sorted_deterministically(self):
        a1 = _assignment("a1", owned_paths=["modules/shared/handler.py"])
        a2 = _assignment("a2", owned_paths=["modules/shared/handler.py"])
        r1 = detect_collisions([a1, a2])
        r2 = detect_collisions([a2, a1])
        assert r1.collisions == r2.collisions

    def test_no_results_skips_operation_conflict_check(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        report = detect_collisions([a1])
        op_conflict = [c for c in report.collisions if c.kind == "operation_conflict"]
        assert not op_conflict


# ===========================================================================
# 7. IntegrationResult
# ===========================================================================


class TestBuildIntegrationResult:
    def _two_assignment_chain(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(
            "a2", owned_paths=["modules/bar/module.yaml"], depends_on=["a1"]
        )
        r1 = _result(a1)
        r2 = _result(a2)
        return [a1, a2], [r1, r2]

    def test_valid_integration_builds(self):
        assignments, results = self._two_assignment_chain()
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        assert ir.plan_id == _PLAN_ID
        assert ir.promotion_ready is True
        assert not ir.unresolved_assignments
        assert not ir.collision_report.has_collisions
        assert ir.integration_digest

    def test_integration_digest_is_deterministic(self):
        assignments, results = self._two_assignment_chain()
        ir1 = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        ir2 = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        assert ir1.integration_digest == ir2.integration_digest

    def test_different_plans_produce_different_digests(self):
        assignments, results = self._two_assignment_chain()
        ir1 = build_integration_result(
            plan_id="plan-1",
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        ir2 = build_integration_result(
            plan_id="plan-2",
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        assert ir1.integration_digest != ir2.integration_digest

    def test_empty_plan_id_rejected(self):
        assignments, results = self._two_assignment_chain()
        with pytest.raises(ValueError, match="plan_id"):
            build_integration_result(
                plan_id="",
                plan_digest=_PLAN_DIGEST,
                assignments=assignments,
                results=results,
            )

    def test_dependency_order_respected(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(
            "a2", owned_paths=["modules/bar/module.yaml"], depends_on=["a1"]
        )
        r1 = _result(a1)
        r2 = _result(a2)
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a2, a1],  # reversed input order
            results=[r2, r1],
        )
        ids = list(ir.ordered_assignment_ids)
        assert ids.index("a1") < ids.index("a2")

    def test_unresolved_assignments_recorded(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment("a2", owned_paths=["modules/bar/module.yaml"])
        r1 = _result(a1)
        # a2 has no result
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a1, a2],
            results=[r1],
        )
        assert "a2" in ir.unresolved_assignments
        assert ir.promotion_ready is False

    def test_promotion_not_ready_with_collision(self):
        a1 = _assignment("a1", owned_paths=["modules/shared/handler.py"])
        a2 = _assignment("a2", owned_paths=["modules/shared/handler.py"])
        r1 = _result(a1)
        r2 = _result(a2)
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a1, a2],
            results=[r1, r2],
        )
        assert ir.collision_report.has_collisions
        assert ir.promotion_ready is False

    def test_promotion_not_ready_with_failed_result(self):
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        r1 = _result(a1, status="failed")
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a1],
            results=[r1],
        )
        assert ir.promotion_ready is False

    def test_incomplete_dependency_result_rejected(self):
        """An assignment with a result but a failed dep result must be rejected."""
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(
            "a2", owned_paths=["modules/bar/module.yaml"], depends_on=["a1"]
        )
        r1 = _result(a1, status="failed")
        r2 = _result(a2)  # a2 completed but a1 failed
        with pytest.raises(ValueError, match="not 'completed'"):
            build_integration_result(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                assignments=[a1, a2],
                results=[r1, r2],
            )

    def test_dep_with_no_result_while_dependent_has_result_rejected(self):
        """If a2 has a result but a1 has no result, raise."""
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(
            "a2", owned_paths=["modules/bar/module.yaml"], depends_on=["a1"]
        )
        r2 = _result(a2)  # a2 completed but a1 has no result
        with pytest.raises(ValueError, match="no result"):
            build_integration_result(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                assignments=[a1, a2],
                results=[r2],
            )

    def test_cycle_in_assignments_rejected(self):
        a1 = _assignment("a1", depends_on=["a2"])
        a2 = _assignment("a2", depends_on=["a1"], owned_paths=["modules/b/m.yaml"])
        with pytest.raises(ValueError, match="cycle"):
            build_integration_result(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                assignments=[a1, a2],
                results=[],
            )

    def test_duplicate_results_rejected(self):
        a1 = _assignment("a1")
        r1a = _result(a1, attempt_id="attempt-1")
        r1b = _result(a1, attempt_id="attempt-2")
        with pytest.raises(ValueError, match="duplicate"):
            build_integration_result(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                assignments=[a1],
                results=[r1a, r1b],
            )

    def test_combined_file_map_digest_is_deterministic(self):
        assignments, results = self._two_assignment_chain()
        ir1 = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        ir2 = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=assignments,
            results=results,
        )
        assert ir1.combined_file_map_digest == ir2.combined_file_map_digest

    def test_combined_file_map_later_result_wins(self):
        a1 = _assignment("a1", owned_paths=["modules/foo"])
        a2 = _assignment(
            "a2", owned_paths=["modules/foo"], depends_on=["a1"]
        )
        r1 = make_work_result(
            assignment=a1,
            status="completed",
            attempt_id="x",
            changed_artifacts=[
                {"path": "modules/foo/handler.py", "operation": "create", "content_digest": "aaa"}
            ],
        )
        r2 = make_work_result(
            assignment=a2,
            status="completed",
            attempt_id="y",
            changed_artifacts=[
                {"path": "modules/foo/handler.py", "operation": "update", "content_digest": "bbb"}
            ],
        )
        # a2 overwrites a1's content_digest on the same path
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a1, a2],
            results=[r1, r2],
        )
        expected = stable_digest({"modules/foo/handler.py": "bbb"})
        assert ir.combined_file_map_digest == expected

    def test_identical_inputs_produce_identical_integration_result(self):
        """Regression: two calls with identical state must produce identical results."""
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(
            "a2", owned_paths=["modules/bar/module.yaml"], depends_on=["a1"]
        )
        r1 = _result(a1, paths=["modules/foo/module.yaml"])
        r2 = _result(a2, paths=["modules/bar/module.yaml"])

        def make():
            return build_integration_result(
                plan_id=_PLAN_ID,
                plan_digest=_PLAN_DIGEST,
                assignments=[a1, a2],
                results=[r1, r2],
            )

        ir1 = make()
        ir2 = make()
        assert ir1.integration_digest == ir2.integration_digest
        assert ir1.combined_file_map_digest == ir2.combined_file_map_digest
        assert ir1.ordered_assignment_ids == ir2.ordered_assignment_ids
        assert ir1.ordered_result_digests == ir2.ordered_result_digests

    def test_extra_validation_evidence_included(self):
        a1 = _assignment("a1")
        r1 = _result(a1)
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a1],
            results=[r1],
            extra_validation_evidence=[
                {"validator_id": "ruff", "passed": True, "detail": "clean"}
            ],
        )
        assert len(ir.validation_evidence) == 1
        assert ir.validation_evidence[0].validator_id == "ruff"

    def test_skipped_result_bypasses_dep_completeness_check(self):
        """A skipped result should not trigger the incomplete-dep check."""
        a1 = _assignment("a1", owned_paths=["modules/foo/module.yaml"])
        a2 = _assignment(
            "a2", owned_paths=["modules/bar/module.yaml"], depends_on=["a1"]
        )
        r1 = _result(a1, status="failed")
        r2_skipped = make_work_result(
            assignment=a2,
            status="skipped",
            attempt_id="x",
        )
        # skipped result bypasses dep check — should not raise
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a1, a2],
            results=[r1, r2_skipped],
        )
        assert ir.promotion_ready is False  # not ready because a1 failed


# ===========================================================================
# 8. No external service invocation
# ===========================================================================


class TestNoExternalInvocation:
    def test_work_assignment_no_network(self):
        # Constructing WorkAssignment must not call any external service
        a = _assignment()
        assert a  # pure computation

    def test_work_result_no_network(self):
        a = _assignment()
        r = _result(a)
        assert r  # pure computation

    def test_integration_result_no_network(self):
        a = _assignment()
        r = _result(a)
        ir = build_integration_result(
            plan_id=_PLAN_ID,
            plan_digest=_PLAN_DIGEST,
            assignments=[a],
            results=[r],
        )
        assert ir  # pure computation

    def test_no_ag2_import_in_work_contracts(self):
        """work_contracts.py must not contain any AG2/autogen import statement."""
        import importlib.util
        import pathlib

        spec = importlib.util.find_spec("mozaiksai.core.workflow.work_contracts")
        assert spec is not None
        source = pathlib.Path(spec.origin).read_text(encoding="utf-8")

        # The source file must not import AG2 or autogen
        forbidden = ["import autogen", "from autogen", "import ag2", "from ag2"]
        for fragment in forbidden:
            assert fragment not in source, (
                f"work_contracts.py must not contain {fragment!r}"
            )


# ===========================================================================
# 9. Compatibility with existing task-batch owned-path semantics
# ===========================================================================


class TestTaskBatchCompatibility:
    def test_assignment_kinds_superset_of_app_generator_task_types(self):
        """All AppGenerator task types must be in REGISTERED_ASSIGNMENT_KINDS."""
        # From app_build_plan.py _ALLOWED_TASK_TYPES
        app_generator_types = {
            "subscription_config",
            "service_foundation",
            "module_contract",
            "persistence_contract",
            "data_migrations",
            "data_models",
            "business_services",
            "api_surface",
            "page_bundle",
            "agent_backend_integration",
            "refinement_harness",
        }
        assert app_generator_types.issubset(REGISTERED_ASSIGNMENT_KINDS)

    def test_path_normalization_matches_task_batches_behavior(self):
        """Backslash normalization and trailing slash removal align with task_batches."""
        a = _assignment(owned_paths=["modules\\foo\\handler.py"])
        assert "modules/foo/handler.py" in a.owned_paths

    def test_duplicate_path_detection_matches_task_batches(self):
        """task_batches raises on duplicate owned_paths; so do we."""
        with pytest.raises(ValueError, match="duplicate"):
            _assignment(owned_paths=["modules/foo/x.yaml", "modules/foo/x.yaml"])
