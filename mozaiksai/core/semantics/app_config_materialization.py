"""ADR 0007 Slice 5D-0B2A deterministic application-family rendering.

One accepted renderer authority — ``deterministic_app_config_renderer@1`` —
produces canonical bytes for the closed application-configuration family set
whose complete input authority exists on accepted semantic payloads:

- ``app_manifest``            -> ``app.json``
- ``app_ui_route_manifest``   -> ``ui/route_manifest.json``
- ``app_config``              -> ``config/ai.json``
- ``app_integrations_config`` -> ``config/integrations.yaml``
- ``app_secret_references``   -> ``security/secrets.yaml``

Every fact rendered here derives from footprint-pinned payloads
(ApplicationPayload, AuthPayload, IntegrationPayload, PagePayload,
WorkflowPayload) or from a deterministic runtime default the normative
loader already defines (``version`` when unauthored is not one of them —
version is authored intent; ``chat_startup_mode`` falls back to the
platform's own ``"ask"`` default only when no agent-driven workflow is
selected, mirroring ``mozaiksai.hosts.platform``).

The renderer is offline substrate: no filesystem, no clocks, no environment,
no AG2, no AppBuildPlan, no production callers. Families whose facts lack a
typed semantic home (subscriptions ``default_plan_id`` and assignment-store
wiring; data-contract collection->module surface ownership) are NOT rendered
here — they remain typed plan gaps for 5D-0B2B with their prerequisites
recorded in the slice tests.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mozaiksai.core.runtime.app.layout_registry import MaterializerIdentifier
from mozaiksai.core.semantics.binding import (
    ImplementationBinding,
    RendererSelection,
    validate_implementation_binding_against_graph,
)
from mozaiksai.core.semantics.compilation_plan import FamilyInstancePlan, PlanDisposition
from mozaiksai.core.semantics.decl_bytes import json_decl_bytes, yaml_decl_bytes
from mozaiksai.core.semantics.graph import SemanticGraphV2
from mozaiksai.core.semantics.payloads import (
    ApplicationPayload,
    AuthPayload,
    IntegrationConfigValueKind,
    IntegrationPayload,
    OptionalFamilyKind,
    OptionalFamilySelectionStatus,
    PagePayload,
    SemanticPayloadBase,
    WorkflowPayload,
    WorkflowStartupMode,
)

APP_CONFIG_RENDERER_IMPLEMENTATION_ID = "deterministic_app_config_renderer"
APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION = "1"

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


def resolve_app_config_renderer_selection(
    binding: ImplementationBinding,
    *,
    graph: SemanticGraphV2,
    layout_registry: Any,
) -> RendererSelection:
    """Resolve the accepted app-config renderer through the binding.

    Fails closed when the binding carries no selection covering the
    application-configuration families, claims a different materializer, or
    pins any implementation identity/version other than the single accepted
    deterministic implementation of this slice. There is no fallback to any
    historical generator path.
    """
    try:
        validate_implementation_binding_against_graph(
            binding, graph, layout_registry=layout_registry
        )
    except ValueError as exc:
        raise AppConfigMaterializationError(
            f"implementation binding rejected: {exc}"
        ) from exc
    matches = [
        selection
        for selection in binding.renderer_selections
        if APP_CONFIG_FAMILIES & set(selection.artifact_families)
    ]
    if not matches:
        raise AppConfigMaterializationError(
            "binding carries no renderer selection for the application-config families"
        )
    selection = matches[0]
    if selection.materializer_id is not MaterializerIdentifier.APP_CONFIG_EXECUTOR:
        raise AppConfigMaterializationError(
            "renderer selection for application-config families pins materializer "
            f"{selection.materializer_id.value!r}, not the app config executor"
        )
    if (
        selection.implementation_id != APP_CONFIG_RENDERER_IMPLEMENTATION_ID
        or selection.implementation_version != APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION
    ):
        raise AppConfigMaterializationError(
            "binding pins unaccepted app-config renderer implementation "
            f"{selection.implementation_id!r}@{selection.implementation_version!r}"
        )
    return selection


def _footprint_payloads(
    unit: FamilyInstancePlan, payload_by_node: Mapping[str, SemanticPayloadBase]
) -> dict[str, SemanticPayloadBase]:
    """Return exactly the payloads pinned by the unit's source footprint."""
    resolved: dict[str, SemanticPayloadBase] = {}
    for source in unit.sources:
        pinned = payload_by_node.get(source.node_id)
        if pinned is None or pinned.payload_digest != source.payload_digest:
            raise AppConfigMaterializationError(
                f"unit {unit.unit_id!r} source {source.node_id!r} is missing or does "
                "not match its pinned payload digest"
            )
        resolved[source.node_id] = pinned
    return resolved


def _one_of(
    payloads: Mapping[str, SemanticPayloadBase],
    payload_type: type,
    *,
    unit: FamilyInstancePlan,
    required: bool,
) -> Any:
    matches = [p for p in payloads.values() if isinstance(p, payload_type)]
    if len(matches) > 1:
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} footprint pins more than one "
            f"{payload_type.__name__}"
        )
    if not matches:
        if required:
            raise AppConfigMaterializationError(
                f"unit {unit.unit_id!r} footprint pins no {payload_type.__name__}"
            )
        return None
    return matches[0]


def _local_id(node_id: str) -> str:
    return node_id.rsplit(".", 1)[-1]


def _auth_required(
    application: ApplicationPayload, auth: AuthPayload | None
) -> bool:
    if auth is not None:
        return bool(auth.auth_required)
    for selection in application.optional_families:
        if selection.family is OptionalFamilyKind.AUTH:
            if selection.status is OptionalFamilySelectionStatus.SELECTED:
                raise AppConfigMaterializationError(
                    "auth is selected but no AuthPayload is pinned in the footprint"
                )
            return False
    raise AppConfigMaterializationError(
        "application selection evidence does not state the auth family"
    )


def _app_manifest_document(
    unit: FamilyInstancePlan, payloads: Mapping[str, SemanticPayloadBase]
) -> dict[str, Any]:
    application: ApplicationPayload = _one_of(
        payloads, ApplicationPayload, unit=unit, required=True
    )
    auth: AuthPayload | None = _one_of(payloads, AuthPayload, unit=unit, required=False)
    document: dict[str, Any] = {
        "appId": application.application_id,
        "appName": application.display_name,
        "version": application.version,
    }
    if application.description is not None:
        document["description"] = application.description
    document["authRequired"] = _auth_required(application, auth)
    document["startup"] = {"landing_spot": application.default_route}
    return document


def _route_manifest_document(
    unit: FamilyInstancePlan, payloads: Mapping[str, SemanticPayloadBase]
) -> dict[str, Any]:
    application: ApplicationPayload = _one_of(
        payloads, ApplicationPayload, unit=unit, required=True
    )
    pages = sorted(
        (p for p in payloads.values() if isinstance(p, PagePayload)),
        key=lambda p: str(p.route or ""),
    )
    entries: list[dict[str, Any]] = []
    declared_routes: set[str] = set()
    for page in pages:
        if not page.route or not page.title:
            raise AppConfigMaterializationError(
                f"page {page.node_id!r} lacks the route/title facts the route "
                "manifest requires"
            )
        declared_routes.add(page.route)
        entries.append(
            {
                "path": page.route,
                "component": "SchemaPage",
                "label": page.title,
                "schema": page.page_id,
            }
        )
    if application.default_route not in declared_routes:
        raise AppConfigMaterializationError(
            "application default_route does not resolve to a declared page route"
        )
    return {"pages": entries}


def _ai_config_document(
    unit: FamilyInstancePlan, payloads: Mapping[str, SemanticPayloadBase]
) -> dict[str, Any]:
    _one_of(payloads, ApplicationPayload, unit=unit, required=True)
    workflows = [p for p in payloads.values() if isinstance(p, WorkflowPayload)]
    mode = _CHAT_STARTUP_DEFAULT
    if any(w.startup_mode is WorkflowStartupMode.AGENT_DRIVEN for w in workflows):
        mode = "workflow"
    return {"chat": {"chat_startup_mode": mode}}


def _integration_entries(
    payloads: Mapping[str, SemanticPayloadBase],
) -> list[IntegrationPayload]:
    return sorted(
        (p for p in payloads.values() if isinstance(p, IntegrationPayload)),
        key=lambda p: p.integration_id,
    )


_CONFIG_VALUE_TYPES = {
    IntegrationConfigValueKind.TEXT: "text",
    IntegrationConfigValueKind.URL: "url",
    IntegrationConfigValueKind.SECRET: "secret",
}


def _integrations_document(
    unit: FamilyInstancePlan, payloads: Mapping[str, SemanticPayloadBase]
) -> dict[str, Any]:
    application: ApplicationPayload = _one_of(
        payloads, ApplicationPayload, unit=unit, required=True
    )
    integrations = _integration_entries(payloads)
    selected = any(
        s.family is OptionalFamilyKind.INTEGRATIONS
        and s.status is OptionalFamilySelectionStatus.SELECTED
        for s in application.optional_families
    )
    if selected and not integrations:
        raise AppConfigMaterializationError(
            "integrations are selected but no IntegrationPayload is pinned"
        )
    entries: list[dict[str, Any]] = []
    for integration in integrations:
        entry: dict[str, Any] = {
            "service": integration.integration_id,
            "kind": integration.integration_kind.value,
        }
        if integration.purpose is not None:
            entry["purpose"] = integration.purpose
        entry["required_at"] = integration.required_at.value
        entry["optional"] = integration.optional
        entry["required_fields"] = [
            {
                "name": requirement.name,
                "type": _CONFIG_VALUE_TYPES[requirement.value_kind],
                "required": requirement.required,
            }
            for requirement in integration.config_requirements
        ]
        entries.append(entry)
    return {"integrations": entries}


def _secret_references_document(
    unit: FamilyInstancePlan, payloads: Mapping[str, SemanticPayloadBase]
) -> dict[str, Any]:
    _one_of(payloads, ApplicationPayload, unit=unit, required=True)
    names: set[str] = set()
    for integration in _integration_entries(payloads):
        for requirement in integration.config_requirements:
            if requirement.value_kind is IntegrationConfigValueKind.SECRET:
                names.add(requirement.name)
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
    payload_by_node: Mapping[str, SemanticPayloadBase],
) -> bytes:
    """Render one application-configuration unit's canonical bytes.

    The unit's plan-owned output path selects the artifact contract; the
    renderer never chooses paths and never reads state outside the unit's
    pinned source footprint.
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
    build_document, serialize = builder
    payloads = _footprint_payloads(unit, payload_by_node)
    return serialize(build_document(unit, payloads))


__all__ = [
    "APP_CONFIG_FAMILIES",
    "APP_CONFIG_RENDERER_IMPLEMENTATION_ID",
    "APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION",
    "AppConfigMaterializationError",
    "render_app_config_unit",
    "resolve_app_config_renderer_selection",
]
