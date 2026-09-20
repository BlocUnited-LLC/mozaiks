"""Shared 5C revision fixtures built on canonically derived plans.

Every revision here pins its exact plan-authority document
(``compilation_plan_authority_ref``) and flows through the canonical
validation chain: derived plan -> authority validation -> composition ->
evidence -> revision -> persistence. No synthetic plans, no bearer trust.
"""

from __future__ import annotations

import hashlib

from mozaiksai.core.semantics.artifact_revision import (
    ValidatorReceipt,
    build_artifact_revision,
    build_artifact_revision_validation_evidence,
)
from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts
from mozaiksai.core.semantics.plan_authority import (
    compilation_plan_authority_ref,
)
from mozaiksai.core.semantics.refs import (
    CompilationPlanRef,
    ImplementationBindingRef,
    SemanticGraphRef,
)
from tests.slice_5b_composition_helpers import (
    _binding,
    composition_fixture,
)


def _receipts(plan, ledger) -> tuple[ValidatorReceipt, ...]:
    from mozaiksai.core.runtime.app.layout_registry import ValidatorIdentifier
    from mozaiksai.core.workflow.structured_output_contracts import stable_digest

    validators = sorted(
        {
            unit.validator
            for unit in plan.units
            if unit.validator is not ValidatorIdentifier.NONE
        },
        key=lambda item: item.value,
    )
    return tuple(
        ValidatorReceipt(
            validator=validator,
            subject_digest=ledger.bundle_digest,
            passed=True,
            evidence_digest=stable_digest(
                {
                    "validator": validator.value,
                    "subject_digest": ledger.bundle_digest,
                    "passed": True,
                }
            ),
        )
        for validator in validators
    )


def revision_fixture() -> dict[str, object]:
    """Canonical genesis revision closure over the derived successor plan."""
    source = composition_fixture()
    plan = source["successor"]
    graph = source["graph"]
    authority_inputs = source["authority_inputs"]
    resolver = source["resolver"]

    binding = _binding(graph)
    resolver.register_implementation_binding(binding)

    composed = compose_plan_artifacts(
        plan=plan,
        authority_inputs=authority_inputs,
        resolver=resolver,
        assignments=source["assignments"],
        assignment_results=(source["result"],),
        materialized_bundle=source["materialized"],
        base_revision_digest=None,
    )
    ledger = composed.ledger

    evidence = build_artifact_revision_validation_evidence(
        scope=plan.scope,
        app_id=plan.graph_id,
        plan=plan,
        authority_inputs=authority_inputs,
        ledger=ledger,
        assignment_results=(source["result"],),
        bundle_validator_receipts=_receipts(plan, ledger),
    )
    authority_ref = compilation_plan_authority_ref(authority_inputs)
    revision = build_artifact_revision(
        scope=plan.scope,
        app_id=plan.graph_id,
        parent_revision_ref=None,
        semantic_graph_ref=SemanticGraphRef(
            subject_id=graph.graph_id,
            subject_version=graph.version,
            content_digest=graph.graph_digest,
            scope=graph.scope,
        ),
        implementation_binding_ref=ImplementationBindingRef(
            subject_id=binding.binding_id,
            subject_version=binding.version,
            content_digest=binding.binding_digest,
            scope=binding.scope,
        ),
        compilation_plan_ref=CompilationPlanRef(
            subject_id=plan.graph_id,
            subject_version=plan.graph_version,
            content_digest=plan.plan_digest,
            scope=plan.scope,
        ),
        compilation_plan_authority_ref=authority_ref,
        composition_ledger_digest=ledger.ledger_digest,
        bundle_digest=ledger.bundle_digest,
        validation_evidence_digest=evidence.evidence_digest,
    )
    return {
        **source,
        "plan": plan,
        "binding": binding,
        "bundle": composed,
        "ledger": ledger,
        "evidence": evidence,
        "revision": revision,
        "authority_ref": authority_ref,
        "assignment_results": (source["result"],),
        "app_id": plan.graph_id,
        "receipts": _receipts(plan, ledger),
    }


def executable_revision_fixture() -> dict[str, object]:
    """Alias retained for suites exercising the agent-authored closure —
    the canonical fixture already carries an executable assignment."""
    return dict(revision_fixture())


def content_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
