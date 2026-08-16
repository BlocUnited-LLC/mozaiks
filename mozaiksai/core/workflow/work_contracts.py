"""Typed deterministic contracts for multi-agent work assignments and integration.

This module provides the canonical typed boundary between plan approval and
execution. It covers three contracts:

  WorkAssignment  — a single deterministic unit of work with owned paths,
                    dependency refs, structured-output requirement, and a
                    stable assignment digest.

  WorkResult      — the typed outcome of executing one WorkAssignment, with
                    changed artifact identities, validation evidence, and an
                    output digest bounded to the assignment's owned paths.

  IntegrationResult — the combined outcome of integrating an ordered set of
                    WorkResults, with collision detection, dependency-order
                    enforcement, and a stable integration digest.

Design rules:
  - Extends rather than parallels task_batches.py path/DAG semantics.
  - References queue.py lease/retry bounds (max_attempts [1, 25]).
  - No AG2 construction, no external service calls, no autonomous delegation.
  - All digests are deterministic SHA-256 over canonical sorted JSON.
  - Unknown fields are rejected at construction time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants — aligned with existing task-batch and queue contracts
# ---------------------------------------------------------------------------

# Superset of _ALLOWED_TASK_TYPES from app_build_plan.py plus integration kinds.
REGISTERED_ASSIGNMENT_KINDS: frozenset[str] = frozenset(
    {
        # AppGenerator task types (task_batches.py / app_build_plan.py)
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
        # Work-integration kinds
        "integration",
        "validation",
    }
)

# Queue bounds from queue.py: max_attempts ∈ [1, 25].
# We expose a softer retry_limit (0–5) matching TaskBatchExecution.retry_limit.
_MAX_RETRY_LIMIT: int = 5

_GLOB_CHARS: frozenset[str] = frozenset("*?[")
_SECRET_PATH_TERMS: frozenset[str] = frozenset(
    {".env", "secret", "vault", "credential", "key", ".pem", ".p12", ".pfx"}
)

# ---------------------------------------------------------------------------
# Path helpers — extended from _normalize_owned_paths in task_batches.py
# ---------------------------------------------------------------------------


def _normalize_relative_path(path: str) -> str:
    """Return a normalized relative path or raise ValueError.

    Rules (aligned with task_batches._normalize_owned_paths):
    - Must be a non-empty string.
    - No absolute paths (leading / or drive letter).
    - No path traversal (..).
    - No glob characters (* ? [).
    - No secret-term path segments.
    - Backslashes normalized to forward slashes.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"empty or non-string path: {path!r}")

    # Reject absolute paths
    cleaned = path.strip()
    if cleaned.startswith("/"):
        raise ValueError(f"absolute path not allowed: {path!r}")
    if len(cleaned) > 1 and cleaned[1] == ":" and cleaned[0].isalpha():
        raise ValueError(f"absolute Windows path not allowed: {path!r}")

    # Reject glob characters
    if any(c in cleaned for c in _GLOB_CHARS):
        raise ValueError(f"glob characters not allowed in owned paths: {path!r}")

    # Normalize separators
    normalized = cleaned.replace("\\", "/").rstrip("/")

    # Reject path traversal
    for part in normalized.split("/"):
        if part == "..":
            raise ValueError(f"path traversal not allowed: {path!r}")

    # Reject secret-term paths
    lower = normalized.lower()
    for term in _SECRET_PATH_TERMS:
        if term in lower:
            raise ValueError(
                f"secret-term path not allowed in owned paths: {path!r} (term: {term!r})"
            )

    if not normalized:
        raise ValueError(f"path normalized to empty string: {path!r}")

    return normalized


def _normalize_and_deduplicate_paths(paths: list[str]) -> tuple[str, ...]:
    """Normalize, deduplicate (raising on duplicates), and sort owned paths."""
    seen: set[str] = set()
    result: list[str] = []
    for p in paths:
        n = _normalize_relative_path(p)
        if n in seen:
            raise ValueError(f"duplicate owned path: {n!r}")
        seen.add(n)
        result.append(n)
    return tuple(sorted(result))


def _check_case_collisions(paths: list[str]) -> None:
    """Raise ValueError on case-normalization collision within a path list."""
    lower_map: dict[str, str] = {}
    for p in paths:
        lower = p.lower()
        if lower in lower_map and lower_map[lower] != p:
            raise ValueError(
                f"case-normalization collision: {lower_map[lower]!r} and {p!r}"
                " differ only by case"
            )
        lower_map[lower] = p


def _path_is_within_owned(path: str, owned: set[str]) -> bool:
    """True if path is directly owned or is a child of an owned directory."""
    if path in owned:
        return True
    for o in owned:
        if path.startswith(o + "/"):
            return True
    return False


# ---------------------------------------------------------------------------
# Stable deterministic digest
# ---------------------------------------------------------------------------


def stable_digest(data: Any) -> str:
    """Return a deterministic SHA-256 hex digest over canonical sorted JSON.

    Any JSON-serializable value is accepted. dict keys are recursively sorted.
    """
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


# ---------------------------------------------------------------------------
# WorkAssignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkAssignment:
    """Typed deterministic contract for a single unit of work.

    Immutable after construction. The assignment_digest field is a
    deterministic SHA-256 fingerprint of all other fields, enabling stable
    identity across agents and time.

    Use ``make_work_assignment()`` to construct; do not instantiate directly.
    """

    assignment_id: str
    plan_id: str
    plan_digest: str
    baseline_sha: str
    assignment_kind: str
    owned_paths: tuple[str, ...]
    dependency_context_refs: tuple[str, ...]
    allowed_agent_ids: tuple[str, ...]
    required_structured_output_id: str | None
    depends_on: tuple[str, ...]
    required_validators: tuple[str, ...]
    retry_limit: int
    retry_policy_ref: str | None
    assignment_digest: str


def make_work_assignment(
    *,
    assignment_id: str,
    plan_id: str,
    plan_digest: str,
    baseline_sha: str,
    assignment_kind: str,
    owned_paths: list[str],
    dependency_context_refs: list[str] | None = None,
    allowed_agent_ids: list[str] | None = None,
    required_structured_output_id: str | None = None,
    depends_on: list[str] | None = None,
    required_validators: list[str] | None = None,
    retry_limit: int = 0,
    retry_policy_ref: str | None = None,
) -> WorkAssignment:
    """Construct and validate a WorkAssignment.

    Raises ValueError on any validation violation including:
    - empty/missing required identifiers
    - unregistered assignment_kind
    - empty owned_paths
    - path traversal, absolute paths, glob chars, or secret-term paths
    - duplicate or case-colliding owned paths
    - retry_limit outside [0, 5]
    - self-dependency in depends_on
    """
    # Required identifiers
    for name, value in (
        ("assignment_id", assignment_id),
        ("plan_id", plan_id),
        ("plan_digest", plan_digest),
        ("baseline_sha", baseline_sha),
        ("assignment_kind", assignment_kind),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string, got {value!r}")

    # Registered kind
    if assignment_kind not in REGISTERED_ASSIGNMENT_KINDS:
        raise ValueError(
            f"assignment_kind {assignment_kind!r} is not registered. "
            f"Allowed: {sorted(REGISTERED_ASSIGNMENT_KINDS)}"
        )

    # Owned paths: non-empty, no duplicates, no case collisions
    if not owned_paths:
        raise ValueError("owned_paths must not be empty")
    normalized_paths = _normalize_and_deduplicate_paths(list(owned_paths))
    _check_case_collisions(list(normalized_paths))

    # Retry limit: 0–5 (matching TaskBatchExecution.retry_limit)
    if not isinstance(retry_limit, int) or isinstance(retry_limit, bool):
        raise ValueError(f"retry_limit must be an int, got {retry_limit!r}")
    if retry_limit < 0 or retry_limit > _MAX_RETRY_LIMIT:
        raise ValueError(
            f"retry_limit must be in [0, {_MAX_RETRY_LIMIT}], got {retry_limit}"
        )

    dep_context = tuple(sorted(set(dependency_context_refs or [])))
    agent_ids = tuple(sorted(set(allowed_agent_ids or [])))
    deps = tuple(depends_on or [])
    validators = tuple(required_validators or [])

    # No self-dependency
    if assignment_id in deps:
        raise ValueError(
            f"assignment {assignment_id!r} cannot depend on itself"
        )

    # Compute stable digest (all fields except the digest itself)
    digest_data = {
        "assignment_id": assignment_id,
        "plan_id": plan_id,
        "plan_digest": plan_digest,
        "baseline_sha": baseline_sha,
        "assignment_kind": assignment_kind,
        "owned_paths": list(normalized_paths),
        "dependency_context_refs": list(dep_context),
        "allowed_agent_ids": list(agent_ids),
        "required_structured_output_id": required_structured_output_id,
        "depends_on": list(deps),
        "required_validators": list(validators),
        "retry_limit": retry_limit,
        "retry_policy_ref": retry_policy_ref,
    }
    assignment_digest = stable_digest(digest_data)

    return WorkAssignment(
        assignment_id=assignment_id,
        plan_id=plan_id,
        plan_digest=plan_digest,
        baseline_sha=baseline_sha,
        assignment_kind=assignment_kind,
        owned_paths=normalized_paths,
        dependency_context_refs=dep_context,
        allowed_agent_ids=agent_ids,
        required_structured_output_id=required_structured_output_id,
        depends_on=deps,
        required_validators=validators,
        retry_limit=retry_limit,
        retry_policy_ref=retry_policy_ref,
        assignment_digest=assignment_digest,
    )


# ---------------------------------------------------------------------------
# WorkResult support types
# ---------------------------------------------------------------------------

WorkResultStatus = Literal["completed", "failed", "skipped"]
OperationKind = Literal["create", "update", "delete"]


@dataclass(frozen=True)
class ArtifactIdentity:
    """Identifies a single changed artifact within a WorkResult."""

    path: str
    operation: OperationKind
    content_digest: str


@dataclass(frozen=True)
class WorkDiagnostic:
    """A single structured diagnostic message."""

    level: Literal["error", "warning", "info"]
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True)
class ValidationEvidence:
    """Evidence that a required validator was run."""

    validator_id: str
    passed: bool
    detail: str | None = None


# ---------------------------------------------------------------------------
# WorkResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkResult:
    """Typed outcome of executing a single WorkAssignment.

    Immutable after construction. result_digest is a deterministic
    fingerprint of assignment identity, status, artifact set, and attempt.

    Use ``make_work_result()`` to construct; do not instantiate directly.
    """

    assignment_id: str
    assignment_digest: str
    baseline_sha: str
    status: WorkResultStatus
    changed_artifacts: tuple[ArtifactIdentity, ...]
    diagnostics: tuple[WorkDiagnostic, ...]
    validation_evidence: tuple[ValidationEvidence, ...]
    output_digest: str
    attempt_id: str
    result_digest: str


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
    """Construct and validate a WorkResult against its WorkAssignment.

    Each changed artifact's path must be within the assignment's owned_paths
    (exact match or child of an owned directory). Paths outside owned_paths
    are rejected.

    ``file_map`` provides path→content for computing output_digest and
    optional content_digest derivation. If ``content_digest`` is absent from
    a changed_artifact dict, it is derived from file_map[path] if available.

    Raises ValueError on:
    - empty attempt_id
    - changed artifact path outside assignment owned_paths
    - unknown operation kind
    """
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise ValueError("attempt_id must be a non-empty string")

    owned = set(assignment.owned_paths)

    # Parse and validate changed artifacts
    artifacts: list[ArtifactIdentity] = []
    _valid_ops: frozenset[str] = frozenset({"create", "update", "delete"})
    for a in changed_artifacts or []:
        raw_path = a.get("path", "")
        path = _normalize_relative_path(raw_path)
        operation = a.get("operation", "")
        if operation not in _valid_ops:
            raise ValueError(
                f"WorkResult: unknown operation {operation!r}; "
                f"must be one of {sorted(_valid_ops)}"
            )
        # Ownership check
        if not _path_is_within_owned(path, owned):
            raise ValueError(
                f"WorkResult: changed artifact {path!r} is outside "
                f"assignment {assignment.assignment_id!r} owned paths "
                f"{sorted(owned)}"
            )
        # Content digest: explicit > derived from file_map > empty string
        content_digest = a.get("content_digest") or ""
        if not content_digest and file_map and path in file_map:
            content_digest = stable_digest(file_map[path])
        artifacts.append(
            ArtifactIdentity(path=path, operation=operation, content_digest=content_digest)
        )

    # Sort artifacts by path for determinism
    sorted_artifacts = tuple(sorted(artifacts, key=lambda x: x.path))

    # Output digest: digest of file_map sorted by path
    output_digest = stable_digest(dict(sorted((file_map or {}).items())))

    # Diagnostics
    _valid_levels: frozenset[str] = frozenset({"error", "warning", "info"})
    diags: list[WorkDiagnostic] = []
    for d in diagnostics or []:
        level = d.get("level", "")
        if level not in _valid_levels:
            raise ValueError(
                f"WorkDiagnostic: unknown level {level!r}; "
                f"must be one of {sorted(_valid_levels)}"
            )
        diags.append(
            WorkDiagnostic(
                level=level,
                code=d.get("code", ""),
                message=d.get("message", ""),
                path=d.get("path"),
            )
        )

    # Validation evidence
    evidence: list[ValidationEvidence] = []
    for v in validation_evidence or []:
        evidence.append(
            ValidationEvidence(
                validator_id=v.get("validator_id", ""),
                passed=bool(v.get("passed", False)),
                detail=v.get("detail"),
            )
        )

    # Result digest (covers identity, status, artifacts, attempt — not prose)
    digest_data = {
        "assignment_id": assignment.assignment_id,
        "assignment_digest": assignment.assignment_digest,
        "baseline_sha": assignment.baseline_sha,
        "status": status,
        "changed_artifacts": [
            {
                "path": a.path,
                "operation": a.operation,
                "content_digest": a.content_digest,
            }
            for a in sorted_artifacts
        ],
        "output_digest": output_digest,
        "attempt_id": attempt_id,
    }
    result_digest = stable_digest(digest_data)

    return WorkResult(
        assignment_id=assignment.assignment_id,
        assignment_digest=assignment.assignment_digest,
        baseline_sha=assignment.baseline_sha,
        status=status,
        changed_artifacts=sorted_artifacts,
        diagnostics=tuple(diags),
        validation_evidence=tuple(evidence),
        output_digest=output_digest,
        attempt_id=attempt_id,
        result_digest=result_digest,
    )


# ---------------------------------------------------------------------------
# DAG validation and topological ordering
# ---------------------------------------------------------------------------


def _topological_sort(assignments: list[WorkAssignment]) -> list[WorkAssignment]:
    """Return assignments in dependency-first topological order.

    Uses Kahn's algorithm with deterministic (alphabetical) tie-breaking.

    Raises ValueError if:
    - a depends_on ID is not in the provided assignment set
    - a dependency cycle is detected
    """
    by_id: dict[str, WorkAssignment] = {a.assignment_id: a for a in assignments}

    # Validate all dep IDs exist in the set
    for a in assignments:
        for dep in a.depends_on:
            if dep not in by_id:
                raise ValueError(
                    f"assignment {a.assignment_id!r} depends on {dep!r} "
                    "which is not in the assignment set"
                )

    # Build adjacency and in-degree
    in_degree: dict[str, int] = {a.assignment_id: len(set(a.depends_on)) for a in assignments}
    dependents: dict[str, list[str]] = {a.assignment_id: [] for a in assignments}
    for a in assignments:
        for dep in set(a.depends_on):
            dependents[dep].append(a.assignment_id)

    # Kahn's with alphabetical tie-breaking for determinism
    ready = sorted(a_id for a_id, deg in in_degree.items() if deg == 0)
    ordered_ids: list[str] = []

    while ready:
        node_id = ready.pop(0)
        ordered_ids.append(node_id)
        for dep_id in sorted(dependents.get(node_id, [])):
            in_degree[dep_id] -= 1
            if in_degree[dep_id] == 0:
                ready.append(dep_id)
                ready.sort()

    if len(ordered_ids) != len(assignments):
        remaining = sorted(set(by_id) - set(ordered_ids))
        raise ValueError(
            f"dependency cycle detected among assignments: {remaining}"
        )

    return [by_id[a_id] for a_id in ordered_ids]


def validate_assignment_dag(assignments: list[WorkAssignment]) -> None:
    """Validate the dependency DAG for a set of assignments.

    Raises ValueError on cycles or missing dependency IDs. This is a
    standalone check before results are available.
    """
    _topological_sort(assignments)


# ---------------------------------------------------------------------------
# Collision detection
# ---------------------------------------------------------------------------

CollisionKind = Literal[
    "direct_path",        # two assignments own identical path
    "parent_child",       # one owns a directory parent of another's owned path
    "case_collision",     # paths differ only by case
    "operation_conflict", # create + delete on same path in results
]


@dataclass(frozen=True)
class CollisionEntry:
    """A single detected collision."""

    kind: CollisionKind
    path: str
    assignment_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class CollisionReport:
    """Report of all collisions across a set of assignments and results."""

    collisions: tuple[CollisionEntry, ...]

    @property
    def has_collisions(self) -> bool:
        return bool(self.collisions)


def detect_collisions(
    assignments: list[WorkAssignment],
    results: list[WorkResult] | None = None,
) -> CollisionReport:
    """Detect path collisions across assignments and results.

    Four collision kinds are checked:
    1. direct_path — two assignments claim the same path.
    2. parent_child — one assignment owns a dir that contains another's path.
    3. case_collision — paths differ only by case (filesystem-unsafe).
    4. operation_conflict — create+delete on the same path across results.
    """
    entries: list[CollisionEntry] = []

    # Build path→owners map across all assignments
    path_owners: dict[str, list[str]] = {}
    for a in assignments:
        for p in a.owned_paths:
            path_owners.setdefault(p, []).append(a.assignment_id)

    # 1. Direct-path collisions
    for path, owners in sorted(path_owners.items()):
        unique_owners = sorted(set(owners))
        if len(unique_owners) > 1:
            entries.append(
                CollisionEntry(
                    kind="direct_path",
                    path=path,
                    assignment_ids=tuple(unique_owners),
                    detail=(
                        f"path {path!r} claimed by {len(unique_owners)} assignments"
                    ),
                )
            )

    # 2. Parent/child conflicts — owner A owns "foo" and owner B owns "foo/bar.py"
    all_paths = sorted(path_owners.keys())
    for i, p1 in enumerate(all_paths):
        for p2 in all_paths[i + 1 :]:
            if not p2.startswith(p1 + "/"):
                continue
            owners1 = set(path_owners[p1])
            owners2 = set(path_owners[p2])
            if owners1 != owners2:
                all_owners = tuple(sorted(owners1 | owners2))
                entries.append(
                    CollisionEntry(
                        kind="parent_child",
                        path=p2,
                        assignment_ids=all_owners,
                        detail=(
                            f"{p1!r} (parent dir) and {p2!r} (child path) "
                            "owned by different assignments"
                        ),
                    )
                )

    # 3. Case-normalization collisions
    lower_to_paths: dict[str, list[str]] = {}
    for p in path_owners:
        lower_to_paths.setdefault(p.lower(), []).append(p)
    for _lower, paths in sorted(lower_to_paths.items()):
        if len(paths) > 1:
            all_owners: set[str] = set()
            for p in paths:
                all_owners.update(path_owners[p])
            entries.append(
                CollisionEntry(
                    kind="case_collision",
                    path=paths[0],
                    assignment_ids=tuple(sorted(all_owners)),
                    detail=f"case-normalization collision: {sorted(paths)}",
                )
            )

    # 4. Operation conflicts — create + delete on same path across results
    if results:
        ops_by_path: dict[str, dict[str, list[str]]] = {}
        for r in results:
            for artifact in r.changed_artifacts:
                ops_by_path.setdefault(artifact.path, {}).setdefault(
                    artifact.operation, []
                ).append(r.assignment_id)
        for path, ops in sorted(ops_by_path.items()):
            if "create" in ops and "delete" in ops:
                conflict_owners: set[str] = set()
                for op_owners in ops.values():
                    conflict_owners.update(op_owners)
                entries.append(
                    CollisionEntry(
                        kind="operation_conflict",
                        path=path,
                        assignment_ids=tuple(sorted(conflict_owners)),
                        detail=(
                            f"conflicting create+delete operations on {path!r}"
                        ),
                    )
                )

    # Sort for determinism: kind first, then path
    entries.sort(key=lambda c: (c.kind, c.path))
    return CollisionReport(collisions=tuple(entries))


# ---------------------------------------------------------------------------
# IntegrationResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationResult:
    """Combined outcome of integrating an ordered set of WorkResults.

    Immutable. integration_digest is a deterministic fingerprint of the plan,
    ordered assignment/result IDs, combined file map, and promotion state.

    Use ``build_integration_result()`` to construct.
    """

    plan_id: str
    plan_digest: str
    ordered_assignment_ids: tuple[str, ...]
    ordered_result_digests: tuple[str, ...]
    combined_file_map_digest: str
    dependency_order: tuple[str, ...]
    collision_report: CollisionReport
    validation_evidence: tuple[ValidationEvidence, ...]
    unresolved_assignments: tuple[str, ...]
    promotion_ready: bool
    integration_digest: str


def build_integration_result(
    *,
    plan_id: str,
    plan_digest: str,
    assignments: list[WorkAssignment],
    results: list[WorkResult],
    extra_validation_evidence: list[dict[str, Any]] | None = None,
) -> IntegrationResult:
    """Build a deterministic IntegrationResult from a set of assignments and results.

    Raises ValueError if:
    - plan_id or plan_digest are empty
    - duplicate results for the same assignment_id
    - a dependency cycle exists among assignments
    - a dependency ID is missing from the assignment set
    - an assignment has a result but its dependency has no result or is not completed

    Incomplete assignments (no result yet) are recorded in unresolved_assignments;
    promotion_ready is False when any are present.

    The combined_file_map_digest is a deterministic digest of the merged
    (path → content_digest) map across results in dependency order, where
    later results overwrite earlier ones on the same path.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise ValueError("plan_id must be a non-empty string")
    if not isinstance(plan_digest, str) or not plan_digest.strip():
        raise ValueError("plan_digest must be a non-empty string")

    # Index results; reject duplicates
    result_by_id: dict[str, WorkResult] = {}
    for r in results:
        if r.assignment_id in result_by_id:
            raise ValueError(
                f"duplicate WorkResult for assignment {r.assignment_id!r}"
            )
        result_by_id[r.assignment_id] = r

    # Topological sort (raises on cycles / missing dep IDs)
    ordered = _topological_sort(assignments)

    # Validate dependency completeness for assignments that have results.
    # An assignment with a result may only exist if all its deps also have
    # completed results. This enforces "refuse incomplete dependency results".
    for a in ordered:
        r = result_by_id.get(a.assignment_id)
        if r is None:
            continue  # no result yet — counted as unresolved below
        if r.status == "skipped":
            continue  # skipped results bypass dep completeness check
        for dep_id in a.depends_on:
            dep_result = result_by_id.get(dep_id)
            if dep_result is None:
                raise ValueError(
                    f"assignment {a.assignment_id!r} has a result but depends on "
                    f"{dep_id!r} which has no result; cannot integrate"
                )
            if dep_result.status != "completed":
                raise ValueError(
                    f"assignment {a.assignment_id!r} has a result but depends on "
                    f"{dep_id!r} whose status is {dep_result.status!r}, "
                    f"not 'completed'; cannot integrate"
                )

    # Detect all collisions
    collision_report = detect_collisions(assignments, results)

    # Unresolved assignments
    assignment_ids = {a.assignment_id for a in assignments}
    resolved_ids = set(result_by_id.keys())
    unresolved = tuple(sorted(assignment_ids - resolved_ids))

    # Ordered IDs and digests
    ordered_assignment_ids = tuple(a.assignment_id for a in ordered)
    ordered_result_digests = tuple(
        result_by_id[a_id].result_digest if a_id in result_by_id else ""
        for a_id in ordered_assignment_ids
    )

    # Combined file map: merge in dependency order (later results win)
    combined_file_map: dict[str, str] = {}
    for a in ordered:
        r = result_by_id.get(a.assignment_id)
        if r:
            for artifact in r.changed_artifacts:
                combined_file_map[artifact.path] = artifact.content_digest
    combined_file_map_digest = stable_digest(dict(sorted(combined_file_map.items())))

    # Extra validation evidence
    evidence = tuple(
        ValidationEvidence(
            validator_id=v.get("validator_id", ""),
            passed=bool(v.get("passed", False)),
            detail=v.get("detail"),
        )
        for v in (extra_validation_evidence or [])
    )

    # Promotion readiness
    all_completed = all(
        (
            result_by_id.get(a.assignment_id) is not None
            and result_by_id[a.assignment_id].status == "completed"
        )
        for a in assignments
    )
    promotion_ready = (
        not collision_report.has_collisions and not unresolved and all_completed
    )

    # Integration digest
    digest_data = {
        "plan_id": plan_id,
        "plan_digest": plan_digest,
        "ordered_assignment_ids": list(ordered_assignment_ids),
        "ordered_result_digests": list(ordered_result_digests),
        "combined_file_map_digest": combined_file_map_digest,
        "unresolved_assignments": list(unresolved),
        "promotion_ready": promotion_ready,
    }
    integration_digest = stable_digest(digest_data)

    return IntegrationResult(
        plan_id=plan_id,
        plan_digest=plan_digest,
        ordered_assignment_ids=ordered_assignment_ids,
        ordered_result_digests=ordered_result_digests,
        combined_file_map_digest=combined_file_map_digest,
        dependency_order=ordered_assignment_ids,
        collision_report=collision_report,
        validation_evidence=evidence,
        unresolved_assignments=unresolved,
        promotion_ready=promotion_ready,
        integration_digest=integration_digest,
    )


__all__ = [
    # Constants
    "REGISTERED_ASSIGNMENT_KINDS",
    # Path helpers
    "stable_digest",
    # WorkAssignment
    "WorkAssignment",
    "make_work_assignment",
    "validate_assignment_dag",
    # WorkResult types
    "ArtifactIdentity",
    "WorkDiagnostic",
    "ValidationEvidence",
    "WorkResult",
    "make_work_result",
    # CollisionReport types
    "CollisionEntry",
    "CollisionReport",
    "detect_collisions",
    # IntegrationResult
    "IntegrationResult",
    "build_integration_result",
]
