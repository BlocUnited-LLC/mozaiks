"""ADR 0007 Slice 2: strict, immutable semantic-compiler contracts.

This package is a contract layer behind non-production/test seams: no host,
loader, generator, workflow, or control-plane code imports it.  It takes no
authority from the current generator, runtime contracts, refinement system, or
``AppBuildPlan``.  Later rollout slices wire it in; importing it never changes
runtime behavior.
"""

from mozaiksai.core.semantics.binding import (
    ActionInputBinding,
    CapabilityPackSelection,
    CommitApprovalRequirement,
    ConstantSource,
    DeploymentProfileSelection,
    FieldsProjection,
    IdentityProjection,
    ImplementationBinding,
    ModuleActionImplementationSelection,
    RendererSelection,
    RequestContextPropertySource,
    ResolvedImplementationBindingAuthority,
    ResultPropertySource,
    WorkflowImplementationSelection,
    WorkflowResultCommitBinding,
    WorkflowResultImplementationBinding,
    build_implementation_binding,
    empty_request_context_contract,
    validate_implementation_binding_against_graph,
    validate_implementation_binding_content_authority,
)
from mozaiksai.core.semantics.canonical import (
    CANONICAL_SERIALIZATION_VERSION,
    CanonicalSerializationError,
    canonical_digest,
    canonical_json,
)
from mozaiksai.core.semantics.capabilities import (
    SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY,
    SEMANTIC_TAXONOMY_CAPABILITY,
    advertised_semantic_compiler_capabilities,
    semantic_capability_advertisement_gate,
)
from mozaiksai.core.semantics.graph import (
    SemanticEdge,
    SemanticEdgeKind,
    SemanticGraph,
    SemanticNode,
    SemanticNodeKind,
    TaxonomyReference,
    build_semantic_graph,
    validate_semantic_graph_taxonomy_closure,
)
from mozaiksai.core.semantics.manifest import (
    ApplicationManifest,
    build_application_manifest,
)
from mozaiksai.core.semantics.opaque_artifact import PreservedOpaqueArtifact
from mozaiksai.core.semantics.refs import (
    ApplicationManifestRef,
    ArtifactRevisionRef,
    BuildContextBindingRef,
    ChildContractRef,
    CompilationPlanRef,
    ExecutionAccessScopeRef,
    ImplementationBindingRef,
    PlanUnitRef,
    RefDocumentType,
    RefinementPatchRef,
    SemanticGraphRef,
    SemanticRefError,
    TaxonomyNamespaceRef,
)
from mozaiksai.core.semantics.resolver import (
    ReferenceResolutionError,
    SemanticReferenceResolver,
)

__all__ = [
    "ActionInputBinding",
    "ApplicationManifest",
    "ApplicationManifestRef",
    "ArtifactRevisionRef",
    "BuildContextBindingRef",
    "CANONICAL_SERIALIZATION_VERSION",
    "CanonicalSerializationError",
    "CapabilityPackSelection",
    "ChildContractRef",
    "CommitApprovalRequirement",
    "CompilationPlanRef",
    "ConstantSource",
    "DeploymentProfileSelection",
    "ExecutionAccessScopeRef",
    "FieldsProjection",
    "IdentityProjection",
    "ImplementationBinding",
    "ImplementationBindingRef",
    "ModuleActionImplementationSelection",
    "PlanUnitRef",
    "PreservedOpaqueArtifact",
    "RefDocumentType",
    "ReferenceResolutionError",
    "RefinementPatchRef",
    "RendererSelection",
    "RequestContextPropertySource",
    "ResolvedImplementationBindingAuthority",
    "ResultPropertySource",
    "SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY",
    "SEMANTIC_TAXONOMY_CAPABILITY",
    "SemanticEdge",
    "SemanticEdgeKind",
    "SemanticGraph",
    "SemanticGraphRef",
    "SemanticNode",
    "SemanticNodeKind",
    "SemanticRefError",
    "SemanticReferenceResolver",
    "TaxonomyNamespaceRef",
    "TaxonomyReference",
    "WorkflowImplementationSelection",
    "WorkflowResultCommitBinding",
    "WorkflowResultImplementationBinding",
    "advertised_semantic_compiler_capabilities",
    "build_application_manifest",
    "build_implementation_binding",
    "build_semantic_graph",
    "canonical_digest",
    "canonical_json",
    "empty_request_context_contract",
    "semantic_capability_advertisement_gate",
    "validate_implementation_binding_against_graph",
    "validate_implementation_binding_content_authority",
    "validate_semantic_graph_taxonomy_closure",
]
