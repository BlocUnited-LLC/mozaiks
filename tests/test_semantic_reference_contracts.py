"""Adversarial tests for the strict scoped/unscoped reference contracts."""

from __future__ import annotations

import pydantic
import pytest

from mozaiksai.core.semantics import (
    ApplicationManifestRef,
    ArtifactRevisionRef,
    BuildContextBindingRef,
    ChildContractRef,
    CompilationPlanRef,
    ExecutionAccessScopeRef,
    ImplementationBindingRef,
    RefDocumentType,
    ReferenceResolutionError,
    RefinementPatchRef,
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraphRef,
    SemanticNode,
    SemanticNodeKind,
    SemanticReferenceResolver,
    TaxonomyNamespaceRef,
    build_semantic_graph,
)

DIGEST = "0" * 64
SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="ws-1")
OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-2")

SCOPED_REF_TYPES = [
    ApplicationManifestRef,
    SemanticGraphRef,
    ImplementationBindingRef,
    CompilationPlanRef,
    BuildContextBindingRef,
    RefinementPatchRef,
    ArtifactRevisionRef,
]


def _ref(ref_type, **overrides):
    fields = {
        "subject_id": "subject-1",
        "subject_version": 1,
        "content_digest": DIGEST,
        "scope": SCOPE,
    }
    fields.update(overrides)
    return ref_type(**fields)


@pytest.mark.parametrize("ref_type", SCOPED_REF_TYPES)
def test_scoped_refs_pin_all_identity_fields(ref_type) -> None:
    ref = _ref(ref_type)
    assert ref.subject_version == 1
    assert ref.content_digest == DIGEST
    assert ref.scope == SCOPE
    assert ref.ref_schema_version.startswith("mozaiks.")


@pytest.mark.parametrize("ref_type", SCOPED_REF_TYPES)
@pytest.mark.parametrize(
    "missing", ["subject_id", "subject_version", "content_digest", "scope"]
)
def test_missing_immutable_identity_fields_fail_closed(ref_type, missing) -> None:
    fields = {
        "subject_id": "subject-1",
        "subject_version": 1,
        "content_digest": DIGEST,
        "scope": SCOPE,
    }
    del fields[missing]
    with pytest.raises(pydantic.ValidationError):
        ref_type(**fields)


@pytest.mark.parametrize("ref_type", SCOPED_REF_TYPES)
def test_unknown_fields_fail_closed(ref_type) -> None:
    with pytest.raises(pydantic.ValidationError):
        _ref(ref_type, surprise="value")


@pytest.mark.parametrize("ref_type", SCOPED_REF_TYPES)
def test_refs_are_immutable(ref_type) -> None:
    ref = _ref(ref_type)
    with pytest.raises(pydantic.ValidationError):
        ref.subject_version = 2


@pytest.mark.parametrize("alias", ["latest", "current", "head", "tip", "newest"])
def test_mutable_alias_subject_ids_fail_closed(alias: str) -> None:
    with pytest.raises(pydantic.ValidationError, match="mutable alias"):
        _ref(SemanticGraphRef, subject_id=alias)


@pytest.mark.parametrize("bad_version", ["latest", "1", 0, -1, 1.5, None])
def test_non_immutable_versions_fail_closed(bad_version) -> None:
    with pytest.raises(pydantic.ValidationError):
        _ref(SemanticGraphRef, subject_version=bad_version)


@pytest.mark.parametrize("bad_digest", ["", "abc", "Z" * 64, "0" * 63, "0" * 65])
def test_malformed_digests_fail_closed(bad_digest: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        _ref(SemanticGraphRef, content_digest=bad_digest)


def test_taxonomy_namespace_ref_is_unscoped() -> None:
    ref = TaxonomyNamespaceRef(
        namespace_id="mozaiks.events", namespace_version=1, content_digest=DIGEST
    )
    assert not hasattr(ref, "scope")
    with pytest.raises(pydantic.ValidationError):
        TaxonomyNamespaceRef(
            namespace_id="mozaiks.events",
            namespace_version=1,
            content_digest=DIGEST,
            scope=SCOPE,
        )


def test_taxonomy_namespace_ref_rejects_alias_and_bad_version() -> None:
    with pytest.raises(pydantic.ValidationError):
        TaxonomyNamespaceRef(namespace_id="latest", namespace_version=1, content_digest=DIGEST)
    with pytest.raises(pydantic.ValidationError):
        TaxonomyNamespaceRef(
            namespace_id="mozaiks.events", namespace_version="latest", content_digest=DIGEST
        )


def test_child_contract_ref_pins_family_path_and_schema() -> None:
    ref = ChildContractRef(
        subject_id="module-users",
        subject_version=3,
        content_digest=DIGEST,
        scope=SCOPE,
        artifact_family="module_manifest",
        canonical_relative_path="modules/users/module.yaml",
        contract_schema_version="mozaiks.module.v1",
    )
    assert ref.artifact_family == "module_manifest"


@pytest.mark.parametrize(
    "bad_path",
    [
        "/abs/path.yaml",
        "a\\b.yaml",
        "C:/x.yaml",
        "../escape.yaml",
        "a/../b.yaml",
        "a//b.yaml",
        "a/./b.yaml",
        "a/*.yaml",
        "",
    ],
)
def test_child_contract_ref_rejects_non_canonical_paths(bad_path: str) -> None:
    with pytest.raises(pydantic.ValidationError):
        ChildContractRef(
            subject_id="module-users",
            subject_version=1,
            content_digest=DIGEST,
            scope=SCOPE,
            artifact_family="module_manifest",
            canonical_relative_path=bad_path,
            contract_schema_version="mozaiks.module.v1",
        )


def test_scope_ref_requires_valid_identifiers() -> None:
    with pytest.raises(pydantic.ValidationError):
        ExecutionAccessScopeRef(tenant_id="")
    with pytest.raises(pydantic.ValidationError):
        ExecutionAccessScopeRef(tenant_id="latest")
    scope = ExecutionAccessScopeRef(tenant_id="t1", pre_app_scope_id="pre-1")
    assert scope.pre_app_scope_id == "pre-1"


# ---------------------------------------------------------------------------
# Resolver behavior
# ---------------------------------------------------------------------------


def _graph(scope=SCOPE, version=1):
    node = SemanticNode(node_id="mozaiks.pages.home", kind=SemanticNodeKind.PAGE)
    event = SemanticNode(node_id="mozaiks.events.created", kind=SemanticNodeKind.EVENT)
    edge = SemanticEdge(
        kind=SemanticEdgeKind.EMITS,
        source_node_id="mozaiks.pages.home",
        target_node_id="mozaiks.events.created",
    )
    return build_semantic_graph(
        graph_id="graph-1", version=version, scope=scope, nodes=[node, event], edges=[edge]
    )


def _graph_ref(graph, **overrides):
    fields = {
        "subject_id": graph.graph_id,
        "subject_version": graph.version,
        "content_digest": graph.graph_digest,
        "scope": graph.scope,
    }
    fields.update(overrides)
    return SemanticGraphRef(**fields)


def test_resolver_returns_content_for_fully_pinned_ref() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    resolved = resolver.resolve(_graph_ref(graph), requesting_scope=SCOPE)
    assert resolved is graph


def test_resolver_rejects_unknown_version_without_fallback() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    with pytest.raises(ReferenceResolutionError, match="never fall back"):
        resolver.resolve(
            _graph_ref(graph, subject_version=2, content_digest=DIGEST),
            requesting_scope=SCOPE,
        )


def test_resolver_rejects_digest_mismatch() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    with pytest.raises(ReferenceResolutionError, match="digest mismatch"):
        resolver.resolve(_graph_ref(graph, content_digest=DIGEST), requesting_scope=SCOPE)


def test_resolver_rejects_document_type_mismatch() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    wrong_type_ref = ApplicationManifestRef(
        subject_id=graph.graph_id,
        subject_version=graph.version,
        content_digest=graph.graph_digest,
        scope=SCOPE,
    )
    with pytest.raises(ReferenceResolutionError, match="document type mismatch"):
        resolver.resolve(wrong_type_ref, requesting_scope=SCOPE)


def test_resolver_rejects_cross_scope_access() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    with pytest.raises(ReferenceResolutionError, match="cross-scope"):
        resolver.resolve(_graph_ref(graph), requesting_scope=OTHER_SCOPE)


def test_resolver_rejects_ref_scope_that_does_not_own_subject() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    foreign_ref = _graph_ref(graph, scope=OTHER_SCOPE)
    with pytest.raises(ReferenceResolutionError, match="scope"):
        resolver.resolve(foreign_ref, requesting_scope=OTHER_SCOPE)


def test_resolver_subjects_are_immutable() -> None:
    resolver = SemanticReferenceResolver()
    graph = _graph()
    resolver.register_semantic_graph(graph)
    with pytest.raises(ReferenceResolutionError, match="immutable"):
        resolver.register_semantic_graph(graph)


def test_resolver_has_no_bare_id_lookup_surface() -> None:
    resolver = SemanticReferenceResolver()
    public = [name for name in dir(resolver) if not name.startswith("_")]
    assert set(public) == {
        "register_semantic_graph",
        "register_application_manifest",
        "register_implementation_binding",
        "register_taxonomy_namespace",
        "register_opaque_subject",
        "resolve",
        "resolve_manifest_ref",
        "resolve_taxonomy_namespace",
    }


def test_opaque_subjects_resolve_for_ref_only_kinds() -> None:
    resolver = SemanticReferenceResolver()
    resolver.register_opaque_subject(
        kind=RefDocumentType.COMPILATION_PLAN,
        subject_id="plan-1",
        version=1,
        digest=DIGEST,
        scope=SCOPE,
    )
    ref = CompilationPlanRef(
        subject_id="plan-1", subject_version=1, content_digest=DIGEST, scope=SCOPE
    )
    assert resolver.resolve(ref, requesting_scope=SCOPE) is None


def test_content_bearing_kinds_cannot_register_opaque() -> None:
    resolver = SemanticReferenceResolver()
    with pytest.raises(ReferenceResolutionError, match="content-bearing"):
        resolver.register_opaque_subject(
            kind=RefDocumentType.SEMANTIC_GRAPH,
            subject_id="graph-1",
            version=1,
            digest=DIGEST,
            scope=SCOPE,
        )
