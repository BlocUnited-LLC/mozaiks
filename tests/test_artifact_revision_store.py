from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Any

import pytest
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.artifacts.content_store import (
    ContentIntegrityError,
    ContentNotFoundError,
    LocalArtifactContentStore,
)
from mozaiksai.core.artifacts.revision_store import (
    ArtifactRevisionStore,
    PublicationConflictError,
    RevisionIntegrityError,
    RevisionNotFoundError,
)
from mozaiksai.core.semantics.artifact_revision import (
    PublicationOutcome,
    build_artifact_revision,
    build_artifact_revision_validation_evidence,
)
from mozaiksai.core.semantics.binding import build_implementation_binding
from mozaiksai.core.semantics.composition_ledger import (
    CanonicalComposedBundle,
    CompositionLedger,
)
from mozaiksai.core.semantics.refs import (
    ArtifactRevisionRef,
    CompilationPlanRef,
    ExecutionAccessScopeRef,
    ImplementationBindingRef,
    SemanticGraphRef,
)
from mozaiksai.core.workflow.structured_output_contracts import stable_digest
from tests.slice_5c_revision_helpers import revision_fixture


class _InsertResult:
    pass


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    return all(document.get(key) == value for key, value in query.items())


class _MemoryCollection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.indexes: dict[str, dict[str, Any]] = {"_id_": {"key": [("_id", 1)], "unique": True}}
        self.lock = asyncio.Lock()

    async def create_index(self, keys, *, name: str, unique: bool = False):
        async with self.lock:
            prior = self.indexes.get(name)
            declared = {"key": list(keys), "unique": unique}
            if prior is not None and prior != declared:
                raise RuntimeError("index definition conflict")
            self.indexes[name] = declared
        return name

    async def index_information(self):
        async with self.lock:
            return copy.deepcopy(self.indexes)

    async def insert_one(self, document: dict[str, Any]):
        async with self.lock:
            identifier = document["_id"]
            if identifier in self.documents:
                raise DuplicateKeyError("duplicate _id")
            for index in self.indexes.values():
                if not index.get("unique") or index["key"] == [("_id", 1)]:
                    continue
                fields = [field for field, _direction in index["key"]]
                if any(
                    all(existing.get(field) == document.get(field) for field in fields)
                    for existing in self.documents.values()
                ):
                    raise DuplicateKeyError("duplicate unique index")
            self.documents[identifier] = copy.deepcopy(document)
        return _InsertResult()

    async def find_one(self, query: dict[str, Any]):
        async with self.lock:
            for document in self.documents.values():
                if _matches(document, query):
                    return copy.deepcopy(document)
        return None

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], **_kwargs: Any
    ):
        async with self.lock:
            for identifier, document in self.documents.items():
                if not _matches(document, query):
                    continue
                for key, value in update.get("$set", {}).items():
                    document[key] = copy.deepcopy(value)
                for key, value in update.get("$inc", {}).items():
                    document[key] = document.get(key, 0) + value
                self.documents[identifier] = document
                return copy.deepcopy(document)
        return None


class _MemoryDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _MemoryCollection] = {}

    def __getitem__(self, name: str) -> _MemoryCollection:
        return self.collections.setdefault(name, _MemoryCollection())


class _MemoryClient:
    def __init__(self) -> None:
        self.databases: dict[str, _MemoryDatabase] = {}

    def __getitem__(self, name: str) -> _MemoryDatabase:
        return self.databases.setdefault(name, _MemoryDatabase())


def _store(tmp_path: Path, fixture: dict[str, object], client=None):
    return ArtifactRevisionStore(
        content_store=LocalArtifactContentStore(root=tmp_path),
        semantic_resolver=fixture["resolver"],
        client=client or _MemoryClient(),
    )


async def _persist(store: ArtifactRevisionStore, fixture: dict[str, object]):
    return await store.persist_revision_closure(
        bundle=fixture["bundle"],
        assignment_results=fixture["assignment_results"],
        evidence=fixture["evidence"],
        revision=fixture["revision"],
        authority_inputs=fixture["authority_inputs"],
    )


def _binding_variant(fixture: dict[str, object], suffix: str):
    graph = fixture["graph"]
    revision = fixture["revision"]
    binding = build_implementation_binding(
        binding_id=f"slice-5c-binding-{suffix}",
        version=1,
        scope=graph.scope,
        semantic_graph_ref=revision.semantic_graph_ref,
        capability_pack_selections=(),
        renderer_selections=(),
        deployment_profile_selections=(),
    )
    fixture["resolver"].register_implementation_binding(binding)
    binding_ref = ImplementationBindingRef(
        subject_id=binding.binding_id,
        subject_version=binding.version,
        content_digest=binding.binding_digest,
        scope=binding.scope,
    )
    return build_artifact_revision(
        **{
            **revision.model_dump(
                mode="python",
                exclude={
                    "revision_schema_version",
                    "revision_digest",
                    "implementation_binding_ref",
                },
            ),
            "implementation_binding_ref": binding_ref,
        }
    )


def _child_fixture(fixture: dict[str, object], parent_ref: ArtifactRevisionRef):
    base_ledger = fixture["bundle"].ledger
    ledger_payload = {
        "ledger_schema_version": base_ledger.ledger_schema_version,
        "compilation_plan_ref": base_ledger.compilation_plan_ref,
        "base_revision_digest": parent_ref.revision_digest,
        "unit_entries": base_ledger.unit_entries,
        "removed_base_artifacts": base_ledger.removed_base_artifacts,
        "final_bundle_manifest": base_ledger.final_bundle_manifest,
        "bundle_digest": base_ledger.bundle_digest,
    }
    candidate = CompositionLedger.model_construct(**ledger_payload, ledger_digest="0" * 64)
    ledger = CompositionLedger(
        **ledger_payload,
        ledger_digest=stable_digest(candidate.model_dump(mode="json", exclude={"ledger_digest"})),
    )
    bundle = CanonicalComposedBundle(
        plan_digest=fixture["bundle"].plan_digest,
        artifacts=fixture["bundle"].artifacts,
        ledger=ledger,
    )
    evidence = build_artifact_revision_validation_evidence(
        scope=fixture["revision"].scope,
        app_id=fixture["app_id"],
        plan=fixture["plan"],
        authority_inputs=fixture["authority_inputs"],
        ledger=ledger,
        assignment_results=fixture["assignment_results"],
        bundle_validator_receipts=fixture["receipts"],
    )
    revision = build_artifact_revision(
        **{
            **fixture["revision"].model_dump(
                mode="python",
                exclude={
                    "revision_schema_version",
                    "revision_digest",
                    "parent_revision_ref",
                    "composition_ledger_digest",
                    "validation_evidence_digest",
                },
            ),
            "parent_revision_ref": parent_ref,
            "composition_ledger_digest": ledger.ledger_digest,
            "validation_evidence_digest": evidence.evidence_digest,
        }
    )
    return {**fixture, "bundle": bundle, "evidence": evidence, "revision": revision}


@pytest.mark.asyncio
async def test_persist_cold_resolve_restore_and_idempotency(tmp_path: Path) -> None:
    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    ref = await _persist(store, fixture)
    assert await _persist(store, fixture) == ref
    assert await store.resolve_revision(ref, requesting_scope=ref.scope) == fixture["revision"]
    restored = await store.restore_revision(ref, requesting_scope=ref.scope)
    assert restored.ledger == fixture["bundle"].ledger
    assert restored.artifacts == fixture["bundle"].artifacts


@pytest.mark.asyncio
async def test_same_revision_digest_cannot_hide_unequal_stored_document(tmp_path: Path) -> None:
    fixture = revision_fixture()
    client = _MemoryClient()
    store = _store(tmp_path, fixture, client=client)
    await _persist(store, fixture)
    revisions = client["mozaiksai"]["ArtifactRevisionsV1"]
    stored = next(iter(revisions.documents.values()))
    stored["document"]["bundle_digest"] = "f" * 64

    with pytest.raises(RevisionIntegrityError, match="different content"):
        await _persist(store, fixture)


@pytest.mark.asyncio
async def test_modified_blob_and_foreign_scope_fail_restore(tmp_path: Path) -> None:
    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    ref = await _persist(store, fixture)
    digest = fixture["bundle"].artifacts[0].content_digest
    (tmp_path / "sha256" / digest[:2] / digest).write_bytes(b"tampered")
    with pytest.raises(ContentIntegrityError):
        await store.restore_revision(ref, requesting_scope=ref.scope)

    foreign = ExecutionAccessScopeRef(tenant_id="foreign-tenant")
    forged = ArtifactRevisionRef(
        scope=foreign, app_id=ref.app_id, revision_digest=ref.revision_digest
    )
    with pytest.raises(RevisionIntegrityError, match="cross-scope"):
        await store.resolve_revision(forged, requesting_scope=ref.scope)


@pytest.mark.asyncio
async def test_swapped_blob_locations_fail_exact_digest_verification(tmp_path: Path) -> None:
    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    ref = await _persist(store, fixture)
    first, second = fixture["bundle"].artifacts[:2]
    first_path = tmp_path / "sha256" / first.content_digest[:2] / first.content_digest
    second_path = tmp_path / "sha256" / second.content_digest[:2] / second.content_digest
    first_bytes = first_path.read_bytes()
    second_bytes = second_path.read_bytes()
    first_path.write_bytes(second_bytes)
    second_path.write_bytes(first_bytes)

    with pytest.raises(ContentIntegrityError):
        await store.restore_revision(ref, requesting_scope=ref.scope)


@pytest.mark.asyncio
async def test_missing_blob_foreign_app_and_stale_manifest_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = revision_fixture()
    client = _MemoryClient()
    store = _store(tmp_path, fixture, client=client)
    ref = await _persist(store, fixture)
    digest = fixture["bundle"].artifacts[0].content_digest
    (tmp_path / "sha256" / digest[:2] / digest).unlink()
    with pytest.raises(ContentNotFoundError, match="not found"):
        await store.restore_revision(ref, requesting_scope=ref.scope)

    foreign_app = ArtifactRevisionRef(
        scope=ref.scope,
        app_id="another-app",
        revision_digest=ref.revision_digest,
    )
    with pytest.raises(RevisionNotFoundError, match="no exact"):
        await store.resolve_revision(foreign_app, requesting_scope=ref.scope)

    await _persist(store, fixture)
    ledger_collection = client["mozaiksai"]["CompositionLedgersV1"]
    ledger_document = next(iter(ledger_collection.documents.values()))
    ledger_document["document"]["final_bundle_manifest"][0]["content_digest"] = "f" * 64
    with pytest.raises(RevisionIntegrityError, match="cold validation"):
        await store.resolve_revision(ref, requesting_scope=ref.scope)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("composition_ledger_digest", "composition ledger"),
        ("bundle_digest", "bundle digest"),
        ("validation_evidence_digest", "validation evidence"),
    ],
)
async def test_revision_cannot_substitute_authoritative_closure_digests(
    tmp_path: Path, field: str, message: str
) -> None:
    fixture = revision_fixture()
    arguments = fixture["revision"].model_dump(
        mode="python", exclude={"revision_schema_version", "revision_digest"}
    )
    arguments[field] = "f" * 64
    fixture["revision"] = build_artifact_revision(**arguments)
    store = _store(tmp_path, fixture)
    with pytest.raises(RevisionIntegrityError, match=message):
        await _persist(store, fixture)


@pytest.mark.asyncio
async def test_forged_compilation_plan_ref_fails_before_publication(tmp_path: Path) -> None:
    fixture = revision_fixture()
    revision = fixture["revision"]
    forged_plan = CompilationPlanRef(
        subject_id=revision.compilation_plan_ref.subject_id,
        subject_version=revision.compilation_plan_ref.subject_version,
        content_digest="f" * 64,
        scope=revision.scope,
    )
    arguments = revision.model_dump(
        mode="python",
        exclude={"revision_schema_version", "revision_digest", "compilation_plan_ref"},
    )
    arguments["compilation_plan_ref"] = forged_plan
    fixture["revision"] = build_artifact_revision(**arguments)
    store = _store(tmp_path, fixture)
    with pytest.raises(RevisionIntegrityError, match="CompilationPlan|cold resolution"):
        await _persist(store, fixture)
    assert await store.get_publication(scope=revision.scope, app_id=revision.app_id) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("authority", ["graph", "binding"])
async def test_forged_graph_or_binding_ref_fails_before_publication(
    tmp_path: Path, authority: str
) -> None:
    fixture = revision_fixture()
    revision = fixture["revision"]
    arguments = revision.model_dump(
        mode="python", exclude={"revision_schema_version", "revision_digest"}
    )
    if authority == "graph":
        arguments["semantic_graph_ref"] = SemanticGraphRef(
            subject_id=revision.semantic_graph_ref.subject_id,
            subject_version=revision.semantic_graph_ref.subject_version,
            content_digest="f" * 64,
            scope=revision.scope,
        )
    else:
        arguments["implementation_binding_ref"] = ImplementationBindingRef(
            subject_id=revision.implementation_binding_ref.subject_id,
            subject_version=revision.implementation_binding_ref.subject_version,
            content_digest="f" * 64,
            scope=revision.scope,
        )
    fixture["revision"] = build_artifact_revision(**arguments)
    store = _store(tmp_path, fixture)
    with pytest.raises(RevisionIntegrityError, match="semantic authority"):
        await _persist(store, fixture)
    assert await store.get_publication(scope=revision.scope, app_id=revision.app_id) is None


@pytest.mark.asyncio
async def test_concurrent_genesis_has_exactly_one_winner_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    first = await _persist(store, fixture)
    second_revision = _binding_variant(fixture, "alternative")
    second_fixture = {**fixture, "revision": second_revision}
    second = await _persist(store, second_fixture)

    async def promote(ref: ArtifactRevisionRef):
        try:
            return await store.promote_revision(
                scope=ref.scope,
                app_id=ref.app_id,
                expected_current_revision_ref=None,
                expected_generation=0,
                new_revision_ref=ref,
            )
        except PublicationConflictError as exc:
            return exc

    outcomes = await asyncio.gather(promote(first), promote(second))
    winners = [item for item in outcomes if not isinstance(item, Exception)]
    conflicts = [item for item in outcomes if isinstance(item, PublicationConflictError)]
    assert len(winners) == 1
    assert len(conflicts) == 1
    assert winners[0].outcome is PublicationOutcome.PROMOTED
    winner_ref = winners[0].publication.current_revision_ref
    retry = await store.promote_revision(
        scope=winner_ref.scope,
        app_id=winner_ref.app_id,
        expected_current_revision_ref=None,
        expected_generation=0,
        new_revision_ref=winner_ref,
    )
    assert retry.outcome is PublicationOutcome.ALREADY_CURRENT
    assert retry.publication.generation == 1

    other_workspace = ExecutionAccessScopeRef(
        tenant_id=winner_ref.scope.tenant_id,
        workspace_id="another-workspace",
    )
    with pytest.raises(RevisionIntegrityError, match="another scope or app"):
        await store.promote_revision(
            scope=other_workspace,
            app_id=winner_ref.app_id,
            expected_current_revision_ref=None,
            expected_generation=0,
            new_revision_ref=winner_ref,
        )


@pytest.mark.asyncio
async def test_refinement_siblings_use_parent_and_generation_cas(tmp_path: Path) -> None:
    fixture = revision_fixture()
    store = _store(tmp_path, fixture)
    parent = await _persist(store, fixture)
    promoted = await store.promote_revision(
        scope=parent.scope,
        app_id=parent.app_id,
        expected_current_revision_ref=None,
        expected_generation=0,
        new_revision_ref=parent,
    )
    assert promoted.publication.generation == 1

    first_fixture = _child_fixture(fixture, parent)
    first_child = await _persist(store, first_fixture)
    alternative = _binding_variant(first_fixture, "sibling")
    second_fixture = {**first_fixture, "revision": alternative}
    second_child = await _persist(store, second_fixture)

    first_result = await store.promote_revision(
        scope=parent.scope,
        app_id=parent.app_id,
        expected_current_revision_ref=parent,
        expected_generation=1,
        new_revision_ref=first_child,
    )
    assert first_result.publication.generation == 2
    with pytest.raises(PublicationConflictError) as conflict:
        await store.promote_revision(
            scope=parent.scope,
            app_id=parent.app_id,
            expected_current_revision_ref=parent,
            expected_generation=1,
            new_revision_ref=second_child,
        )
    assert conflict.value.publication.current_revision_ref == first_child
    assert (
        await store.resolve_current_revision(scope=parent.scope, app_id=parent.app_id)
        == first_fixture["revision"]
    )


@pytest.mark.asyncio
async def test_stale_generation_and_aba_attempt_leave_current_unchanged(tmp_path: Path) -> None:
    fixture = revision_fixture()
    client = _MemoryClient()
    store = _store(tmp_path, fixture, client=client)
    parent = await _persist(store, fixture)
    await store.promote_revision(
        scope=parent.scope,
        app_id=parent.app_id,
        expected_current_revision_ref=None,
        expected_generation=0,
        new_revision_ref=parent,
    )
    child_fixture = _child_fixture(fixture, parent)
    child = await _persist(store, child_fixture)

    with pytest.raises(PublicationConflictError):
        await store.promote_revision(
            scope=parent.scope,
            app_id=parent.app_id,
            expected_current_revision_ref=parent,
            expected_generation=0,
            new_revision_ref=child,
        )
    assert (await store.get_publication(scope=parent.scope, app_id=parent.app_id)).generation == 1

    publication_collection = client["mozaiksai"]["ApplicationPublicationsV1"]
    publication_document = next(iter(publication_collection.documents.values()))
    publication_document["current_revision_ref"] = parent.model_dump(mode="json")
    publication_document["generation"] = 3
    with pytest.raises(PublicationConflictError) as conflict:
        await store.promote_revision(
            scope=parent.scope,
            app_id=parent.app_id,
            expected_current_revision_ref=parent,
            expected_generation=1,
            new_revision_ref=child,
        )
    assert conflict.value.publication.current_revision_ref == parent
    assert conflict.value.publication.generation == 3


@pytest.mark.asyncio
async def test_index_verification_failure_fails_store_closed(tmp_path: Path) -> None:
    fixture = revision_fixture()
    client = _MemoryClient()
    revisions = client["mozaiksai"]["ArtifactRevisionsV1"]
    revisions.indexes["artifact_revision_scope_app_digest"] = {
        "key": [("scope_key", 1)],
        "unique": False,
    }
    store = _store(tmp_path, fixture, client=client)
    with pytest.raises(RuntimeError, match="index definition conflict"):
        await store.initialize()


@pytest.mark.asyncio
async def test_blob_storage_failure_cannot_create_current_pointer(tmp_path: Path) -> None:
    fixture = revision_fixture()

    class _UnavailableContentStore(LocalArtifactContentStore):
        async def put_blob(self, data: bytes, *, expected_digest: str) -> str:
            del data, expected_digest
            raise RuntimeError("blob authority unavailable")

    store = ArtifactRevisionStore(
        content_store=_UnavailableContentStore(root=tmp_path),
        semantic_resolver=fixture["resolver"],
        client=_MemoryClient(),
    )
    with pytest.raises(RuntimeError, match="blob authority unavailable"):
        await _persist(store, fixture)
    assert (
        await store.get_publication(scope=fixture["revision"].scope, app_id=fixture["app_id"])
        is None
    )
