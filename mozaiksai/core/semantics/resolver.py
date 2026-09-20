"""Strict reference resolution over an in-memory subject store (test seam).

Every resolution verifies required fields (enforced by the ref models), the
expected document type, the exact immutable version, the content digest, and
the execution scope before returning anything.  There is no bare-id, path,
family-name, or "current"/"latest" lookup surface: the only way in is a fully
pinned reference plus the caller's own scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from mozaiksai.core.semantics.binding import ImplementationBinding
from mozaiksai.core.semantics.compilation_plan import CompilationPlan
from mozaiksai.core.semantics.graph import SemanticGraph, SemanticGraphV2
from mozaiksai.core.semantics.manifest import ApplicationManifest
from mozaiksai.core.semantics.payloads import SemanticPayloadBase, parse_semantic_payload
from mozaiksai.core.semantics.refs import (
    ApplicationManifestRef,
    ArtifactRevisionRef,
    BuildContextBindingRef,
    ChildContractRef,
    ExecutionAccessScopeRef,
    PlanUnitRef,
    RefDocumentType,
    RefinementPatchRef,
    SemanticPayloadRef,
    TaxonomyNamespaceRef,
    _ScopedRef,
)
from mozaiksai.core.taxonomy import TaxonomyNamespace

if TYPE_CHECKING:
    from mozaiksai.core.semantics.artifact_revision import ArtifactRevision


class ReferenceResolutionError(ValueError):
    """Raised when a reference fails verification against the store."""


@dataclass(frozen=True)
class _Subject:
    kind: RefDocumentType
    subject_id: str
    version: int
    digest: str
    scope: ExecutionAccessScopeRef | None
    content: Any
    reference_payload: dict[str, Any] | None = None


class SemanticReferenceResolver:
    """In-memory resolver for Slice 2 contract tests.

    Registration requires the full immutable identity; resolution re-verifies
    it.  Content-bearing documents (manifest, graph, binding, taxonomy
    namespaces, CompilationPlans, and ArtifactRevisions) are stored with
    content. Build-context bindings, refinement patches, and child contracts
    remain opaque digested subjects until their owning slices land.
    """

    def __init__(self) -> None:
        self._subjects: dict[tuple[RefDocumentType, str, int], _Subject] = {}
        self._artifact_revisions: dict[
            tuple[ExecutionAccessScopeRef, str, str], ArtifactRevision
        ] = {}
        self._plan_authority_inputs: dict[tuple[str, str], Any] = {}

    def _register(self, subject: _Subject) -> None:
        key = (subject.kind, subject.subject_id, subject.version)
        existing = self._subjects.get(key)
        if existing is not None:
            raise ReferenceResolutionError(
                f"subject {subject.subject_id!r} version {subject.version} is immutable "
                "and already registered"
            )
        self._subjects[key] = subject

    def register_semantic_graph(self, graph: SemanticGraph) -> None:
        self._register(
            _Subject(
                kind=RefDocumentType.SEMANTIC_GRAPH,
                subject_id=graph.graph_id,
                version=graph.version,
                digest=graph.graph_digest,
                scope=graph.scope,
                content=graph,
            )
        )

    def register_application_manifest(self, manifest: ApplicationManifest) -> None:
        self._register(
            _Subject(
                kind=RefDocumentType.APPLICATION_MANIFEST,
                subject_id=manifest.manifest_id,
                version=manifest.version,
                digest=manifest.manifest_digest,
                scope=manifest.scope,
                content=manifest,
            )
        )

    def register_implementation_binding(self, binding: ImplementationBinding) -> None:
        self._register(
            _Subject(
                kind=RefDocumentType.IMPLEMENTATION_BINDING,
                subject_id=binding.binding_id,
                version=binding.version,
                digest=binding.binding_digest,
                scope=binding.scope,
                content=binding,
            )
        )

    def register_semantic_payload(self, payload: SemanticPayloadBase) -> None:
        """Register one typed payload as an immutable content-bearing subject.

        Identity is (node_id, payload_version); re-registration fails closed.
        Many graph versions may pin one registered payload; cross-node or
        cross-scope reuse is impossible because resolution re-verifies the
        node binding and scope.
        """
        try:
            verified = parse_semantic_payload(payload.model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise ReferenceResolutionError(
                f"semantic payload failed cold validation: {exc}"
            ) from exc
        self._register(
            _Subject(
                kind=RefDocumentType.SEMANTIC_PAYLOAD,
                subject_id=verified.node_id,
                version=verified.payload_version,
                digest=verified.payload_digest,
                scope=verified.scope,
                content=verified,
            )
        )

    def resolve_semantic_payload(
        self, ref: SemanticPayloadRef, *, requesting_scope: ExecutionAccessScopeRef
    ) -> SemanticPayloadBase:
        """Verify document type, node binding, kind, version, digest, and scope."""
        subject = self._subjects.get(
            (RefDocumentType.SEMANTIC_PAYLOAD, ref.node_id, ref.payload_version)
        )
        if subject is None:
            raise ReferenceResolutionError(
                f"no semantic payload for node {ref.node_id!r} at immutable version "
                f"{ref.payload_version}; refs never fall back to another version"
            )
        if subject.kind is not RefDocumentType.SEMANTIC_PAYLOAD:
            raise ReferenceResolutionError(
                f"document type mismatch: ref expects semantic_payload, "
                f"stored subject is {subject.kind.value!r}"
            )
        if subject.digest != ref.content_digest:
            raise ReferenceResolutionError(
                f"content digest mismatch for payload of node {ref.node_id!r} "
                f"version {ref.payload_version}"
            )
        if subject.scope != ref.scope:
            raise ReferenceResolutionError(
                f"scope mismatch: ref scope does not own payload of node {ref.node_id!r}"
            )
        if requesting_scope != subject.scope:
            raise ReferenceResolutionError(
                f"cross-scope access to payload of node {ref.node_id!r} fails closed"
            )
        payload = subject.content
        if not isinstance(payload, SemanticPayloadBase):
            raise ReferenceResolutionError(
                f"subject for node {ref.node_id!r} did not resolve to a semantic payload"
            )
        kind = getattr(payload, "payload_kind", None)
        if getattr(kind, "value", None) != ref.payload_kind:
            raise ReferenceResolutionError(
                f"payload kind mismatch for node {ref.node_id!r}: ref expects {ref.payload_kind!r}"
            )
        return payload

    def register_semantic_graph_v2(self, graph: SemanticGraphV2) -> None:
        """Register a v2 graph only after its complete payload closure resolves.

        Every pinned payload reference must already resolve in this store —
        there is no partial registration and no "current"/latest payload
        lookup anywhere; resolution is by full identity only.
        """
        try:
            verified = SemanticGraphV2.model_validate(graph.model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise ReferenceResolutionError(
                f"semantic graph v2 failed cold validation: {exc}"
            ) from exc
        for node in verified.nodes:
            self.resolve_semantic_payload(node.payload_ref, requesting_scope=verified.scope)
        self._register(
            _Subject(
                kind=RefDocumentType.SEMANTIC_GRAPH,
                subject_id=verified.graph_id,
                version=verified.version,
                digest=verified.graph_digest,
                scope=verified.scope,
                content=verified,
            )
        )

    def register_compilation_plan(self, plan: CompilationPlan) -> None:
        """Register the aggregate plan as an immutable content-bearing subject.

        Only the aggregate registers: embedded family-instance plans have no
        document type and no registration surface of their own.
        """
        try:
            verified = CompilationPlan.model_validate(plan.model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise ReferenceResolutionError(
                f"compilation plan failed cold validation: {exc}"
            ) from exc
        self._register(
            _Subject(
                kind=RefDocumentType.COMPILATION_PLAN,
                subject_id=verified.graph_id,
                version=verified.graph_version,
                digest=verified.plan_digest,
                scope=verified.scope,
                content=verified,
            )
        )

    def register_compilation_plan_authority_inputs(self, authority_inputs) -> None:
        """Register one immutable authority-inputs document, digest-keyed.

        The document is cold-revalidated on registration and again on
        resolution; registration never confers derivation trust — consumers
        must canonically rederive the plan from the resolved document.
        """
        from mozaiksai.core.semantics.plan_authority import (
            CompilationPlanAuthorityInputs,
            compilation_plan_authority_digest,
        )

        try:
            verified = CompilationPlanAuthorityInputs.model_validate(
                authority_inputs.model_dump(mode="json")
            )
        except (TypeError, ValueError) as exc:
            raise ReferenceResolutionError(
                f"authority inputs failed cold validation: {exc}"
            ) from exc
        digest = compilation_plan_authority_digest(verified)
        key = (verified.graph.scope.model_dump_json(), digest)
        existing = self._plan_authority_inputs.get(key)
        if existing is not None:
            if compilation_plan_authority_digest(existing) != digest:
                raise ReferenceResolutionError(
                    "authority-inputs registration is immutable"
                )
            return
        self._plan_authority_inputs[key] = verified

    def resolve_compilation_plan_authority_inputs(
        self, ref, *, requesting_scope
    ):
        """Resolve one authority-inputs document by exact scope and digest."""
        from mozaiksai.core.semantics.plan_authority import (
            CompilationPlanAuthorityInputs,
            compilation_plan_authority_digest,
        )
        from mozaiksai.core.semantics.refs import CompilationPlanAuthorityRef

        verified_ref = CompilationPlanAuthorityRef.model_validate(
            ref.model_dump(mode="json")
        )
        if requesting_scope != verified_ref.scope:
            raise ReferenceResolutionError(
                "authority-inputs resolution crosses execution scope"
            )
        key = (verified_ref.scope.model_dump_json(), verified_ref.authority_digest)
        document = self._plan_authority_inputs.get(key)
        if document is None:
            raise ReferenceResolutionError(
                "unknown compilation-plan authority inputs "
                f"{verified_ref.authority_digest[:12]!r}"
            )
        revalidated = CompilationPlanAuthorityInputs.model_validate(
            document.model_dump(mode="json")
        )
        if (
            compilation_plan_authority_digest(revalidated)
            != verified_ref.authority_digest
        ):
            raise ReferenceResolutionError(
                "authority-inputs content digest mismatch"
            )
        return revalidated

    def register_artifact_revision(self, revision: ArtifactRevision) -> None:
        """Register one content-bearing revision by its full scoped digest identity."""

        from mozaiksai.core.semantics.artifact_revision import ArtifactRevision

        try:
            verified = ArtifactRevision.model_validate(revision.model_dump(mode="json"))
        except (TypeError, ValueError) as exc:
            raise ReferenceResolutionError(
                f"artifact revision failed cold validation: {exc}"
            ) from exc
        key = (verified.scope, verified.app_id, verified.revision_digest)
        if key in self._artifact_revisions:
            raise ReferenceResolutionError(
                f"artifact revision {verified.revision_digest!r} is immutable and already registered"
            )
        self._artifact_revisions[key] = verified

    def resolve_artifact_revision(
        self,
        ref: ArtifactRevisionRef,
        *,
        requesting_scope: ExecutionAccessScopeRef,
    ) -> ArtifactRevision:
        """Resolve a revision without any mutable alias or cross-scope fallback."""

        from mozaiksai.core.semantics.artifact_revision import ArtifactRevision

        verified_ref = ArtifactRevisionRef.model_validate(ref.model_dump(mode="json"))
        if requesting_scope != verified_ref.scope:
            raise ReferenceResolutionError("cross-scope artifact revision access fails closed")
        revision = self._artifact_revisions.get(
            (verified_ref.scope, verified_ref.app_id, verified_ref.revision_digest)
        )
        if revision is None:
            raise ReferenceResolutionError("artifact revision ref did not resolve exactly")
        verified = ArtifactRevision.model_validate(revision.model_dump(mode="json"))
        if verified.ref != verified_ref:
            raise ReferenceResolutionError("artifact revision ref identity mismatch")
        return cast(ArtifactRevision, verified)

    def resolve_plan_unit(
        self, ref: PlanUnitRef, *, requesting_scope: ExecutionAccessScopeRef
    ) -> Any:
        """Resolve only through the registered aggregate and verify unit identity."""
        from mozaiksai.core.semantics.canonical import canonical_digest

        verified_ref = PlanUnitRef.model_validate(ref.model_dump(mode="json"))
        content = self.resolve(verified_ref.compilation_plan_ref, requesting_scope=requesting_scope)
        if not isinstance(content, CompilationPlan):
            raise ReferenceResolutionError("compilation plan ref did not resolve to a plan")
        try:
            verified_plan = CompilationPlan.model_validate(content.model_dump(mode="json"))
            unit = verified_plan.unit(verified_ref.unit_id)
        except (TypeError, ValueError) as exc:
            raise ReferenceResolutionError(f"plan unit failed cold resolution: {exc}") from exc
        if canonical_digest(unit.identity_payload) != verified_ref.unit_digest:
            raise ReferenceResolutionError("plan unit digest mismatch")
        return unit

    def register_taxonomy_namespace(self, namespace: TaxonomyNamespace, digest: str) -> None:
        ref = TaxonomyNamespaceRef(
            namespace_id=namespace.namespace_id,
            namespace_version=namespace.version,
            content_digest=digest,
        )
        self._register(
            _Subject(
                kind=RefDocumentType.TAXONOMY_NAMESPACE,
                subject_id=ref.namespace_id,
                version=ref.namespace_version,
                digest=ref.content_digest,
                scope=None,
                content=namespace,
            )
        )

    def register_opaque_subject(
        self,
        *,
        kind: RefDocumentType,
        subject_id: str,
        version: int,
        digest: str,
        scope: ExecutionAccessScopeRef,
        artifact_family: str | None = None,
        canonical_relative_path: str | None = None,
        contract_schema_version: str | None = None,
    ) -> None:
        if kind in {
            RefDocumentType.SEMANTIC_GRAPH,
            RefDocumentType.APPLICATION_MANIFEST,
            RefDocumentType.IMPLEMENTATION_BINDING,
            RefDocumentType.TAXONOMY_NAMESPACE,
            RefDocumentType.SEMANTIC_PAYLOAD,
            RefDocumentType.COMPILATION_PLAN,
            RefDocumentType.ARTIFACT_REVISION,
        }:
            raise ReferenceResolutionError(
                f"{kind.value} is content-bearing in this slice; register its document"
            )

        fields: dict[str, Any] = {
            "subject_id": subject_id,
            "subject_version": version,
            "content_digest": digest,
            "scope": scope,
        }
        ref_types = {
            RefDocumentType.BUILD_CONTEXT_BINDING: BuildContextBindingRef,
            RefDocumentType.REFINEMENT_PATCH: RefinementPatchRef,
        }
        if kind is RefDocumentType.CHILD_CONTRACT:
            fields.update(
                artifact_family=artifact_family,
                canonical_relative_path=canonical_relative_path,
                contract_schema_version=contract_schema_version,
            )
            ref: _ScopedRef = ChildContractRef(**fields)
        else:
            if any(
                value is not None
                for value in (
                    artifact_family,
                    canonical_relative_path,
                    contract_schema_version,
                )
            ):
                raise ReferenceResolutionError(
                    "child-contract identity fields are valid only for child contracts"
                )
            ref_type = ref_types.get(kind)
            if ref_type is None:
                raise ReferenceResolutionError(f"unsupported opaque document type {kind!r}")
            ref = ref_type(**fields)
        self._register(
            _Subject(
                kind=kind,
                subject_id=ref.subject_id,
                version=ref.subject_version,
                digest=ref.content_digest,
                scope=ref.scope,
                content=None,
                reference_payload=ref.model_dump(mode="json"),
            )
        )

    def resolve(self, ref: _ScopedRef, *, requesting_scope: ExecutionAccessScopeRef) -> Any:
        """Verify type, exact version, digest, and scope; return stored content."""
        subject = self._subjects.get((type(ref).document_type, ref.subject_id, ref.subject_version))
        if subject is None:
            conflicting = next(
                (
                    candidate
                    for (kind, subject_id, version), candidate in self._subjects.items()
                    if subject_id == ref.subject_id
                    and version == ref.subject_version
                    and kind is not type(ref).document_type
                ),
                None,
            )
            if conflicting is not None:
                raise ReferenceResolutionError(
                    f"document type mismatch: ref expects {type(ref).document_type.value!r}, "
                    f"stored subject is {conflicting.kind.value!r}"
                )
            raise ReferenceResolutionError(
                f"no subject {ref.subject_id!r} at immutable version "
                f"{ref.subject_version}; refs never fall back to another version"
            )
        if subject.kind is not type(ref).document_type:
            raise ReferenceResolutionError(
                f"document type mismatch: ref expects {type(ref).document_type.value!r}, "
                f"stored subject is {subject.kind.value!r}"
            )
        if subject.digest != ref.content_digest:
            raise ReferenceResolutionError(
                f"content digest mismatch for {ref.subject_id!r} version {ref.subject_version}"
            )
        if subject.scope != ref.scope:
            raise ReferenceResolutionError(
                f"scope mismatch: ref scope does not own subject {ref.subject_id!r}"
            )
        if requesting_scope != subject.scope:
            raise ReferenceResolutionError(f"cross-scope access to {ref.subject_id!r} fails closed")
        if (
            subject.reference_payload is not None
            and ref.model_dump(mode="json") != subject.reference_payload
        ):
            raise ReferenceResolutionError(
                f"typed reference identity mismatch for {ref.subject_id!r}"
            )
        return subject.content

    def resolve_manifest_ref(
        self, ref: ApplicationManifestRef, *, requesting_scope: ExecutionAccessScopeRef
    ) -> ApplicationManifest:
        content = self.resolve(ref, requesting_scope=requesting_scope)
        if not isinstance(content, ApplicationManifest):
            raise ReferenceResolutionError(
                f"subject {ref.subject_id!r} did not resolve to an ApplicationManifest"
            )
        return content

    def resolve_taxonomy_namespace(self, ref: TaxonomyNamespaceRef) -> TaxonomyNamespace:
        subject = self._subjects.get(
            (RefDocumentType.TAXONOMY_NAMESPACE, ref.namespace_id, ref.namespace_version)
        )
        if subject is None:
            raise ReferenceResolutionError(
                f"no taxonomy namespace {ref.namespace_id!r} at version {ref.namespace_version}"
            )
        if subject.kind is not RefDocumentType.TAXONOMY_NAMESPACE:
            raise ReferenceResolutionError(
                f"document type mismatch: {ref.namespace_id!r} is {subject.kind.value!r}"
            )
        if subject.digest != ref.content_digest:
            raise ReferenceResolutionError(
                f"content digest mismatch for taxonomy namespace {ref.namespace_id!r}"
            )
        if not isinstance(subject.content, TaxonomyNamespace):
            raise ReferenceResolutionError(
                f"subject {ref.namespace_id!r} did not resolve to a TaxonomyNamespace"
            )
        return subject.content


__all__ = [
    "ReferenceResolutionError",
    "SemanticReferenceResolver",
]
