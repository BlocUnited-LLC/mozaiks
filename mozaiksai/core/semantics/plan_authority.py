"""Canonical-authority validation for CompilationPlans (offline-only).

A ``CompilationPlan`` self-digest proves body integrity: the digest matches
the bytes the caller supplied. It does not prove the body was truthfully
derived. A caller can mutate any derived plan fact — a unit's source
footprint, disposition, output path, validator, an emitted gap — recompute
the self-digest, and obtain a structurally cold-valid plan.

This module establishes the one canonical validation rule closing that trust
boundary: before a plan may authorize materialization, rematerialization,
historical-output reuse, or composition, the candidate is re-derived from its
exact immutable authorities — the semantic graph, the complete payload
closure, the canonical layout registry, and the same optional derivation
inputs (scope selection, structured-output configs) — through the ONE
existing canonical derivation function, and compared for exact canonical
equality. There is no second derivation implementation here and no partial
field-by-field re-derivation: every execution-authorizing fact (units, source
footprints, paths, dispositions, materializers, conditions, validators,
assignment contracts, structured-output refs, dependencies, emitted gaps) is
covered because the whole plan must match.

Diagnostics versus authority: ``CompilationPlan.gaps`` is the literal emitted
first-blocker set and IS execution authority — it is part of the compared
canonical body. Latent/composite diagnostics live outside the plan (in
``CompilationGapReport``) and never enter materialization or reuse, so they
are not re-verified here.

The returned :class:`PlanAuthorityProof` is a bounded in-process attestation
that one exact plan digest was verified against one exact graph/registry
identity. It carries no mutable state and no global cache; downstream offline
boundaries that cannot hold the full authorities (5B composition, whose
preserve-unowned inputs are not greenfield-derivable until the brownfield
base-input contract exists) may accept it in place of re-deriving.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    CompilationScopeSelection,
    derive_compilation_plan,
)
from mozaiksai.core.semantics.graph import SemanticGraphV2


class PlanAuthorityMismatch(StrEnum):
    """Finite typed categories of plan-authority failure."""

    PLAN_BODY_INVALID = "plan_body_invalid"
    CANONICAL_DERIVATION_MISMATCH = "canonical_derivation_mismatch"
    REQUIRED_AUTHORITY_MISSING = "required_authority_missing"


class PlanAuthorityError(ValueError):
    """The candidate plan is not the canonical derivation of its authorities.

    Carries deterministic audit identity only — category, plan digest, and
    the first mismatching unit/gap identity where known. Never candidate
    bodies, payload contents, or arbitrary metadata.
    """

    def __init__(
        self,
        category: PlanAuthorityMismatch,
        message: str,
        *,
        plan_digest: str | None = None,
        unit_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.plan_digest = plan_digest
        self.unit_id = unit_id


@dataclass(frozen=True)
class PlanAuthorityProof:
    """Attestation that one exact plan was verified against exact authorities.

    Produced only by :func:`validate_compilation_plan_against_authority`.
    Immutable, scope-free of payload content, and valid only for the exact
    plan digest it names — there is no ambient trusted-plan set and no TTL.
    """

    plan_digest: str
    graph_digest: str
    registry_digest: str

    def covers(self, plan: CompilationPlan) -> bool:
        return (
            self.plan_digest == plan.plan_digest
            and self.graph_digest == plan.graph_digest
            and self.registry_digest == plan.registry_digest
        )


def _first_difference(
    candidate: CompilationPlan, canonical: CompilationPlan
) -> tuple[str, str | None]:
    """Locate the first authority difference for the audit identity."""
    candidate_units = {unit.unit_id: unit for unit in candidate.units}
    canonical_units = {unit.unit_id: unit for unit in canonical.units}
    for unit_id in candidate_units.keys() - canonical_units.keys():
        return ("unit not present in canonical derivation", unit_id)
    for unit_id in canonical_units.keys() - candidate_units.keys():
        return ("canonical unit missing from candidate", unit_id)
    for unit_id, unit in candidate_units.items():
        if unit.unit_digest != canonical_units[unit_id].unit_digest:
            return ("unit body differs from canonical derivation", unit_id)
    if len(candidate.units) != len(canonical.units):
        return ("duplicate unit identity in candidate", None)
    if [u.unit_id for u in candidate.units] != [u.unit_id for u in canonical.units]:
        return ("unit ordering differs from canonical derivation", None)
    candidate_gaps = [gap.model_dump(mode="json") for gap in candidate.gaps]
    canonical_gaps = [gap.model_dump(mode="json") for gap in canonical.gaps]
    if candidate_gaps != canonical_gaps:
        return ("emitted gap set differs from canonical derivation", None)
    return ("plan header differs from canonical derivation", None)


def validate_compilation_plan_against_authority(
    candidate: CompilationPlan,
    *,
    graph: SemanticGraphV2,
    payloads: Any,
    registry: Any,
    scope_selection: CompilationScopeSelection | None = None,
    structured_output_configs: Mapping[str, Any] | None = None,
) -> PlanAuthorityProof:
    """Verify a candidate plan is exactly its canonical derivation.

    1. Cold-validate the candidate body (self-digest and structural closure).
    2. Re-derive the canonical plan from the supplied immutable authorities
       through :func:`derive_compilation_plan` — the single derivation
       implementation; nothing is re-implemented here.
    3. Require exact canonical equality. Any difference — an added, missing,
       duplicated, or altered unit; a changed source footprint, path,
       disposition, materializer, condition, validator, assignment kind,
       structured-output ref, or dependency; a removed or fabricated emitted
       gap; a header mutation — rejects before any output or historical
       reuse is consulted.

    The same optional derivation inputs the plan was created with must be
    supplied (``scope_selection``, ``structured_output_configs``); they are
    part of the plan's authority, and omitting them simply yields a canonical
    mismatch for plans that required them.
    """
    if candidate is None:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            "no candidate CompilationPlan was supplied",
        )
    if graph is None or registry is None:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            "graph and registry authorities are required to verify a plan",
            plan_digest=candidate.plan_digest,
        )
    try:
        verified = CompilationPlan.model_validate(candidate.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.PLAN_BODY_INVALID,
            f"candidate plan failed cold body validation: {exc}",
            plan_digest=getattr(candidate, "plan_digest", None),
        ) from exc
    try:
        canonical = derive_compilation_plan(
            graph=graph,
            payloads=payloads,
            registry=registry,
            scope_selection=scope_selection or CompilationScopeSelection(),
            structured_output_configs=structured_output_configs,
        )
    except (TypeError, ValueError) as exc:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            f"canonical derivation failed for the supplied authorities: {exc}",
            plan_digest=verified.plan_digest,
        ) from exc
    if (
        verified.plan_digest != canonical.plan_digest
        or verified.canonical_payload() != canonical.canonical_payload()
    ):
        detail, unit_id = _first_difference(verified, canonical)
        raise PlanAuthorityError(
            PlanAuthorityMismatch.CANONICAL_DERIVATION_MISMATCH,
            "candidate plan is not the canonical derivation of its "
            f"authorities: {detail} "
            f"(candidate {verified.plan_digest[:12]}, "
            f"canonical {canonical.plan_digest[:12]})",
            plan_digest=verified.plan_digest,
            unit_id=unit_id,
        )
    return PlanAuthorityProof(
        plan_digest=canonical.plan_digest,
        graph_digest=canonical.graph_digest,
        registry_digest=canonical.registry_digest,
    )


__all__ = [
    "PlanAuthorityError",
    "PlanAuthorityMismatch",
    "PlanAuthorityProof",
    "validate_compilation_plan_against_authority",
]
