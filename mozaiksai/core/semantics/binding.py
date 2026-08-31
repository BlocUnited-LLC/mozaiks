"""Immutable ``mozaiks.implementation_binding.v1`` contract.

An implementation binding maps graph-authored requirements to verified
capability-pack identities and digests, registry-owned artifact families to
renderer implementation/version identities, and deployment-target
implementation profiles. Capability and deployment selections can only name
typed requirement nodes in the pinned graph. Renderer selections can only
name existing layout-registry families under their declared materializer and
must target graph v2. No selection can add semantic facts, registry rows,
private strategy, build-context input, or provider policy.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.runtime.app.layout_registry import (
    AppLayoutRegistry,
    MaterializerIdentifier,
    default_app_layout_registry,
)
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import (
    SemanticGraph,
    SemanticNodeKind,
    _validate_node_id,
)
from mozaiksai.core.semantics.refs import (
    ExecutionAccessScopeRef,
    SemanticGraphRef,
    SemanticsModel,
    _validate_digest,
    _validate_identifier,
)

IMPLEMENTATION_BINDING_SCHEMA_VERSION: Literal["mozaiks.implementation_binding.v1"] = (
    "mozaiks.implementation_binding.v1"
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


def _sorted_unique(
    selections: tuple[_Selection, ...], *, category: str
) -> tuple[_Selection, ...]:
    ordered = tuple(sorted(selections, key=lambda item: item.requirement_node_id))
    requirements = [item.requirement_node_id for item in ordered]
    if len(requirements) != len(set(requirements)):
        raise ValueError(f"duplicate {category} selections for one requirement node")
    return ordered


class ImplementationBinding(SemanticsModel):
    schema_version: Literal["mozaiks.implementation_binding.v1"] = (
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

    @model_validator(mode="after")
    def _validate_binding(self) -> ImplementationBinding:
        if self.semantic_graph_ref.scope != self.scope:
            raise ValueError(
                "semantic_graph_ref scope does not match the binding scope; "
                "cross-scope references fail closed"
            )
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.binding_digest != expected:
            raise ValueError("binding_digest does not match binding content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "binding_id": self.binding_id,
            "version": self.version,
            "scope": self.scope.model_dump(mode="json"),
            "semantic_graph_ref": self.semantic_graph_ref.model_dump(mode="json"),
            "capability_pack_selections": [
                item.model_dump(mode="json") for item in self.capability_pack_selections
            ],
            "renderer_selections": [
                item.model_dump(mode="json") for item in self.renderer_selections
            ],
            "deployment_profile_selections": [
                item.model_dump(mode="json") for item in self.deployment_profile_selections
            ],
        }
        if include_digest:
            payload["binding_digest"] = self.binding_digest
        return payload


def build_implementation_binding(**fields: Any) -> ImplementationBinding:
    """Construct a binding with its content digest computed canonically."""
    normalized = {
        "capability_pack_selections": tuple(
            sorted(
                tuple(fields.get("capability_pack_selections", ())),
                key=lambda item: item.requirement_node_id,
            )
        ),
        "renderer_selections": tuple(
            sorted(
                tuple(fields.get("renderer_selections", ())),
                key=lambda item: (
                    item.materializer_id.value,
                    item.implementation_id,
                    item.implementation_version,
                ),
            )
        ),
        "deployment_profile_selections": tuple(
            sorted(
                tuple(fields.get("deployment_profile_selections", ())),
                key=lambda item: item.requirement_node_id,
            )
        ),
    }
    payload = {
        "schema_version": IMPLEMENTATION_BINDING_SCHEMA_VERSION,
        "binding_id": str(fields["binding_id"]).strip(),
        "version": fields["version"],
        "scope": fields["scope"].model_dump(mode="json"),
        "semantic_graph_ref": fields["semantic_graph_ref"].model_dump(mode="json"),
        "capability_pack_selections": [
            item.model_dump(mode="json") for item in normalized["capability_pack_selections"]
        ],
        "renderer_selections": [
            item.model_dump(mode="json") for item in normalized["renderer_selections"]
        ],
        "deployment_profile_selections": [
            item.model_dump(mode="json") for item in normalized["deployment_profile_selections"]
        ],
    }
    return ImplementationBinding(
        **{**fields, **normalized}, binding_digest=canonical_digest(payload)
    )


def validate_implementation_binding_against_graph(
    binding: ImplementationBinding,
    graph: SemanticGraph | _GraphSubject,
    *,
    layout_registry: AppLayoutRegistry | None = None,
) -> None:
    """Fail closed unless every selection satisfies an existing typed requirement.

    The binding may only choose among implementations for requirement nodes the
    graph already declares; a selection naming an absent node, or a node of an
    inappropriate kind, is an attempt to introduce semantics through the
    binding and is rejected.
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


__all__ = [
    "CAPABILITY_PACK_REQUIREMENT_KINDS",
    "CapabilityPackSelection",
    "DEPLOYMENT_REQUIREMENT_KINDS",
    "DeploymentProfileSelection",
    "IMPLEMENTATION_BINDING_SCHEMA_VERSION",
    "ImplementationBinding",
    "ImplementationBindingError",
    "RENDERER_GRAPH_SCHEMA_VERSION",
    "RendererSelection",
    "build_implementation_binding",
    "validate_implementation_binding_against_graph",
]
