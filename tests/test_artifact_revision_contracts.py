from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from mozaiksai.core.semantics.artifact_revision import (
    ApplicationPublication,
    ArtifactRevision,
    ArtifactRevisionValidationEvidence,
    build_artifact_revision,
    validate_artifact_revision_validation_evidence,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.refs import ArtifactRevisionRef, ExecutionAccessScopeRef
from mozaiksai.core.semantics.resolver import (
    ReferenceResolutionError,
    SemanticReferenceResolver,
)
from tests.slice_5c_revision_helpers import revision_fixture


def test_revision_contract_is_closed_required_nullable_and_deterministic() -> None:
    fixture = revision_fixture()
    revision = fixture["revision"]
    assert ArtifactRevision.model_validate(revision.model_dump(mode="json")) == revision
    assert revision.parent_revision_ref is None
    assert set(ArtifactRevision.model_fields) == {
        "revision_schema_version",
        "scope",
        "app_id",
        "parent_revision_ref",
        "semantic_graph_ref",
        "implementation_binding_ref",
        "compilation_plan_ref",
        "composition_ledger_digest",
        "bundle_digest",
        "validation_evidence_digest",
        "revision_digest",
    }
    duplicate = build_artifact_revision(
        **revision.model_dump(mode="python", exclude={"revision_schema_version", "revision_digest"})
    )
    assert duplicate == revision

    missing_parent = revision.model_dump(mode="json")
    del missing_parent["parent_revision_ref"]
    with pytest.raises(ValidationError):
        ArtifactRevision.model_validate(missing_parent)


@pytest.mark.parametrize(
    "field",
    [
        "composition_ledger_digest",
        "bundle_digest",
        "validation_evidence_digest",
    ],
)
def test_every_authoritative_revision_digest_changes_identity(field: str) -> None:
    revision = revision_fixture()["revision"]
    arguments = revision.model_dump(
        mode="python", exclude={"revision_schema_version", "revision_digest"}
    )
    arguments[field] = "f" * 64
    changed = build_artifact_revision(**arguments)
    assert changed.revision_digest != revision.revision_digest


def test_parent_and_scope_are_identity_and_authorization() -> None:
    revision = revision_fixture()["revision"]
    parent = ArtifactRevisionRef(
        scope=revision.scope,
        app_id=revision.app_id,
        revision_digest="a" * 64,
    )
    arguments = revision.model_dump(
        mode="python", exclude={"revision_schema_version", "revision_digest"}
    )
    arguments["parent_revision_ref"] = parent
    child = build_artifact_revision(**arguments)
    assert child.revision_digest != revision.revision_digest

    foreign = ExecutionAccessScopeRef(tenant_id="foreign-tenant")
    arguments["parent_revision_ref"] = ArtifactRevisionRef(
        scope=foreign, app_id=revision.app_id, revision_digest="b" * 64
    )
    with pytest.raises(ValidationError, match="same scope"):
        build_artifact_revision(**arguments)

    arguments["parent_revision_ref"] = ArtifactRevisionRef(
        scope=revision.scope, app_id="another-app", revision_digest="b" * 64
    )
    with pytest.raises(ValidationError, match="same scope and app"):
        build_artifact_revision(**arguments)


def test_validation_evidence_cold_closure_and_tampering() -> None:
    fixture = revision_fixture()
    evidence = validate_artifact_revision_validation_evidence(
        evidence=fixture["evidence"],
        plan=fixture["plan"],
        ledger=fixture["bundle"].ledger,
        assignment_results=(),
    )
    assert evidence == fixture["evidence"]

    stale = evidence.model_dump(mode="json")
    stale["bundle_validator_receipts"][0]["subject_digest"] = "e" * 64
    with pytest.raises(ValidationError, match="receipt|evidence_digest"):
        ArtifactRevisionValidationEvidence.model_validate(stale)

    duplicate = evidence.model_dump(mode="json")
    duplicate["bundle_validator_receipts"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        ArtifactRevisionValidationEvidence.model_validate(duplicate)

    forged_result_set = evidence.model_dump(mode="json")
    forged_result_set["assignment_result_digests"] = ["f" * 64]
    forged_result_set["evidence_digest"] = canonical_digest(
        {
            key: value
            for key, value in forged_result_set.items()
            if key != "evidence_digest"
        }
    )
    forged = ArtifactRevisionValidationEvidence.model_validate(forged_result_set)
    with pytest.raises(ValueError, match="assignment_result_digests"):
        validate_artifact_revision_validation_evidence(
            evidence=forged,
            plan=fixture["plan"],
            ledger=fixture["bundle"].ledger,
            assignment_results=(),
        )


def test_publication_is_only_mutable_current_authority() -> None:
    revision = revision_fixture()["revision"]
    publication = ApplicationPublication(
        scope=revision.scope,
        app_id=revision.app_id,
        current_revision_ref=revision.ref,
        generation=1,
    )
    assert set(ApplicationPublication.model_fields) == {
        "publication_schema_version",
        "scope",
        "app_id",
        "current_revision_ref",
        "generation",
    }
    with pytest.raises(ValidationError):
        publication.generation = 2

    document = copy.deepcopy(publication.model_dump(mode="json"))
    del document["current_revision_ref"]
    with pytest.raises(ValidationError):
        ApplicationPublication.model_validate(document)


def test_artifact_revision_ref_resolves_content_not_opaque_placeholder() -> None:
    revision = revision_fixture()["revision"]
    resolver = SemanticReferenceResolver()
    resolver.register_artifact_revision(revision)
    assert (
        resolver.resolve_artifact_revision(revision.ref, requesting_scope=revision.scope)
        == revision
    )
    with pytest.raises(ReferenceResolutionError, match="cross-scope"):
        resolver.resolve_artifact_revision(
            revision.ref,
            requesting_scope=ExecutionAccessScopeRef(tenant_id="foreign-tenant"),
        )
    forged = ArtifactRevisionRef(
        scope=revision.scope,
        app_id=revision.app_id,
        revision_digest="f" * 64,
    )
    with pytest.raises(ReferenceResolutionError, match="exactly"):
        resolver.resolve_artifact_revision(forged, requesting_scope=revision.scope)
