"""Canonical CompilationPlan authority contract (offline-only).

A ``CompilationPlan`` self-digest proves body integrity: the digest matches
the bytes the caller supplied. It does not prove the body was truthfully
derived. A caller can mutate any derived plan fact — a unit's source
footprint, disposition, output path, validator, an emitted gap — recompute
the self-digests, and obtain a structurally cold-valid plan.

This module defines the two halves of the closing rule:

1. :class:`CompilationPlanAuthorityInputs` — the ONE strict immutable
   authority-input contract: exactly the documents canonical plan derivation
   consumes (semantic graph, complete payload closure, canonical layout
   registry snapshot, scope selection, structured-output configuration
   documents). It is frozen, ``extra="forbid"``, recursively closed over
   typed models and JSON-safe documents, deterministic, and serializable —
   serialization is safe precisely because the object is never trusted by
   possession: every use cold-validates its contents and rederives the plan.

2. :func:`validate_compilation_plan_against_authority` — the one canonical
   validator. It cold-validates the candidate body and every authority
   input, re-derives the canonical plan through the single existing
   :func:`derive_compilation_plan` implementation (no second derivation of
   source footprints, conditions, paths, assignments, or gaps exists here),
   requires exact execution-authorizing equality, and returns the CANONICAL
   REDERIVED plan. There is no proof object, no token, and no bearer
   capability: possession of nothing establishes validity. The returned plan
   is safe because it was rederived during that invocation; callers may use
   it within the current call chain, and durable callers must rederive again
   after restart.

Authority versus diagnostics: every plan unit fact (family, instance, path,
disposition, materializer, source and edge footprints, assignment contract,
validator, dependencies), every emitted gap, and the graph/registry/scope
identity header are execution authority and are compared exactly (the whole
canonical body must match). Latent/composite diagnostics live outside the
plan in ``CompilationGapReport`` and cannot affect rendering, reuse,
composition, persistence, or promotion — they are not re-verified here.

Brownfield boundary, stated explicitly: canonical derivation has no
base/brownfield input today, so plans carrying ``preserve_unowned`` (or any
other non-greenfield-derivable) content cannot be truthfully rederived and
are REJECTED by this validator — fail-closed, never accepted-unverified.
The immutable base-input contract that would make them derivable is a
separate identified prerequisite; no test-only issuance shortcut and no
self-digest-only fallback exists or may be added.

This is deterministic application-compiler integrity (MOZAIKS_OSS). It
carries no AG2 runtime concept, no model/provider identity, no prompt
content, no filesystem paths, and no hosted state.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    CompilationScopeSelection,
    LayoutRegistrySnapshot,
    derive_compilation_plan,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.graph import SemanticGraphV2
from mozaiksai.core.semantics.payloads import (
    SemanticPayload,
    SemanticPayloadBase,
)

AUTHORITY_INPUTS_SCHEMA_VERSION = "mozaiks.compilation_plan_authority_inputs.v1"


class PlanAuthorityMismatch(StrEnum):
    """Finite typed categories of plan-authority failure."""

    PLAN_BODY_INVALID = "plan_body_invalid"
    CANONICAL_DERIVATION_MISMATCH = "canonical_derivation_mismatch"
    REQUIRED_AUTHORITY_MISSING = "required_authority_missing"


class PlanAuthorityError(ValueError):
    """The candidate plan is not the canonical derivation of its authorities.

    Carries deterministic audit identity only — category, plan digest, and
    the first mismatching unit identity where known. Never candidate bodies,
    payload contents, or arbitrary metadata.
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


class CompilationPlanAuthorityInputs(BaseModel):
    """The exact immutable inputs canonical plan derivation consumes.

    One instance carries everything :func:`derive_compilation_plan` needs to
    reproduce a plan: nothing may be silently reconstructed differently at
    validation time. ``structured_output_configs`` holds the exact immutable
    configuration documents (JSON-safe mappings keyed by workflow name) the
    plan was derived with — omitting them for a config-derived plan simply
    yields a canonical mismatch. The execution scope and optional-family
    selection travel inside the graph/payload closure and the scope
    selection; the assignment-contract vocabulary is a closed code-level
    registry pinned by the layout snapshot's row content. There is no
    base/brownfield input field because canonical derivation consumes none —
    see the module docstring for that identified prerequisite.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    authority_schema_version: str = AUTHORITY_INPUTS_SCHEMA_VERSION
    graph: SemanticGraphV2
    payloads: tuple[SemanticPayload, ...]
    registry_snapshot: LayoutRegistrySnapshot
    scope_selection: CompilationScopeSelection = CompilationScopeSelection()
    structured_output_configs: Mapping[str, Any] | None = None

    @field_validator("authority_schema_version")
    @classmethod
    def _schema_version(cls, value: str) -> str:
        if value != AUTHORITY_INPUTS_SCHEMA_VERSION:
            raise ValueError(
                "unsupported authority-inputs schema version "
                f"{value!r}; expected {AUTHORITY_INPUTS_SCHEMA_VERSION!r}"
            )
        return value

    @field_validator("payloads")
    @classmethod
    def _payloads(
        cls, value: tuple[SemanticPayloadBase, ...]
    ) -> tuple[SemanticPayloadBase, ...]:
        if not value:
            raise ValueError("authority inputs require the complete payload closure")
        return value


def build_compilation_plan_authority_inputs(
    *,
    graph: SemanticGraphV2,
    payloads: Any,
    registry: Any,
    scope_selection: CompilationScopeSelection | None = None,
    structured_output_configs: Mapping[str, Any] | None = None,
) -> CompilationPlanAuthorityInputs:
    """Snapshot live authority objects into the immutable input contract.

    ``registry`` may be the live ``AppLayoutRegistry`` or an already-built
    snapshot; the snapshot identity is always recomputed from row content,
    never taken from a claimed digest.
    """
    snapshot = (
        registry
        if isinstance(registry, LayoutRegistrySnapshot)
        else snapshot_layout_registry(registry)
    )
    return CompilationPlanAuthorityInputs(
        graph=graph,
        payloads=tuple(payloads),
        registry_snapshot=snapshot,
        scope_selection=scope_selection or CompilationScopeSelection(),
        structured_output_configs=structured_output_configs,
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
    candidate_plan: CompilationPlan,
    authority_inputs: CompilationPlanAuthorityInputs,
) -> CompilationPlan:
    """Verify a candidate plan is exactly its canonical derivation.

    1. Cold-validate the candidate body (self-digests, structural closure).
    2. Cold-validate every authority input and resolve every reference (the
       authority contract re-parses its own serialized form; graph/payload
       closure and registry identity are re-verified by derivation itself).
    3. Re-derive the canonical plan through the single existing
       :func:`derive_compilation_plan` implementation.
    4. Require exact execution-authorizing equality — any added, missing,
       duplicated, or altered unit; any changed source or edge footprint,
       path, disposition, materializer, condition, validator, assignment
       kind, structured-output ref, or dependency; any removed or fabricated
       emitted gap; any header/scope/registry/graph mutation — rejects.
    5. Return the CANONICAL REDERIVED plan (never the caller's object, never
       a proof token). Its safety is the rederivation that just happened;
       durable callers must rederive again after restart.
    """
    if candidate_plan is None:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            "no candidate CompilationPlan was supplied",
        )
    if authority_inputs is None or not isinstance(
        authority_inputs, CompilationPlanAuthorityInputs
    ):
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            "CompilationPlanAuthorityInputs are required to verify a plan",
            plan_digest=getattr(candidate_plan, "plan_digest", None),
        )
    try:
        verified = CompilationPlan.model_validate(
            candidate_plan.model_dump(mode="json")
        )
    except (TypeError, ValueError) as exc:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.PLAN_BODY_INVALID,
            f"candidate plan failed cold body validation: {exc}",
            plan_digest=getattr(candidate_plan, "plan_digest", None),
        ) from exc
    try:
        verified_inputs = CompilationPlanAuthorityInputs.model_validate(
            authority_inputs.model_dump(mode="json")
        )
    except (TypeError, ValueError) as exc:
        raise PlanAuthorityError(
            PlanAuthorityMismatch.REQUIRED_AUTHORITY_MISSING,
            f"authority inputs failed cold validation: {exc}",
            plan_digest=verified.plan_digest,
        ) from exc
    try:
        canonical = derive_compilation_plan(
            graph=verified_inputs.graph,
            payloads=verified_inputs.payloads,
            registry=verified_inputs.registry_snapshot,
            scope_selection=verified_inputs.scope_selection,
            structured_output_configs=verified_inputs.structured_output_configs,
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
    return canonical


__all__ = [
    "AUTHORITY_INPUTS_SCHEMA_VERSION",
    "CompilationPlanAuthorityInputs",
    "PlanAuthorityError",
    "PlanAuthorityMismatch",
    "build_compilation_plan_authority_inputs",
    "validate_compilation_plan_against_authority",
]
