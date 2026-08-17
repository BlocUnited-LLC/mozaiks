"""Deterministic approved-plan-to-assignment compiler.

Converts a validated, approved implementation plan into an immutable, ordered
set of canonical WorkAssignments before anything is enqueued.

This module owns zero persistence, no queue mutations, no AG2 calls, no
filesystem access, and no external service calls. It is a pure, deterministic
function: same approved plan → same assignments → same digest, every time.

Canonical shared owners reused from PR #280:
- ``AssignmentKind``           — closed taxonomy (assignment_kinds.py)
- ``deterministic_topological_order`` — DAG sort (dependency_graph.py)
- ``detect_owned_path_collisions``    — collision detection (path_ownership.py)
- ``normalize_owned_paths``           — path normalization (path_ownership.py)
- ``make_work_assignment``            — immutable assignment builder (work_contracts.py)
- ``validate_assignment_dag``         — DAG validation (work_contracts.py)
- ``detect_collisions``               — cross-assignment collision detection (work_contracts.py)
- ``stable_digest``                   — deterministic SHA-256 digest (work_contracts.py)

This module does NOT duplicate DAG algorithms, path normalization, collision
detection, assignment-kind taxonomy, or digest logic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .assignment_kinds import registered_assignment_kind_values
from .dependency_graph import deterministic_topological_order
from .path_ownership import normalize_owned_paths
from .work_contracts import (
    WorkAssignment,
    detect_collisions,
    make_work_assignment,
    stable_digest,
    validate_assignment_dag,
)

_REGISTERED_KINDS: frozenset[str] = registered_assignment_kind_values()

# ─────────────────────────────────────────────────────────────────────────────
# Compiler input contract
# ─────────────────────────────────────────────────────────────────────────────


class ApprovedAssignmentSpec(BaseModel):
    """One work assignment declared inside an approved plan.

    ``logical_id`` is a plan-local dependency reference only. It is never
    promoted into the immutable ``WorkAssignment.assignment_id``. The compiler
    derives assignment identity from the approved plan identity plus canonical
    execution semantics so callers cannot select unstable IDs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_id: str = Field(min_length=1)
    assignment_kind: str = Field(min_length=1)
    owned_paths: list[str] = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    dependency_context_refs: list[str] = Field(default_factory=list)
    allowed_agent_ids: list[str] = Field(default_factory=list)
    required_structured_output_id: str | None = None
    required_validators: list[str] = Field(default_factory=list)
    assignment_retry_limit: int = Field(default=0, ge=0, le=5)

    @field_validator("logical_id")
    @classmethod
    def _strip_id(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("logical_id must not be empty or whitespace")
        return stripped

    @field_validator("assignment_kind")
    @classmethod
    def _validate_kind(cls, v: str) -> str:
        stripped = v.strip()
        if stripped not in _REGISTERED_KINDS:
            raise ValueError(
                f"assignment_kind {v!r} is not a registered AssignmentKind. "
                f"Allowed: {sorted(_REGISTERED_KINDS)}"
            )
        return stripped

    @field_validator("assignment_retry_limit", mode="before")
    @classmethod
    def _no_bool_retry(cls, v: Any) -> Any:
        if isinstance(v, bool):
            raise ValueError("assignment_retry_limit must be an int, not bool")
        return v

    @field_validator("owned_paths")
    @classmethod
    def _non_empty_paths(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("owned_paths must not be empty")
        return v


class ApprovedPlan(BaseModel):
    """A validated, approved implementation plan ready for compilation.

    Identity fields (plan_id, plan_digest, baseline_sha) are immutable and
    required. Timestamps and prose are explicitly excluded from digest inputs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str = Field(min_length=1)
    plan_digest: str = Field(min_length=1)
    baseline_sha: str = Field(min_length=1)
    assignments: list[ApprovedAssignmentSpec] = Field(min_length=1)

    @field_validator("plan_id", "plan_digest", "baseline_sha")
    @classmethod
    def _strip_identity(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("identity field must not be empty or whitespace")
        return stripped

    @field_validator("assignments")
    @classmethod
    def _no_duplicate_ids(cls, specs: list[ApprovedAssignmentSpec]) -> list[ApprovedAssignmentSpec]:
        seen: set[str] = set()
        for spec in specs:
            if spec.logical_id in seen:
                raise ValueError(f"duplicate logical_id in approved plan: {spec.logical_id!r}")
            seen.add(spec.logical_id)
        return specs

    @model_validator(mode="after")
    def _all_depends_on_declared(self) -> ApprovedPlan:
        declared = {spec.logical_id for spec in self.assignments}
        for spec in self.assignments:
            missing = [dep for dep in spec.depends_on if dep not in declared]
            if missing:
                raise ValueError(
                    f"assignment {spec.logical_id!r} depends on undeclared logical IDs: {missing}"
                )
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Compiler output contract
# ─────────────────────────────────────────────────────────────────────────────


class CompiledAssignmentSet(BaseModel):
    """Immutable, enqueue-ready result produced by compile_approved_plan().

    ``assignment_set_digest`` covers all execution-relevant fields (plan
    identity, baseline SHA, all assignment digests in dependency order).
    Timestamps and prose are excluded from the digest.

    This is NOT an integration result — it carries no WorkResults. It is the
    precursor used to safely enqueue work without yet executing it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_id: str
    plan_digest: str
    baseline_sha: str
    ordered_assignments: tuple[WorkAssignment, ...]
    assignment_set_digest: str

    @property
    def assignment_count(self) -> int:
        return len(self.ordered_assignments)

    @property
    def assignment_ids_in_order(self) -> tuple[str, ...]:
        return tuple(a.assignment_id for a in self.ordered_assignments)

    @property
    def assignment_by_id(self) -> dict[str, WorkAssignment]:
        return {a.assignment_id: a for a in self.ordered_assignments}

    @model_validator(mode="after")
    def _validate_digest(self) -> CompiledAssignmentSet:
        expected = _assignment_set_digest(
            plan_id=self.plan_id,
            plan_digest=self.plan_digest,
            baseline_sha=self.baseline_sha,
            ordered=self.ordered_assignments,
        )
        if self.assignment_set_digest != expected:
            raise ValueError("assignment_set_digest does not match compiled assignment fields")
        return self


# ─────────────────────────────────────────────────────────────────────────────
# Compiler
# ─────────────────────────────────────────────────────────────────────────────


def compile_approved_plan(plan: ApprovedPlan) -> CompiledAssignmentSet:
    """Convert an approved plan into an immutable, ordered set of WorkAssignments.

    This function is deterministic and side-effect-free:
    - identical inputs → identical assignments, identical order, identical digest
    - input order of specs does not affect output order (DAG sorts canonically)
    - assignment IDs are derived, never caller-selected
    - timestamps and prose are excluded from all digests
    - no enqueuing, no AG2, no filesystem, no network

    Rejects:
    - unknown assignment kinds (validated at ApprovedPlan parse time)
    - duplicate logical IDs (validated at ApprovedPlan parse time)
    - missing dependency references (validated at ApprovedPlan parse time)
    - dependency cycles (detected via deterministic_topological_order / validate_assignment_dag)
    - direct path collisions (via detect_collisions)
    - parent/child path collisions (via detect_collisions)
    - case-normalization collisions (via detect_collisions)
    - unsafe paths — absolute, traversal, glob, secret-term (via make_work_assignment → normalize_owned_paths)

    Raises:
        ValueError: on any structural, identity, or collision violation.
        pydantic.ValidationError: if plan input fails schema validation.
    """
    spec_ordering = deterministic_topological_order(
        plan.assignments,
        item_id=lambda spec: spec.logical_id,
        dependencies=lambda spec: spec.depends_on,
    )
    spec_by_logical_id = {spec.logical_id: spec for spec in plan.assignments}

    # Build immutable WorkAssignments from approved specs in dependency order.
    # make_work_assignment remains the canonical owner for WorkAssignment
    # validation, assignment digests, path normalization, and kind validation.
    assignments: list[WorkAssignment] = []
    assignment_id_by_logical_id: dict[str, str] = {}
    for logical_id in spec_ordering.ordered_ids:
        spec = spec_by_logical_id[logical_id]
        dependency_assignment_ids = sorted(assignment_id_by_logical_id[dep] for dep in spec.depends_on)
        assignment_id = _derived_assignment_id(
            plan=plan,
            spec=spec,
            dependency_assignment_ids=dependency_assignment_ids,
        )
        assignment = make_work_assignment(
            assignment_id=assignment_id,
            plan_id=plan.plan_id,
            plan_digest=plan.plan_digest,
            baseline_sha=plan.baseline_sha,
            assignment_kind=spec.assignment_kind,
            owned_paths=list(spec.owned_paths),
            dependency_context_refs=list(spec.dependency_context_refs),
            allowed_agent_ids=list(spec.allowed_agent_ids),
            required_structured_output_id=spec.required_structured_output_id,
            depends_on=dependency_assignment_ids,
            required_validators=list(spec.required_validators),
            assignment_retry_limit=spec.assignment_retry_limit,
        )
        if assignment.assignment_id in {item.assignment_id for item in assignments}:
            raise ValueError(
                "Approved plan contains path ownership collisions or duplicate derived "
                f"assignment identity: {assignment.assignment_id!r}"
            )
        assignments.append(assignment)
        assignment_id_by_logical_id[logical_id] = assignment.assignment_id

    # Validate DAG (cycles, missing dependencies) via shared owner.
    # This raises ValueError on any cycle or missing dep.
    validate_assignment_dag(assignments)

    # Detect all path ownership collisions via shared owner.
    # No downstream-writer-wins: any collision is a hard rejection.
    collision_report = detect_collisions(assignments)
    if collision_report.has_collisions:
        raise ValueError(
            "Approved plan contains path ownership collisions: "
            + "; ".join(f"{c.kind}:{c.path}:{c.assignment_ids}" for c in collision_report.collisions)
        )

    # Produce dependency-first canonical order via shared owner (already
    # validated above, but we re-derive the order from the built assignments).
    ordering = deterministic_topological_order(
        assignments,
        item_id=lambda a: a.assignment_id,
        dependencies=lambda a: a.depends_on,
    )
    by_id = {a.assignment_id: a for a in assignments}
    ordered = tuple(by_id[aid] for aid in ordering.ordered_ids)

    # Produce the assignment-set digest: covers plan identity + baseline SHA +
    # every assignment digest in dependency order. Excludes timestamps and prose.
    digest = _assignment_set_digest(plan_id=plan.plan_id, plan_digest=plan.plan_digest, baseline_sha=plan.baseline_sha, ordered=ordered)

    return CompiledAssignmentSet(
        plan_id=plan.plan_id,
        plan_digest=plan.plan_digest,
        baseline_sha=plan.baseline_sha,
        ordered_assignments=ordered,
        assignment_set_digest=digest,
    )


def _derived_assignment_id(
    *,
    plan: ApprovedPlan,
    spec: ApprovedAssignmentSpec,
    dependency_assignment_ids: list[str],
) -> str:
    """Derive immutable assignment identity from plan identity and semantics."""
    payload = {
        "plan_id": plan.plan_id.strip(),
        "plan_digest": plan.plan_digest.strip(),
        "baseline_sha": plan.baseline_sha.strip(),
        **_assignment_semantics_payload(
            spec=spec,
            dependency_assignment_ids=dependency_assignment_ids,
        ),
    }
    return f"wa_{stable_digest(payload)[:24]}"


def _assignment_semantics_payload(
    *,
    spec: ApprovedAssignmentSpec,
    dependency_assignment_ids: list[str],
) -> dict[str, Any]:
    """Execution-relevant assignment semantics used for derived identity."""
    return {
        "assignment_kind": spec.assignment_kind.strip(),
        "owned_paths": list(sorted(normalize_owned_paths(spec.owned_paths, reject_duplicates=True))),
        "dependency_context_refs": _normalized_text_list(spec.dependency_context_refs),
        "allowed_agent_ids": _normalized_text_list(spec.allowed_agent_ids),
        "required_structured_output_id": (
            spec.required_structured_output_id.strip()
            if isinstance(spec.required_structured_output_id, str)
            and spec.required_structured_output_id.strip()
            else None
        ),
        "depends_on_assignment_ids": sorted(str(item).strip() for item in dependency_assignment_ids if str(item).strip()),
        "required_validators": _normalized_text_list(spec.required_validators),
        "assignment_retry_limit": spec.assignment_retry_limit,
    }


def _normalized_text_list(value: list[str]) -> list[str]:
    return sorted({str(item or "").strip() for item in value if str(item or "").strip()})


def _assignment_set_digest(
    *,
    plan_id: str,
    plan_digest: str,
    baseline_sha: str,
    ordered: tuple[WorkAssignment, ...],
) -> str:
    """Deterministic digest over plan identity + ordered assignment digests.

    Excludes all timestamps, prose, optional annotation fields. Only
    execution-relevant canonical identity fields are covered.
    """
    payload = {
        "plan_id": plan_id.strip(),
        "plan_digest": plan_digest.strip(),
        "baseline_sha": baseline_sha.strip(),
        "assignment_digests_in_order": [a.assignment_digest for a in ordered],
    }
    return stable_digest(payload)


__all__ = [
    "ApprovedAssignmentSpec",
    "ApprovedPlan",
    "CompiledAssignmentSet",
    "compile_approved_plan",
]
