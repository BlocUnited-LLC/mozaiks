"""Strict immutable reference contracts for the ADR 0007 semantic compiler.

Every scoped reference pins its ref schema version, typed subject identifier,
immutable subject version, content digest, and owning
:class:`ExecutionAccessScopeRef`.  ``TaxonomyNamespaceRef`` is the one unscoped
registry reference: it pins namespace identifier, version, and digest without
inventing tenant scope.  No reference resolves by bare id, path, artifact
family, or a mutable alias such as ``latest``.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mozaiksai.core.taxonomy import SemanticCategory, validate_identifier_grammar

_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

#: Mutable-alias identifiers that must never masquerade as immutable identity.
MUTABLE_ALIASES = frozenset({"latest", "current", "head", "tip", "newest"})


class SemanticRefError(ValueError):
    """Raised when a reference is structurally invalid."""


class SemanticsModel(BaseModel):
    """Base for every compiler-surface model: unknown fields rejected, frozen."""

    model_config = ConfigDict(extra="forbid", frozen=True)


def _validate_identifier(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text or _IDENTIFIER.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a lowercase identifier, got {value!r}")
    if text in MUTABLE_ALIASES:
        raise ValueError(f"{field_name} must be immutable identity, got mutable alias {text!r}")
    return text


def _validate_digest(value: str, *, field_name: str) -> str:
    text = str(value or "").strip()
    if _HEX_DIGEST.fullmatch(text) is None:
        raise ValueError(f"{field_name} must be a 64-char lowercase hex sha256 digest")
    return text


class RefDocumentType(StrEnum):
    """The document type a reference expects its subject to be."""

    APPLICATION_MANIFEST = "application_manifest"
    SEMANTIC_GRAPH = "semantic_graph"
    IMPLEMENTATION_BINDING = "implementation_binding"
    COMPILATION_PLAN = "compilation_plan"
    BUILD_CONTEXT_BINDING = "build_context_binding"
    REFINEMENT_PATCH = "refinement_patch"
    ARTIFACT_REVISION = "artifact_revision"
    CHILD_CONTRACT = "child_contract"
    TAXONOMY_NAMESPACE = "taxonomy_namespace"


class ExecutionAccessScopeRef(SemanticsModel):
    """Immutable owning scope: tenant, optional workspace, optional pre-app scope.

    A pre-app manifest uses ``pre_app_scope_id`` as its creation scope; it never
    fabricates an ``app_id``.  Later association of a real ``app_id`` happens in
    a new manifest version and cannot replace, widen, or weaken this scope.
    """

    ref_schema_version: Literal["mozaiks.execution_access_scope_ref.v1"] = (
        "mozaiks.execution_access_scope_ref.v1"
    )
    tenant_id: str
    workspace_id: str | None = None
    pre_app_scope_id: str | None = None

    @field_validator("tenant_id")
    @classmethod
    def _tenant(cls, value: str) -> str:
        return _validate_identifier(value, field_name="tenant_id")

    @field_validator("workspace_id", "pre_app_scope_id")
    @classmethod
    def _optional_ids(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        return _validate_identifier(value, field_name=info.field_name)


class _ScopedRef(SemanticsModel):
    """Common shape of every scoped compiler reference."""

    document_type: ClassVar[RefDocumentType]

    subject_id: str
    subject_version: int = Field(ge=1, strict=True)
    content_digest: str
    scope: ExecutionAccessScopeRef

    @field_validator("subject_id")
    @classmethod
    def _subject_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="subject_id")

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="content_digest")


class ApplicationManifestRef(_ScopedRef):
    document_type: ClassVar[RefDocumentType] = RefDocumentType.APPLICATION_MANIFEST
    ref_schema_version: Literal["mozaiks.application_manifest_ref.v1"] = (
        "mozaiks.application_manifest_ref.v1"
    )


class SemanticGraphRef(_ScopedRef):
    document_type: ClassVar[RefDocumentType] = RefDocumentType.SEMANTIC_GRAPH
    ref_schema_version: Literal["mozaiks.semantic_graph_ref.v1"] = "mozaiks.semantic_graph_ref.v1"


class ImplementationBindingRef(_ScopedRef):
    document_type: ClassVar[RefDocumentType] = RefDocumentType.IMPLEMENTATION_BINDING
    ref_schema_version: Literal["mozaiks.implementation_binding_ref.v1"] = (
        "mozaiks.implementation_binding_ref.v1"
    )


class CompilationPlanRef(_ScopedRef):
    """Reference contract only; ``CompilationPlan`` content is later-slice work."""

    document_type: ClassVar[RefDocumentType] = RefDocumentType.COMPILATION_PLAN
    ref_schema_version: Literal["mozaiks.compilation_plan_ref.v1"] = (
        "mozaiks.compilation_plan_ref.v1"
    )


class BuildContextBindingRef(_ScopedRef):
    """Reference contract only; binding content assembly is later-slice work."""

    document_type: ClassVar[RefDocumentType] = RefDocumentType.BUILD_CONTEXT_BINDING
    ref_schema_version: Literal["mozaiks.build_context_binding_ref.v1"] = (
        "mozaiks.build_context_binding_ref.v1"
    )


class RefinementPatchRef(_ScopedRef):
    """Reference contract only; ``RefinementPatch`` application is later-slice work."""

    document_type: ClassVar[RefDocumentType] = RefDocumentType.REFINEMENT_PATCH
    ref_schema_version: Literal["mozaiks.refinement_patch_ref.v1"] = (
        "mozaiks.refinement_patch_ref.v1"
    )


class ArtifactRevisionRef(_ScopedRef):
    """Reference contract only; ``ArtifactRevision`` content is later-slice work."""

    document_type: ClassVar[RefDocumentType] = RefDocumentType.ARTIFACT_REVISION
    ref_schema_version: Literal["mozaiks.artifact_revision_ref.v1"] = (
        "mozaiks.artifact_revision_ref.v1"
    )


class ChildContractRef(_ScopedRef):
    """Typed child-contract reference: pins artifact family, canonical relative
    path, contract schema version, and content digest on top of scoped identity."""

    document_type: ClassVar[RefDocumentType] = RefDocumentType.CHILD_CONTRACT
    ref_schema_version: Literal["mozaiks.child_contract_ref.v1"] = "mozaiks.child_contract_ref.v1"
    artifact_family: str
    canonical_relative_path: str
    contract_schema_version: str

    @field_validator("artifact_family")
    @classmethod
    def _family(cls, value: str) -> str:
        return validate_identifier_grammar(SemanticCategory.ARTIFACT_FAMILY, value)

    @field_validator("contract_schema_version")
    @classmethod
    def _contract_schema_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("contract_schema_version must be non-empty")
        return text

    @field_validator("canonical_relative_path")
    @classmethod
    def _path(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("canonical_relative_path must be non-empty")
        if "\\" in text or text.startswith("/") or ":" in text:
            raise ValueError(
                "canonical_relative_path must be a POSIX relative path without "
                f"drive or absolute components, got {text!r}"
            )
        segments = text.split("/")
        for segment in segments:
            if segment in {"", ".", ".."} or _PATH_SEGMENT.fullmatch(segment) is None:
                raise ValueError(
                    f"canonical_relative_path segment {segment!r} is not allowed in {text!r}"
                )
        return text


class TaxonomyNamespaceRef(SemanticsModel):
    """Unscoped registry reference pinning namespace identifier, version, digest."""

    document_type: ClassVar[RefDocumentType] = RefDocumentType.TAXONOMY_NAMESPACE
    ref_schema_version: Literal["mozaiks.taxonomy_namespace_ref.v1"] = (
        "mozaiks.taxonomy_namespace_ref.v1"
    )
    namespace_id: str
    namespace_version: int = Field(ge=1, strict=True)
    content_digest: str

    @field_validator("namespace_id")
    @classmethod
    def _namespace_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if re.fullmatch(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*", text) is None:
            raise ValueError("namespace_id must be a lowercase dotted identifier")
        if text in MUTABLE_ALIASES:
            raise ValueError(f"namespace_id must be immutable identity, got {text!r}")
        return text

    @field_validator("content_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return _validate_digest(value, field_name="content_digest")


__all__ = [
    "ApplicationManifestRef",
    "ArtifactRevisionRef",
    "BuildContextBindingRef",
    "ChildContractRef",
    "CompilationPlanRef",
    "ExecutionAccessScopeRef",
    "ImplementationBindingRef",
    "MUTABLE_ALIASES",
    "RefDocumentType",
    "RefinementPatchRef",
    "SemanticGraphRef",
    "SemanticRefError",
    "SemanticsModel",
    "TaxonomyNamespaceRef",
]
