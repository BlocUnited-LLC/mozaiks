"""Minimal versioned ``mozaiks.app_manifest.v1`` reference document.

The manifest references; it does not duplicate.  Modules, pages, data, events,
plans, and workflows live as graph nodes and rendered child contracts, never as
inline manifest content.  Manifest versions are immutable: assigning a real
``app_id`` to a pre-app manifest requires a new manifest version and never
mutates the pre-app creation scope.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from mozaiksai.core.app_context.models import AppContextMode
from mozaiksai.core.semantics.canonical import canonical_digest
from mozaiksai.core.semantics.refs import (
    ArtifactRevisionRef,
    BuildContextBindingRef,
    ExecutionAccessScopeRef,
    ImplementationBindingRef,
    SemanticGraphRef,
    SemanticsModel,
    TaxonomyNamespaceRef,
    _validate_identifier,
)

APP_MANIFEST_SCHEMA_VERSION: Literal["mozaiks.app_manifest.v1"] = "mozaiks.app_manifest.v1"


class ApplicationManifest(SemanticsModel):
    schema_version: Literal["mozaiks.app_manifest.v1"] = APP_MANIFEST_SCHEMA_VERSION
    manifest_id: str
    version: int = Field(ge=1, strict=True)
    scope: ExecutionAccessScopeRef
    mode: AppContextMode
    app_id: str | None = None
    semantic_graph_ref: SemanticGraphRef
    implementation_binding_ref: ImplementationBindingRef
    taxonomy_refs: tuple[TaxonomyNamespaceRef, ...] = Field(min_length=1)
    build_context_binding_ref: BuildContextBindingRef
    artifact_revision_ref: ArtifactRevisionRef | None = None
    compiler_version: str
    renderer_registry_version: str
    manifest_digest: str = Field(min_length=64, max_length=64)

    @field_validator("manifest_id")
    @classmethod
    def _manifest_id(cls, value: str) -> str:
        return _validate_identifier(value, field_name="manifest_id")

    @field_validator("app_id")
    @classmethod
    def _app_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_identifier(value, field_name="app_id")

    @field_validator("compiler_version", "renderer_registry_version")
    @classmethod
    def _versions(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be non-empty")
        return text

    @field_validator("taxonomy_refs")
    @classmethod
    def _taxonomy_refs(
        cls, value: tuple[TaxonomyNamespaceRef, ...]
    ) -> tuple[TaxonomyNamespaceRef, ...]:
        ordered = tuple(sorted(value, key=lambda item: (item.namespace_id, item.namespace_version)))
        identities = [item.namespace_id for item in ordered]
        if len(identities) != len(set(identities)):
            raise ValueError("duplicate pinned taxonomy namespaces")
        return ordered

    @model_validator(mode="after")
    def _validate_manifest(self) -> ApplicationManifest:
        if self.app_id is None and self.scope.pre_app_scope_id is None:
            raise ValueError(
                "a pre-app manifest (app_id is None) requires scope.pre_app_scope_id; "
                "it must not invent an app_id"
            )
        for name, ref in (
            ("semantic_graph_ref", self.semantic_graph_ref),
            ("implementation_binding_ref", self.implementation_binding_ref),
            ("build_context_binding_ref", self.build_context_binding_ref),
            ("artifact_revision_ref", self.artifact_revision_ref),
        ):
            if ref is not None and ref.scope != self.scope:
                raise ValueError(
                    f"{name} scope does not match the manifest ExecutionAccessScopeRef; "
                    "cross-scope references fail closed"
                )
        if self.artifact_revision_ref is not None and (
            self.app_id is None or self.artifact_revision_ref.app_id != self.app_id
        ):
            raise ValueError(
                "artifact_revision_ref must belong to the manifest application; "
                "pre-app or foreign-app revision references fail closed"
            )
        expected = canonical_digest(self.canonical_payload(include_digest=False))
        if self.manifest_digest != expected:
            raise ValueError("manifest_digest does not match manifest content")
        return self

    def canonical_payload(self, *, include_digest: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "version": self.version,
            "scope": self.scope.model_dump(mode="json"),
            "mode": self.mode.value,
            "app_id": self.app_id,
            "semantic_graph_ref": self.semantic_graph_ref.model_dump(mode="json"),
            "implementation_binding_ref": self.implementation_binding_ref.model_dump(mode="json"),
            "taxonomy_refs": [item.model_dump(mode="json") for item in self.taxonomy_refs],
            "build_context_binding_ref": self.build_context_binding_ref.model_dump(mode="json"),
            "artifact_revision_ref": (
                None
                if self.artifact_revision_ref is None
                else self.artifact_revision_ref.model_dump(mode="json")
            ),
            "compiler_version": self.compiler_version,
            "renderer_registry_version": self.renderer_registry_version,
        }
        if include_digest:
            payload["manifest_digest"] = self.manifest_digest
        return payload


def build_application_manifest(**fields: Any) -> ApplicationManifest:
    """Construct a manifest with its content digest computed canonically."""
    taxonomy_refs = tuple(
        sorted(
            tuple(fields["taxonomy_refs"]),
            key=lambda item: (item.namespace_id, item.namespace_version),
        )
    )
    payload = {
        "schema_version": APP_MANIFEST_SCHEMA_VERSION,
        "manifest_id": str(fields["manifest_id"]).strip(),
        "version": fields["version"],
        "scope": fields["scope"].model_dump(mode="json"),
        "mode": AppContextMode(fields["mode"]).value,
        "app_id": fields.get("app_id"),
        "semantic_graph_ref": fields["semantic_graph_ref"].model_dump(mode="json"),
        "implementation_binding_ref": fields["implementation_binding_ref"].model_dump(mode="json"),
        "taxonomy_refs": [item.model_dump(mode="json") for item in taxonomy_refs],
        "build_context_binding_ref": fields["build_context_binding_ref"].model_dump(mode="json"),
        "artifact_revision_ref": (
            None
            if fields.get("artifact_revision_ref") is None
            else fields["artifact_revision_ref"].model_dump(mode="json")
        ),
        "compiler_version": str(fields["compiler_version"]).strip(),
        "renderer_registry_version": str(fields["renderer_registry_version"]).strip(),
    }
    return ApplicationManifest(**fields, manifest_digest=canonical_digest(payload))


__all__ = [
    "APP_MANIFEST_SCHEMA_VERSION",
    "ApplicationManifest",
    "build_application_manifest",
]
