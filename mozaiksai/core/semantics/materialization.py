"""ADR 0007 Slice 4C deterministic offline materialization (offline-only).

This module owns the deterministic materialization path:

    SemanticGraphV2 -> CompilationPlan -> accepted ImplementationBinding
    -> canonical family bytes -> composed candidate bundle

Page schemas, the closed application-configuration families, and workspace
workflow module interfaces render from each unit's exact semantic footprint.
The interface projection includes owned advisory results and pinned event
taxonomy; its pure renderer lives in the separate interface materialization
module. This owner consumes no AppBuildPlan, agent context,
generated files, conversation history, runtime state, environment variables,
clocks, randomness, or filesystem enumeration — this module deliberately
imports none of those capabilities.

Authorities are unchanged: ``layout_registry`` remains the sole
family/materializer authority; the ``CompilationPlan`` owns dispositions,
source footprints, and physical output paths; the graph-v2
``ImplementationBinding`` pins the one accepted deterministic implementation
identity/version for each selected materializer. This module adds no
registry, no alternate family map, no alternate output ownership, and no
alternate artifact identity.

Canonical serialization contract (``mozaiks.page_bytes.v1``): the rendered
page document is the normative :class:`AppPageSchema` runtime model dumped in
declaration order with ``None`` fields omitted, serialized as YAML with
stable key order (no re-sorting), UTF-8 encoding, LF newlines, unbounded line
width (no content-dependent wrapping), and no timestamps, identifiers,
comments, or provenance. Same canonical input -> same path -> same bytes ->
same content digest, across repeated invocations and fresh processes.

Non-renderable dispositions stay truthful: ``preserve_unowned`` units consume
exact caller-supplied bytes pinned by the existing ``ChildContractRef``
identity (digest verified, never normalized or re-encoded);
``external_handoff`` and ``inapplicable`` units and every typed ``PlanGap``
are reported explicitly; nothing is silently omitted and no gap is turned
into guessed content. Instance-relative output scopes (``module_relative``,
``workflow_relative``) have no declared physical-root binding in this slice
and are reported explicitly as deferred rather than being invented here.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import UnionType
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import yaml
from pydantic import BaseModel

from mozaiksai.core.runtime.app.layout_registry import MaterializerIdentifier
from mozaiksai.core.runtime.app.page_schema import (
    _CHILD_CONFIG_MODELS,
    _TOP_LEVEL_CONFIG_MODELS,
    AppPageChildSection,
    AppPageSchema,
    AppPageSection,
)
from mozaiksai.core.semantics.app_config_materialization import (
    APP_CONFIG_FAMILIES,
    APP_CONFIG_RENDERER_IMPLEMENTATION_ID,
    APP_CONFIG_RENDERER_IMPLEMENTATION_VERSION,
    APP_FAMILY_RENDER_INPUT_VERSION,
    AppConfigMaterializationError,
    AppFamilyRenderInput,
    AppManifestRenderInput,
    IntegrationsConfigRenderInput,
    RenderInputConfigRequirement,
    RenderInputIntegration,
    RenderInputPage,
    RenderInputSource,
    RouteManifestRenderInput,
    SecretReferencesRenderInput,
    render_app_config_unit,
)
from mozaiksai.core.semantics.binding import (
    ImplementationBinding,
    RendererSelection,
    validate_implementation_binding_against_graph,
)
from mozaiksai.core.semantics.compilation_plan import (
    CompilationPlan,
    FamilyInstancePlan,
    LayoutRegistrySnapshot,
    PlanDisposition,
    RegenerationClosure,
    plan_regeneration_closure,
    snapshot_layout_registry,
)
from mozaiksai.core.semantics.graph import SemanticGraphV2
from mozaiksai.core.semantics.opaque_artifact import PreservedOpaqueArtifact
from mozaiksai.core.semantics.payloads import (
    ApplicationPayload,
    AuthPayload,
    IntegrationConfigValueKind,
    IntegrationPayload,
    ModulePayload,
    OptionalFamilyKind,
    OptionalFamilySelectionStatus,
    PagePayload,
    SectionPayload,
    SemanticPayloadBase,
    WorkflowCapabilityBindingPayload,
    WorkflowCapabilityBindingRole,
    WorkflowCapabilityPayload,
    WorkflowPayload,
    WorkflowResultPayload,
    parse_semantic_payload,
)
from mozaiksai.core.semantics.plan_authority import (
    CompilationPlanAuthorityInputs,
    PlanAuthorityError,
    validate_compilation_plan_against_authority,
)
from mozaiksai.core.semantics.portable_path import detect_collisions
from mozaiksai.core.semantics.workflow_interface_materialization import (
    WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID,
    WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION,
    WORKFLOW_MODULE_INTERFACE,
    RenderInputCommitsResultBinding,
    RenderInputConsumesActionBinding,
    RenderInputTriggeredByEventBinding,
    RenderInputWorkflowBinding,
    RenderInputWorkflowCapability,
    RenderInputWorkflowResult,
    WorkflowInterfaceMaterializationError,
    WorkflowInterfaceRenderInput,
    render_workflow_module_interface_unit,
)

PAGE_SCHEMA_FAMILY: Literal["app_ui_page_schema"] = "app_ui_page_schema"
PAGE_BYTES_SCHEMA_VERSION: Literal["mozaiks.page_bytes.v1"] = "mozaiks.page_bytes.v1"

#: The single accepted deterministic implementation identity for the
#: ``page_schema_executor`` materializer in this slice. An ImplementationBinding
#: must pin exactly this identity/version or materialization fails closed.
PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID = "deterministic_page_schema_renderer"
PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION = "1"

#: Physical destination roots this slice can compose into one bundle file map.
#: Instance-relative scopes have no declared physical-root binding yet.
_COMPOSABLE_PATH_SCOPES = frozenset({"app_bundle_root", "workspace_root"})

_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class MaterializationError(ValueError):
    """The materialization inputs violate the Slice 4C contract."""


# ---------------------------------------------------------------------------
# Recursive structural closure gate (the general #450 compiler gate)
# ---------------------------------------------------------------------------

#: The only fields allowed to carry a generic mapping annotation: the page
#: section config dispatchers, whose model validators exhaustively convert the
#: raw mapping into a closed primitive-specific model before it can become
#: authoritative content. Their closure is proven by probe, not trusted.
_DISPATCHER_FIELDS = frozenset(
    {(AppPageSection, "config"), (AppPageChildSection, "config")}
)

_CLOSED_SCALARS = (str, int, float, bool, bytes, type(None))


def _is_closed_annotation(annotation: Any) -> bool:
    if annotation in _CLOSED_SCALARS or annotation is Any:
        return annotation is not Any
    if isinstance(annotation, type):
        if issubclass(annotation, Enum):
            return True
        if issubclass(annotation, BaseModel):
            return True  # walked separately
        return annotation in _CLOSED_SCALARS
    origin = get_origin(annotation)
    if origin is Annotated:
        return _is_closed_annotation(get_args(annotation)[0])
    if origin is Literal:
        return True
    if origin in (Union, UnionType):
        return all(_is_closed_annotation(arg) for arg in get_args(annotation))
    if origin in (list, tuple, frozenset, set):
        return all(
            _is_closed_annotation(arg) for arg in get_args(annotation) if arg is not Ellipsis
        )
    if origin is dict:
        key_type, value_type = get_args(annotation)
        return key_type is str and _is_closed_annotation(value_type)
    return False


def _walk_model_closure(
    model: type[BaseModel], path: str, violations: list[str], visited: set[type[BaseModel]]
) -> None:
    if model in visited:
        return
    visited.add(model)
    if model.model_config.get("extra") != "forbid":
        violations.append(f"{path}: {model.__name__} does not forbid unknown fields")
    for name, model_field in model.model_fields.items():
        annotation = model_field.annotation
        if (model, name) in _DISPATCHER_FIELDS:
            continue  # closed by exhaustive dispatch, proven below
        if not _is_closed_annotation(annotation):
            violations.append(f"{path}.{name}: open annotation {annotation!r}")
        stack: list[Any] = [annotation]
        while stack:
            candidate = stack.pop()
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                _walk_model_closure(candidate, f"{path}.{name}", violations, visited)
            else:
                stack.extend(get_args(candidate))


_closure_gate_passed = False


def validate_page_renderer_input_closure() -> None:
    """Fail closed unless every reachable renderer input domain is closed.

    Walks every model reachable from the semantic page/section payloads and
    from the section-config primitive dispatch, then proves the dispatcher
    exception empirically: every registered primitive must reject an unknown
    config key, so the generic dispatcher annotation can never admit content
    that survives into semantic authority. No family-specific exceptions.
    """
    global _closure_gate_passed
    if _closure_gate_passed:
        return
    violations: list[str] = []
    visited: set[type[BaseModel]] = set()
    for entry_model in (PagePayload, SectionPayload, AppPageSchema, AppPageSection):
        _walk_model_closure(entry_model, entry_model.__name__, violations, visited)
    dispatch: dict[str, type[BaseModel]] = {}
    dispatch.update(_TOP_LEVEL_CONFIG_MODELS)
    dispatch.update(_CHILD_CONFIG_MODELS)
    for primitive, config_model in sorted(dispatch.items()):
        _walk_model_closure(config_model, f"config[{primitive}]", violations, visited)
    for primitive in sorted(_TOP_LEVEL_CONFIG_MODELS):
        try:
            AppPageSection(
                id="closure_probe",
                primitive=primitive,
                config={"mozaiks_closure_probe_key": 1},
            )
        except ValueError:
            continue
        violations.append(
            f"config[{primitive}]: dispatcher accepted an unknown config key"
        )
    if violations:
        raise MaterializationError(
            "renderer input closure violated: " + "; ".join(sorted(violations))
        )
    _closure_gate_passed = True


# ---------------------------------------------------------------------------
# Implementation resolution (binding is the only implementation authority)
# ---------------------------------------------------------------------------


def resolve_page_schema_renderer_selection(
    binding: ImplementationBinding,
    *,
    graph: SemanticGraphV2,
    layout_registry: Any,
) -> RendererSelection:
    """Resolve the accepted page-schema renderer through the binding.

    Fails closed when the binding carries no selection for the page family,
    claims a materializer the registry does not declare for it, targets a
    non-v2 graph, or pins any implementation identity/version other than the
    single accepted deterministic implementation of this slice. There is no
    fallback to any historical generator path.
    """
    try:
        validate_implementation_binding_against_graph(
            binding, graph, layout_registry=layout_registry
        )
    except ValueError as exc:
        raise MaterializationError(f"implementation binding rejected: {exc}") from exc
    matches = [
        selection
        for selection in binding.renderer_selections
        if PAGE_SCHEMA_FAMILY in selection.artifact_families
    ]
    if not matches:
        raise MaterializationError(
            f"binding carries no renderer selection for {PAGE_SCHEMA_FAMILY!r}"
        )
    selection = matches[0]
    if selection.materializer_id is not MaterializerIdentifier.PAGE_SCHEMA_EXECUTOR:
        raise MaterializationError(
            f"renderer selection for {PAGE_SCHEMA_FAMILY!r} pins materializer "
            f"{selection.materializer_id.value!r}, not the page schema executor"
        )
    if (
        selection.implementation_id != PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID
        or selection.implementation_version != PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION
    ):
        raise MaterializationError(
            "binding pins unaccepted page renderer implementation "
            f"{selection.implementation_id!r}@{selection.implementation_version!r}; "
            f"accepted: {PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID!r}"
            f"@{PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION!r}"
        )
    return selection


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
    if len(matches) != 1:
        raise AppConfigMaterializationError(
            "application-config families are split across multiple renderer "
            "selections; deterministic_app_config_renderer@1 requires one "
            "selection declaring its exact family set"
        )
    selection = matches[0]
    declared = set(selection.artifact_families)
    if declared != APP_CONFIG_FAMILIES:
        raise AppConfigMaterializationError(
            "renderer selection is the authorization boundary for "
            "deterministic_app_config_renderer@1 and must declare exactly its "
            f"supported family set {sorted(APP_CONFIG_FAMILIES)}; "
            f"got {sorted(declared)}"
        )
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


# ---------------------------------------------------------------------------
# Family render input (the one payload->renderer boundary)
# ---------------------------------------------------------------------------


def resolve_workflow_interface_renderer_selection(
    binding: ImplementationBinding,
    *,
    graph: SemanticGraphV2,
    layout_registry: Any,
) -> RendererSelection:
    """Bind the one interface family to its accepted deterministic implementation."""
    validate_implementation_binding_against_graph(binding, graph, layout_registry=layout_registry)
    matches = [
        selection for selection in binding.renderer_selections
        if WORKFLOW_MODULE_INTERFACE in selection.artifact_families
    ]
    if len(matches) != 1:
        raise WorkflowInterfaceMaterializationError("binding requires one interface renderer selection")
    selection = matches[0]
    if (
        set(selection.artifact_families) != {WORKFLOW_MODULE_INTERFACE}
        or selection.materializer_id is not MaterializerIdentifier.WORKFLOW_INTERFACE_EXECUTOR
        or selection.implementation_id != WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_ID
        or selection.implementation_version != WORKFLOW_INTERFACE_RENDERER_IMPLEMENTATION_VERSION
    ):
        raise WorkflowInterfaceMaterializationError("binding pins an unaccepted interface renderer contract")
    return selection


def project_workflow_interface_render_input(
    *, unit: FamilyInstancePlan, payload_by_node: Mapping[str, SemanticPayloadBase],
) -> WorkflowInterfaceRenderInput:
    """Project only plan-pinned payloads and event identities; never consult ambient graph facts."""
    selected: dict[str, SemanticPayloadBase] = {}
    for source in unit.sources:
        payload = payload_by_node.get(source.node_id)
        if payload is None or payload.payload_digest != source.payload_digest:
            raise WorkflowInterfaceMaterializationError("interface payload source is absent or stale")
        selected[source.node_id] = payload
    workflows = [p for p in selected.values() if isinstance(p, WorkflowPayload)]
    if len(workflows) != 1:
        raise WorkflowInterfaceMaterializationError("interface requires exactly one workflow payload")
    workflow = workflows[0]
    capabilities = [p for p in selected.values() if isinstance(p, WorkflowCapabilityPayload)]
    capability_nodes = {p.node_id for p in capabilities}
    results = [p for p in selected.values() if isinstance(p, WorkflowResultPayload)]
    bindings = [p for p in selected.values() if isinstance(p, WorkflowCapabilityBindingPayload)]
    if (
        any(p.workflow_node_id != workflow.node_id for p in capabilities)
        or any(p.workflow_capability_node_id not in capability_nodes for p in results)
        or any(p.workflow_capability_node_id not in capability_nodes for p in bindings)
    ):
        raise WorkflowInterfaceMaterializationError("interface source ownership escapes its workflow")
    expected = {
        workflow.node_id, *capability_nodes,
        *(p.node_id for p in results), *(p.node_id for p in bindings),
        *(p.module_action.module_node_id for p in bindings if p.module_action),
    }
    if set(selected) != expected:
        raise WorkflowInterfaceMaterializationError("interface payload footprint is not exact")
    referenced_events = {p.event_node_id for p in bindings if p.event_node_id}
    if (
        {pin.node_id for pin in unit.taxonomy_sources} != referenced_events
        or any(pin.category != "event" for pin in unit.taxonomy_sources)
    ):
        raise WorkflowInterfaceMaterializationError("interface event taxonomy footprint is not exact")
    event_identity = {pin.node_id: pin.identifier for pin in unit.taxonomy_sources}
    projected = []
    for capability in capabilities:
        projected_bindings: list[RenderInputWorkflowBinding] = []
        for binding in bindings:
            if binding.workflow_capability_node_id != capability.node_id:
                continue
            if binding.binding_role is WorkflowCapabilityBindingRole.TRIGGERED_BY_EVENT:
                if binding.event_node_id is None:
                    raise WorkflowInterfaceMaterializationError("trigger binding lacks event identity")
                projected_bindings.append(RenderInputTriggeredByEventBinding(
                    event_type=event_identity[binding.event_node_id],
                ))
                continue
            ref = binding.module_action
            module = selected.get(ref.module_node_id) if ref else None
            if ref is None or not isinstance(module, ModulePayload):
                raise WorkflowInterfaceMaterializationError("interface action binding lacks its module payload")
            if binding.binding_role is WorkflowCapabilityBindingRole.CONSUMES_ACTION:
                projected_bindings.append(RenderInputConsumesActionBinding(
                    module_id=module.module_id, action_node_id=ref.action_node_id,
                ))
            else:
                result = selected.get(binding.workflow_result_node_id or "")
                if not isinstance(result, WorkflowResultPayload) or result.workflow_capability_node_id != capability.node_id:
                    raise WorkflowInterfaceMaterializationError("commit binding lacks its owned result")
                projected_bindings.append(RenderInputCommitsResultBinding(
                    module_id=module.module_id, action_node_id=ref.action_node_id,
                    workflow_result_id=result.result_id,
                ))
        projected.append(RenderInputWorkflowCapability(
            capability_id=capability.capability_id,
            description=capability.description,
            results=tuple(
                RenderInputWorkflowResult(result_id=p.result_id, description=p.description)
                for p in results if p.workflow_capability_node_id == capability.node_id
            ),
            bindings=tuple(projected_bindings),
        ))
    return WorkflowInterfaceRenderInput(
        workflow_id=workflow.workflow_id,
        sources=unit.sources,
        edge_sources=unit.edge_sources,
        taxonomy_sources=unit.taxonomy_sources,
        capabilities=tuple(projected),
    )


_CONFIG_VALUE_TYPES: dict[IntegrationConfigValueKind, Literal["text", "url", "secret"]] = {
    IntegrationConfigValueKind.TEXT: "text",
    IntegrationConfigValueKind.URL: "url",
    IntegrationConfigValueKind.SECRET: "secret",
}


def project_app_family_render_input(
    *,
    unit: FamilyInstancePlan,
    payload_by_node: Mapping[str, SemanticPayloadBase],
) -> AppFamilyRenderInput:
    """Project one unit's plan-pinned sources into its family-local input.

    This is the single boundary where semantic payload classes feed the
    application-family renderer. It resolves exactly the payloads pinned by
    THIS unit's source footprint, validates that family's closure, and
    normalizes the already-authoritative facts into the frozen family-local
    render input. The renderer never sees a payload object, and missing facts
    for one family never block another family whose own closure is complete.
    """
    if (
        unit.disposition is not PlanDisposition.RENDER
        or unit.family_kind not in APP_CONFIG_FAMILIES
    ):
        raise AppConfigMaterializationError(
            f"unit {unit.unit_id!r} ({unit.family_kind!r}) is not an active "
            "application-configuration render unit"
        )
    resolved: dict[str, SemanticPayloadBase] = {}
    for source in unit.sources:
        pinned = payload_by_node.get(source.node_id)
        if pinned is None or pinned.payload_digest != source.payload_digest:
            raise AppConfigMaterializationError(
                f"unit {unit.unit_id!r} source {source.node_id!r} is missing "
                "or does not match its pinned payload digest"
            )
        resolved[source.node_id] = pinned
    sources = tuple(
        RenderInputSource(node_id=node_id, payload_digest=payload.payload_digest)
        for node_id, payload in resolved.items()
    )

    def _one_application() -> ApplicationPayload:
        applications = [
            payload
            for payload in resolved.values()
            if isinstance(payload, ApplicationPayload)
        ]
        if len(applications) != 1:
            raise AppConfigMaterializationError(
                f"unit {unit.unit_id!r} footprint must pin exactly one "
                "ApplicationPayload"
            )
        return applications[0]

    def _family_selected(
        application: ApplicationPayload, family: OptionalFamilyKind
    ) -> bool:
        for selection in application.optional_families:
            if selection.family is family:
                return selection.status is OptionalFamilySelectionStatus.SELECTED
        raise AppConfigMaterializationError(
            f"application selection evidence does not state the {family.value} family"
        )

    if unit.family_kind == "app_manifest":
        application = _one_application()
        auths = [
            payload for payload in resolved.values() if isinstance(payload, AuthPayload)
        ]
        if len(auths) > 1:
            raise AppConfigMaterializationError(
                "app-manifest projection pins more than one AuthPayload"
            )
        auth = auths[0] if auths else None
        auth_selected = _family_selected(application, OptionalFamilyKind.AUTH)
        if auth is not None:
            if not auth_selected:
                raise AppConfigMaterializationError(
                    "an AuthPayload is pinned while the application declares "
                    "the auth family absent; contradictory evidence cannot render"
                )
            auth_required = bool(auth.auth_required)
        else:
            if auth_selected:
                raise AppConfigMaterializationError(
                    "auth is selected but no AuthPayload is pinned in the footprint"
                )
            auth_required = False
        return AppManifestRenderInput(
            render_input_schema_version=APP_FAMILY_RENDER_INPUT_VERSION,
            application_id=application.application_id,
            display_name=application.display_name,
            version=application.version,
            description=application.description,
            default_route=application.default_route,
            auth_required=auth_required,
            sources=sources,
        )

    if unit.family_kind == "app_ui_route_manifest":
        application = _one_application()
        pages: list[RenderInputPage] = []
        for payload in resolved.values():
            if isinstance(payload, PagePayload):
                if not payload.route or not payload.title:
                    raise AppConfigMaterializationError(
                        f"page {payload.node_id!r} lacks the route/title facts "
                        "the route manifest requires"
                    )
                pages.append(
                    RenderInputPage(
                        page_id=payload.page_id,
                        route=payload.route,
                        title=payload.title,
                    )
                )
        return RouteManifestRenderInput(
            render_input_schema_version=APP_FAMILY_RENDER_INPUT_VERSION,
            default_route=application.default_route,
            pages=tuple(pages),
            sources=sources,
        )

    if unit.family_kind == "app_integrations_config":
        application = _one_application()
        integration_payloads = [
            payload
            for payload in resolved.values()
            if isinstance(payload, IntegrationPayload)
        ]
        if _family_selected(application, OptionalFamilyKind.INTEGRATIONS):
            if not integration_payloads:
                raise AppConfigMaterializationError(
                    "integrations are selected but no IntegrationPayload is pinned"
                )
        elif integration_payloads:
            raise AppConfigMaterializationError(
                "IntegrationPayloads are pinned while the application declares "
                "the integrations family absent; contradictory evidence cannot "
                "render"
            )
        return IntegrationsConfigRenderInput(
            render_input_schema_version=APP_FAMILY_RENDER_INPUT_VERSION,
            integrations=tuple(
                RenderInputIntegration(
                    integration_id=payload.integration_id,
                    kind=payload.integration_kind.value,
                    purpose=payload.purpose,
                    required_at=payload.required_at.value,
                    optional=payload.optional,
                    config_requirements=tuple(
                        RenderInputConfigRequirement(
                            name=requirement.name,
                            value_type=_CONFIG_VALUE_TYPES[requirement.value_kind],
                            required=requirement.required,
                        )
                        for requirement in payload.config_requirements
                    ),
                )
                for payload in integration_payloads
            ),
            sources=sources,
        )

    if unit.family_kind == "app_secret_references":
        application = _one_application()
        integration_payloads = [
            payload
            for payload in resolved.values()
            if isinstance(payload, IntegrationPayload)
        ]
        if (
            _family_selected(application, OptionalFamilyKind.INTEGRATIONS)
            and not integration_payloads
        ):
            raise AppConfigMaterializationError(
                "integrations are selected but no IntegrationPayload is pinned; "
                "the names-only secret surface cannot render empty output"
            )
        names = {
            requirement.name
            for payload in integration_payloads
            for requirement in payload.config_requirements
            if requirement.value_kind is IntegrationConfigValueKind.SECRET
        }
        return SecretReferencesRenderInput(
            render_input_schema_version=APP_FAMILY_RENDER_INPUT_VERSION,
            secret_names=tuple(sorted(names)),
            sources=sources,
        )

    raise AppConfigMaterializationError(
        f"unit {unit.unit_id!r} family {unit.family_kind!r} has no family-local "
        "render-input projection in this slice"
    )


# ---------------------------------------------------------------------------
# Canonical page bytes
# ---------------------------------------------------------------------------


def _canonical_yaml_bytes(document: Mapping[str, Any]) -> bytes:
    text: str = yaml.safe_dump(
        dict(document),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=2_000_000_000,
    )
    if not text.endswith("\n"):
        text += "\n"
    return text.replace("\r\n", "\n").encode("utf-8")


def render_app_ui_page_schema_unit(
    *,
    unit: FamilyInstancePlan,
    payload_by_node: Mapping[str, SemanticPayloadBase],
) -> bytes:
    """Render one page unit's canonical bytes from its footprint alone.

    The renderer reads only payloads pinned by the unit's declared source
    footprint; a page whose section list references any node outside that
    footprint fails closed instead of reaching for wider state.
    """
    validate_page_renderer_input_closure()
    if unit.family_kind != PAGE_SCHEMA_FAMILY:
        raise MaterializationError(
            f"unit {unit.unit_id!r} family {unit.family_kind!r} is not renderer-ready "
            f"in this slice; only {PAGE_SCHEMA_FAMILY!r} renders"
        )
    if unit.disposition is not PlanDisposition.RENDER:
        raise MaterializationError(
            f"unit {unit.unit_id!r} disposition {unit.disposition.value!r} is not render"
        )
    footprint = {source.node_id for source in unit.sources}
    pages: list[PagePayload] = []
    for node_id in sorted(footprint):
        candidate = payload_by_node.get(node_id)
        if isinstance(candidate, PagePayload):
            pages.append(candidate)
    if len(pages) != 1:
        raise MaterializationError(
            f"unit {unit.unit_id!r} footprint must pin exactly one page payload, "
            f"found {len(pages)}"
        )
    page = pages[0]
    for source in unit.sources:
        pinned = payload_by_node.get(source.node_id)
        if pinned is None or pinned.payload_digest != source.payload_digest:
            raise MaterializationError(
                f"unit {unit.unit_id!r} source {source.node_id!r} is missing or does "
                "not match its pinned payload digest"
            )
    if unit.placeholder_values != (("page_id", page.page_id),):
        raise MaterializationError(
            f"unit {unit.unit_id!r} instance identity {unit.placeholder_values!r} does "
            f"not match page canonical id {page.page_id!r}"
        )
    route = page.route
    title = page.title
    page_type = page.page_type
    layout = page.layout
    if (
        route is None
        or title is None
        or page_type is None
        or layout is None
        or not page.sections
    ):
        missing = sorted(
            name
            for name, value in (
                ("route", route),
                ("title", title),
                ("page_type", page_type),
                ("layout", layout),
            )
            if value is None
        )
        raise MaterializationError(
            f"page {page.node_id!r} is not renderer-input complete; missing "
            f"{missing or ['sections']}"
        )
    declaratives: list[AppPageSection] = []
    for entry in page.sections:
        if entry.section_node_id not in footprint:
            raise MaterializationError(
                f"page {page.node_id!r} references section {entry.section_node_id!r} "
                "outside the unit's declared source footprint"
            )
        section = payload_by_node[entry.section_node_id]
        if not isinstance(section, SectionPayload) or section.declarative is None:
            raise MaterializationError(
                f"section {entry.section_node_id!r} carries no normative declarative; "
                "the page is not renderer-input complete"
            )
        declaratives.append(section.declarative)
    schema = AppPageSchema(
        schema_version="mozaiks.app_page.v1",
        name=page.page_id,
        route=route,
        title=title,
        page_type=page_type,
        layout=layout,
        shell_mode=page.shell_mode,
        roles=None if page.roles is None else list(page.roles),
        navigation=page.navigation,
        meta=page.meta,
        sections=list(declaratives),
    )
    rendered = _canonical_yaml_bytes(schema.model_dump(mode="json", exclude_none=True))
    reparsed = AppPageSchema.model_validate(yaml.safe_load(rendered.decode("utf-8")))
    if reparsed != schema:
        raise MaterializationError(
            f"canonical page bytes for {page.page_id!r} failed round-trip verification"
        )
    return rendered


# ---------------------------------------------------------------------------
# Bundle materialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterializedOutput:
    """One physical output produced under plan authority."""

    unit_id: str
    path_scope: str
    path: str
    content: bytes
    origin: Literal["rendered", "preserved", "reused"]
    content_digest: str


@dataclass(frozen=True)
class MaterializedBundle:
    """Result of materializing one CompilationPlan. Not a wire contract."""

    plan_digest: str
    outputs: tuple[MaterializedOutput, ...]
    external_handoff_units: tuple[str, ...]
    inapplicable_units: tuple[str, ...]
    unsupplied_preserved_units: tuple[str, ...]
    input_only_units: tuple[str, ...]
    instance_scope_deferred_units: tuple[str, ...]
    gap_count: int
    closure: RegenerationClosure | None = field(default=None)

    def files(self) -> dict[str, bytes]:
        return {output.path: output.content for output in self.outputs}


def _cold_validate(plan: CompilationPlan, graph: SemanticGraphV2, payloads: Iterable[SemanticPayloadBase]):
    try:
        verified_plan = CompilationPlan.model_validate(plan.model_dump(mode="json"))
        verified_graph = SemanticGraphV2.model_validate(graph.model_dump(mode="json"))
        verified_payloads = [
            parse_semantic_payload(payload.model_dump(mode="json")) for payload in payloads
        ]
    except ValueError as exc:
        raise MaterializationError(f"materialization inputs failed cold validation: {exc}") from exc
    if verified_plan.graph_digest != verified_graph.graph_digest:
        raise MaterializationError("plan does not pin the supplied graph identity")
    return verified_plan, verified_graph, {p.node_id: p for p in verified_payloads}


def _assert_registry_identity(plan: CompilationPlan, layout_registry: Any) -> None:
    """The plan must pin the identity of the sole registry actually supplied.

    A pre-built snapshot is cold-revalidated, so a forged or tampered
    snapshot identity fails here rather than being trusted.
    """
    try:
        snapshot = (
            LayoutRegistrySnapshot.model_validate(layout_registry.model_dump(mode="json"))
            if isinstance(layout_registry, LayoutRegistrySnapshot)
            else snapshot_layout_registry(layout_registry)
        )
    except ValueError as exc:
        raise MaterializationError(f"layout registry snapshot rejected: {exc}") from exc
    if snapshot.snapshot_digest != plan.registry_digest:
        raise MaterializationError(
            "layout registry snapshot does not match the plan's pinned registry identity"
        )


def _match_preserved(
    plan: CompilationPlan,
    artifacts: Iterable[PreservedOpaqueArtifact],
) -> dict[str, PreservedOpaqueArtifact]:
    by_unit: dict[str, PreservedOpaqueArtifact] = {}
    for artifact in artifacts:
        ref = artifact.contract_ref
        matches = [
            unit
            for unit in plan.units
            if unit.disposition is PlanDisposition.PRESERVE_UNOWNED
            and unit.family_kind == ref.artifact_family
            and any(
                output.path == ref.canonical_relative_path
                and output.path_scope in _COMPOSABLE_PATH_SCOPES
                for output in unit.outputs
            )
        ]
        if len(matches) != 1:
            raise MaterializationError(
                f"preserved artifact {ref.artifact_family!r}:{ref.canonical_relative_path!r} "
                f"matches {len(matches)} plan units; exactly one required"
            )
        unit = matches[0]
        if unit.unit_id in by_unit:
            raise MaterializationError(
                f"unit {unit.unit_id!r} received more than one preserved artifact"
            )
        by_unit[unit.unit_id] = artifact
    return by_unit


def _assert_app_config_output_closure(
    plan: CompilationPlan,
    outputs: Iterable[MaterializedOutput],
    selection: RendererSelection | None,
) -> None:
    """Bijection gate for the application-configuration output set.

    Every emitted app-config output must come from an active, authorized
    RENDER unit of the validated plan at that unit's plan-owned path; every
    active authorized unit must have produced exactly one output; and no
    output may exist for an inactive or unauthorized family. Inactive
    conditional units are simply absent — never an error, never bytes.
    """
    unit_by_id = {unit.unit_id: unit for unit in plan.units}
    active_expected = {
        unit.unit_id: unit
        for unit in plan.units
        if unit.disposition is PlanDisposition.RENDER
        and unit.family_kind in APP_CONFIG_FAMILIES
        and any(o.path_scope in _COMPOSABLE_PATH_SCOPES for o in unit.outputs)
    }
    emitted_counts: dict[str, int] = {}
    for output in outputs:
        unit = unit_by_id.get(output.unit_id)
        if unit is None or unit.family_kind not in APP_CONFIG_FAMILIES:
            continue
        if selection is None or unit.family_kind not in selection.artifact_families:
            raise AppConfigMaterializationError(
                f"output {output.path!r} was emitted for unauthorized "
                f"application-config family {unit.family_kind!r}"
            )
        if unit.unit_id not in active_expected:
            raise AppConfigMaterializationError(
                f"output {output.path!r} was emitted for inactive "
                f"application-config unit {output.unit_id!r}"
            )
        owned_paths = {
            o.path for o in unit.outputs if o.path_scope in _COMPOSABLE_PATH_SCOPES
        }
        if output.path not in owned_paths:
            raise AppConfigMaterializationError(
                f"output {output.path!r} does not equal the plan-owned path of "
                f"unit {output.unit_id!r}"
            )
        emitted_counts[output.unit_id] = emitted_counts.get(output.unit_id, 0) + 1
    for unit_id, unit in active_expected.items():
        if emitted_counts.get(unit_id, 0) != 1:
            raise AppConfigMaterializationError(
                f"active application-config unit {unit_id!r} "
                f"({unit.family_kind!r}) must produce exactly one output; "
                f"produced {emitted_counts.get(unit_id, 0)}"
            )


def _materialize_unit(
    unit: FamilyInstancePlan,
    *,
    payload_by_node: Mapping[str, SemanticPayloadBase],
    app_config_selection: RendererSelection | None,
    workflow_interface_selection: RendererSelection | None,
    preserved_by_unit: Mapping[str, PreservedOpaqueArtifact],
    bundle_outputs: list[MaterializedOutput],
    external: list[str],
    inapplicable: list[str],
    unsupplied: list[str],
    input_only: list[str],
    deferred: list[str],
) -> None:
    composable = [o for o in unit.outputs if o.path_scope in _COMPOSABLE_PATH_SCOPES]
    non_composable = [o for o in unit.outputs if o.path_scope not in _COMPOSABLE_PATH_SCOPES]
    if non_composable:
        deferred.append(unit.unit_id)
    if unit.disposition is PlanDisposition.RENDER:
        if not composable:
            return
        if len(composable) != 1:
            raise MaterializationError(
                f"render unit {unit.unit_id!r} must own exactly one composable output"
            )
        if unit.family_kind == WORKFLOW_MODULE_INTERFACE:
            if workflow_interface_selection is None:
                raise WorkflowInterfaceMaterializationError("interface renderer selection was not resolved")
            content = render_workflow_module_interface_unit(
                unit=unit,
                render_input=project_workflow_interface_render_input(
                    unit=unit, payload_by_node=payload_by_node,
                ),
            )
        elif unit.family_kind in APP_CONFIG_FAMILIES:
            if app_config_selection is None:
                raise MaterializationError(
                    f"render unit {unit.unit_id!r} requires the resolved "
                    "app-config renderer selection, which was not constructed"
                )
            # A renderer selection cannot authorize a family by implication:
            # the unit's family must be explicitly named by the resolved
            # selection even though resolution already pinned the exact set.
            if unit.family_kind not in app_config_selection.artifact_families:
                raise AppConfigMaterializationError(
                    f"unit {unit.unit_id!r} family {unit.family_kind!r} is not "
                    "authorized by the resolved app-config renderer selection"
                )
            # Lazy family-local projection: only this unit's plan-pinned
            # sources feed its closed input, so a typed gap in one family
            # (e.g. app_config) never blocks another family's rendering.
            content = render_app_config_unit(
                unit=unit,
                render_input=project_app_family_render_input(
                    unit=unit, payload_by_node=payload_by_node
                ),
            )
        else:
            content = render_app_ui_page_schema_unit(unit=unit, payload_by_node=payload_by_node)
        target = composable[0]
        bundle_outputs.append(
            MaterializedOutput(
                unit_id=unit.unit_id,
                path_scope=target.path_scope,
                path=target.path,
                content=content,
                origin="rendered",
                content_digest=hashlib.sha256(content).hexdigest(),
            )
        )
    elif unit.disposition is PlanDisposition.PRESERVE_UNOWNED:
        artifact = preserved_by_unit.get(unit.unit_id)
        if artifact is None:
            if composable:
                unsupplied.append(unit.unit_id)
            return
        target = next(
            output
            for output in composable
            if output.path == artifact.contract_ref.canonical_relative_path
        )
        bundle_outputs.append(
            MaterializedOutput(
                unit_id=unit.unit_id,
                path_scope=target.path_scope,
                path=target.path,
                content=artifact.content,
                origin="preserved",
                content_digest=artifact.contract_ref.content_digest,
            )
        )
    elif unit.disposition is PlanDisposition.EXTERNAL_HANDOFF:
        external.append(unit.unit_id)
    elif unit.disposition is PlanDisposition.INAPPLICABLE:
        inapplicable.append(unit.unit_id)
    elif unit.disposition is PlanDisposition.INPUT_ONLY:
        # Input-only families feed later derivations; they never produce
        # bundle bytes and are reported explicitly, never silently skipped.
        input_only.append(unit.unit_id)
    else:
        raise MaterializationError(
            f"unit {unit.unit_id!r} disposition {unit.disposition.value!r} has no "
            "materialization path in this slice"
        )


def materialize_plan(
    *,
    plan: CompilationPlan,
    authority_inputs: CompilationPlanAuthorityInputs,
    graph: SemanticGraphV2,
    payloads: Iterable[SemanticPayloadBase],
    binding: ImplementationBinding,
    layout_registry: Any,
    preserved_artifacts: Iterable[PreservedOpaqueArtifact] = (),
) -> MaterializedBundle:
    """Materialize every plan-authorized output for one CompilationPlan.

    Renders exactly the renderer-ready units through their bound accepted
    implementation, places exact preserved bytes for supplied
    ``preserve_unowned`` units, and reports every other unit's disposition
    explicitly. Canonical authority inputs are required and the submitted plan
    must equal the plan re-derived from them. The registry snapshot identity
    must also match the plan's pinned registry digest.
    """
    try:
        canonical_plan = validate_compilation_plan_against_authority(plan, authority_inputs)
    except PlanAuthorityError as exc:
        raise MaterializationError(
            "compilation plan rejected by canonical authority validation"
        ) from exc
    verified_plan, verified_graph, payload_by_node = _cold_validate(
        canonical_plan, graph, payloads
    )
    _assert_registry_identity(verified_plan, layout_registry)
    resolve_page_schema_renderer_selection(
        binding, graph=verified_graph, layout_registry=layout_registry
    )
    app_config_selection: RendererSelection | None = None
    if any(
        unit.disposition is PlanDisposition.RENDER
        and unit.family_kind in APP_CONFIG_FAMILIES
        for unit in verified_plan.units
    ):
        app_config_selection = resolve_app_config_renderer_selection(
            binding, graph=verified_graph, layout_registry=layout_registry
        )
    workflow_interface_selection: RendererSelection | None = None
    if any(
        unit.disposition is PlanDisposition.RENDER
        and unit.family_kind == WORKFLOW_MODULE_INTERFACE
        and any(output.path_scope in _COMPOSABLE_PATH_SCOPES for output in unit.outputs)
        for unit in verified_plan.units
    ):
        workflow_interface_selection = resolve_workflow_interface_renderer_selection(
            binding, graph=verified_graph, layout_registry=layout_registry,
        )
    preserved_by_unit = _match_preserved(verified_plan, preserved_artifacts)

    outputs: list[MaterializedOutput] = []
    external: list[str] = []
    inapplicable: list[str] = []
    unsupplied: list[str] = []
    input_only: list[str] = []
    deferred: list[str] = []
    for unit in verified_plan.units:
        _materialize_unit(
            unit,
            payload_by_node=payload_by_node,
            app_config_selection=app_config_selection,
            workflow_interface_selection=workflow_interface_selection,
            preserved_by_unit=preserved_by_unit,
            bundle_outputs=outputs,
            external=external,
            inapplicable=inapplicable,
            unsupplied=unsupplied,
            input_only=input_only,
            deferred=deferred,
        )
    _assert_output_ownership(verified_plan, outputs)
    _assert_app_config_output_closure(verified_plan, outputs, app_config_selection)
    return MaterializedBundle(
        plan_digest=verified_plan.plan_digest,
        outputs=tuple(sorted(outputs, key=lambda o: o.path)),
        external_handoff_units=tuple(sorted(external)),
        inapplicable_units=tuple(sorted(inapplicable)),
        unsupplied_preserved_units=tuple(sorted(unsupplied)),
        input_only_units=tuple(sorted(input_only)),
        instance_scope_deferred_units=tuple(sorted(set(deferred))),
        gap_count=len(verified_plan.gaps),
    )


def _assert_output_ownership(plan: CompilationPlan, outputs: list[MaterializedOutput]) -> None:
    """Every produced path must be the plan-assigned path of its own unit."""
    assigned: dict[tuple[str, str], str] = {}
    for unit in plan.units:
        for output in unit.outputs:
            assigned[(output.path_scope, output.path)] = unit.unit_id
    seen: set[str] = set()
    for produced in outputs:
        owner = assigned.get((produced.path_scope, produced.path))
        if owner != produced.unit_id:
            raise MaterializationError(
                f"output {produced.path!r} is not assigned to unit {produced.unit_id!r} "
                "by the compilation plan"
            )
        if produced.path in seen:
            raise MaterializationError(f"duplicate materialized output {produced.path!r}")
        seen.add(produced.path)
    detect_collisions(sorted(seen))


def rematerialize_plan(
    *,
    base_bundle: MaterializedBundle,
    base_plan: CompilationPlan,
    base_authority_inputs: CompilationPlanAuthorityInputs,
    successor_plan: CompilationPlan,
    successor_authority_inputs: CompilationPlanAuthorityInputs,
    graph: SemanticGraphV2,
    payloads: Iterable[SemanticPayloadBase],
    binding: ImplementationBinding,
    layout_registry: Any,
    preserved_artifacts: Iterable[PreservedOpaqueArtifact] = (),
) -> MaterializedBundle:
    """Selectively rematerialize a successor plan against a base bundle.

    Affected and added units are produced fresh; reusable units are copied
    byte-for-byte from the base bundle (preserved reuse re-verifies the
    pinned content digest); removed units' outputs are absent. The closure is
    computed by the 4B authority and attached to the result for inspection.
    Both plans must first equal the plans re-derived from their respective
    canonical authority inputs, before any historical bytes can be reused.
    """
    try:
        canonical_base_plan = validate_compilation_plan_against_authority(
            base_plan, base_authority_inputs
        )
        canonical_successor_plan = validate_compilation_plan_against_authority(
            successor_plan, successor_authority_inputs
        )
    except PlanAuthorityError as exc:
        raise MaterializationError(
            "compilation plan rejected by canonical authority validation"
        ) from exc
    if base_bundle.plan_digest != canonical_base_plan.plan_digest:
        raise MaterializationError("base bundle does not correspond to the base plan")
    closure = plan_regeneration_closure(canonical_base_plan, canonical_successor_plan)
    reusable_ids = set(closure.reusable)

    verified_plan, verified_graph, payload_by_node = _cold_validate(
        canonical_successor_plan, graph, payloads
    )
    _assert_registry_identity(verified_plan, layout_registry)
    resolve_page_schema_renderer_selection(
        binding, graph=verified_graph, layout_registry=layout_registry
    )
    app_config_selection: RendererSelection | None = None
    if any(
        unit.disposition is PlanDisposition.RENDER
        and unit.family_kind in APP_CONFIG_FAMILIES
        for unit in verified_plan.units
    ):
        app_config_selection = resolve_app_config_renderer_selection(
            binding, graph=verified_graph, layout_registry=layout_registry
        )
    workflow_interface_selection: RendererSelection | None = None
    if any(
        unit.disposition is PlanDisposition.RENDER
        and unit.family_kind == WORKFLOW_MODULE_INTERFACE
        and any(output.path_scope in _COMPOSABLE_PATH_SCOPES for output in unit.outputs)
        for unit in verified_plan.units
    ):
        workflow_interface_selection = resolve_workflow_interface_renderer_selection(
            binding, graph=verified_graph, layout_registry=layout_registry,
        )
    preserved_by_unit = _match_preserved(verified_plan, preserved_artifacts)
    base_by_unit: dict[str, list[MaterializedOutput]] = {}
    for output in base_bundle.outputs:
        base_by_unit.setdefault(output.unit_id, []).append(output)

    outputs: list[MaterializedOutput] = []
    external: list[str] = []
    inapplicable: list[str] = []
    unsupplied: list[str] = []
    input_only: list[str] = []
    deferred: list[str] = []
    for unit in verified_plan.units:
        if unit.unit_id in reusable_ids:
            reused = base_by_unit.get(unit.unit_id, [])
            expected_paths = {
                (o.path_scope, o.path)
                for o in unit.outputs
                if o.path_scope in _COMPOSABLE_PATH_SCOPES
            }
            if unit.disposition in (PlanDisposition.RENDER, PlanDisposition.PRESERVE_UNOWNED):
                produced_paths = {(o.path_scope, o.path) for o in reused}
                if expected_paths and produced_paths != expected_paths:
                    if unit.disposition is PlanDisposition.PRESERVE_UNOWNED and not reused:
                        unsupplied.append(unit.unit_id)
                        continue
                    raise MaterializationError(
                        f"reusable unit {unit.unit_id!r} has no matching base outputs "
                        "to reuse byte-for-byte"
                    )
                for prior in reused:
                    if hashlib.sha256(prior.content).hexdigest() != prior.content_digest:
                        raise MaterializationError(
                            f"base output {prior.path!r} failed digest re-verification"
                        )
                    outputs.append(
                        MaterializedOutput(
                            unit_id=prior.unit_id,
                            path_scope=prior.path_scope,
                            path=prior.path,
                            content=prior.content,
                            origin="reused",
                            content_digest=prior.content_digest,
                        )
                    )
            elif unit.disposition is PlanDisposition.EXTERNAL_HANDOFF:
                external.append(unit.unit_id)
            elif unit.disposition is PlanDisposition.INAPPLICABLE:
                inapplicable.append(unit.unit_id)
            elif unit.disposition is PlanDisposition.INPUT_ONLY:
                input_only.append(unit.unit_id)
            if any(o.path_scope not in _COMPOSABLE_PATH_SCOPES for o in unit.outputs):
                deferred.append(unit.unit_id)
            continue
        _materialize_unit(
            unit,
            payload_by_node=payload_by_node,
            app_config_selection=app_config_selection,
            workflow_interface_selection=workflow_interface_selection,
            preserved_by_unit=preserved_by_unit,
            bundle_outputs=outputs,
            external=external,
            inapplicable=inapplicable,
            unsupplied=unsupplied,
            input_only=input_only,
            deferred=deferred,
        )
    _assert_output_ownership(verified_plan, outputs)
    _assert_app_config_output_closure(verified_plan, outputs, app_config_selection)
    return MaterializedBundle(
        plan_digest=verified_plan.plan_digest,
        outputs=tuple(sorted(outputs, key=lambda o: o.path)),
        external_handoff_units=tuple(sorted(external)),
        inapplicable_units=tuple(sorted(inapplicable)),
        unsupplied_preserved_units=tuple(sorted(unsupplied)),
        input_only_units=tuple(sorted(input_only)),
        instance_scope_deferred_units=tuple(sorted(set(deferred))),
        gap_count=len(verified_plan.gaps),
        closure=closure,
    )


def compose_bundle(
    bundle: MaterializedBundle,
    authored_files: Mapping[str, str | bytes],
) -> dict[str, bytes]:
    """Compose plan-owned outputs with authored files for gapped families.

    Authored files represent families whose legitimate disposition is a typed
    gap or authored content in this slice. They may never overlap — or
    collide under case-fold/prefix rules — with any plan-owned output path.
    """
    plan_owned = bundle.files()
    authored: dict[str, bytes] = {
        path: (content.encode("utf-8") if isinstance(content, str) else bytes(content))
        for path, content in authored_files.items()
    }
    overlap = sorted(set(plan_owned) & set(authored))
    if overlap:
        raise MaterializationError(
            f"authored files overlap plan-owned outputs: {overlap}"
        )
    detect_collisions(sorted(set(plan_owned) | set(authored)))
    combined = dict(authored)
    combined.update(plan_owned)
    return combined


__all__ = [
    "PAGE_BYTES_SCHEMA_VERSION",
    "PAGE_SCHEMA_FAMILY",
    "PAGE_SCHEMA_RENDERER_IMPLEMENTATION_ID",
    "PAGE_SCHEMA_RENDERER_IMPLEMENTATION_VERSION",
    "MaterializationError",
    "MaterializedBundle",
    "MaterializedOutput",
    "compose_bundle",
    "materialize_plan",
    "rematerialize_plan",
    "render_app_ui_page_schema_unit",
    "resolve_page_schema_renderer_selection",
    "validate_page_renderer_input_closure",
]
