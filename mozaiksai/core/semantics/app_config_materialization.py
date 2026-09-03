"""ADR 0007 Slice 5D-0B2A deterministic application-family rendering.

One accepted renderer authority — ``deterministic_app_config_renderer@1`` —
produces canonical bytes for the closed application-configuration family set
whose complete input authority exists on accepted semantic payloads:

- ``app_manifest``            -> ``app.json``
- ``app_ui_route_manifest``   -> ``ui/route_manifest.json``
- ``app_integrations_config`` -> ``config/integrations.yaml``
- ``app_secret_references``   -> ``security/secrets.yaml``

``app_config`` (``config/ai.json``) is deliberately NOT renderer-ready in this
slice: per-workflow ``workflow_startup_mode`` is not application-level chat
launch authority, and the semantic model has no application-level AI-launch
facts (chat startup mode, workflow entry point). The family stays a typed
plan gap until that prerequisite exists; nothing here infers a startup mode
or entry point.

Each family consumes its own closed, frozen, family-local render input —
``AppManifestRenderInput``, ``RouteManifestRenderInput``,
``IntegrationsConfigRenderInput``, ``SecretReferencesRenderInput`` — projected
by the central offline materialization owner from exactly that unit's
plan-pinned sources. Missing facts for one family never block another family
whose own source closure is complete. Dependency direction stays one-way:
this module imports no semantic payload classes, no graph model, and no
binding machinery; the inputs are derived data pinned to exact source payload
digests, never a second authored semantic model.

The renderer is offline substrate: no filesystem, no clocks, no environment,
no AG2, no AppBuildPlan, no production callers.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from mozaiksai.core.semantics.compilation_plan import FamilyInstancePlan, PlanDisposition
from mozaiksai.core.semantics.decl_bytes import json_decl_bytes, yaml_decl_bytes

APP_CONFIG_RENDERER_IMPLEMENTATION_ID = "deterministic_app_config_renderer"
APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION = "1"

APP_FAMILY_RENDER_INPUT_VERSION: Literal["mozaiks.app_family_render_input.v1"] = (
    "mozaiks.app_family_render_input.v1"
)

#: The closed family set this renderer implementation may produce.
#: ``app_config`` is intentionally excluded: application-level AI-launch
#: authority does not exist in the semantic model yet.
APP_CONFIG_FAMILIES: frozenset[str] = frozenset(
    {
        "app_manifest",
        "app_ui_route_manifest",
        "app_integrations_config",
        "app_secret_references",
    }
)

#: Output templates this implementation renders. ``config/ai.json`` and
#: ``config/asset_manifest.json`` are not in this set; rendering them fails
#: closed as explicit deferred-prerequisite boundaries.
_RENDERED_TEMPLATES: frozenset[str] = frozenset(
    {
        "app.json",
        "app/app.json",
        "ui/route_manifest.json",
        "config/integrations.yaml",
        "security/secrets.yaml",
    }
)


class AppConfigMaterializationError(ValueError):
    """The inputs violate the Slice 5D-0B2A application-family contract."""


class _ClosedRenderInputModel(BaseModel):
    """Frozen, unknown-field-rejecting base for every render-input component."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class RenderInputSource(_ClosedRenderInputModel):
    """One pinned semantic source: exact node identity and payload digest."""

    node_id: str
    payload_digest: str


class RenderInputPage(_ClosedRenderInputModel):
    """The route-manifest facts of one declared page."""

    page_id: str
    route: str
    title: str


class RenderInputConfigRequirement(_ClosedRenderInputModel):
    """One integration configuration requirement, names only — never values."""

    name: str
    value_type: Literal["text", "url", "secret"]
    required: bool


class RenderInputIntegration(_ClosedRenderInputModel):
    """The declaration facts of one selected integration."""

    integration_id: str
    kind: str
    purpose: str | None
    required_at: str
    optional: bool
    config_requirements: tuple[RenderInputConfigRequirement, ...]


class _FamilyRenderInputBase(_ClosedRenderInputModel):
    """Shared identity of every family-local render input.

    Each input carries only the facts its one family consumes plus the exact
    source payload digests that produced them. Inputs are normalized to one
    canonical order so equivalent facts supplied in any order render identical
    bytes, and they are recursively frozen and closed — no arbitrary metadata,
    provider state, clocks, or environment can enter.
    """

    render_input_schema_version: Literal["mozaiks.app_family_render_input.v1"]
    sources: tuple[RenderInputSource, ...]

    @model_validator(mode="after")
    def _canonical_sources(self) -> _FamilyRenderInputBase:
        sources = tuple(sorted(self.sources, key=lambda s: s.node_id))
        source_ids = [s.node_id for s in sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("render input declares duplicate source node ids")
        if not sources:
            raise ValueError("render input pins no semantic sources")
        object.__setattr__(self, "sources", sources)
        return self

    def source_digest(self, node_id: str) -> str | None:
        for source in self.sources:
            if source.node_id == node_id:
                return source.payload_digest
        return None


class AppManifestRenderInput(_FamilyRenderInputBase):
    """Facts consumed by ``app.json`` only."""

    family: Literal["app_manifest"] = "app_manifest"
    application_id: str
    display_name: str
    version: str
    description: str | None
    default_route: str
    auth_required: bool


class RouteManifestRenderInput(_FamilyRenderInputBase):
    """Facts consumed by ``ui/route_manifest.json`` only."""

    family: Literal["app_ui_route_manifest"] = "app_ui_route_manifest"
    default_route: str
    pages: tuple[RenderInputPage, ...]

    @model_validator(mode="after")
    def _canonical_pages(self) -> RouteManifestRenderInput:
        pages = tuple(sorted(self.pages, key=lambda p: p.route))
        routes = [p.route for p in pages]
        if len(set(routes)) != len(routes):
            raise ValueError("render input declares duplicate page routes")
        object.__setattr__(self, "pages", pages)
        return self


class IntegrationsConfigRenderInput(_FamilyRenderInputBase):
    """Facts consumed by ``config/integrations.yaml`` only."""

    family: Literal["app_integrations_config"] = "app_integrations_config"
    integrations: tuple[RenderInputIntegration, ...]

    @model_validator(mode="after")
    def _canonical_integrations(self) -> IntegrationsConfigRenderInput:
        integrations = tuple(
            sorted(self.integrations, key=lambda i: i.integration_id)
        )
        integration_ids = [i.integration_id for i in integrations]
        if len(set(integration_ids)) != len(integration_ids):
            raise ValueError("render input declares duplicate integration ids")
        object.__setattr__(self, "integrations", integrations)
        return self


class SecretReferencesRenderInput(_FamilyRenderInputBase):
    """Names-only secret handles consumed by ``security/secrets.yaml``."""

    family: Literal["app_secret_references"] = "app_secret_references"
    secret_names: tuple[str, ...]

    @model_validator(mode="after")
    def _canonical_names(self) -> SecretReferencesRenderInput:
        names = tuple(sorted(set(self.secret_names)))
        object.__setattr__(self, "secret_names", names)
        return self


AppFamilyRenderInput = (
    AppManifestRenderInput
    | RouteManifestRenderInput
    | IntegrationsConfigRenderInput
    | SecretReferencesRenderInput
)


def _verify_unit_sources(
    unit: FamilyInstancePlan, render_input: AppFamilyRenderInput
) -> None:
    """The render input must bind exactly the unit's pinned source set.

    Not a subset, not a superset, not "at least the required sources": after
    canonical normalization, the (node_id, payload_digest) identity tuples of
    the render input must equal the PlanUnit's source footprint exactly. A
    missing source, an additional source, a duplicate, a stale digest, or a
    substituted identity all fail closed — no unrelated payload can become
    hidden provenance of a family's bytes.
    """
    expected = tuple(
        sorted((s.node_id, s.payload_digest) for s in unit.sources)
    )
    if len({node_id for node_id, _ in expected}) != len(expected):
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} pins duplicate source node ids"
        )
    actual = tuple((s.node_id, s.payload_digest) for s in render_input.sources)
    if actual != expected:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} render input does not bind exactly the "
            "unit's pinned source set — a source is missing, extra, "
            "substituted, or does not match its pinned payload digest: "
            f"expected {[n for n, _ in expected]!r}, "
            f"got {[n for n, _ in actual]!r}"
        )


def _app_manifest_document(
    unit: FamilyInstancePlan, render_input: AppManifestRenderInput
) -> dict[str, object]:
    document: dict[str, object] = {
        "appId": render_input.application_id,
        "appName": render_input.display_name,
        "version": render_input.version,
    }
    if render_input.description is not None:
        document["description"] = render_input.description
    document["authRequired"] = render_input.auth_required
    document["startup"] = {"landing_spot": render_input.default_route}
    return document


def _route_manifest_document(
    unit: FamilyInstancePlan, render_input: RouteManifestRenderInput
) -> dict[str, object]:
    entries = [
        {
            "path": page.route,
            "component": "SchemaPage",
            "label": page.title,
            "schema": page.page_id,
        }
        for page in render_input.pages
    ]
    if render_input.default_route not in {page.route for page in render_input.pages}:
        raise AppConfigMaterializationError(
            "application default_route does not resolve to a declared page route"
        )
    return {"pages": entries}


def _integrations_document(
    unit: FamilyInstancePlan, render_input: IntegrationsConfigRenderInput
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for integration in render_input.integrations:
        entry: dict[str, object] = {
            "service": integration.integration_id,
            "kind": integration.kind,
        }
        if integration.purpose is not None:
            entry["purpose"] = integration.purpose
        entry["required_at"] = integration.required_at
        entry["optional"] = integration.optional
        entry["required_fields"] = [
            {
                "name": requirement.name,
                "type": requirement.value_type,
                "required": requirement.required,
            }
            for requirement in integration.config_requirements
        ]
        entries.append(entry)
    return {"integrations": entries}


def _secret_references_document(
    unit: FamilyInstancePlan, render_input: SecretReferencesRenderInput
) -> dict[str, object]:
    return {"version": 1, "secrets": list(render_input.secret_names)}


_DOCUMENT_BUILDERS: dict[
    str, tuple[str, Callable[..., dict[str, object]], Callable[..., bytes]]
] = {
    "app.json": ("app_manifest", _app_manifest_document, json_decl_bytes),
    "app/app.json": ("app_manifest", _app_manifest_document, json_decl_bytes),
    "ui/route_manifest.json": (
        "app_ui_route_manifest",
        _route_manifest_document,
        json_decl_bytes,
    ),
    "config/integrations.yaml": (
        "app_integrations_config",
        _integrations_document,
        yaml_decl_bytes,
    ),
    "security/secrets.yaml": (
        "app_secret_references",
        _secret_references_document,
        yaml_decl_bytes,
    ),
}


def render_app_config_unit(
    *,
    unit: FamilyInstancePlan,
    render_input: AppFamilyRenderInput,
) -> bytes:
    """Render one application-configuration unit's canonical bytes.

    The unit's plan-owned output path selects the artifact contract; the
    renderer never chooses paths, never reads state outside the family-local
    render input covering the unit's pinned footprint, and rejects an input
    variant that belongs to a different family.
    """
    if unit.family_kind not in APP_CONFIG_FAMILIES:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} family {unit.family_kind!r} is not an "
            "application-configuration family of this slice"
        )
    if unit.disposition is not PlanDisposition.RENDER:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} disposition {unit.disposition.value!r} is not render"
        )
    if len(unit.outputs) != 1:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} must own exactly one output path"
        )
    path = unit.outputs[0].path
    builder = _DOCUMENT_BUILDERS.get(path)
    if builder is None:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} output {path!r} has no deterministic "
            "application-config contract in this slice"
        )
    family, build_document, serialize = builder
    if unit.family_kind != family or render_input.family != family:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} ({unit.family_kind!r}) does not match the "
            f"{render_input.family!r} render input for output {path!r}"
        )
    _verify_unit_sources(unit, render_input)
    return serialize(build_document(unit, render_input))


__all__ = [
    "APP_CONFIG_FAMILIES",
    "APP_CONFIG_RENDERER_IMPLEMENTATION_ID",
    "APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION",
    "APP_FAMILY_RENDER_INPUT_VERSION",
    "AppConfigMaterializationError",
    "AppFamilyRenderInput",
    "AppManifestRenderInput",
    "IntegrationsConfigRenderInput",
    "RenderInputConfigRequirement",
    "RenderInputIntegration",
    "RenderInputPage",
    "RenderInputSource",
    "RouteManifestRenderInput",
    "SecretReferencesRenderInput",
    "render_app_config_unit",
]
