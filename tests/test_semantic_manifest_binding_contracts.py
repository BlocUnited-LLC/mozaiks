"""Adversarial tests for ApplicationManifest, ImplementationBinding, and the
ADR 0006 capability-advertisement interlock."""

from __future__ import annotations

import pydantic
import pytest

from mozaiksai.core.app_context.models import AppContextMode
from mozaiksai.core.semantics import (
    SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY,
    SEMANTIC_TAXONOMY_CAPABILITY,
    ApplicationManifest,
    ArtifactRevisionRef,
    BuildContextBindingRef,
    CapabilityPackSelection,
    DeploymentProfileSelection,
    ExecutionAccessScopeRef,
    ImplementationBindingRef,
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraphRef,
    SemanticNode,
    SemanticNodeKind,
    TaxonomyNamespaceRef,
    advertised_semantic_compiler_capabilities,
    build_application_manifest,
    build_implementation_binding,
    build_semantic_graph,
    semantic_capability_advertisement_gate,
    validate_implementation_binding_against_graph,
)
from mozaiksai.core.semantics.binding import ImplementationBindingError

DIGEST = "0" * 64
PRE_APP_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-1", pre_app_scope_id="creation-1")
SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-1", workspace_id="ws-1")
OTHER_SCOPE = ExecutionAccessScopeRef(tenant_id="tenant-2")


def _graph(scope=SCOPE):
    nodes = [
        SemanticNode(node_id="mozaiks.pages.home", kind=SemanticNodeKind.PAGE),
        SemanticNode(node_id="mozaiks.capabilities.billing", kind=SemanticNodeKind.CAPABILITY),
        SemanticNode(node_id="mozaiks.targets.default", kind=SemanticNodeKind.DEPLOYMENT_TARGET),
        SemanticNode(node_id="mozaiks.events.paid", kind=SemanticNodeKind.EVENT),
    ]
    edges = [
        SemanticEdge(
            kind=SemanticEdgeKind.EMITS,
            source_node_id="mozaiks.capabilities.billing",
            target_node_id="mozaiks.events.paid",
        )
    ]
    return build_semantic_graph(
        graph_id="graph-1", version=1, scope=scope, nodes=nodes, edges=edges
    )


def _graph_ref(graph):
    return SemanticGraphRef(
        subject_id=graph.graph_id,
        subject_version=graph.version,
        content_digest=graph.graph_digest,
        scope=graph.scope,
    )


def _binding(graph, **overrides):
    fields = {
        "binding_id": "binding-1",
        "version": 1,
        "scope": graph.scope,
        "semantic_graph_ref": _graph_ref(graph),
        "capability_pack_selections": (
            CapabilityPackSelection(
                requirement_node_id="mozaiks.capabilities.billing",
                pack_id="mozaikspay",
                pack_digest=DIGEST,
            ),
        ),
        "renderer_selections": (),
        "deployment_profile_selections": (
            DeploymentProfileSelection(
                requirement_node_id="mozaiks.targets.default",
                profile_id="docker_compose",
                profile_version="1",
            ),
        ),
    }
    fields.update(overrides)
    return build_implementation_binding(**fields)


def _manifest_fields(scope=PRE_APP_SCOPE, **overrides):
    graph = _graph(scope=scope)
    binding = _binding(graph)
    fields = {
        "manifest_id": "manifest-1",
        "version": 1,
        "scope": scope,
        "mode": AppContextMode.GREENFIELD,
        "app_id": None,
        "semantic_graph_ref": _graph_ref(graph),
        "implementation_binding_ref": ImplementationBindingRef(
            subject_id=binding.binding_id,
            subject_version=binding.version,
            content_digest=binding.binding_digest,
            scope=scope,
        ),
        "taxonomy_refs": (
            TaxonomyNamespaceRef(
                namespace_id="mozaiks.events", namespace_version=1, content_digest=DIGEST
            ),
        ),
        "build_context_binding_ref": BuildContextBindingRef(
            subject_id="build-context-1",
            subject_version=1,
            content_digest=DIGEST,
            scope=scope,
        ),
        "artifact_revision_ref": None,
        "compiler_version": "mozaiks.compiler.v1",
        "renderer_registry_version": "mozaiks.app_layout.v1",
    }
    fields.update(overrides)
    return fields


# ---------------------------------------------------------------------------
# ApplicationManifest
# ---------------------------------------------------------------------------


def test_pre_app_manifest_requires_creation_scope_not_app_id() -> None:
    manifest = build_application_manifest(**_manifest_fields())
    assert manifest.app_id is None
    assert manifest.scope.pre_app_scope_id == "creation-1"

    no_creation_scope = ExecutionAccessScopeRef(tenant_id="tenant-1")
    with pytest.raises(pydantic.ValidationError, match="pre_app_scope_id"):
        build_application_manifest(**_manifest_fields(scope=no_creation_scope))


def test_assigning_app_id_is_a_new_immutable_version() -> None:
    pre = build_application_manifest(**_manifest_fields())
    with pytest.raises(pydantic.ValidationError):
        pre.app_id = "app-1"

    assigned = build_application_manifest(**_manifest_fields(version=2, app_id="app-1"))
    assert assigned.version == 2
    assert assigned.app_id == "app-1"
    assert assigned.scope == pre.scope
    assert assigned.manifest_digest != pre.manifest_digest
    assert pre.app_id is None


@pytest.mark.parametrize("mode", list(AppContextMode))
def test_manifest_supports_all_context_modes(mode: AppContextMode) -> None:
    manifest = build_application_manifest(**_manifest_fields(mode=mode))
    assert manifest.mode is mode


def test_manifest_rejects_cross_scope_references() -> None:
    fields = _manifest_fields()
    foreign_graph = _graph(scope=OTHER_SCOPE)
    fields["semantic_graph_ref"] = _graph_ref(foreign_graph)
    with pytest.raises(pydantic.ValidationError, match="cross-scope"):
        build_application_manifest(**fields)


def test_manifest_rejects_foreign_app_artifact_revision_ref() -> None:
    fields = _manifest_fields()
    fields["artifact_revision_ref"] = ArtifactRevisionRef(
        scope=fields["scope"], app_id="another-app", revision_digest=DIGEST
    )
    with pytest.raises(pydantic.ValidationError, match="foreign-app"):
        build_application_manifest(**fields)

    fields = _manifest_fields()
    fields["artifact_revision_ref"] = ArtifactRevisionRef(
        scope=OTHER_SCOPE, app_id="app-1", revision_digest=DIGEST
    )
    with pytest.raises(pydantic.ValidationError, match="cross-scope"):
        build_application_manifest(**fields)


def test_manifest_rejects_unknown_fields_and_duplicate_taxonomy_pins() -> None:
    with pytest.raises(pydantic.ValidationError):
        build_application_manifest(**_manifest_fields(), surprise=True)

    duplicate = (
        TaxonomyNamespaceRef(
            namespace_id="mozaiks.events", namespace_version=1, content_digest=DIGEST
        ),
        TaxonomyNamespaceRef(
            namespace_id="mozaiks.events", namespace_version=2, content_digest=DIGEST
        ),
    )
    with pytest.raises(pydantic.ValidationError, match="duplicate pinned taxonomy"):
        build_application_manifest(**_manifest_fields(taxonomy_refs=duplicate))

    with pytest.raises(pydantic.ValidationError):
        build_application_manifest(**_manifest_fields(taxonomy_refs=()))


def test_manifest_digest_tamper_fails_closed() -> None:
    manifest = build_application_manifest(**_manifest_fields())
    payload = manifest.model_dump()
    payload["manifest_digest"] = "0" * 64
    with pytest.raises(pydantic.ValidationError, match="manifest_digest"):
        ApplicationManifest(**payload)


def test_manifest_references_do_not_duplicate_graph_content() -> None:
    field_names = set(ApplicationManifest.model_fields)
    assert field_names == {
        "schema_version",
        "manifest_id",
        "version",
        "scope",
        "mode",
        "app_id",
        "semantic_graph_ref",
        "implementation_binding_ref",
        "taxonomy_refs",
        "build_context_binding_ref",
        "artifact_revision_ref",
        "compiler_version",
        "renderer_registry_version",
        "manifest_digest",
    }


# ---------------------------------------------------------------------------
# ImplementationBinding
# ---------------------------------------------------------------------------


def test_valid_binding_passes_graph_validation() -> None:
    graph = _graph()
    binding = _binding(graph)
    validate_implementation_binding_against_graph(binding, graph)


def test_binding_cannot_select_for_absent_nodes() -> None:
    graph = _graph()
    binding = _binding(
        graph,
        capability_pack_selections=(
            CapabilityPackSelection(
                requirement_node_id="mozaiks.capabilities.ghost",
                pack_id="mozaikspay",
                pack_digest=DIGEST,
            ),
        ),
    )
    with pytest.raises(ImplementationBindingError, match="cannot widen graph semantics"):
        validate_implementation_binding_against_graph(binding, graph)


def test_binding_cannot_select_against_wrong_node_kind() -> None:
    graph = _graph()
    binding = _binding(
        graph,
        capability_pack_selections=(
            CapabilityPackSelection(
                requirement_node_id="mozaiks.events.paid",
                pack_id="mozaikspay",
                pack_digest=DIGEST,
            ),
        ),
    )
    with pytest.raises(ImplementationBindingError, match="allowed kinds"):
        validate_implementation_binding_against_graph(binding, graph)


def test_binding_has_no_fields_that_could_add_semantics() -> None:
    from mozaiksai.core.semantics.binding import ImplementationBinding

    assert set(ImplementationBinding.model_fields) == {
        "schema_version",
        "binding_id",
        "version",
        "scope",
        "semantic_graph_ref",
        "capability_pack_selections",
        "renderer_selections",
        "deployment_profile_selections",
        "binding_digest",
    }
    with pytest.raises(pydantic.ValidationError):
        _binding(_graph(), pages=("mozaiks.pages.extra",))


def test_binding_pin_must_match_graph_identity() -> None:
    graph = _graph()
    binding = _binding(graph)
    other = build_semantic_graph(
        graph_id="graph-1",
        version=2,
        scope=SCOPE,
        nodes=list(graph.nodes),
        edges=list(graph.edges),
    )
    with pytest.raises(ImplementationBindingError, match="pin"):
        validate_implementation_binding_against_graph(binding, other)


def test_binding_scope_must_match_graph_scope() -> None:
    graph = _graph()
    foreign_graph = _graph(scope=OTHER_SCOPE)
    binding = _binding(foreign_graph)
    with pytest.raises(ImplementationBindingError, match="scope"):
        validate_implementation_binding_against_graph(binding, graph)


def test_duplicate_selections_for_one_requirement_fail_closed() -> None:
    graph = _graph()
    with pytest.raises(pydantic.ValidationError, match="duplicate capability-pack"):
        _binding(
            graph,
            capability_pack_selections=(
                CapabilityPackSelection(
                    requirement_node_id="mozaiks.capabilities.billing",
                    pack_id="mozaikspay",
                    pack_digest=DIGEST,
                ),
                CapabilityPackSelection(
                    requirement_node_id="mozaiks.capabilities.billing",
                    pack_id="other_pack",
                    pack_digest=DIGEST,
                ),
            ),
        )


def test_binding_is_immutable_and_digest_tamper_fails() -> None:
    graph = _graph()
    binding = _binding(graph)
    with pytest.raises(pydantic.ValidationError):
        binding.version = 2
    payload = binding.model_dump()
    payload["binding_digest"] = "0" * 64
    from mozaiksai.core.semantics.binding import ImplementationBinding

    with pytest.raises(pydantic.ValidationError, match="binding_digest"):
        ImplementationBinding(**payload)


# ---------------------------------------------------------------------------
# ADR 0006 capability interlock
# ---------------------------------------------------------------------------


def test_capability_identifiers_match_adr_0007() -> None:
    assert SEMANTIC_TAXONOMY_CAPABILITY == "semantic_taxonomy_v1"
    assert SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY == "semantic_reference_contracts_v1"


def test_capabilities_are_not_advertised_and_never_partially() -> None:
    advertised = advertised_semantic_compiler_capabilities()
    assert advertised == ()
    # Joint-or-none: the only legal non-empty answer is both identifiers.
    assert set(advertised) in (
        set(),
        {SEMANTIC_TAXONOMY_CAPABILITY, SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY},
    )


def test_advertisement_gate_names_the_advisory_mode_blocker() -> None:
    gate = semantic_capability_advertisement_gate()
    assert "advisory" in gate
    assert "blocked" in gate
