"""Offline immutable revision persistence and generation-guarded publication CAS."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, cast

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.artifacts.content_store import ArtifactContentStore
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.semantics.artifact_revision import (
    ApplicationPublication,
    ArtifactRevision,
    ArtifactRevisionValidationEvidence,
    PublicationOutcome,
    PublicationResult,
    validate_artifact_revision_validation_evidence,
)
from mozaiksai.core.semantics.binding import ImplementationBinding
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.compilation_plan import CompilationPlan
from mozaiksai.core.semantics.composition_ledger import (
    AccountedArtifact,
    CanonicalComposedBundle,
    ComposedArtifactContent,
    CompositionLedger,
)
from mozaiksai.core.semantics.graph import SemanticGraph, SemanticGraphV2
from mozaiksai.core.semantics.refs import (
    ArtifactRevisionRef,
    CompilationPlanRef,
    ExecutionAccessScopeRef,
)
from mozaiksai.core.semantics.resolver import (
    ReferenceResolutionError,
    SemanticReferenceResolver,
)
from mozaiksai.core.workflow.assignment_artifacts import AssignmentArtifactResult

_DATABASE = "mozaiksai"
_REVISIONS = "ArtifactRevisionsV1"
_EVIDENCE = "ArtifactRevisionEvidenceV1"
_ASSIGNMENT_RESULTS = "AssignmentArtifactResultsV1"
_LEDGERS = "CompositionLedgersV1"
_PUBLICATIONS = "ApplicationPublicationsV1"


def _address_key(item: AccountedArtifact) -> tuple[str, tuple[tuple[str, str], ...], str]:
    address = item.address
    return (address.path_scope.value, address.placeholder_values, address.path)


class RevisionStoreError(RuntimeError):
    """Base failure for the offline revision/publication substrate."""


class RevisionIntegrityError(RevisionStoreError):
    """Stored immutable content or its referenced closure failed verification."""


class RevisionNotFoundError(RevisionStoreError):
    """An exact immutable revision/ref document was not found."""


class PublicationConflictError(RevisionStoreError):
    """CURRENT changed since the caller observed its expected publication state."""

    def __init__(self, publication: ApplicationPublication) -> None:
        super().__init__(
            "publication compare-and-swap conflict: CURRENT no longer matches "
            "the expected revision and generation"
        )
        self.publication = publication


def _scope_key(scope: ExecutionAccessScopeRef) -> str:
    return cast(str, canonical_digest(scope.model_dump(mode="json")))


def _plan_ref(plan: CompilationPlan) -> CompilationPlanRef:
    return CompilationPlanRef(
        subject_id=plan.graph_id,
        subject_version=plan.graph_version,
        content_digest=plan.plan_digest,
        scope=plan.scope,
    )


def _document_id(collection: str, *, scope_key: str, app_id: str, digest: str) -> str:
    return cast(
        str,
        canonical_digest(
            {
                "collection": collection,
                "scope_key": scope_key,
                "app_id": app_id,
                "digest": digest,
            },
        ),
    )


class ArtifactRevisionStore:
    """Canonical 5C owner for immutable revision closure and CURRENT pointer CAS.

    This class is intentionally not imported by Factory, Studio, BuildRecord,
    AppContext, task-batch, or AG2 production paths until Slice 5D.
    """

    def __init__(
        self,
        *,
        content_store: ArtifactContentStore,
        semantic_resolver: SemanticReferenceResolver,
        client: Any | None = None,
    ) -> None:
        self._content_store = content_store
        self._semantic_resolver = semantic_resolver
        self._client = client
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def initialize(self) -> None:
        """Declare and verify every immutable/CURRENT uniqueness contract."""

        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if self._client is None:
                self._client = get_mongo_client()
            database = self._client[_DATABASE]
            await self._ensure_index(
                database[_REVISIONS],
                (("scope_key", 1), ("app_id", 1), ("revision_digest", 1)),
                name="artifact_revision_scope_app_digest",
                unique=True,
            )
            await self._ensure_index(
                database[_EVIDENCE],
                (("scope_key", 1), ("app_id", 1), ("evidence_digest", 1)),
                name="artifact_revision_evidence_scope_app_digest",
                unique=True,
            )
            await self._ensure_index(
                database[_ASSIGNMENT_RESULTS],
                (("scope_key", 1), ("app_id", 1), ("result_digest", 1)),
                name="assignment_result_scope_app_digest",
                unique=True,
            )
            await self._ensure_index(
                database[_LEDGERS],
                (("scope_key", 1), ("app_id", 1), ("ledger_digest", 1)),
                name="composition_ledger_scope_app_digest",
                unique=True,
            )
            await self._ensure_index(
                database[_PUBLICATIONS],
                (("scope_key", 1), ("app_id", 1)),
                name="application_publication_scope_app",
                unique=True,
            )
            self._initialized = True

    @staticmethod
    async def _ensure_index(
        collection: Any,
        keys: tuple[tuple[str, int], ...],
        *,
        name: str,
        unique: bool,
    ) -> None:
        await collection.create_index(list(keys), name=name, unique=unique)
        information = await collection.index_information()
        declared = information.get(name)
        if declared is None:
            raise RevisionStoreError(f"required index {name!r} was not materialized")
        actual_keys = tuple(tuple(item) for item in declared.get("key", ()))
        if actual_keys != keys or bool(declared.get("unique", False)) is not unique:
            raise RevisionStoreError(f"required index {name!r} failed exact verification")

    async def _collection(self, name: str) -> Any:
        await self.initialize()
        if self._client is None:
            raise RevisionStoreError("revision store client failed initialization")
        return self._client[_DATABASE][name]

    async def persist_revision_closure(
        self,
        *,
        bundle: CanonicalComposedBundle,
        assignment_results: Sequence[AssignmentArtifactResult],
        evidence: ArtifactRevisionValidationEvidence,
        revision: ArtifactRevision,
    ) -> ArtifactRevisionRef:
        """Persist exact bytes and immutable documents, then cold-verify the closure."""

        verified_revision = ArtifactRevision.model_validate(revision.model_dump(mode="json"))
        plan, ledger, verified_results = self._validate_candidate_closure(
            bundle=bundle,
            assignment_results=assignment_results,
            evidence=evidence,
            revision=verified_revision,
        )

        for artifact in bundle.artifacts:
            await self._content_store.put_blob(
                artifact.content, expected_digest=artifact.content_digest
            )

        scope_key = _scope_key(verified_revision.scope)
        for result in verified_results:
            await self._put_immutable(
                collection_name=_ASSIGNMENT_RESULTS,
                scope=verified_revision.scope,
                scope_key=scope_key,
                app_id=verified_revision.app_id,
                digest_field="result_digest",
                digest=result.result_digest,
                document=result.model_dump(mode="json"),
            )
        await self._put_immutable(
            collection_name=_LEDGERS,
            scope=verified_revision.scope,
            scope_key=scope_key,
            app_id=verified_revision.app_id,
            digest_field="ledger_digest",
            digest=ledger.ledger_digest,
            document=ledger.model_dump(mode="json"),
        )
        await self._put_immutable(
            collection_name=_EVIDENCE,
            scope=verified_revision.scope,
            scope_key=scope_key,
            app_id=verified_revision.app_id,
            digest_field="evidence_digest",
            digest=evidence.evidence_digest,
            document=evidence.model_dump(mode="json"),
        )
        await self._put_immutable(
            collection_name=_REVISIONS,
            scope=verified_revision.scope,
            scope_key=scope_key,
            app_id=verified_revision.app_id,
            digest_field="revision_digest",
            digest=verified_revision.revision_digest,
            document=verified_revision.model_dump(mode="json"),
        )
        del plan
        await self.resolve_revision(verified_revision.ref, requesting_scope=verified_revision.scope)
        return cast(ArtifactRevisionRef, verified_revision.ref)

    async def _put_immutable(
        self,
        *,
        collection_name: str,
        scope: ExecutionAccessScopeRef,
        scope_key: str,
        app_id: str,
        digest_field: str,
        digest: str,
        document: Mapping[str, Any],
    ) -> None:
        collection = await self._collection(collection_name)
        envelope = {
            "_id": _document_id(collection_name, scope_key=scope_key, app_id=app_id, digest=digest),
            "scope_key": scope_key,
            "scope": scope.model_dump(mode="json"),
            "app_id": app_id,
            digest_field: digest,
            "document": dict(document),
        }
        try:
            await collection.insert_one(envelope)
        except DuplicateKeyError:
            existing = await collection.find_one({"_id": envelope["_id"]})
            if existing is None or existing.get("document") != envelope["document"]:
                raise RevisionIntegrityError(
                    f"immutable {collection_name} identity resolved to different content"
                ) from None

    def _validate_candidate_closure(
        self,
        *,
        bundle: CanonicalComposedBundle,
        assignment_results: Sequence[AssignmentArtifactResult],
        evidence: ArtifactRevisionValidationEvidence,
        revision: ArtifactRevision,
    ) -> tuple[
        CompilationPlan,
        CompositionLedger,
        tuple[AssignmentArtifactResult, ...],
    ]:
        if bundle.ledger != CompositionLedger.model_validate(bundle.ledger.model_dump(mode="json")):
            raise RevisionIntegrityError("canonical bundle ledger failed cold validation")
        ledger = bundle.ledger
        if revision.composition_ledger_digest != ledger.ledger_digest:
            raise RevisionIntegrityError("revision does not bind the composition ledger")
        if revision.bundle_digest != ledger.bundle_digest:
            raise RevisionIntegrityError("revision does not bind the final bundle digest")
        if revision.compilation_plan_ref != ledger.compilation_plan_ref:
            raise RevisionIntegrityError("revision and ledger bind different CompilationPlans")
        if bundle.plan_digest != revision.compilation_plan_ref.content_digest:
            raise RevisionIntegrityError("canonical bundle belongs to another CompilationPlan")

        plan = self._resolve_semantic_authority(revision)
        if _plan_ref(plan) != revision.compilation_plan_ref:
            raise RevisionIntegrityError("resolved CompilationPlan identity mismatch")
        verified_results = tuple(
            AssignmentArtifactResult.model_validate(item.model_dump(mode="json"))
            for item in assignment_results
        )
        validate_artifact_revision_validation_evidence(
            evidence=evidence,
            plan=plan,
            ledger=ledger,
            assignment_results=verified_results,
        )
        if evidence.scope != revision.scope or evidence.app_id != revision.app_id:
            raise RevisionIntegrityError("validation evidence belongs to another scope or app")
        if revision.validation_evidence_digest != evidence.evidence_digest:
            raise RevisionIntegrityError("revision does not bind validation evidence")

        expected_base = (
            None
            if revision.parent_revision_ref is None
            else revision.parent_revision_ref.revision_digest
        )
        if ledger.base_revision_digest != expected_base:
            raise RevisionIntegrityError("ledger base revision does not match revision parent")

        manifest = tuple(
            AccountedArtifact(address=item.address, content_digest=item.content_digest)
            for item in bundle.artifacts
        )
        if tuple(sorted(manifest, key=_address_key)) != tuple(
            sorted(ledger.final_bundle_manifest, key=_address_key)
        ):
            raise RevisionIntegrityError("bundle bytes do not exactly match ledger manifest")
        by_address = {item.address: item for item in bundle.artifacts}
        if len(by_address) != len(bundle.artifacts):
            raise RevisionIntegrityError("canonical bundle contains duplicate artifact addresses")
        for entry in ledger.unit_entries:
            for artifact in entry.artifacts:
                if artifact.content_digest is None:
                    continue
                content = by_address.get(artifact.address)
                if content is None or content.unit_id != entry.plan_unit_ref.unit_id:
                    raise RevisionIntegrityError(
                        "canonical bundle bytes do not match ledger unit provenance"
                    )
        return plan, ledger, verified_results

    def _resolve_semantic_authority(self, revision: ArtifactRevision) -> CompilationPlan:
        try:
            graph = self._semantic_resolver.resolve(
                revision.semantic_graph_ref, requesting_scope=revision.scope
            )
            binding = self._semantic_resolver.resolve(
                revision.implementation_binding_ref, requesting_scope=revision.scope
            )
            plan = self._semantic_resolver.resolve(
                revision.compilation_plan_ref, requesting_scope=revision.scope
            )
        except ReferenceResolutionError as exc:
            raise RevisionIntegrityError(
                f"artifact revision semantic authority failed cold resolution: {exc}"
            ) from exc
        if not isinstance(graph, (SemanticGraph, SemanticGraphV2)):
            raise RevisionIntegrityError("SemanticGraphRef did not resolve to a graph")
        if not isinstance(binding, ImplementationBinding):
            raise RevisionIntegrityError("ImplementationBindingRef did not resolve to a binding")
        if not isinstance(plan, CompilationPlan):
            raise RevisionIntegrityError("CompilationPlanRef did not resolve to a plan")
        if binding.semantic_graph_ref != revision.semantic_graph_ref:
            raise RevisionIntegrityError("ImplementationBinding targets another graph")
        if (
            plan.graph_id != revision.semantic_graph_ref.subject_id
            or plan.graph_version != revision.semantic_graph_ref.subject_version
            or plan.graph_digest != revision.semantic_graph_ref.content_digest
            or plan.scope != revision.scope
        ):
            raise RevisionIntegrityError("CompilationPlan targets another graph")
        return plan

    async def resolve_revision(
        self,
        ref: ArtifactRevisionRef,
        *,
        requesting_scope: ExecutionAccessScopeRef,
    ) -> ArtifactRevision:
        """Cold-resolve and verify one exact revision including immutable bytes."""

        revision, _bundle = await self._resolve_revision_and_bundle(
            ref, requesting_scope=requesting_scope, seen=frozenset()
        )
        return revision

    async def restore_revision(
        self,
        ref: ArtifactRevisionRef,
        *,
        requesting_scope: ExecutionAccessScopeRef,
    ) -> CanonicalComposedBundle:
        """Restore exact verified bytes; mutable filesystem paths are never consulted."""

        _revision, bundle = await self._resolve_revision_and_bundle(
            ref, requesting_scope=requesting_scope, seen=frozenset()
        )
        return bundle

    async def _resolve_revision_and_bundle(
        self,
        ref: ArtifactRevisionRef,
        *,
        requesting_scope: ExecutionAccessScopeRef,
        seen: frozenset[str],
    ) -> tuple[ArtifactRevision, CanonicalComposedBundle]:
        verified_ref = ArtifactRevisionRef.model_validate(ref.model_dump(mode="json"))
        if requesting_scope != verified_ref.scope:
            raise RevisionIntegrityError("cross-scope artifact revision access fails closed")
        if verified_ref.revision_digest in seen:
            raise RevisionIntegrityError("artifact revision lineage contains a cycle")
        revision_document = await self._get_document(
            collection_name=_REVISIONS,
            scope=verified_ref.scope,
            app_id=verified_ref.app_id,
            digest_field="revision_digest",
            digest=verified_ref.revision_digest,
        )
        try:
            revision = ArtifactRevision.model_validate(revision_document)
        except (TypeError, ValueError) as exc:
            raise RevisionIntegrityError(
                f"stored ArtifactRevision failed cold validation: {exc}"
            ) from exc
        if revision.ref != verified_ref:
            raise RevisionIntegrityError("stored ArtifactRevision does not match its ref")
        plan = self._resolve_semantic_authority(revision)

        ledger_document = await self._get_document(
            collection_name=_LEDGERS,
            scope=revision.scope,
            app_id=revision.app_id,
            digest_field="ledger_digest",
            digest=revision.composition_ledger_digest,
        )
        evidence_document = await self._get_document(
            collection_name=_EVIDENCE,
            scope=revision.scope,
            app_id=revision.app_id,
            digest_field="evidence_digest",
            digest=revision.validation_evidence_digest,
        )
        try:
            ledger = CompositionLedger.model_validate(ledger_document)
            evidence = ArtifactRevisionValidationEvidence.model_validate(evidence_document)
        except (TypeError, ValueError) as exc:
            raise RevisionIntegrityError(
                f"stored revision closure failed cold validation: {exc}"
            ) from exc
        if ledger.compilation_plan_ref != revision.compilation_plan_ref:
            raise RevisionIntegrityError("stored ledger belongs to another CompilationPlan")
        if ledger.bundle_digest != revision.bundle_digest:
            raise RevisionIntegrityError("stored ledger has a stale bundle digest")

        results: list[AssignmentArtifactResult] = []
        for digest in evidence.assignment_result_digests:
            document = await self._get_document(
                collection_name=_ASSIGNMENT_RESULTS,
                scope=revision.scope,
                app_id=revision.app_id,
                digest_field="result_digest",
                digest=digest,
            )
            try:
                results.append(AssignmentArtifactResult.model_validate(document))
            except (TypeError, ValueError) as exc:
                raise RevisionIntegrityError(
                    f"stored assignment result failed cold validation: {exc}"
                ) from exc
        validate_artifact_revision_validation_evidence(
            evidence=evidence,
            plan=plan,
            ledger=ledger,
            assignment_results=results,
        )

        if revision.parent_revision_ref is None:
            if ledger.base_revision_digest is not None:
                raise RevisionIntegrityError("Genesis revision cannot have a base digest")
        else:
            if ledger.base_revision_digest != revision.parent_revision_ref.revision_digest:
                raise RevisionIntegrityError("refinement ledger base does not match parent")
            await self._resolve_revision_and_bundle(
                revision.parent_revision_ref,
                requesting_scope=requesting_scope,
                seen=seen | {revision.revision_digest},
            )

        unit_by_address: dict[Any, str] = {}
        for entry in ledger.unit_entries:
            for artifact in entry.artifacts:
                if artifact.content_digest is not None:
                    if artifact.address in unit_by_address:
                        raise RevisionIntegrityError("ledger contains duplicate artifact ownership")
                    unit_by_address[artifact.address] = entry.plan_unit_ref.unit_id
        artifacts: list[ComposedArtifactContent] = []
        for item in ledger.final_bundle_manifest:
            if item.content_digest is None:
                raise RevisionIntegrityError("final manifest is missing content identity")
            unit_id = unit_by_address.get(item.address)
            if unit_id is None:
                raise RevisionIntegrityError("final manifest artifact has no unit provenance")
            content = await self._content_store.get_verified_blob(item.content_digest)
            artifacts.append(
                ComposedArtifactContent(
                    unit_id=unit_id,
                    address=item.address,
                    content=content,
                    content_digest=item.content_digest,
                )
            )
        bundle = CanonicalComposedBundle(
            plan_digest=plan.plan_digest,
            artifacts=tuple(artifacts),
            ledger=ledger,
        )
        self._validate_candidate_closure(
            bundle=bundle,
            assignment_results=results,
            evidence=evidence,
            revision=revision,
        )
        return revision, bundle

    async def _get_document(
        self,
        *,
        collection_name: str,
        scope: ExecutionAccessScopeRef,
        app_id: str,
        digest_field: str,
        digest: str,
    ) -> Mapping[str, Any]:
        scope_key = _scope_key(scope)
        collection = await self._collection(collection_name)
        document = await collection.find_one(
            {
                "scope_key": scope_key,
                "scope": scope.model_dump(mode="json"),
                "app_id": app_id,
                digest_field: digest,
            }
        )
        if document is None:
            raise RevisionNotFoundError(
                f"no exact {collection_name} document for the requested scope/app/digest"
            )
        payload = document.get("document")
        if not isinstance(payload, Mapping):
            raise RevisionIntegrityError(
                f"stored {collection_name} document has no canonical payload"
            )
        return payload

    async def get_publication(
        self, *, scope: ExecutionAccessScopeRef, app_id: str
    ) -> ApplicationPublication | None:
        collection = await self._collection(_PUBLICATIONS)
        document = await collection.find_one(
            {
                "scope_key": _scope_key(scope),
                "scope": scope.model_dump(mode="json"),
                "app_id": app_id,
            }
        )
        if document is None:
            return None
        return self._parse_publication(document)

    async def _ensure_publication(
        self, *, scope: ExecutionAccessScopeRef, app_id: str
    ) -> ApplicationPublication:
        existing = await self.get_publication(scope=scope, app_id=app_id)
        if existing is not None:
            return existing
        publication = ApplicationPublication(
            scope=scope,
            app_id=app_id,
            current_revision_ref=None,
            generation=0,
        )
        document = {
            "_id": canonical_digest(
                {
                    "publication_schema_version": publication.publication_schema_version,
                    "scope": scope.model_dump(mode="json"),
                    "app_id": app_id,
                }
            ),
            "scope_key": _scope_key(scope),
            **publication.model_dump(mode="json"),
        }
        collection = await self._collection(_PUBLICATIONS)
        try:
            await collection.insert_one(document)
            return publication
        except DuplicateKeyError:
            concurrent = await self.get_publication(scope=scope, app_id=app_id)
            if concurrent is None:
                raise RevisionIntegrityError(
                    "publication uniqueness conflict did not resolve to one canonical row"
                ) from None
            return concurrent

    @staticmethod
    def _parse_publication(document: Mapping[str, Any]) -> ApplicationPublication:
        payload = {
            key: value
            for key, value in document.items()
            if key
            in {
                "publication_schema_version",
                "scope",
                "app_id",
                "current_revision_ref",
                "generation",
            }
        }
        try:
            return cast(ApplicationPublication, ApplicationPublication.model_validate(payload))
        except (TypeError, ValueError) as exc:
            raise RevisionIntegrityError(
                f"stored ApplicationPublication failed cold validation: {exc}"
            ) from exc

    async def promote_revision(
        self,
        *,
        scope: ExecutionAccessScopeRef,
        app_id: str,
        expected_current_revision_ref: ArtifactRevisionRef | None,
        expected_generation: int,
        new_revision_ref: ArtifactRevisionRef,
    ) -> PublicationResult:
        """Atomically select CURRENT using the backend's conditional-update primitive."""

        if expected_generation < 0:
            raise ValueError("expected_generation must be non-negative")
        if new_revision_ref.scope != scope or new_revision_ref.app_id != app_id:
            raise RevisionIntegrityError("new revision belongs to another scope or app")
        if expected_current_revision_ref is not None and (
            expected_current_revision_ref.scope != scope
            or expected_current_revision_ref.app_id != app_id
        ):
            raise RevisionIntegrityError("expected CURRENT belongs to another scope or app")
        new_revision = await self.resolve_revision(new_revision_ref, requesting_scope=scope)
        if new_revision.parent_revision_ref != expected_current_revision_ref:
            raise RevisionIntegrityError(
                "new revision parent does not equal expected CURRENT revision"
            )

        current = await self._ensure_publication(scope=scope, app_id=app_id)
        if current.current_revision_ref == new_revision_ref:
            return PublicationResult(
                outcome=PublicationOutcome.ALREADY_CURRENT, publication=current
            )

        collection = await self._collection(_PUBLICATIONS)
        updated = await collection.find_one_and_update(
            {
                "scope_key": _scope_key(scope),
                "scope": scope.model_dump(mode="json"),
                "app_id": app_id,
                "generation": expected_generation,
                "current_revision_ref": (
                    None
                    if expected_current_revision_ref is None
                    else expected_current_revision_ref.model_dump(mode="json")
                ),
            },
            {
                "$set": {"current_revision_ref": new_revision_ref.model_dump(mode="json")},
                "$inc": {"generation": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if updated is not None:
            return PublicationResult(
                outcome=PublicationOutcome.PROMOTED,
                publication=self._parse_publication(updated),
            )

        actual = await self.get_publication(scope=scope, app_id=app_id)
        if actual is None:
            raise RevisionIntegrityError("canonical publication row disappeared during CAS")
        if actual.current_revision_ref == new_revision_ref:
            return PublicationResult(outcome=PublicationOutcome.ALREADY_CURRENT, publication=actual)
        raise PublicationConflictError(actual)

    async def resolve_current_revision(
        self, *, scope: ExecutionAccessScopeRef, app_id: str
    ) -> ArtifactRevision | None:
        publication = await self.get_publication(scope=scope, app_id=app_id)
        if publication is None or publication.current_revision_ref is None:
            return None
        return await self.resolve_revision(publication.current_revision_ref, requesting_scope=scope)


__all__ = [
    "ArtifactRevisionStore",
    "PublicationConflictError",
    "RevisionIntegrityError",
    "RevisionNotFoundError",
    "RevisionStoreError",
]
