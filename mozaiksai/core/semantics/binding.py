"""Immutable ``mozaiks.implementation_binding.v1`` contract.

An implementation binding maps graph-authored requirements to verified
capability-pack identities and digests, renderer/adapter identities and
versions, and deployment-target implementation profiles.  Structurally it can
only *select* implementations for requirement nodes that already exist in the
pinned graph: it carries no fields that could add pages, actions, events,
entitlements, data requirements, or provider obligations, and validation
rejects any selection whose requirement node is absent from, or of the wrong
kind for, the graph.  No private strategy, build-context input, or provider
choice becomes a second semantic authority.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.graph import SemanticGraph, SemanticNodeKind, _validate_node_id
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
RENDERER_REQUIREMENT_KINDS = frozenset(
    {SemanticNodeKind.SURFACE, SemanticNodeKind.PAGE, SemanticNodeKind.SECTION}
)
DEPLOYMENT_REQUIREMENT_KINDS = frozenset({SemanticNodeKind.DEPLOYMENT_TARGET})


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


class RendererSelection(_Selection):
    renderer_id: str
    renderer_version: str

    @field_validator("renderer_id")
    @classmethod
    def _renderer_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="renderer_id")

    @field_validator("renderer_version")
    @classmethod
    def _renderer_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("renderer_version must be non-empty")
        return text


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
        return _sorted_unique(value, category="renderer")

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
                key=lambda item: item.requirement_node_id,
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
    binding: ImplementationBinding, graph: SemanticGraph
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
        ("renderer", binding.renderer_selections, RENDERER_REQUIREMENT_KINDS),
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


__all__ = [
    "CAPABILITY_PACK_REQUIREMENT_KINDS",
    "CapabilityPackSelection",
    "DEPLOYMENT_REQUIREMENT_KINDS",
    "DeploymentProfileSelection",
    "IMPLEMENTATION_BINDING_SCHEMA_VERSION",
    "ImplementationBinding",
    "ImplementationBindingError",
    "RENDERER_REQUIREMENT_KINDS",
    "RendererSelection",
    "build_implementation_binding",
    "validate_implementation_binding_against_graph",
]
