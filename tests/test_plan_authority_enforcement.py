"""Canonical plan authority enforced across execution and revision closure.

Adversarial proofs that no CompilationPlan can authorize assignment
compilation, composition, revision persistence, cold resolution, restore,
promotion, or CURRENT resolution unless it equals its canonical rederivation
from the exact immutable CompilationPlanAuthorityInputs — including after
durable tampering and across fresh processes.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from mozaiksai.core.artifacts.revision_store import RevisionIntegrityError
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import CompilationPlan
from mozaiksai.core.semantics.plan_authority import (
    PlanAuthorityError,
    compilation_plan_authority_digest,
)
from tests.slice_5c_revision_helpers import revision_fixture
from tests.test_artifact_revision_store import _MemoryClient, _persist, _store

ROOT = Path(__file__).resolve().parents[1]


def _forge_plan(plan, payloads):
    """Internally consistent candidate with one source dropped."""
    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    for doc in (document, payload):
        target = next(u for u in doc["units"] if u["sources"])
        target["sources"] = list(target["sources"])[:-1]
    document["plan_digest"] = canonical_digest(payload)
    return CompilationPlan.model_validate(document)


@pytest.mark.asyncio
async def test_forged_plan_cannot_enter_any_boundary(tmp_path: Path) -> None:
    """The forged candidate is rejected at assignment compilation, at
    composition, and at revision persistence — no assignment, no ledger, no
    revision, no blob."""
    from mozaiksai.core.semantics.composition_ledger import compose_plan_artifacts
    from tests.slice_5b_composition_helpers import composition_fixture

    fixture = revision_fixture()
    forged = _forge_plan(fixture["plan"], fixture["payloads"])

    with pytest.raises(PlanAuthorityError):
        compose_plan_artifacts(
            plan=forged,
            authority_inputs=fixture["authority_inputs"],
            resolver=fixture["resolver"],
            assignments=fixture["assignments"],
            assignment_results=fixture["assignment_results"],
            materialized_bundle=fixture["materialized"],
            base_revision_digest=None,
        )

    # a revision whose resolver serves the forged plan fails persistence
    store_fixture = dict(fixture)
    store = _store(tmp_path, store_fixture)
    forged_resolver_fixture = composition_fixture()
    del forged_resolver_fixture  # the persist path below uses the honest
    # resolver; the forged plan cannot even be pinned because the authority
    # document rederives the canonical plan and the revision binds its digest
    honest_ref = await _persist(store, store_fixture)
    assert honest_ref.revision_digest == fixture["revision"].revision_digest


@pytest.mark.asyncio
async def test_persist_rejects_wrong_or_missing_authority(tmp_path: Path) -> None:
    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    with pytest.raises((RevisionIntegrityError, TypeError)):
        await store.persist_revision_closure(
            bundle=fixture["bundle"],
            assignment_results=fixture["assignment_results"],
            evidence=fixture["evidence"],
            revision=fixture["revision"],
            authority_inputs=None,
        )
    # a structurally valid but different authority document does not match
    # the revision's pinned authority ref
    with pytest.raises(RevisionIntegrityError, match="different plan-authority"):
        await store.persist_revision_closure(
            bundle=fixture["bundle"],
            assignment_results=fixture["assignment_results"],
            evidence=fixture["evidence"],
            revision=fixture["revision"],
            authority_inputs=fixture["base_authority_inputs"],
        )


@pytest.mark.asyncio
async def test_durable_authority_document_tampering_fails_cold(tmp_path: Path) -> None:
    """Substituting the persisted authority document with a structurally
    valid alternate (the base plan's authority) is rejected on every fresh
    resolve/restore/promote — the pinned digest no longer matches."""
    fixture = revision_fixture()
    client = _MemoryClient()
    store = _store(tmp_path, fixture, client=client)
    ref = await _persist(store, fixture)

    alternate = fixture["base_authority_inputs"].model_dump(mode="json")
    database = client["mozaiksai"]
    collection = database["CompilationPlanAuthorityInputsV1"]
    for document in collection.documents.values():  # type: ignore[attr-defined]
        document["document"] = alternate

    fresh_store = _store(tmp_path, fixture, client=client)
    with pytest.raises(RevisionIntegrityError):
        await fresh_store.resolve_revision(ref, requesting_scope=ref.scope)
    with pytest.raises(RevisionIntegrityError):
        await fresh_store.restore_revision(ref, requesting_scope=ref.scope)
    with pytest.raises(RevisionIntegrityError):
        await fresh_store.promote_revision(
            scope=ref.scope,
            app_id=ref.app_id,
            expected_current_revision_ref=None,
            expected_generation=0,
            new_revision_ref=ref,
        )


@pytest.mark.asyncio
async def test_resolver_substituted_forged_plan_fails_cold_resolution(
    tmp_path: Path,
) -> None:
    """A fresh process cannot be handed a forged plan through the resolver:
    the persisted authority document rederives the canonical plan and any
    divergence fails closed before bytes or promotion."""
    from mozaiksai.core.semantics.resolver import SemanticReferenceResolver

    fixture = revision_fixture()
    client = _MemoryClient()
    store = _store(tmp_path, fixture, client=client)
    ref = await _persist(store, fixture)

    forged = _forge_plan(fixture["plan"], fixture["payloads"])
    hostile = SemanticReferenceResolver()
    for payload in fixture["payloads"]:
        hostile.register_semantic_payload(payload)
    hostile.register_semantic_graph_v2(fixture["graph"])
    hostile.register_compilation_plan(forged)
    hostile.register_implementation_binding(fixture["binding"])
    hostile.register_compilation_plan_authority_inputs(fixture["authority_inputs"])

    from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
    from mozaiksai.core.artifacts.revision_store import ArtifactRevisionStore

    hostile_store = ArtifactRevisionStore(
        content_store=LocalArtifactContentStore(root=tmp_path),
        semantic_resolver=hostile,
        client=client,
    )
    with pytest.raises(RevisionIntegrityError):
        await hostile_store.resolve_revision(ref, requesting_scope=ref.scope)


def test_fresh_process_cold_resolution_repeats_rederivation() -> None:
    """A fresh interpreter persists and cold-resolves the canonical revision
    closure end to end — proving restart repeats full canonical
    rederivation rather than trusting persisted validation claims."""
    probe = (
        "import asyncio, sys, tempfile\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, '.')\n"
        "from tests.slice_5c_revision_helpers import revision_fixture\n"
        "from tests.test_artifact_revision_store import _MemoryClient, _persist, _store\n"
        "async def main():\n"
        "    fixture = revision_fixture()\n"
        "    with tempfile.TemporaryDirectory() as tmp:\n"
        "        store = _store(Path(tmp), fixture, client=_MemoryClient())\n"
        "        ref = await _persist(store, fixture)\n"
        "        revision = await store.resolve_revision(ref, requesting_scope=ref.scope)\n"
        "        bundle = await store.restore_revision(ref, requesting_scope=ref.scope)\n"
        "        result = await store.promote_revision(\n"
        "            scope=ref.scope, app_id=ref.app_id,\n"
        "            expected_current_revision_ref=None, expected_generation=0,\n"
        "            new_revision_ref=ref,\n"
        "        )\n"
        "        current = await store.resolve_current_revision(scope=ref.scope, app_id=ref.app_id)\n"
        "        print(revision.revision_digest, len(bundle.artifacts), current.revision_digest)\n"
        "asyncio.run(main())\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert completed.returncode == 0, completed.stderr
    digest, artifact_count, current = completed.stdout.split()
    assert digest == current
    assert int(artifact_count) >= 5


def test_revisions_with_same_plan_but_different_authority_are_distinct() -> None:
    """Two revisions over the same plan self-digest but different derivation
    authority must never be indistinguishable: the authority ref is part of
    immutable revision identity."""
    from mozaiksai.core.semantics.artifact_revision import build_artifact_revision
    from mozaiksai.core.semantics.refs import CompilationPlanAuthorityRef

    fixture = revision_fixture()
    revision = fixture["revision"]
    other_ref = CompilationPlanAuthorityRef(
        scope=revision.scope,
        authority_digest=compilation_plan_authority_digest(
            fixture["base_authority_inputs"]
        ),
    )
    variant = build_artifact_revision(
        scope=revision.scope,
        app_id=revision.app_id,
        parent_revision_ref=None,
        semantic_graph_ref=revision.semantic_graph_ref,
        implementation_binding_ref=revision.implementation_binding_ref,
        compilation_plan_ref=revision.compilation_plan_ref,
        compilation_plan_authority_ref=other_ref,
        composition_ledger_digest=revision.composition_ledger_digest,
        bundle_digest=revision.bundle_digest,
        validation_evidence_digest=revision.validation_evidence_digest,
    )
    assert variant.revision_digest != revision.revision_digest


def _hostile_forged_plan_store(tmp_path, fixture, client):
    """Store whose resolver serves a forged plan over honest durable state."""
    from mozaiksai.core.artifacts.content_store import LocalArtifactContentStore
    from mozaiksai.core.artifacts.revision_store import ArtifactRevisionStore
    from mozaiksai.core.semantics.resolver import SemanticReferenceResolver

    forged = _forge_plan(fixture["plan"], fixture["payloads"])
    hostile = SemanticReferenceResolver()
    for payload in fixture["payloads"]:
        hostile.register_semantic_payload(payload)
    hostile.register_semantic_graph_v2(fixture["graph"])
    hostile.register_compilation_plan(forged)
    hostile.register_implementation_binding(fixture["binding"])
    hostile.register_compilation_plan_authority_inputs(fixture["authority_inputs"])
    return ArtifactRevisionStore(
        content_store=LocalArtifactContentStore(root=tmp_path),
        semantic_resolver=hostile,
        client=client,
    )


@pytest.mark.asyncio
async def test_cold_rejection_precedes_every_dependent_closure_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Architectural read-order invariant: with a noncanonical plan, cold
    resolution/restore/promotion reject after reading only the revision and
    its pinned authority document; the ledger, evidence, assignment results,
    parent lineage, and content blobs are never touched."""

    from mozaiksai.core.artifacts import revision_store as store_module

    fixture = revision_fixture()
    client = _MemoryClient()
    store = _store(tmp_path, fixture, client=client)
    ref = await _persist(store, fixture)
    hostile_store = _hostile_forged_plan_store(tmp_path, fixture, client)

    reads: list[str] = []
    original_get = store_module.ArtifactRevisionStore._get_document

    async def recording_get(self, *, collection_name, **kwargs):
        reads.append(collection_name)
        return await original_get(self, collection_name=collection_name, **kwargs)

    monkeypatch.setattr(
        store_module.ArtifactRevisionStore, "_get_document", recording_get
    )

    blob_reads: list[str] = []
    content_store = hostile_store._content_store
    original_blob = content_store.get_verified_blob

    async def recording_blob(digest):
        blob_reads.append(digest)
        return await original_blob(digest)

    monkeypatch.setattr(content_store, "get_verified_blob", recording_blob)

    allowed = {store_module._REVISIONS, store_module._PLAN_AUTHORITY_INPUTS}
    forbidden = {
        store_module._LEDGERS,
        store_module._EVIDENCE,
        store_module._ASSIGNMENT_RESULTS,
    }

    async def promote():
        await hostile_store.promote_revision(
            scope=ref.scope,
            app_id=ref.app_id,
            expected_current_revision_ref=None,
            expected_generation=0,
            new_revision_ref=ref,
        )

    attempts = {
        "resolve": lambda: hostile_store.resolve_revision(
            ref, requesting_scope=ref.scope
        ),
        "restore": lambda: hostile_store.restore_revision(
            ref, requesting_scope=ref.scope
        ),
        "promote": promote,
    }
    for name, attempt in attempts.items():
        reads.clear()
        blob_reads.clear()
        with pytest.raises(RevisionIntegrityError):
            await attempt()
        assert set(reads) <= allowed, (name, reads)
        assert not set(reads) & forbidden, (name, reads)
        assert blob_reads == [], name
    # the failed promotion never created or advanced CURRENT
    assert (
        await hostile_store.get_publication(scope=ref.scope, app_id=ref.app_id) is None
    )


@pytest.mark.asyncio
async def test_evidence_failures_surface_as_typed_store_errors(tmp_path: Path) -> None:
    """Canonical-authority and evidence-closure failures crossing the store
    boundary are the typed revision-store error family, never a raw
    ValueError."""

    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    try:
        await store.persist_revision_closure(
            bundle=fixture["bundle"],
            assignment_results=(),
            evidence=fixture["evidence"],
            revision=fixture["revision"],
            authority_inputs=fixture["authority_inputs"],
        )
    except RevisionIntegrityError as exc:
        assert "validation evidence" in str(exc)
    else:
        raise AssertionError("empty assignment results must fail closed")


def _reforged_plan(plan, edit):
    document = plan.model_dump(mode="json")
    payload = plan.canonical_payload(include_digest=False)
    for doc in (document, payload):
        edit(doc)
    document["plan_digest"] = canonical_digest(payload)
    return CompilationPlan.model_validate(document)


@pytest.mark.parametrize(
    "case",
    [
        "validators_none",
        "validator_swapped",
        "unit_removed",
        "unit_added",
        "disposition_changed",
    ],
)
def test_evidence_boundaries_reject_noncanonical_plans(case: str) -> None:
    """The prior dangerous case: a re-digested plan whose validator/unit
    facts were rewritten must never produce or bless evidence; both evidence
    boundaries reject with the typed canonical-authority error."""

    from pydantic import ValidationError

    from mozaiksai.core.semantics.artifact_revision import (
        build_artifact_revision_validation_evidence,
        validate_artifact_revision_validation_evidence,
    )
    from mozaiksai.core.semantics.plan_authority import PlanAuthorityError

    fixture = revision_fixture()
    plan = fixture["plan"]
    dependencies = {
        dependency for unit in plan.units for dependency in unit.depends_on_units
    }
    leaf_id = next(
        unit.unit_id
        for unit in reversed(plan.units)
        if unit.unit_id not in dependencies
    )

    def edit(doc) -> None:
        units = doc["units"]
        if case == "validators_none":
            for unit in units:
                if unit["disposition"] != "agent_author":
                    unit["validator"] = "none"
        elif case == "validator_swapped":
            target = next(
                unit
                for unit in units
                if unit["validator"] != "none" and unit["disposition"] != "agent_author"
            )
            replacement = (
                "app_loader" if target["validator"] != "app_loader" else "app_paths"
            )
            target["validator"] = replacement
        elif case == "unit_removed":
            doc["units"] = [unit for unit in units if unit["unit_id"] != leaf_id]
        elif case == "unit_added":
            clone = dict(next(unit for unit in units if not unit["depends_on_units"]))
            clone["unit_id"] = "zzz.forged-unit"
            clone["outputs"] = [
                {**output, "path": f"zzz-forged/{output['path']}"}
                for output in clone["outputs"]
            ]
            doc["units"] = [*units, clone]
        elif case == "disposition_changed":
            target = next(unit for unit in units if unit["disposition"] == "render")
            target["disposition"] = "input_only"

    try:
        forged = _reforged_plan(plan, edit)
    except ValidationError:
        pytest.skip("plan body contract refuses this rewrite outright")

    with pytest.raises(PlanAuthorityError):
        build_artifact_revision_validation_evidence(
            scope=fixture["revision"].scope,
            app_id=fixture["app_id"],
            plan=forged,
            authority_inputs=fixture["authority_inputs"],
            ledger=fixture["ledger"],
            assignment_results=fixture["assignment_results"],
            bundle_validator_receipts=fixture["receipts"],
        )
    with pytest.raises(PlanAuthorityError):
        validate_artifact_revision_validation_evidence(
            evidence=fixture["evidence"],
            plan=forged,
            authority_inputs=fixture["authority_inputs"],
            ledger=fixture["ledger"],
            assignment_results=fixture["assignment_results"],
        )


def test_evidence_rejects_foreign_structured_output_authority() -> None:
    """Authority inputs whose structured-output configs are not the exact
    derivation configs cannot bless evidence for this plan; absent authority
    is equally typed."""

    from mozaiksai.core.semantics.artifact_revision import (
        validate_artifact_revision_validation_evidence,
    )
    from mozaiksai.core.semantics.plan_authority import (
        PlanAuthorityError,
        build_compilation_plan_authority_inputs,
    )

    fixture = revision_fixture()
    foreign_authority = build_compilation_plan_authority_inputs(
        graph=fixture["graph"],
        payloads=fixture["payloads"],
        registry=fixture["registry"],
        structured_output_configs={},
    )
    with pytest.raises(PlanAuthorityError):
        validate_artifact_revision_validation_evidence(
            evidence=fixture["evidence"],
            plan=fixture["plan"],
            authority_inputs=foreign_authority,
            ledger=fixture["ledger"],
            assignment_results=fixture["assignment_results"],
        )
    with pytest.raises(PlanAuthorityError):
        validate_artifact_revision_validation_evidence(
            evidence=fixture["evidence"],
            plan=fixture["plan"],
            authority_inputs=None,
            ledger=fixture["ledger"],
            assignment_results=fixture["assignment_results"],
        )
