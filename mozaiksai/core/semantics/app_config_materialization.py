"""ADR 0007 Slice 5D-0B2A deterministic application-family rendering.

One accepted renderer authority — ``deterministic_app_config_renderer@1`` —
produces canonical bytes for the closed application-configuration family set
whose complete input authority exists on accepted semantic payloads:

- ``app_manifest``            -> ``app.json``
- ``app_ui_route_manifest``   -> ``ui/route_manifest.json``
- ``app_config``              -> ``config/ai.json``
- ``app_integrations_config`` -> ``config/integrations.yaml``
- ``app_secret_references``   -> ``security/secrets.yaml``

Dependency direction is one-way: the central offline materialization owner
(``mozaiksai.core.semantics.materialization``) resolves semantic refs,
validates the graph/plan/binding relationship, and projects the accepted
payload facts into one closed, immutable :class:`ApplicationFamilyRenderInput`
snapshot. This module consumes only that snapshot. It imports no semantic
payload classes, no graph model, and no binding machinery — the snapshot is
derived data pinned to exact source payload digests, never a second authored
semantic model.

The renderer is offline substrate: no filesystem, no clocks, no environment,
no AG2, no AppBuildPlan, no production callers. Families whose facts lack a
typed semantic home (subscriptions ``default_plan_id`` and assignment-store
wiring; data-contract collection->module surface ownership) are NOT rendered
here — they remain typed plan gaps for 5D-0B2B with their prerequisites
recorded in the slice tests.
"""

from __future__ import annotations

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
APP_CONFIG_FAMILIES: frozenset[str] = frozenset(
    {
        "app_manifest",
        "app_ui_route_manifest",
        "app_config",
        "app_integrations_config",
        "app_secret_references",
    }
)

#: Output templates this implementation renders. ``config/asset_manifest.json``
#: shares the ``app_config`` family kind but has no typed semantic source yet;
#: rendering it fails closed as an explicit 0B2B boundary.
_RENDERED_TEMPLATES: frozenset[str] = frozenset(
    {
        "app.json",
        "app/app.json",
        "ui/route_manifest.json",
        "config/ai.json",
        "config/integrations.yaml",
        "security/secrets.yaml",
    }
)

_CHAT_STARTUP_DEFAULT = "ask"


class AppConfigMaterializationError(ValueError):
    """The inputs violate the Slice 5D-0B2A application-family contract."""


class _ClosedRenderInputModel(BaseModel):
    """Frozen, unknown-field-rejecting base for every snapshot component."""

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


class ApplicationFamilyRenderInput(_ClosedRenderInputModel):
    """Closed immutable render input for the application-configuration families.

    Derived data only: the offline materialization owner projects accepted,
    footprint-pinned payload facts into this snapshot and ties it to the exact
    source payload digests in ``sources``. Equivalent facts supplied in any
    order normalize to one canonical snapshot, so identical semantics always
    produce identical bytes. The model is recursively frozen and rejects every
    undeclared field — it cannot carry arbitrary metadata, provider state,
    clocks, or environment.
    """

    render_input_schema_version: Literal["mozaiks.app_family_render_input.v1"]
    application_id: str
    display_name: str
    version: str
    description: str | None
    default_route: str
    auth_required: bool
    pages: tuple[RenderInputPage, ...]
    has_agent_driven_workflow: bool
    integrations: tuple[RenderInputIntegration, ...]
    sources: tuple[RenderInputSource, ...]

    @model_validator(mode="after")
    def _canonical_order(self) -> ApplicationFamilyRenderInput:
        pages = tuple(sorted(self.pages, key=lambda p: p.route))
        routes = [p.route for p in pages]
        if len(set(routes)) != len(routes):
            raise ValueError("render input declares duplicate page routes")
        integrations = tuple(
            sorted(self.integrations, key=lambda i: i.integration_id)
        )
        integration_ids = [i.integration_id for i in integrations]
        if len(set(integration_ids)) != len(integration_ids):
            raise ValueError("render input declares duplicate integration ids")
        sources = tuple(sorted(self.sources, key=lambda s: s.node_id))
        source_ids = [s.node_id for s in sources]
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("render input declares duplicate source node ids")
        if not sources:
            raise ValueError("render input pins no semantic sources")
        object.__setattr__(self, "pages", pages)
        object.__setattr__(self, "integrations", integrations)
        object.__setattr__(self, "sources", sources)
        return self

    def source_digest(self, node_id: str) -> str | None:
        for source in self.sources:
            if source.node_id == node_id:
                return source.payload_digest
        return None


def _verify_unit_sources(
    unit: FamilyInstancePlan, render_input: ApplicationFamilyRenderInput
) -> None:
    """Every unit-pinned source must match the snapshot's exact digest."""
    for source in unit.sources:
        pinned = render_input.source_digest(source.node_id)
        if pinned is None or pinned != source.payload_digest:
            raise AppConfigMaterializationError(
                f"unit {unit.unit_id!r} source {source.node_id!r} is missing or does "
                "not match its pinned payload digest"
            )


def _app_manifest_document(
    unit: FamilyInstancePlan, render_input: ApplicationFamilyRenderInput
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
    unit: FamilyInstancePlan, render_input: ApplicationFamilyRenderInput
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


def _ai_config_document(
    unit: FamilyInstancePlan, render_input: ApplicationFamilyRenderInput
) -> dict[str, object]:
    mode = (
        "workflow"
        if render_input.has_agent_driven_workflow
        else _CHAT_STARTUP_DEFAULT
    )
    return {"chat": {"chat_startup_mode": mode}}


def _integrations_document(
    unit: FamilyInstancePlan, render_input: ApplicationFamilyRenderInput
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
    unit: FamilyInstancePlan, render_input: ApplicationFamilyRenderInput
) -> dict[str, object]:
    names = {
        requirement.name
        for integration in render_input.integrations
        for requirement in integration.config_requirements
        if requirement.value_type == "secret"
    }
    return {"version": 1, "secrets": sorted(names)}


_DOCUMENT_BUILDERS = {
    "app.json": (_app_manifest_document, json_decl_bytes),
    "app/app.json": (_app_manifest_document, json_decl_bytes),
    "ui/route_manifest.json": (_route_manifest_document, json_decl_bytes),
    "config/ai.json": (_ai_config_document, json_decl_bytes),
    "config/integrations.yaml": (_integrations_document, yaml_decl_bytes),
    "security/secrets.yaml": (_secret_references_document, yaml_decl_bytes),
}


def render_app_config_unit(
    *,
    unit: FamilyInstancePlan,
    render_input: ApplicationFamilyRenderInput,
) -> bytes:
    """Render one application-configuration unit's canonical bytes.

    The unit's plan-owned output path selects the artifact contract; the
    renderer never chooses paths and never reads state outside the closed
    render-input snapshot whose sources cover the unit's pinned footprint.
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
    _verify_unit_sources(unit, render_input)
    build_document, serialize = builder
    return serialize(build_document(unit, render_input))


__all__ = [
    "APP_CONFIG_FAMILIES",
    "APP_CONFIG_RENDERER_IMPLEMENTATION_ID",
    "APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION",
    "APP_FAMILY_RENDER_INPUT_VERSION",
    "AppConfigMaterializationError",
    "ApplicationFamilyRenderInput",
    "RenderInputConfigRequirement",
    "RenderInputIntegration",
    "RenderInputPage",
    "RenderInputSource",
    "render_app_config_unit",
]
