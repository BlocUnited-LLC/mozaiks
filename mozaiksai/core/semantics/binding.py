"""Immutable ``mozaiks.implementation_binding.v2`` contract.

An implementation binding answers exactly one question: WHICH exact
implementation realizes this SemanticGraph.  It maps graph-authored
requirements to verified capability-pack identities and digests,
registry-owned artifact families to renderer implementation/version
identities, deployment-target implementation profiles, and — new in v2 —
exact content-addressed workflow implementations, certified module-action
implementations, and deterministic workflow-result realization:

- every semantic WORKFLOW that owns a capability binds to its exact
  ``orchestrator.yaml``/``structured_outputs.yaml`` documents through the
  #488 selection primitives;
- every module action a workflow-capability binding consumes or commits
  through binds to its exact certified implementation sources, pinning the
  recomputable certification identity;
- every semantic WORKFLOW_RESULT binds to one structured-output contract of
  its own workflow's exact selected documents, one bounded projection under
  ``mozaiks.result_projection.v1``, and the complete explicit action-input
  wiring for each semantic commit relationship.

SemanticGraph still owns WHAT the application means; CompilationPlan is still
derived later.  No selection can add semantic facts, registry rows, private
strategy, build-context input, provider policy, runtime approval state,
provider/model identity, or AG2 execution identity.  The binding stays
immutable, content-addressed, provider-neutral, runtime-neutral, and
recomputable: ``binding_digest`` covers every meaning-bearing field, and cold
validation re-resolves every selection through verified content only.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import Field, ValidationInfo, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import (
    AppLayoutRegistry,
    MaterializerIdentifier,
    default_app_layout_registry,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.canonical_json import CanonicalJsonValue
from mozaiksai.core.semantics.closed_contracts import ObjectContract
from mozaiksai.core.semantics.graph import (
    SemanticGraph,
    SemanticGraphV2,
    SemanticNodeKind,
    _validate_node_id,
)
from mozaiksai.core.semantics.implementation_artifacts import (
    HandlerCertificationMode,
    HandlerMethodSource,
    ImplementationArtifactError,
    ResolvedModuleActionImplementation,
    ResolvedWorkflowImplementation,
    SelectedAccountedArtifact,
    SelectedContractArtifact,
    pair_workflow_implementation_artifacts,
    resolve_module_action_implementation,
    resolve_selected_structured_output_contract,
    resolve_workflow_orchestrator_artifact,
    resolve_workflow_structured_outputs_artifact,
)
from mozaiksai.core.semantics.payloads import (
    ActionPayload,
    ModulePayload,
    SemanticPayloadBase,
    WorkflowCapabilityBindingPayload,
    WorkflowCapabilityBindingRole,
    WorkflowCapabilityPayload,
    WorkflowPayload,
    WorkflowResultPayload,
    validate_semantic_graph_v2_payload_closure,
)
from mozaiksai.core.semantics.refs import (
    ExecutionAccessScopeRef,
    SemanticGraphRef,
    SemanticsModel,
    _validate_digest,
    _validate_identifier,
)
from mozaiksai.core.workflow.structured_output_contracts import (
    StructuredOutputContractRef,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from mozaiksai.core.artifacts.content_store import ArtifactContentStore

IMPLEMENTATION_BINDING_SCHEMA_VERSION: Literal["mozaiks.implementation_binding.v2"] = (
    "mozaiks.implementation_binding.v2"
)

#: The one supported result-projection language of this binding version.
RESULT_PROJECTION_PROFILE_VERSION: Literal["mozaiks.result_projection.v1"] = (
    "mozaiks.result_projection.v1"
)

#: Node kinds each selection category may satisfy.  A selection against any
#: other kind is an attempt to widen graph semantics and fails closed.
CAPABILITY_PACK_REQUIREMENT_KINDS = frozenset(
    {SemanticNodeKind.CAPABILITY, SemanticNodeKind.MODULE}
)
RENDERER_GRAPH_SCHEMA_VERSION: Literal["mozaiks.semantic_graph.v2"] = (
    "mozaiks.semantic_graph.v2"
)
DEPLOYMENT_REQUIREMENT_KINDS = frozenset({SemanticNodeKind.DEPLOYMENT_TARGET})

#: One top-level property name.  The grammar structurally excludes every
#: traversal form — dots, brackets, indexes, wildcards, expressions — so a
#: nested/dotted/indexed projection or wiring attempt cannot even parse.
_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: The binding-role subset that demands an exact action implementation.
_ACTION_IMPLEMENTATION_ROLES = frozenset(
    {
        WorkflowCapabilityBindingRole.CONSUMES_ACTION,
        WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION,
    }
)

_BUILDER_CONTEXT_KEY = "mozaiks_implementation_binding_builder"
_PLACEHOLDER_DIGEST = "0" * 64


class _GraphSubject(Protocol):
    """Structural surface shared by immutable graph v1 and graph v2.

    Members are read-only properties so frozen graph models with narrower
    field types (graph v2's ``Literal`` schema version and typed node tuple)
    satisfy the protocol covariantly.
    """

    @property
    def schema_version(self) -> str: ...

    @property
    def graph_id(self) -> str: ...

    @property
    def version(self) -> int: ...

    @property
    def scope(self) -> ExecutionAccessScopeRef: ...

    @property
    def graph_digest(self) -> str: ...

    @property
    def nodes(self) -> tuple[Any, ...]: ...


class ImplementationBindingError(ValueError):
    """Raised when a binding violates the contract against its graph."""


def _validate_property_name(value: str, *, field_name: str) -> str:
    text = str(value or "")
    if text != text.strip() or _PROPERTY_NAME.fullmatch(text) is None:
        raise ValueError(
            f"{field_name} must be one top-level property name "
            f"(no paths, indexes, wildcards, or expressions), got {value!r}"
        )
    return text


def _single_instance_placeholder(
    selection_address: Any, *, placeholder: str, where: str
) -> str:
    """The one instance placeholder an instance-relative selection must carry."""
    values = tuple(selection_address.placeholder_values)
    names = tuple(name for name, _value in values)
    if names != (placeholder,):
        raise ValueError(
            f"{where} must be addressed with exactly the {placeholder!r} instance "
            f"placeholder, got {names!r}"
        )
    return str(values[0][1])


def _module_instance_of_address(selection_address: Any, *, where: str) -> str:
    """The module instance one canonical module-artifact address names.

    Module artifacts have exactly two canonical addressings: module-relative
    with the ``module_id`` placeholder, or a global scope under the canonical
    ``modules/{module_id}/...`` layout prefix.  This mirrors the layout
    registry's own path templates; cold resolution independently re-proves
    the canonical layout row, so this is a coherence check, never the layout
    authority.
    """
    values = dict(selection_address.placeholder_values)
    placeholder_value = values.get("module_id")
    if placeholder_value is not None:
        if set(values) != {"module_id"}:
            raise ValueError(
                f"{where} must carry only the 'module_id' instance placeholder"
            )
        return str(placeholder_value)
    segments = str(selection_address.path).split("/")
    if len(segments) >= 3 and segments[0] == "modules" and segments[1]:
        return segments[1]
    raise ValueError(
        f"{where} does not address a canonical module artifact, got "
        f"{selection_address.path!r}"
    )


def empty_request_context_contract() -> ObjectContract:
    """The canonical empty (no-context) request-context contract."""
    return ObjectContract(nullable=False, properties=(), additional_properties=False)


class _Selection(SemanticsModel):
    requirement_node_id: str

    @field_validator("requirement_node_id")
    @classmethod
    def _requirement(cls, value: str) -> str:
        return _validate_node_id(value)


class CapabilityPackSelection(_Selection):
    pack_id: str
    pack_digest: str

    @field_validator("pack_id")
    @classmethod
    def _pack_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="pack_id")

    @field_validator("pack_digest")
    @classmethod
    def _pack_digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="pack_digest")


class RendererSelection(SemanticsModel):
    """Bind registry materializer categories to deterministic implementations.

    Artifact families remain owned by ``layout_registry``.  This selection
    merely pins an implementation identity/version for rows whose declared
    materializer matches, and explicitly states graph-v2 compatibility.
    """

    materializer_id: MaterializerIdentifier
    implementation_id: str
    implementation_version: str
    artifact_families: tuple[str, ...] = Field(min_length=1)
    graph_schema_versions: tuple[Literal["mozaiks.semantic_graph.v2"], ...] = (
        RENDERER_GRAPH_SCHEMA_VERSION,
    )

    @field_validator("implementation_id")
    @classmethod
    def _implementation_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="implementation_id")

    @field_validator("implementation_version")
    @classmethod
    def _implementation_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("implementation_version must be non-empty")
        return text

    @field_validator("artifact_families")
    @classmethod
    def _families(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        from mozaiksai.core.taxonomy import SemanticCategory, validate_identifier_grammar

        ordered = tuple(
            sorted(
                {
                    validate_identifier_grammar(SemanticCategory.ARTIFACT_FAMILY, item)
                    for item in value
                }
            )
        )
        if len(ordered) != len(value):
            raise ValueError("artifact_families must be unique")
        return ordered

    @field_validator("graph_schema_versions")
    @classmethod
    def _graph_versions(
        cls, value: tuple[Literal["mozaiks.semantic_graph.v2"], ...]
    ) -> tuple[Literal["mozaiks.semantic_graph.v2"], ...]:
        if value != (RENDERER_GRAPH_SCHEMA_VERSION,):
            raise ValueError("renderer implementations must explicitly target graph v2")
        return value


class DeploymentProfileSelection(_Selection):
    profile_id: str
    profile_version: str

    @field_validator("profile_id")
    @classmethod
    def _profile_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="profile_id")

    @field_validator("profile_version")
    @classmethod
    def _profile_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("profile_version must be non-empty")
        return text


class WorkflowImplementationSelection(SemanticsModel):
    """Bind ONE semantic WORKFLOW node to its exact implementation documents.

    Both selections use the public #488 artifact-selection primitive: cold
    resolution proves canonical layout, exact bytes, strict document
    contracts, and same-workflow pairing.  The selection carries NO
    caller-authored workflow name — the runtime workflow identity is derived
    from the exact document bytes and checked against the semantic
    ``WorkflowPayload`` during validation.
    """

    workflow_node_id: str
    orchestrator_selection: SelectedContractArtifact
    structured_outputs_selection: SelectedContractArtifact

    @field_validator("workflow_node_id")
    @classmethod
    def _workflow_node(cls, value: str) -> str:
        return _validate_node_id(value)

    @model_validator(mode="after")
    def _coherent_pair(self) -> WorkflowImplementationSelection:
        if self.orchestrator_selection.address.path != "orchestrator.yaml":
            raise ValueError(
                "workflow orchestrator selection must address orchestrator.yaml, "
                f"got {self.orchestrator_selection.address.path!r}"
            )
        if self.structured_outputs_selection.address.path != "structured_outputs.yaml":
            raise ValueError(
                "workflow structured-output selection must address "
                "structured_outputs.yaml, got "
                f"{self.structured_outputs_selection.address.path!r}"
            )
        orchestrator_instance = _single_instance_placeholder(
            self.orchestrator_selection.address,
            placeholder="workflow_id",
            where="workflow orchestrator selection",
        )
        outputs_instance = _single_instance_placeholder(
            self.structured_outputs_selection.address,
            placeholder="workflow_id",
            where="workflow structured-output selection",
        )
        if orchestrator_instance != outputs_instance:
            raise ValueError(
                f"workflow implementation selection pairs documents of workflows "
                f"{orchestrator_instance!r} and {outputs_instance!r}"
            )
        if (
            self.orchestrator_selection.contract_ref.scope
            != self.structured_outputs_selection.contract_ref.scope
        ):
            raise ValueError(
                "workflow implementation documents belong to different execution scopes"
            )
        return self

    @property
    def workflow_instance(self) -> str:
        """The addressed workflow instance both selected documents share."""
        return str(self.orchestrator_selection.address.placeholder_values[0][1])

    @property
    def scope(self) -> ExecutionAccessScopeRef:
        return self.orchestrator_selection.contract_ref.scope


class ModuleActionImplementationSelection(SemanticsModel):
    """Bind ONE semantic ACTION to the exact implementation proven by #488.

    ``certification_mode``, ``method_source``, and ``implementation_digest``
    pin the recomputable certification result: cold validation re-runs
    :func:`resolve_module_action_implementation` over the exact selected
    bytes and requires the recomputed
    ``ModuleActionExportProof.implementation_digest`` (the certified
    source-closure identity) to equal the pinned value exactly.  A bare or
    fabricated proof never becomes authority — the authoritative resolver
    remains the source of truth.  Source content digests live only inside
    the selected artifacts; they are never repeated as caller-authored
    strings here.
    """

    action_node_id: str
    module_selection: SelectedContractArtifact
    handler_selection: SelectedAccountedArtifact
    base_handler_selection: SelectedAccountedArtifact | None = None
    pack_contract_selection: SelectedAccountedArtifact | None = None
    certification_mode: HandlerCertificationMode
    method_source: HandlerMethodSource
    implementation_digest: str

    @field_validator("action_node_id")
    @classmethod
    def _action_node(cls, value: str) -> str:
        return _validate_node_id(value)

    @field_validator("implementation_digest")
    @classmethod
    def _implementation_digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="implementation_digest")

    @model_validator(mode="after")
    def _coherent_selection(self) -> ModuleActionImplementationSelection:
        if self.module_selection.address.path.split("/")[-1] != "module.yaml":
            raise ValueError(
                "module selection must address module.yaml, got "
                f"{self.module_selection.address.path!r}"
            )
        module_instance = _module_instance_of_address(
            self.module_selection.address, where="module selection"
        )
        scope = self.module_selection.contract_ref.scope
        scoped_sources: list[tuple[str, SelectedAccountedArtifact, bool]] = [
            ("handler selection", self.handler_selection, True),
        ]
        if self.base_handler_selection is not None:
            scoped_sources.append(
                ("base-handler selection", self.base_handler_selection, True)
            )
        if self.pack_contract_selection is not None:
            scoped_sources.append(
                ("pack-contract selection", self.pack_contract_selection, False)
            )
        for where, selection, module_scoped in scoped_sources:
            if selection.scope != scope:
                raise ValueError(
                    f"{where} scope does not equal the module selection's scope"
                )
            if module_scoped:
                source_instance = _module_instance_of_address(
                    selection.artifact.address, where=where
                )
                if source_instance != module_instance:
                    raise ValueError(
                        f"{where} addresses module {source_instance!r}, not the "
                        f"selected module {module_instance!r}"
                    )
        if self.certification_mode is HandlerCertificationMode.EXPLICIT_HANDLER:
            if self.method_source is not HandlerMethodSource.HANDLER:
                raise ValueError(
                    "EXPLICIT_HANDLER selections certify the handler source method"
                )
            if (
                self.base_handler_selection is not None
                or self.pack_contract_selection is not None
            ):
                raise ValueError(
                    "EXPLICIT_HANDLER selections carry no base or pack-contract "
                    "selection"
                )
        else:
            if self.base_handler_selection is None or self.pack_contract_selection is None:
                raise ValueError(
                    "CANONICAL_BASE_HANDLER selections require the exact base and "
                    "pack-contract selections together"
                )
        return self

    @property
    def module_instance(self) -> str:
        return _module_instance_of_address(
            self.module_selection.address, where="module selection"
        )

    @property
    def scope(self) -> ExecutionAccessScopeRef:
        return self.module_selection.contract_ref.scope


class IdentityProjection(SemanticsModel):
    """The exact selected structured output becomes the semantic result as-is."""

    projection_kind: Literal["identity"] = "identity"


class FieldsProjection(SemanticsModel):
    """Select a finite set of TOP-LEVEL object properties from the exact output.

    Property names only, preserved exactly (no renaming), canonically sorted
    and unique.  The grammar structurally excludes JSONPath, dotted paths,
    nested traversal, array indexes, wildcards, expressions, functions,
    coercion, defaulting, and computed values.
    """

    projection_kind: Literal["fields"] = "fields"
    fields: tuple[str, ...] = Field(min_length=1)

    @field_validator("fields")
    @classmethod
    def _fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        ordered = tuple(
            sorted(
                _validate_property_name(item, field_name="projected field")
                for item in value
            )
        )
        if len(ordered) != len(set(ordered)):
            raise ValueError("duplicate projected fields")
        return ordered


ResultProjection = Annotated[
    IdentityProjection | FieldsProjection, Field(discriminator="projection_kind")
]


class ResultPropertySource(SemanticsModel):
    """One TOP-LEVEL property of the projected workflow result."""

    source_kind: Literal["result_property"] = "result_property"
    property_name: str

    @field_validator("property_name")
    @classmethod
    def _property(cls, value: str) -> str:
        return _validate_property_name(value, field_name="property_name")


class ConstantSource(SemanticsModel):
    """One canonical JSON-compatible constant value."""

    source_kind: Literal["constant"] = "constant"
    value: CanonicalJsonValue


class RequestContextPropertySource(SemanticsModel):
    """One TOP-LEVEL property of ``ImplementationBinding.request_context_contract``."""

    source_kind: Literal["request_context_property"] = "request_context_property"
    property_name: str

    @field_validator("property_name")
    @classmethod
    def _property(cls, value: str) -> str:
        return _validate_property_name(value, field_name="property_name")


ActionInputSource = Annotated[
    ResultPropertySource | ConstantSource | RequestContextPropertySource,
    Field(discriminator="source_kind"),
]


class ActionInputBinding(SemanticsModel):
    """Explicitly wire ONE top-level action request property to one source.

    No paths, no transforms, no coercion, no hidden defaults: the target
    names one top-level property of the exact selected action's closed
    request contract, and the source is exactly one of the three closed
    variants.
    """

    target_property: str
    source: ActionInputSource

    @field_validator("target_property")
    @classmethod
    def _target(cls, value: str) -> str:
        return _validate_property_name(value, field_name="target_property")


class CommitApprovalRequirement(StrEnum):
    """STATIC application semantics of one commit relationship.

    This is not an approval receipt, approval state, approver identity,
    token, runtime user, or permission grant — no runtime authority enters
    ImplementationBinding.
    """

    AUTOMATIC = "automatic"
    HUMAN_APPROVAL_REQUIRED = "human_approval_required"


class WorkflowResultCommitBinding(SemanticsModel):
    """Realize ONE semantic ``commits_result_through_action`` binding node.

    The semantic binding node already owns the workflow capability, the exact
    workflow result, and the exact module action — those endpoints are
    derived from the graph during validation, never repeated here as
    caller-authored strings.
    """

    workflow_capability_binding_node_id: str
    approval_requirement: CommitApprovalRequirement
    action_inputs: tuple[ActionInputBinding, ...] = Field(default_factory=tuple)

    @field_validator("workflow_capability_binding_node_id")
    @classmethod
    def _binding_node(cls, value: str) -> str:
        return _validate_node_id(value)

    @field_validator("action_inputs")
    @classmethod
    def _inputs(
        cls, value: tuple[ActionInputBinding, ...]
    ) -> tuple[ActionInputBinding, ...]:
        ordered = tuple(sorted(value, key=lambda item: item.target_property))
        targets = [item.target_property for item in ordered]
        if len(targets) != len(set(targets)):
            raise ValueError("duplicate action-input target properties")
        return ordered


class WorkflowResultImplementationBinding(SemanticsModel):
    """Realize ONE semantic WORKFLOW_RESULT node.

    The structured-output contract reference must cold-resolve against the
    owning workflow's exact selected ``structured_outputs.yaml`` document —
    a same-schema output model from another workflow is never
    interchangeable, and no free model-name string establishes authority.
    """

    workflow_result_node_id: str
    structured_output_contract_ref: StructuredOutputContractRef
    projection: ResultProjection
    commit_bindings: tuple[WorkflowResultCommitBinding, ...] = Field(default_factory=tuple)

    @field_validator("workflow_result_node_id")
    @classmethod
    def _result_node(cls, value: str) -> str:
        return _validate_node_id(value)

    @field_validator("commit_bindings")
    @classmethod
    def _commits(
        cls, value: tuple[WorkflowResultCommitBinding, ...]
    ) -> tuple[WorkflowResultCommitBinding, ...]:
        ordered = tuple(
            sorted(value, key=lambda item: item.workflow_capability_binding_node_id)
        )
        nodes = [item.workflow_capability_binding_node_id for item in ordered]
        if len(nodes) != len(set(nodes)):
            raise ValueError("duplicate commit bindings for one semantic binding node")
        return ordered

    @model_validator(mode="after")
    def _projected_result_sources(self) -> WorkflowResultImplementationBinding:
        if isinstance(self.projection, FieldsProjection):
            projected = set(self.projection.fields)
            for commit in self.commit_bindings:
                for item in commit.action_inputs:
                    if (
                        isinstance(item.source, ResultPropertySource)
                        and item.source.property_name not in projected
                    ):
                        raise ValueError(
                            f"action input {item.target_property!r} reads result "
                            f"property {item.source.property_name!r} outside the "
                            "projected fields"
                        )
        return self


def _sorted_unique(
    selections: tuple[_Selection, ...], *, category: str
) -> tuple[_Selection, ...]:
    ordered = tuple(sorted(selections, key=lambda item: item.requirement_node_id))
    requirements = [item.requirement_node_id for item in ordered]
    if len(requirements) != len(set(requirements)):
        raise ValueError(f"duplicate {category} selections for one requirement node")
    return ordered


class ImplementationBinding(SemanticsModel):
    schema_version: Literal["mozaiks.implementation_binding.v2"] = (
        IMPLEMENTATION_BINDING_SCHEMA_VERSION
    )
    binding_id: str
    version: int = Field(ge=1, strict=True)
    scope: ExecutionAccessScopeRef
    semantic_graph_ref: SemanticGraphRef
    capability_pack_selections: tuple[CapabilityPackSelection, ...] = Field(default_factory=tuple)
    renderer_selections: tuple[RendererSelection, ...] = Field(default_factory=tuple)
    deployment_profile_selections: tuple[DeploymentProfileSelection, ...] = Field(
        default_factory=tuple
    )
    projection_profile_version: Literal["mozaiks.result_projection.v1"] = (
        RESULT_PROJECTION_PROFILE_VERSION
    )
    #: Application request context available to deterministic result/action
    #: wiring.  NOT authenticated runtime identity, user session state, AG2
    #: context, secrets, or provider state.  Always a closed non-null OBJECT.
    request_context_contract: ObjectContract = Field(
        default_factory=empty_request_context_contract
    )
    workflow_implementation_selections: tuple[WorkflowImplementationSelection, ...] = Field(
        default_factory=tuple
    )
    module_action_implementation_selections: tuple[
        ModuleActionImplementationSelection, ...
    ] = Field(default_factory=tuple)
    workflow_result_bindings: tuple[WorkflowResultImplementationBinding, ...] = Field(
        default_factory=tuple
    )
    binding_digest: str = Field(min_length=64, max_length=64)

    @field_validator("binding_id")
    @classmethod
    def _binding_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="binding_id")

    @field_validator("capability_pack_selections")
    @classmethod
    def _packs(cls, value):
        return _sorted_unique(value, category="capability-pack")

    @field_validator("renderer_selections")
    @classmethod
    def _renderers(cls, value):
        ordered = tuple(
            sorted(
                value,
                key=lambda item: (
                    item.materializer_id.value,
                    item.implementation_id,
                    item.implementation_version,
                ),
            )
        )
        families = [family for item in ordered for family in item.artifact_families]
        if len(families) != len(set(families)):
            raise ValueError("one artifact family cannot select multiple renderer implementations")
        return ordered

    @field_validator("deployment_profile_selections")
    @classmethod
    def _profiles(cls, value):
        return _sorted_unique(value, category="deployment-profile")

    @field_validator("request_context_contract")
    @classmethod
    def _request_context(cls, value: ObjectContract) -> ObjectContract:
        if value.nullable:
            raise ValueError("request_context_contract must be a non-null OBJECT")
        return value

    @field_validator("workflow_implementation_selections")
    @classmethod
    def _workflow_selections(cls, value):
        ordered = tuple(sorted(value, key=lambda item: item.workflow_node_id))
        nodes = [item.workflow_node_id for item in ordered]
        if len(nodes) != len(set(nodes)):
            raise ValueError("duplicate workflow implementation selections for one workflow")
        return ordered

    @field_validator("module_action_implementation_selections")
    @classmethod
    def _action_selections(cls, value):
        ordered = tuple(sorted(value, key=lambda item: item.action_node_id))
        nodes = [item.action_node_id for item in ordered]
        if len(nodes) != len(set(nodes)):
            raise ValueError("duplicate action implementation selections for one action")
        return ordered

    @field_validator("workflow_result_bindings")
    @classmethod
    def _result_bindings(cls, value):
        ordered = tuple(sorted(value, key=lambda item: item.workflow_result_node_id))
        nodes = [item.workflow_result_node_id for item in ordered]
        if len(nodes) != len(set(nodes)):
            raise ValueError("duplicate result realization for one workflow result")
        return ordered

    @model_validator(mode="after")
    def _validate_binding(self, info: ValidationInfo) -> ImplementationBinding:
        if self.semantic_graph_ref.scope != self.scope:
            raise ValueError(
                "semantic_graph_ref scope does not match the binding scope; "
                "cross-scope references fail closed"
            )
        for selection in self.workflow_implementation_selections:
            if selection.scope != self.scope:
                raise ValueError(
                    f"workflow implementation selection {selection.workflow_node_id!r} "
                    "is selected in a different execution scope than the binding"
                )
        for action_selection in self.module_action_implementation_selections:
            if action_selection.scope != self.scope:
                raise ValueError(
                    f"action implementation selection {action_selection.action_node_id!r} "
                    "is selected in a different execution scope than the binding"
                )
        context_properties = {
            prop.name for prop in self.request_context_contract.properties
        }
        seen_commit_nodes: set[str] = set()
        for result_binding in self.workflow_result_bindings:
            for commit in result_binding.commit_bindings:
                node = commit.workflow_capability_binding_node_id
                if node in seen_commit_nodes:
                    raise ValueError(
                        f"semantic commit binding {node!r} is realized under more "
                        "than one workflow result"
                    )
                seen_commit_nodes.add(node)
                for item in commit.action_inputs:
                    if (
                        isinstance(item.source, RequestContextPropertySource)
                        and item.source.property_name not in context_properties
                    ):
                        raise ValueError(
                            f"action input {item.target_property!r} reads request-"
                            f"context property {item.source.property_name!r} that "
                            "request_context_contract does not declare"
                        )
        if info.context is not None and info.context.get(_BUILDER_CONTEXT_KEY):
            return self
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.binding_digest != expected:
            raise ValueError("binding_digest does not match binding content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        """Canonical digest payload: every field participates except the digest."""
        payload = self.model_dump(mode="json", exclude={"binding_digest"})
        if include_digest:
            payload["binding_digest"] = self.binding_digest
        return payload


def build_implementation_binding(**fields: Any) -> ImplementationBinding:
    """Construct a binding with its content digest computed canonically.

    Validation runs twice: a builder-context pass normalizes every collection
    into canonical order, then the definitive pass re-validates the serialized
    document with the computed digest — the returned binding is exactly what a
    cold parse would accept, and the digest is independent of caller input
    order.
    """
    probe = ImplementationBinding.model_validate(
        {**fields, "binding_digest": _PLACEHOLDER_DIGEST},
        context={_BUILDER_CONTEXT_KEY: True},
    )
    document = probe.model_dump(mode="json", exclude={"binding_digest"})
    document["binding_digest"] = canonical_digest(document)
    return ImplementationBinding.model_validate(document)


# ---------------------------------------------------------------------------
# Typed graph validation (v2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _TypedGraphContext:
    """The typed-closure facts graph validation proves once and cold reuse."""

    payload_by_node: dict[str, SemanticPayloadBase]
    workflow_payloads: dict[str, WorkflowPayload]
    module_payload_by_action: dict[str, ModulePayload]
    action_payloads: dict[str, ActionPayload]
    capability_payloads: dict[str, WorkflowCapabilityPayload]
    result_payloads: dict[str, WorkflowResultPayload]
    commit_payloads: dict[str, WorkflowCapabilityBindingPayload]


def _validate_action_input_closure(
    commit: WorkflowResultCommitBinding,
    contract: ObjectContract,
    *,
    where: str,
) -> None:
    """Exact explicit wiring against one closed action request contract.

    Every required request property has exactly one binding; optional
    properties may be omitted; unknown targets reject.  (Duplicate targets
    are already impossible at the model boundary.)  This proves identity and
    closure only — structural source/target type compatibility is the later
    receipt slice.
    """
    names = {prop.name for prop in contract.properties}
    required = {prop.name for prop in contract.properties if prop.required}
    bound = {item.target_property for item in commit.action_inputs}
    unknown = sorted(bound - names)
    if unknown:
        raise ImplementationBindingError(
            f"{where} wires unknown target action properties {unknown!r}"
        )
    missing = sorted(required - bound)
    if missing:
        raise ImplementationBindingError(
            f"{where} leaves required target action properties {missing!r} unwired"
        )


def _typed_graph_validation(
    binding: ImplementationBinding,
    graph: SemanticGraphV2,
    payloads: Iterable[SemanticPayloadBase],
) -> _TypedGraphContext:
    """Exact typed closure between the binding's v2 selections and the graph.

    The complete supplied payload set is first proven to be the graph's own
    bijective payload closure, so every typed fact used here is the pinned
    meaning authority — never a caller-side claim and never node-id parsing
    where typed payload authority exists.
    """
    try:
        validate_semantic_graph_v2_payload_closure(graph, payloads)
    except ValueError as exc:
        raise ImplementationBindingError(
            f"semantic graph payload closure rejected: {exc}"
        ) from exc
    payload_by_node = {payload.node_id: payload for payload in payloads}

    capability_payloads = {
        payload.node_id: payload
        for payload in payload_by_node.values()
        if isinstance(payload, WorkflowCapabilityPayload)
    }
    result_payloads = {
        payload.node_id: payload
        for payload in payload_by_node.values()
        if isinstance(payload, WorkflowResultPayload)
    }
    binding_payloads = [
        payload
        for payload in payload_by_node.values()
        if isinstance(payload, WorkflowCapabilityBindingPayload)
    ]

    # --- workflow selection completeness -----------------------------------
    demanded_workflows = {
        payload.workflow_node_id for payload in capability_payloads.values()
    }
    selected_workflows = {
        selection.workflow_node_id
        for selection in binding.workflow_implementation_selections
    }
    missing_workflows = sorted(demanded_workflows - selected_workflows)
    if missing_workflows:
        raise ImplementationBindingError(
            f"absent workflow implementation for capability-owning workflows "
            f"{missing_workflows!r}"
        )
    extra_workflows = sorted(selected_workflows - demanded_workflows)
    if extra_workflows:
        raise ImplementationBindingError(
            f"workflow implementation selections target {extra_workflows!r}, which "
            "are absent, non-WORKFLOW, or own no workflow capability; a binding "
            "cannot widen graph semantics"
        )
    workflow_payloads: dict[str, WorkflowPayload] = {}
    for selection in binding.workflow_implementation_selections:
        workflow_payload = payload_by_node.get(selection.workflow_node_id)
        if not isinstance(workflow_payload, WorkflowPayload):
            raise ImplementationBindingError(
                f"workflow implementation selection targets non-workflow node "
                f"{selection.workflow_node_id!r}"
            )
        if selection.workflow_instance != workflow_payload.workflow_id:
            raise ImplementationBindingError(
                f"workflow implementation selection addresses workflow instance "
                f"{selection.workflow_instance!r} but the semantic workflow is "
                f"{workflow_payload.workflow_id!r}"
            )
        workflow_payloads[selection.workflow_node_id] = workflow_payload

    # --- module action selection completeness ------------------------------
    demanded_actions: dict[str, str] = {}
    for payload in binding_payloads:
        if payload.binding_role not in _ACTION_IMPLEMENTATION_ROLES:
            continue
        module_action = payload.module_action
        if module_action is None:  # pragma: no cover - payload-model-validated
            continue
        demanded_actions[module_action.action_node_id] = module_action.module_node_id
    selected_actions = {
        selection.action_node_id
        for selection in binding.module_action_implementation_selections
    }
    missing_actions = sorted(set(demanded_actions) - selected_actions)
    if missing_actions:
        raise ImplementationBindingError(
            f"absent action implementation for workflow-referenced actions "
            f"{missing_actions!r}"
        )
    extra_actions = sorted(selected_actions - set(demanded_actions))
    if extra_actions:
        raise ImplementationBindingError(
            f"action implementation selections target {extra_actions!r}, which no "
            "workflow-capability binding consumes or commits through; a binding "
            "cannot widen graph semantics"
        )
    module_payload_by_action: dict[str, ModulePayload] = {}
    action_payloads: dict[str, ActionPayload] = {}
    for action_selection in binding.module_action_implementation_selections:
        action_payload = payload_by_node.get(action_selection.action_node_id)
        if not isinstance(action_payload, ActionPayload):
            raise ImplementationBindingError(
                f"action implementation selection targets non-action node "
                f"{action_selection.action_node_id!r}"
            )
        module_node_id = demanded_actions[action_selection.action_node_id]
        module_payload = payload_by_node.get(module_node_id)
        if not isinstance(module_payload, ModulePayload):
            raise ImplementationBindingError(
                f"action {action_selection.action_node_id!r} is referenced through "
                f"non-module node {module_node_id!r}"
            )
        if action_selection.module_instance != module_payload.module_id:
            raise ImplementationBindingError(
                f"action implementation selection for "
                f"{action_selection.action_node_id!r} addresses module "
                f"{action_selection.module_instance!r}, not the owning "
                f"semantic module {module_payload.module_id!r}"
            )
        module_payload_by_action[action_selection.action_node_id] = module_payload
        action_payloads[action_selection.action_node_id] = action_payload

    # --- result realization completeness -----------------------------------
    demanded_results = set(result_payloads)
    realized_results = {
        result_binding.workflow_result_node_id
        for result_binding in binding.workflow_result_bindings
    }
    missing_results = sorted(demanded_results - realized_results)
    if missing_results:
        raise ImplementationBindingError(
            f"absent result realization for workflow results {missing_results!r}"
        )
    extra_results = sorted(realized_results - demanded_results)
    if extra_results:
        raise ImplementationBindingError(
            f"result realizations target {extra_results!r}, which are absent or "
            "not WORKFLOW_RESULT nodes; a binding cannot widen graph semantics"
        )

    # --- semantic commit closure -------------------------------------------
    commit_payloads: dict[str, WorkflowCapabilityBindingPayload] = {}
    commit_nodes_by_result: dict[str, set[str]] = {}
    for payload in binding_payloads:
        if (
            payload.binding_role
            is not WorkflowCapabilityBindingRole.COMMITS_RESULT_THROUGH_ACTION
        ):
            continue
        commit_payloads[payload.node_id] = payload
        result_node = payload.workflow_result_node_id
        if result_node is None:  # pragma: no cover - payload-model-validated
            continue
        commit_nodes_by_result.setdefault(result_node, set()).add(payload.node_id)
    for result_binding in binding.workflow_result_bindings:
        result_node = result_binding.workflow_result_node_id
        expected_commits = commit_nodes_by_result.get(result_node, set())
        actual_commits = {
            commit.workflow_capability_binding_node_id
            for commit in result_binding.commit_bindings
        }
        missing_commits = sorted(expected_commits - actual_commits)
        if missing_commits:
            raise ImplementationBindingError(
                f"result realization {result_node!r} lacks commit bindings for "
                f"semantic commit nodes {missing_commits!r}"
            )
        extra_commits = sorted(actual_commits - expected_commits)
        if extra_commits:
            raise ImplementationBindingError(
                f"result realization {result_node!r} carries commit bindings "
                f"{extra_commits!r} that are not semantic "
                "commits_result_through_action bindings of this exact result"
            )
        for commit in result_binding.commit_bindings:
            commit_payload = commit_payloads[
                commit.workflow_capability_binding_node_id
            ]
            module_action = commit_payload.module_action
            if module_action is None:  # pragma: no cover - payload-model-validated
                continue
            action_payload = action_payloads[module_action.action_node_id]
            _validate_action_input_closure(
                commit,
                action_payload.request_contract,
                where=(
                    f"commit binding {commit.workflow_capability_binding_node_id!r}"
                ),
            )

    return _TypedGraphContext(
        payload_by_node=payload_by_node,
        workflow_payloads=workflow_payloads,
        module_payload_by_action=module_payload_by_action,
        action_payloads=action_payloads,
        capability_payloads=capability_payloads,
        result_payloads=result_payloads,
        commit_payloads=commit_payloads,
    )


def validate_implementation_binding_against_graph(
    binding: ImplementationBinding,
    graph: SemanticGraph | SemanticGraphV2 | _GraphSubject,
    *,
    layout_registry: AppLayoutRegistry | None = None,
    payloads: Iterable[SemanticPayloadBase] | None = None,
) -> None:
    """Fail closed unless every selection satisfies an existing typed requirement.

    The binding may only choose among implementations for requirement nodes
    the graph already declares; a selection naming an absent node, or a node
    of an inappropriate kind, is an attempt to introduce semantics through
    the binding and is rejected.

    Graph-v2 subjects that declare workflow-capability semantics (or bindings
    that carry any v2 workflow/action/result selection) additionally require
    the graph's complete typed payload closure via ``payloads`` — workflow
    selection completeness, referenced-action implementation completeness,
    result-realization completeness, semantic commit closure, and explicit
    action-input closure are all validated against the typed payloads.
    """
    if binding.scope != graph.scope:
        raise ImplementationBindingError("binding scope does not match graph scope")
    if (
        binding.semantic_graph_ref.subject_id != graph.graph_id
        or binding.semantic_graph_ref.subject_version != graph.version
        or binding.semantic_graph_ref.content_digest != graph.graph_digest
    ):
        raise ImplementationBindingError(
            "binding does not pin this graph's id, immutable version, and digest"
        )

    known = {node.node_id: node for node in graph.nodes}
    checks = (
        ("capability-pack", binding.capability_pack_selections, CAPABILITY_PACK_REQUIREMENT_KINDS),
        ("deployment-profile", binding.deployment_profile_selections, DEPLOYMENT_REQUIREMENT_KINDS),
    )
    for category, selections, allowed_kinds in checks:
        for selection in selections:
            node = known.get(selection.requirement_node_id)
            if node is None:
                raise ImplementationBindingError(
                    f"{category} selection targets node "
                    f"{selection.requirement_node_id!r} that is absent from the graph; "
                    "a binding cannot widen graph semantics"
                )
            if node.kind not in allowed_kinds:
                raise ImplementationBindingError(
                    f"{category} selection targets {node.kind.value!r} node "
                    f"{selection.requirement_node_id!r}; allowed kinds: "
                    f"{sorted(kind.value for kind in allowed_kinds)}"
                )

    if binding.renderer_selections and graph.schema_version != RENDERER_GRAPH_SCHEMA_VERSION:
        raise ImplementationBindingError(
            "renderer implementation selections require a semantic graph v2 subject"
        )
    registry = layout_registry or default_app_layout_registry()
    rows_by_kind: dict[str, list] = {}
    for row in registry.families:
        rows_by_kind.setdefault(row.kind.value, []).append(row)
    for renderer in binding.renderer_selections:
        for family in renderer.artifact_families:
            rows = rows_by_kind.get(family)
            if not rows:
                raise ImplementationBindingError(
                    f"renderer selection targets unregistered artifact family {family!r}"
                )
            mismatched = [
                row.materializer.value
                for row in rows
                if row.materializer is not renderer.materializer_id
            ]
            if mismatched:
                raise ImplementationBindingError(
                    f"renderer selection for {family!r} claims materializer "
                    f"{renderer.materializer_id.value!r}, but layout_registry declares "
                    f"{sorted(set(mismatched))!r}"
                )

    has_v2_selections = bool(
        binding.workflow_implementation_selections
        or binding.module_action_implementation_selections
        or binding.workflow_result_bindings
    )
    if not isinstance(graph, SemanticGraphV2):
        if has_v2_selections:
            raise ImplementationBindingError(
                "workflow, action, and result implementation selections require "
                "a semantic graph v2 subject"
            )
        return

    typed_kinds = {
        SemanticNodeKind.WORKFLOW_CAPABILITY,
        SemanticNodeKind.WORKFLOW_RESULT,
        SemanticNodeKind.WORKFLOW_CAPABILITY_BINDING,
    }
    graph_demands_typed = any(node.kind in typed_kinds for node in graph.nodes)
    if not (graph_demands_typed or has_v2_selections):
        return
    if payloads is None:
        raise ImplementationBindingError(
            "typed workflow-capability validation requires the graph's complete "
            "payload closure; supply payloads"
        )
    _typed_graph_validation(binding, graph, tuple(payloads))


# ---------------------------------------------------------------------------
# Cold implementation validation (content authority)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResolvedImplementationBindingAuthority:
    """The exact resolved implementations one accepted binding pins.

    Produced only by :func:`validate_implementation_binding_content_authority`
    after every selection resolved through verified content.  Keys are the
    semantic node ids the binding realizes.
    """

    workflow_implementations: Mapping[str, ResolvedWorkflowImplementation]
    module_action_implementations: Mapping[str, ResolvedModuleActionImplementation]
    result_output_models: Mapping[str, type[BaseModel]]


def _derived_action_id(action_node_id: str, module_id: str) -> str:
    """The declared action id one canonical ACTION node addresses.

    Canonical ACTION node identity is ``{namespace}.{module_id}_{action_id}``
    and the module id comes from the typed ``ModulePayload`` — the one exact
    prefix strip is unambiguous under that typed authority and fails closed
    on any node identity that does not carry it.
    """
    local = action_node_id.rsplit(".", 1)[1]
    prefix = f"{module_id}_"
    if not local.startswith(prefix) or len(local) <= len(prefix):
        raise ImplementationBindingError(
            f"action node {action_node_id!r} does not carry the canonical "
            f"{module_id!r}-owned action identity"
        )
    return local[len(prefix):]


async def validate_implementation_binding_content_authority(
    binding: ImplementationBinding,
    graph: SemanticGraphV2,
    *,
    payloads: Iterable[SemanticPayloadBase],
    content_store: ArtifactContentStore,
    layout_registry: AppLayoutRegistry | None = None,
) -> ResolvedImplementationBindingAuthority:
    """Cold-resolve every implementation selection through verified content.

    Graph-only validation is not enough: this entrypoint re-validates the
    binding document, proves the typed graph closure, then resolves every
    workflow implementation, structured-output contract, and certified module
    action through the #488 exact-content resolvers.  There is no filesystem
    fallback, no mutable working-tree authority, and no caller-supplied
    resolved object: bytes come only from ``content_store`` and every
    recomputable certification identity must equal the pinned one.
    """
    try:
        verified = ImplementationBinding.model_validate(binding.model_dump(mode="json"))
    except (TypeError, ValueError) as exc:
        raise ImplementationBindingError(
            f"implementation binding failed cold validation: {exc}"
        ) from exc
    registry = layout_registry or default_app_layout_registry()
    payload_tuple = tuple(payloads)
    validate_implementation_binding_against_graph(
        verified, graph, layout_registry=registry, payloads=payload_tuple
    )
    context = _typed_graph_validation(verified, graph, payload_tuple)

    workflow_implementations: dict[str, ResolvedWorkflowImplementation] = {}
    for selection in verified.workflow_implementation_selections:
        try:
            orchestrator = await resolve_workflow_orchestrator_artifact(
                selection.orchestrator_selection,
                content_store=content_store,
                requesting_scope=verified.scope,
                layout_registry=registry,
            )
            structured_outputs = await resolve_workflow_structured_outputs_artifact(
                selection.structured_outputs_selection,
                content_store=content_store,
                requesting_scope=verified.scope,
                layout_registry=registry,
            )
            implementation = pair_workflow_implementation_artifacts(
                orchestrator, structured_outputs
            )
        except ImplementationArtifactError as exc:
            raise ImplementationBindingError(
                f"workflow implementation for {selection.workflow_node_id!r} did "
                f"not resolve exactly: {exc}"
            ) from exc
        workflow_payload = context.workflow_payloads[selection.workflow_node_id]
        if implementation.workflow_instance != workflow_payload.workflow_id:
            raise ImplementationBindingError(
                f"resolved workflow instance {implementation.workflow_instance!r} "
                f"does not truthfully correspond to semantic workflow "
                f"{workflow_payload.workflow_id!r}"
            )
        workflow_implementations[selection.workflow_node_id] = implementation

    module_action_implementations: dict[str, ResolvedModuleActionImplementation] = {}
    for action_selection in verified.module_action_implementation_selections:
        module_payload = context.module_payload_by_action[
            action_selection.action_node_id
        ]
        action_id = _derived_action_id(
            action_selection.action_node_id, module_payload.module_id
        )
        try:
            resolved = await resolve_module_action_implementation(
                action_selection.module_selection,
                action_selection.handler_selection,
                action_id=action_id,
                content_store=content_store,
                requesting_scope=verified.scope,
                layout_registry=registry,
                base_handler_selection=action_selection.base_handler_selection,
                pack_contract_selection=action_selection.pack_contract_selection,
            )
        except ImplementationArtifactError as exc:
            raise ImplementationBindingError(
                f"action implementation for {action_selection.action_node_id!r} did "
                f"not resolve exactly: {exc}"
            ) from exc
        proof = resolved.export_proof
        if proof.mode is not action_selection.certification_mode:
            raise ImplementationBindingError(
                f"action {action_selection.action_node_id!r} pins certification "
                f"mode {action_selection.certification_mode.value!r} but the "
                f"recomputed certification is {proof.mode.value!r}"
            )
        if proof.method_source is not action_selection.method_source:
            raise ImplementationBindingError(
                f"action {action_selection.action_node_id!r} pins method_source "
                f"{action_selection.method_source.value!r} but the recomputed "
                f"certification is {proof.method_source.value!r}"
            )
        if proof.implementation_digest != action_selection.implementation_digest:
            raise ImplementationBindingError(
                f"action {action_selection.action_node_id!r} pins an "
                "implementation_digest the recomputed certified source-closure "
                "identity does not produce"
            )
        semantic_contract = context.action_payloads[
            action_selection.action_node_id
        ].request_contract
        if resolved.request_contract.contract_digest != semantic_contract.contract_digest:
            raise ImplementationBindingError(
                f"action {action_selection.action_node_id!r} resolved an "
                "implementation whose closed request contract does not equal the "
                "semantic action's request contract"
            )
        module_action_implementations[action_selection.action_node_id] = resolved

    result_output_models: dict[str, type[BaseModel]] = {}
    for result_binding in verified.workflow_result_bindings:
        result_payload = context.result_payloads[result_binding.workflow_result_node_id]
        capability_payload = context.capability_payloads[
            result_payload.workflow_capability_node_id
        ]
        implementation = workflow_implementations[capability_payload.workflow_node_id]
        try:
            model = resolve_selected_structured_output_contract(
                implementation, result_binding.structured_output_contract_ref
            )
        except ImplementationArtifactError as exc:
            raise ImplementationBindingError(
                f"result realization {result_binding.workflow_result_node_id!r} did "
                f"not resolve its structured-output contract exactly: {exc}"
            ) from exc
        model_fields = set(model.model_fields)
        if isinstance(result_binding.projection, FieldsProjection):
            unknown_fields = sorted(
                set(result_binding.projection.fields) - model_fields
            )
            if unknown_fields:
                raise ImplementationBindingError(
                    f"result realization {result_binding.workflow_result_node_id!r} "
                    f"projects fields {unknown_fields!r} absent from the exact "
                    "resolved structured-output contract"
                )
            projected = set(result_binding.projection.fields)
        else:
            projected = model_fields
        for commit in result_binding.commit_bindings:
            commit_payload = context.commit_payloads[
                commit.workflow_capability_binding_node_id
            ]
            module_action = commit_payload.module_action
            if module_action is None:  # pragma: no cover - payload-model-validated
                continue
            resolved_action = module_action_implementations[
                module_action.action_node_id
            ]
            request_contract = resolved_action.request_contract
            if not isinstance(request_contract, ObjectContract):
                raise ImplementationBindingError(
                    f"commit binding {commit.workflow_capability_binding_node_id!r} "
                    "targets an action whose request contract is not a closed object"
                )
            _validate_action_input_closure(
                commit,
                request_contract,
                where=(
                    f"commit binding {commit.workflow_capability_binding_node_id!r}"
                ),
            )
            for item in commit.action_inputs:
                if (
                    isinstance(item.source, ResultPropertySource)
                    and item.source.property_name not in projected
                ):
                    raise ImplementationBindingError(
                        f"commit binding "
                        f"{commit.workflow_capability_binding_node_id!r} reads "
                        f"result property {item.source.property_name!r} absent "
                        "from the projected result shape"
                    )
        result_output_models[result_binding.workflow_result_node_id] = model

    return ResolvedImplementationBindingAuthority(
        workflow_implementations=workflow_implementations,
        module_action_implementations=module_action_implementations,
        result_output_models=result_output_models,
    )


__all__ = [
    "CAPABILITY_PACK_REQUIREMENT_KINDS",
    "ActionInputBinding",
    "ActionInputSource",
    "CapabilityPackSelection",
    "CommitApprovalRequirement",
    "ConstantSource",
    "DEPLOYMENT_REQUIREMENT_KINDS",
    "DeploymentProfileSelection",
    "FieldsProjection",
    "IMPLEMENTATION_BINDING_SCHEMA_VERSION",
    "IdentityProjection",
    "ImplementationBinding",
    "ImplementationBindingError",
    "ModuleActionImplementationSelection",
    "RENDERER_GRAPH_SCHEMA_VERSION",
    "RESULT_PROJECTION_PROFILE_VERSION",
    "RendererSelection",
    "RequestContextPropertySource",
    "ResolvedImplementationBindingAuthority",
    "ResultProjection",
    "ResultPropertySource",
    "WorkflowImplementationSelection",
    "WorkflowResultCommitBinding",
    "WorkflowResultImplementationBinding",
    "build_implementation_binding",
    "empty_request_context_contract",
    "validate_implementation_binding_against_graph",
    "validate_implementation_binding_content_authority",
]
