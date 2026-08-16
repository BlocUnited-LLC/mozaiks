"""Strict typed contracts for deterministic workflow work integration."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .assignment_kinds import AssignmentKind, registered_assignment_kind_values
from .dependency_graph import deterministic_topological_order
from .path_ownership import (
    PathCollisionKind,
    detect_owned_path_collisions,
    normalize_owned_path,
    normalize_owned_paths,
    path_is_within_owned,
)

REGISTERED_ASSIGNMENT_KINDS: frozenset[str] = registered_assignment_kind_values()
ASSIGNMENT_RETRY_LIMIT_RANGE = (0, 5)
QUEUE_DELIVERY_MAX_ATTEMPTS_RANGE = (1, 25)
AG2_TRANSIENT_RETRY_FIELD = "retry_count"

WorkResultStatus = Literal["completed", "failed", "skipped"]
OperationKind = Literal["create", "update", "delete"]
DiagnosticLevel = Literal["error", "warning", "info"]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def stable_digest(data: Any) -> str:
    canonical = json.dumps(_jsonable(data), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class WorkAssignment(_FrozenModel):
    assignment_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    baseline_sha: str = Field(min_length=1)
    assignment_kind: AssignmentKind
    owned_paths: tuple[str, ...] = Field(min_length=1)
    dependency_context_refs: tuple[str, ...] = Field(default_factory=tuple)
    allowed_agent_ids: tuple[str, ...] = Field(default_factory=tuple)
    required_structured_output_id: str | None = None
    depends_on: tuple[str, ...] = Field(default_factory=tuple)
    required_validators: tuple[str, ...] = Field(default_factory=tuple)
    assignment_retry_limit: int = Field(default=0, ge=0, le=5)
    retry_policy_ref: str | None = None
    assignment_digest: str = Field(min_length=1)

    @field_validator("assignment_id", "plan_id", "plan_digest", "baseline_sha")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("required identifier must be non-empty")
        return text

    @field_validator("owned_paths", mode="before")
    @classmethod
    def _normalize_owned_paths(cls, value: Any) -> tuple[str, ...]:
        return tuple(sorted(normalize_owned_paths(value, reject_duplicates=True)))

    @field_validator("dependency_context_refs", "allowed_agent_ids", "depends_on", "required_validators", mode="before")
    @classmethod
    def _normalize_tuple_text(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list | tuple):
            raise ValueError("value must be a list")
        return tuple(sorted({str(item or "").strip() for item in value if str(item or "").strip()}))

    @model_validator(mode="after")
    def _validate_assignment(self) -> WorkAssignment:
        if self.assignment_id in self.depends_on:
            raise ValueError(f"assignment {self.assignment_id!r} cannot depend on itself")
        expected = _assignment_digest(self)
        if self.assignment_digest != expected:
            raise ValueError("assignment_digest does not match assignment fields")
        return self

class ArtifactIdentity(_FrozenModel):
    path: str = Field(min_length=1)
    operation: OperationKind
    content_digest: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def _normalize_path(cls, value: str) -> str:
        return normalize_owned_path(value)


class WorkDiagnostic(_FrozenModel):
    level: DiagnosticLevel
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    path: str | None = None

    @field_validator("path")
    @classmethod
    def _normalize_optional_path(cls, value: str | None) -> str | None:
        return normalize_owned_path(value) if value else None


class ValidationEvidence(_FrozenModel):
    validator_id: str = Field(min_length=1)
    passed: bool
    evidence_digest: str | None = None
    detail: str | None = None

    @field_validator("validator_id")
    @classmethod
    def _strip_validator_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("validator_id must be non-empty")
        return text


class WorkResult(_FrozenModel):
    assignment_id: str = Field(min_length=1)
    assignment_digest: str = Field(min_length=1)
    baseline_sha: str = Field(min_length=1)
    status: WorkResultStatus
    changed_artifacts: tuple[ArtifactIdentity, ...] = Field(default_factory=tuple)
    diagnostics: tuple[WorkDiagnostic, ...] = Field(default_factory=tuple)
    validation_evidence: tuple[ValidationEvidence, ...] = Field(default_factory=tuple)
    output_digest: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    result_digest: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_result_digest(self) -> WorkResult:
        if self.result_digest != _result_digest(self):
            raise ValueError("result_digest does not match result fields")
        return self


OperationConflictKind = Literal[
    "overlapping_writer",
    "parent_child_writer",
    "case_collision",
    "operation_conflict",
]


class CollisionEntry(_FrozenModel):
    kind: OperationConflictKind
    path: str
    assignment_ids: tuple[str, ...]
    detail: str


class CollisionReport(_FrozenModel):
    collisions: tuple[CollisionEntry, ...] = Field(default_factory=tuple)

    @property
    def has_collisions(self) -> bool:
        return bool(self.collisions)


class IntegrationResult(_FrozenModel):
    plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    baseline_sha: str = Field(min_length=1)
    ordered_assignment_ids: tuple[str, ...]
    ordered_result_digests: tuple[str, ...]
    combined_file_map_digest: str
    dependency_order: tuple[str, ...]
    collision_report: CollisionReport
    validation_evidence: tuple[ValidationEvidence, ...] = Field(default_factory=tuple)
    unresolved_assignments: tuple[str, ...] = Field(default_factory=tuple)
    promotion_ready: bool
    integration_digest: str

    @model_validator(mode="after")
    def _validate_integration_digest(self) -> IntegrationResult:
        if self.integration_digest != _integration_digest(self):
            raise ValueError("integration_digest does not match integration fields")
        return self


def make_work_assignment(
    *,
    assignment_id: str,
    plan_id: str,
    plan_digest: str,
    baseline_sha: str,
    assignment_kind: str | AssignmentKind,
    owned_paths: list[str],
    dependency_context_refs: list[str] | None = None,
    allowed_agent_ids: list[str] | None = None,
    required_structured_output_id: str | None = None,
    depends_on: list[str] | None = None,
    required_validators: list[str] | None = None,
    retry_limit: int | None = None,
    assignment_retry_limit: int | None = None,
    retry_policy_ref: str | None = None,
) -> WorkAssignment:
    resolved_retry_limit = assignment_retry_limit if assignment_retry_limit is not None else (retry_limit or 0)
    if isinstance(resolved_retry_limit, bool):
        raise ValueError("assignment_retry_limit/retry_limit must be an int, not bool")
    assignment_kind_value = assignment_kind.value if isinstance(assignment_kind, AssignmentKind) else str(assignment_kind)
    if assignment_kind_value not in REGISTERED_ASSIGNMENT_KINDS:
        raise ValueError(
            f"assignment_kind {assignment_kind!r} is not registered. "
            f"Allowed: {sorted(REGISTERED_ASSIGNMENT_KINDS)}"
        )
    normalized_owned_paths = tuple(sorted(normalize_owned_paths(owned_paths, reject_duplicates=True)))
    normalized_dependency_context_refs = _normalized_text_tuple(dependency_context_refs or [])
    normalized_allowed_agent_ids = _normalized_text_tuple(allowed_agent_ids or [])
    normalized_depends_on = _normalized_text_tuple(depends_on or [])
    normalized_required_validators = _normalized_text_tuple(required_validators or [])
    payload: dict[str, Any] = {
        "assignment_id": assignment_id,
        "plan_id": plan_id,
        "plan_digest": plan_digest,
        "baseline_sha": baseline_sha,
        "assignment_kind": assignment_kind_value,
        "owned_paths": normalized_owned_paths,
        "dependency_context_refs": normalized_dependency_context_refs,
        "allowed_agent_ids": normalized_allowed_agent_ids,
        "required_structured_output_id": required_structured_output_id,
        "depends_on": normalized_depends_on,
        "required_validators": normalized_required_validators,
        "assignment_retry_limit": int(resolved_retry_limit),
        "retry_policy_ref": retry_policy_ref,
    }
    digest = stable_digest(_assignment_digest_payload(payload))
    return WorkAssignment(
        assignment_id=assignment_id,
        plan_id=plan_id,
        plan_digest=plan_digest,
        baseline_sha=baseline_sha,
        assignment_kind=AssignmentKind(assignment_kind_value),
        owned_paths=normalized_owned_paths,
        dependency_context_refs=normalized_dependency_context_refs,
        allowed_agent_ids=normalized_allowed_agent_ids,
        required_structured_output_id=required_structured_output_id,
        depends_on=normalized_depends_on,
        required_validators=normalized_required_validators,
        assignment_retry_limit=int(resolved_retry_limit),
        retry_policy_ref=retry_policy_ref,
        assignment_digest=digest,
    )


def make_work_result(
    *,
    assignment: WorkAssignment,
    status: WorkResultStatus,
    attempt_id: str,
    changed_artifacts: list[dict[str, str]] | None = None,
    diagnostics: list[dict[str, Any]] | None = None,
    validation_evidence: list[dict[str, Any]] | None = None,
    file_map: dict[str, str] | None = None,
) -> WorkResult:
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id must be a non-empty string")
    owned = set(assignment.owned_paths)
    normalized_file_map = _normalize_file_map(file_map or {}, owned)
    artifacts = _artifact_identities(changed_artifacts or [], normalized_file_map, owned)
    if status == "completed":
        _validate_required_artifacts(assignment, artifacts)
        _validate_required_validators(assignment, validation_evidence or [])
    output_digest = stable_digest(normalized_file_map)
    diagnostic_entries = tuple(WorkDiagnostic(**item) for item in diagnostics or [])
    evidence_entries = tuple(ValidationEvidence(**item) for item in validation_evidence or [])
    payload: dict[str, Any] = {
        "assignment_id": assignment.assignment_id,
        "assignment_digest": assignment.assignment_digest,
        "baseline_sha": assignment.baseline_sha,
        "status": status,
        "changed_artifacts": artifacts,
        "diagnostics": diagnostic_entries,
        "validation_evidence": evidence_entries,
        "output_digest": output_digest,
        "attempt_id": attempt_id,
    }
    return WorkResult(
        assignment_id=assignment.assignment_id,
        assignment_digest=assignment.assignment_digest,
        baseline_sha=assignment.baseline_sha,
        status=status,
        changed_artifacts=artifacts,
        diagnostics=diagnostic_entries,
        validation_evidence=evidence_entries,
        output_digest=output_digest,
        attempt_id=attempt_id,
        result_digest=stable_digest(_result_digest_payload(payload)),
    )


def validate_work_result_for_assignment(
    *,
    assignment: WorkAssignment,
    result: WorkResult,
) -> WorkResult:
    """Validate an executor-supplied result against assignment authority."""
    result = WorkResult.model_validate(result.model_dump(mode="json"))
    if result.assignment_id != assignment.assignment_id:
        raise ValueError(f"WorkResult assignment_id {result.assignment_id!r} does not match assignment")
    if result.assignment_digest != assignment.assignment_digest:
        raise ValueError(f"WorkResult for {assignment.assignment_id!r} has mismatched assignment_digest")
    if result.baseline_sha != assignment.baseline_sha:
        raise ValueError(f"WorkResult for {assignment.assignment_id!r} has mismatched baseline_sha")

    owned = set(assignment.owned_paths)
    seen_paths: set[str] = set()
    for artifact in result.changed_artifacts:
        if artifact.path in seen_paths:
            raise ValueError(f"duplicate changed artifact path: {artifact.path!r}")
        seen_paths.add(artifact.path)
        if not path_is_within_owned(artifact.path, owned):
            raise ValueError(f"WorkResult changed artifact {artifact.path!r} is outside assignment owned_paths")

    if result.status == "completed":
        _validate_required_artifacts(assignment, result.changed_artifacts)
        evidence_payload = [item.model_dump(mode="json") for item in result.validation_evidence]
        _validate_required_validators(assignment, evidence_payload)
    return result


def validate_assignment_dag(assignments: list[WorkAssignment]) -> None:
    _topological_sort(assignments)


def _topological_sort(assignments: list[WorkAssignment]) -> list[WorkAssignment]:
    by_id = {assignment.assignment_id: assignment for assignment in assignments}
    ordered = deterministic_topological_order(
        assignments,
        item_id=lambda assignment: assignment.assignment_id,
        dependencies=lambda assignment: assignment.depends_on,
    )
    return [by_id[assignment_id] for assignment_id in ordered.ordered_ids]


def detect_collisions(
    assignments: list[WorkAssignment],
    results: list[WorkResult] | None = None,
) -> CollisionReport:
    assignment_collision_report = detect_owned_path_collisions(
        {assignment.assignment_id: assignment.owned_paths for assignment in assignments}
    )
    entries = [
        CollisionEntry(
            kind=_path_collision_kind(collision.kind),
            path=collision.path,
            assignment_ids=collision.owner_ids,
            detail=collision.detail,
        )
        for collision in assignment_collision_report.collisions
    ]
    entries.extend(_result_write_collisions(results or []))
    entries.sort(key=lambda item: (item.kind, item.path, item.assignment_ids))
    return CollisionReport(collisions=tuple(entries))


def build_integration_result(
    *,
    plan_id: str,
    plan_digest: str,
    assignments: list[WorkAssignment],
    results: list[WorkResult],
    extra_validation_evidence: list[dict[str, Any]] | None = None,
) -> IntegrationResult:
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")
    if not isinstance(plan_digest, str) or not plan_digest.strip():
        raise ValueError("plan_digest must be a non-empty string")
    if not assignments:
        raise ValueError("assignments must not be empty")

    _validate_plan_and_baseline(plan_id=plan_id, plan_digest=plan_digest, assignments=assignments)
    ordered = _topological_sort(assignments)
    assignment_by_id = {assignment.assignment_id: assignment for assignment in assignments}
    result_by_id = _index_results(results)
    _validate_results_match_assignments(assignment_by_id, result_by_id)
    _validate_dependency_results(ordered, result_by_id)

    collision_report = detect_collisions(assignments, results)
    if collision_report.has_collisions:
        raise ValueError(f"IntegrationResult rejects overlapping writers: {collision_report.model_dump(mode='json')}")

    unresolved = tuple(sorted(set(assignment_by_id) - set(result_by_id)))
    ordered_assignment_ids = tuple(assignment.assignment_id for assignment in ordered)
    ordered_result_digests = tuple(
        result_by_id[assignment_id].result_digest if assignment_id in result_by_id else ""
        for assignment_id in ordered_assignment_ids
    )
    combined_file_map_digest = stable_digest(
        {
            artifact.path: artifact.content_digest
            for assignment in ordered
            for result in [result_by_id.get(assignment.assignment_id)]
            if result is not None
            for artifact in result.changed_artifacts
        }
    )
    evidence = tuple(ValidationEvidence(**item) for item in extra_validation_evidence or [])
    promotion_ready = not unresolved and all(
        result_by_id.get(assignment.assignment_id) is not None
        and result_by_id[assignment.assignment_id].status == "completed"
        for assignment in assignments
    )
    baseline_sha = assignments[0].baseline_sha
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "plan_digest": plan_digest,
        "baseline_sha": baseline_sha,
        "ordered_assignment_ids": ordered_assignment_ids,
        "ordered_result_digests": ordered_result_digests,
        "combined_file_map_digest": combined_file_map_digest,
        "dependency_order": ordered_assignment_ids,
        "collision_report": collision_report,
        "validation_evidence": evidence,
        "unresolved_assignments": unresolved,
        "promotion_ready": promotion_ready,
    }
    return IntegrationResult(
        plan_id=plan_id,
        plan_digest=plan_digest,
        baseline_sha=baseline_sha,
        ordered_assignment_ids=ordered_assignment_ids,
        ordered_result_digests=ordered_result_digests,
        combined_file_map_digest=combined_file_map_digest,
        dependency_order=ordered_assignment_ids,
        collision_report=collision_report,
        validation_evidence=evidence,
        unresolved_assignments=unresolved,
        promotion_ready=promotion_ready,
        integration_digest=stable_digest(_integration_digest_payload(payload)),
    )


def _assignment_digest(assignment: WorkAssignment) -> str:
    return stable_digest(_assignment_digest_payload(assignment.model_dump(mode="json", exclude={"assignment_digest"})))


def _assignment_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    retry_limit = payload.get("assignment_retry_limit")
    if isinstance(retry_limit, bool):
        raise ValueError("assignment_retry_limit/retry_limit must be an int, not bool")
    return {
        "assignment_id": str(payload["assignment_id"]).strip(),
        "plan_id": str(payload["plan_id"]).strip(),
        "plan_digest": str(payload["plan_digest"]).strip(),
        "baseline_sha": str(payload["baseline_sha"]).strip(),
        "assignment_kind": str(payload["assignment_kind"]),
        "owned_paths": list(sorted(normalize_owned_paths(payload["owned_paths"], reject_duplicates=True))),
        "dependency_context_refs": sorted({str(item).strip() for item in payload.get("dependency_context_refs") or [] if str(item).strip()}),
        "allowed_agent_ids": sorted({str(item).strip() for item in payload.get("allowed_agent_ids") or [] if str(item).strip()}),
        "required_structured_output_id": payload.get("required_structured_output_id"),
        "depends_on": sorted({str(item).strip() for item in payload.get("depends_on") or [] if str(item).strip()}),
        "required_validators": sorted({str(item).strip() for item in payload.get("required_validators") or [] if str(item).strip()}),
        "assignment_retry_limit": int(retry_limit or 0),
        "retry_policy_ref": payload.get("retry_policy_ref"),
    }


def _normalized_text_tuple(value: list[str]) -> tuple[str, ...]:
    return tuple(sorted({str(item or "").strip() for item in value if str(item or "").strip()}))


def _result_digest(result: WorkResult) -> str:
    return stable_digest(_result_digest_payload(result.model_dump(mode="json", exclude={"result_digest"})))


def _result_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "assignment_id": payload["assignment_id"],
        "assignment_digest": payload["assignment_digest"],
        "baseline_sha": payload["baseline_sha"],
        "status": payload["status"],
        "changed_artifacts": _jsonable(payload["changed_artifacts"]),
        "validation_evidence": _jsonable(payload["validation_evidence"]),
        "output_digest": payload["output_digest"],
        "attempt_id": payload["attempt_id"],
    }


def _integration_digest(integration: IntegrationResult) -> str:
    return stable_digest(_integration_digest_payload(integration.model_dump(mode="json", exclude={"integration_digest"})))


def _integration_digest_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": payload["plan_id"],
        "plan_digest": payload["plan_digest"],
        "baseline_sha": payload["baseline_sha"],
        "ordered_assignment_ids": list(payload["ordered_assignment_ids"]),
        "ordered_result_digests": list(payload["ordered_result_digests"]),
        "combined_file_map_digest": payload["combined_file_map_digest"],
        "dependency_order": list(payload["dependency_order"]),
        "collision_report": _jsonable(payload["collision_report"]),
        "validation_evidence": _jsonable(payload["validation_evidence"]),
        "unresolved_assignments": list(payload["unresolved_assignments"]),
        "promotion_ready": payload["promotion_ready"],
    }


def _normalize_file_map(file_map: dict[str, str], owned: set[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_path, content in sorted((file_map or {}).items()):
        path = normalize_owned_path(raw_path)
        if not path_is_within_owned(path, owned):
            raise ValueError(f"WorkResult file_map path {path!r} is outside assignment owned_paths")
        if path in normalized:
            raise ValueError(f"duplicate WorkResult file_map path: {path!r}")
        normalized[path] = str(content)
    return normalized


def _artifact_identities(
    changed_artifacts: list[dict[str, str]],
    file_map: dict[str, str],
    owned: set[str],
) -> tuple[ArtifactIdentity, ...]:
    artifacts: list[ArtifactIdentity] = []
    seen_paths: set[str] = set()
    for item in changed_artifacts:
        path = normalize_owned_path(str(item.get("path") or ""))
        if path in seen_paths:
            raise ValueError(f"duplicate changed artifact path: {path!r}")
        seen_paths.add(path)
        if not path_is_within_owned(path, owned):
            raise ValueError(f"WorkResult changed artifact {path!r} is outside assignment owned_paths")
        operation_text = str(item.get("operation") or "")
        if operation_text == "create":
            operation: OperationKind = "create"
        elif operation_text == "update":
            operation = "update"
        elif operation_text == "delete":
            operation = "delete"
        else:
            raise ValueError(f"WorkResult: unknown operation {operation_text!r}; must be create, update, or delete")
        content_digest = item.get("content_digest") or (stable_digest(file_map[path]) if path in file_map else None)
        if not content_digest:
            raise ValueError(f"changed artifact {path!r} must provide content_digest or matching file_map content")
        artifacts.append(ArtifactIdentity(path=path, operation=operation, content_digest=content_digest))
    return tuple(sorted(artifacts, key=lambda artifact: (artifact.path, artifact.operation)))


def _validate_required_artifacts(assignment: WorkAssignment, artifacts: tuple[ArtifactIdentity, ...]) -> None:
    emitted = {artifact.path for artifact in artifacts}
    missing = [
        owned
        for owned in assignment.owned_paths
        if not any(path == owned or path.startswith(f"{owned}/") for path in emitted)
    ]
    if missing:
        raise ValueError(f"WorkResult omitted required owned artifacts: {missing}")


def _validate_required_validators(assignment: WorkAssignment, validation_evidence: list[dict[str, Any]]) -> None:
    passed = {
        str(item.get("validator_id") or "").strip()
        for item in validation_evidence
        if item.get("passed") is True
    }
    missing = sorted(set(assignment.required_validators) - passed)
    if missing:
        raise ValueError(f"WorkResult missing required validation evidence: {missing}")


def _path_collision_kind(kind: PathCollisionKind) -> OperationConflictKind:
    if kind == PathCollisionKind.CASE_COLLISION:
        return "case_collision"
    if kind == PathCollisionKind.PARENT_CHILD:
        return "parent_child_writer"
    return "overlapping_writer"


def _result_write_collisions(results: list[WorkResult]) -> list[CollisionEntry]:
    path_ops: dict[str, dict[str, list[str]]] = {}
    case_paths: dict[str, list[str]] = {}
    entries: list[CollisionEntry] = []
    for result in results:
        for artifact in result.changed_artifacts:
            path_ops.setdefault(artifact.path, {}).setdefault(artifact.operation, []).append(result.assignment_id)
            case_paths.setdefault(artifact.path.lower(), []).append(artifact.path)

    for path, operations in sorted(path_ops.items()):
        owners = sorted({owner for op_owners in operations.values() for owner in op_owners})
        if len(owners) > 1:
            entries.append(
                CollisionEntry(
                    kind="overlapping_writer",
                    path=path,
                    assignment_ids=tuple(owners),
                    detail=f"path {path!r} is written by multiple assignments",
                )
            )
        if len(operations) > 1:
            entries.append(
                CollisionEntry(
                    kind="operation_conflict",
                    path=path,
                    assignment_ids=tuple(owners),
                    detail=f"conflicting operations on {path!r}: {sorted(operations)}",
                )
            )

    for paths in case_paths.values():
        unique_paths = sorted(set(paths))
        if len(unique_paths) <= 1:
            continue
        owners = sorted(
            {
                owner
                for path in unique_paths
                for op_owners in path_ops[path].values()
                for owner in op_owners
            }
        )
        entries.append(
            CollisionEntry(
                kind="case_collision",
                path=unique_paths[0],
                assignment_ids=tuple(owners),
                detail=f"case-normalization collision among written paths: {unique_paths}",
            )
        )

    result_paths = sorted(path_ops)
    for index, parent in enumerate(result_paths):
        for child in result_paths[index + 1 :]:
            if child.startswith(f"{parent}/"):
                owners = sorted(
                    {
                        owner
                        for path in (parent, child)
                        for op_owners in path_ops[path].values()
                        for owner in op_owners
                    }
                )
                entries.append(
                    CollisionEntry(
                        kind="parent_child_writer",
                        path=child,
                        assignment_ids=tuple(owners),
                        detail=f"parent path {parent!r} and child path {child!r} are both written",
                    )
                )
    return entries


def _validate_plan_and_baseline(*, plan_id: str, plan_digest: str, assignments: list[WorkAssignment]) -> None:
    baselines = {assignment.baseline_sha for assignment in assignments}
    if len(baselines) != 1:
        raise ValueError(f"assignments have mismatched baseline_sha values: {sorted(baselines)}")
    for assignment in assignments:
        if assignment.plan_id != plan_id or assignment.plan_digest != plan_digest:
            raise ValueError(f"assignment {assignment.assignment_id!r} does not match integration plan identity")


def _index_results(results: list[WorkResult]) -> dict[str, WorkResult]:
    by_id: dict[str, WorkResult] = {}
    for result in results:
        if result.assignment_id in by_id:
            raise ValueError(f"duplicate WorkResult for assignment {result.assignment_id!r}")
        by_id[result.assignment_id] = result
    return by_id


def _validate_results_match_assignments(
    assignment_by_id: dict[str, WorkAssignment],
    result_by_id: dict[str, WorkResult],
) -> None:
    unknown = sorted(set(result_by_id) - set(assignment_by_id))
    if unknown:
        raise ValueError(f"WorkResult references unknown assignments: {unknown}")
    for assignment_id, result in result_by_id.items():
        assignment = assignment_by_id[assignment_id]
        if result.assignment_digest != assignment.assignment_digest:
            raise ValueError(f"WorkResult for {assignment_id!r} has mismatched assignment_digest")
        if result.baseline_sha != assignment.baseline_sha:
            raise ValueError(f"WorkResult for {assignment_id!r} has mismatched baseline_sha")


def _validate_dependency_results(assignments: list[WorkAssignment], result_by_id: dict[str, WorkResult]) -> None:
    for assignment in assignments:
        result = result_by_id.get(assignment.assignment_id)
        if result is None:
            continue
        for dep_id in assignment.depends_on:
            dep_result = result_by_id.get(dep_id)
            if dep_result is None:
                raise ValueError(
                    f"assignment {assignment.assignment_id!r} has a result but dependency {dep_id!r} has no result"
                )
            if dep_result.status != "completed":
                raise ValueError(
                    f"assignment {assignment.assignment_id!r} has a result but dependency {dep_id!r} is {dep_result.status!r}"
                )


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    return value


__all__ = [
    "AG2_TRANSIENT_RETRY_FIELD",
    "ASSIGNMENT_RETRY_LIMIT_RANGE",
    "QUEUE_DELIVERY_MAX_ATTEMPTS_RANGE",
    "REGISTERED_ASSIGNMENT_KINDS",
    "ArtifactIdentity",
    "CollisionEntry",
    "CollisionReport",
    "IntegrationResult",
    "ValidationEvidence",
    "WorkAssignment",
    "WorkDiagnostic",
    "WorkResult",
    "build_integration_result",
    "detect_collisions",
    "make_work_assignment",
    "make_work_result",
    "stable_digest",
    "validate_work_result_for_assignment",
    "validate_assignment_dag",
]
