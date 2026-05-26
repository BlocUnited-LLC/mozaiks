"""Map ExistingAppDiscovery output into canonical app-context contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mozaiksai.core.app_context.models import (
    AdoptionPath,
    AdoptionPhase,
    AdoptionPlan,
    AllowedOperation,
    AppContextGraph,
    AppContextGraphEdge,
    AppContextGraphNode,
    AppContextStaleStatus,
    ApplicationInventory,
    BrownfieldRegistration,
    BrownfieldRegistrationStatus,
    GraphEdgeType,
    GraphNodeType,
    IntegrationInventory,
    IntegrationReadinessStatus,
    OwnershipBoundary,
    OwnershipClass,
    RiskItem,
    RiskReport,
    SourceRef,
    SourceRefKind,
    SurfaceRef,
    UnknownItem,
)

APP_CONTEXT_ARTIFACT_KINDS = (
    "application_inventory",
    "ownership_boundary",
    "integration_inventory",
    "risk_report",
    "adoption_plan",
    "brownfield_registration",
    "app_context_graph",
)


@dataclass(frozen=True)
class ExistingAppContextArtifactDrafts:
    """Derived canonical app-context drafts from one discovery artifact."""

    app_id: str
    application_inventory: ApplicationInventory
    ownership_boundaries: list[OwnershipBoundary]
    integration_inventory: list[IntegrationInventory]
    risk_report: RiskReport
    adoption_plan: AdoptionPlan
    brownfield_registration: BrownfieldRegistration
    app_context_graph: AppContextGraph | None

    def as_artifact_payloads(self) -> dict[str, Any]:
        payloads: dict[str, Any] = {
            "application_inventory": self.application_inventory.model_dump(mode="json"),
            "ownership_boundary": {
                "app_id": self.app_id,
                "ownership_boundaries": [
                    boundary.model_dump(mode="json") for boundary in self.ownership_boundaries
                ],
            },
            "integration_inventory": {
                "app_id": self.app_id,
                "integrations": [
                    integration.model_dump(mode="json") for integration in self.integration_inventory
                ],
            },
            "risk_report": self.risk_report.model_dump(mode="json"),
            "adoption_plan": self.adoption_plan.model_dump(mode="json"),
            "brownfield_registration": self.brownfield_registration.model_dump(mode="json"),
        }
        if self.app_context_graph is not None:
            payloads["app_context_graph"] = self.app_context_graph.model_dump(mode="json")
        return payloads


def build_existing_app_context_artifacts(
    discovery_artifact: Mapping[str, Any],
    context_variables: Mapping[str, Any] | Any | None = None,
) -> ExistingAppContextArtifactDrafts:
    """Conservatively derive canonical brownfield app-context contracts."""
    product_spec = _as_mapping(discovery_artifact.get("existing_product_spec"))
    capability_specs = _as_list(discovery_artifact.get("capability_specs"))
    augmentation_plan = _as_mapping(discovery_artifact.get("agent_augmentation_plan"))
    decomposition_plan = _parse_decomposition_plan(
        _context_get(context_variables, "module_decomposition_plan")
    )
    indexed_at = datetime.now(UTC)

    app_id = _app_id(product_spec, discovery_artifact, context_variables)
    source_refs = _source_refs(product_spec, discovery_artifact, context_variables, app_id, indexed_at)
    default_source_ref = source_refs[0].source_ref_id if source_refs else None

    integrations = _integration_inventory(product_spec)
    unknowns = _unknowns(discovery_artifact)
    inventory = ApplicationInventory(
        app_id=app_id,
        source_refs=source_refs,
        stacks=_split_stack(product_spec.get("tech_stack")),
        services=_service_surfaces(product_spec, default_source_ref),
        routes=_route_surfaces(product_spec, default_source_ref),
        api_endpoints=_api_endpoints(product_spec, context_variables, default_source_ref),
        pages=_pages(product_spec, default_source_ref),
        components=[],
        data_entities=_data_entities(product_spec, default_source_ref),
        integrations=integrations,
        deployment_boundaries=_deployment_boundaries(product_spec, default_source_ref),
        ci_boundaries=_ci_boundaries(context_variables, default_source_ref),
        unknowns=unknowns,
    )

    ownership_boundaries = _ownership_boundaries(
        product_spec=product_spec,
        capability_specs=capability_specs,
        augmentation_plan=augmentation_plan,
        decomposition_plan=decomposition_plan,
        default_source_ref=default_source_ref,
    )
    risk_report = _risk_report(
        product_spec=product_spec,
        capability_specs=capability_specs,
        augmentation_plan=augmentation_plan,
        context_variables=context_variables,
        unknowns=unknowns,
    )
    adoption_plan = _adoption_plan(
        augmentation_plan=augmentation_plan,
        capability_specs=capability_specs,
        unknowns=unknowns,
        context_variables=context_variables,
    )
    registration = BrownfieldRegistration(
        registration_id=_stable_id("brownfield_registration", app_id, _context_get(context_variables, "chat_id")),
        app_id=app_id,
        source_refs=source_refs,
        context_version_id=_stable_id("app_context", app_id, _context_get(context_variables, "chat_id")),
        scan_policy_ref=_scan_policy_ref(context_variables),
        status=BrownfieldRegistrationStatus.PENDING,
        created_at=indexed_at,
    )
    graph = _app_context_graph(
        app_id=app_id,
        source_refs=source_refs,
        inventory=inventory,
        capability_specs=capability_specs,
        risk_report=risk_report,
        ownership_boundaries=ownership_boundaries,
        decomposition_plan=decomposition_plan,
        chat_id=_context_get(context_variables, "chat_id"),
        indexed_at=indexed_at,
    )

    return ExistingAppContextArtifactDrafts(
        app_id=app_id,
        application_inventory=inventory,
        ownership_boundaries=ownership_boundaries,
        integration_inventory=integrations,
        risk_report=risk_report,
        adoption_plan=adoption_plan,
        brownfield_registration=registration,
        app_context_graph=graph,
    )


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _context_get(context_variables: Mapping[str, Any] | Any | None, key: str) -> Any:
    if context_variables is None:
        return None
    try:
        if hasattr(context_variables, "get"):
            return context_variables.get(key)
    except Exception:
        return None
    return None


def _slug(value: Any, *, fallback: str = "item") -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "_", _clean(value).lower()).strip("_")
    return text or fallback


def _stable_id(prefix: str, *parts: Any) -> str:
    clean_parts = [_slug(part, fallback="") for part in parts if _clean(part)]
    if clean_parts:
        return "_".join([_slug(prefix), *clean_parts])[:120]
    return _slug(prefix)


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        clean = _clean(value)
        if clean and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def _split_stack(value: Any) -> list[str]:
    raw = _clean(value)
    if not raw:
        return []
    return _unique_strings([part.strip() for part in re.split(r"[,;/|]+", raw) if part.strip()])


def _app_id(
    product_spec: Mapping[str, Any],
    discovery_artifact: Mapping[str, Any],
    context_variables: Mapping[str, Any] | Any | None,
) -> str:
    return _slug(
        _context_get(context_variables, "app_id")
        or discovery_artifact.get("app_id")
        or product_spec.get("app_id")
        or product_spec.get("app_name")
        or _context_get(context_variables, "app_name")
        or _context_get(context_variables, "chat_id")
        or "existing_app",
        fallback="existing_app",
    )


def _source_refs(
    product_spec: Mapping[str, Any],
    discovery_artifact: Mapping[str, Any],
    context_variables: Mapping[str, Any] | Any | None,
    app_id: str,
    indexed_at: datetime,
) -> list[SourceRef]:
    chat_id = _clean(_context_get(context_variables, "chat_id"))
    refs: list[SourceRef] = [
        SourceRef(
            source_ref_id="src_discovery_snapshot",
            kind=SourceRefKind.DISCOVERY_SNAPSHOT,
            uri=f"existing-app-discovery://{chat_id or app_id}",
            ref=_clean(discovery_artifact.get("artifact_version")) or None,
            indexed_at=indexed_at,
            metadata={"source_workflow": "ExistingAppDiscovery"},
        )
    ]

    for key in ("repo_summary", "frontend_repo_summary", "backend_repo_summary"):
        summary = _as_mapping(_context_get(context_variables, key))
        uri = _clean(
            summary.get("repo_url")
            or summary.get("repo_path")
            or summary.get("path")
            or summary.get("repo_name")
        )
        if uri:
            refs.append(
                SourceRef(
                    source_ref_id=_stable_id("src", key),
                    kind=SourceRefKind.REPO,
                    uri=uri,
                    ref=_clean(summary.get("git_ref") or summary.get("branch") or summary.get("commit"))
                    or None,
                    checksum=_clean(summary.get("checksum") or summary.get("content_hash")) or None,
                    indexed_at=indexed_at,
                    metadata={
                        "summary_key": key,
                        "source": _clean(summary.get("source")) or None,
                    },
                )
            )

    app_url = _clean(product_spec.get("app_url") or _context_get(context_variables, "app_url"))
    if app_url:
        refs.append(
            SourceRef(
                source_ref_id="src_existing_app_url",
                kind=SourceRefKind.EXTERNAL_SYSTEM,
                uri=app_url,
                indexed_at=indexed_at,
            )
        )

    api_inventory = _as_mapping(_context_get(context_variables, "api_inventory"))
    api_uri = _clean(api_inventory.get("spec_location") or api_inventory.get("openapi_url"))
    if api_uri:
        refs.append(
            SourceRef(
                source_ref_id="src_api_inventory",
                kind=SourceRefKind.API_SPEC,
                uri=api_uri,
                checksum=_clean(api_inventory.get("checksum")) or None,
                indexed_at=indexed_at,
            )
        )
    elif _clean(product_spec.get("api_surface")):
        refs.append(
            SourceRef(
                source_ref_id="src_api_surface_summary",
                kind=SourceRefKind.MANUAL_EVIDENCE,
                uri=f"manual-api-surface://{app_id}",
                indexed_at=indexed_at,
            )
        )

    return _dedupe_source_refs(refs)


def _dedupe_source_refs(refs: list[SourceRef]) -> list[SourceRef]:
    seen: set[str] = set()
    result: list[SourceRef] = []
    for ref in refs:
        if ref.source_ref_id in seen:
            continue
        seen.add(ref.source_ref_id)
        result.append(ref)
    return result


def _surface_ref(
    *,
    prefix: str,
    index: int,
    kind: str,
    label: Any = None,
    location: Any = None,
    source_ref_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> SurfaceRef:
    identifier = _clean(location) or _clean(label) or str(index)
    return SurfaceRef(
        surface_id=_stable_id(prefix, identifier, index),
        label=_clean(label) or None,
        kind=kind,
        location=_clean(location) or None,
        source_ref_id=source_ref_id,
        metadata=dict(metadata or {}),
    )


def _service_surfaces(product_spec: Mapping[str, Any], source_ref_id: str | None) -> list[SurfaceRef]:
    surfaces: list[SurfaceRef] = []
    for index, item in enumerate(_as_list(product_spec.get("service_surfaces")), start=1):
        surface = _as_mapping(item)
        surfaces.append(
            _surface_ref(
                prefix="service",
                index=index,
                kind=_clean(surface.get("kind")) or "service",
                label=surface.get("name"),
                location=surface.get("location"),
                source_ref_id=source_ref_id,
            )
        )
    return surfaces


def _route_surfaces(product_spec: Mapping[str, Any], source_ref_id: str | None) -> list[SurfaceRef]:
    surfaces: list[SurfaceRef] = []
    for index, item in enumerate(_as_list(product_spec.get("route_surfaces")), start=1):
        route = _as_mapping(item)
        surfaces.append(
            _surface_ref(
                prefix="route",
                index=index,
                kind="route",
                label=route.get("module") or route.get("path"),
                location=route.get("path"),
                source_ref_id=source_ref_id,
            )
        )
    return surfaces


def _pages(product_spec: Mapping[str, Any], source_ref_id: str | None) -> list[SurfaceRef]:
    pages: list[SurfaceRef] = []
    for index, item in enumerate(_as_list(product_spec.get("route_surfaces")), start=1):
        route = _as_mapping(item)
        pages.append(
            _surface_ref(
                prefix="page",
                index=index,
                kind="page",
                label=route.get("module") or route.get("path"),
                location=route.get("path"),
                source_ref_id=source_ref_id,
            )
        )
    return pages


def _api_endpoints(
    product_spec: Mapping[str, Any],
    context_variables: Mapping[str, Any] | Any | None,
    source_ref_id: str | None,
) -> list[SurfaceRef]:
    endpoints: list[SurfaceRef] = []
    api_inventory = _as_mapping(_context_get(context_variables, "api_inventory"))
    raw_endpoints = _as_list(api_inventory.get("endpoints") or api_inventory.get("paths"))
    for index, item in enumerate(raw_endpoints, start=1):
        if isinstance(item, str):
            label = item
            location = item
            method = None
        else:
            endpoint = _as_mapping(item)
            label = endpoint.get("operation_id") or endpoint.get("name") or endpoint.get("path")
            location = endpoint.get("path") or endpoint.get("location")
            method = endpoint.get("method")
        endpoints.append(
            _surface_ref(
                prefix="api",
                index=index,
                kind="api_endpoint",
                label=label,
                location=location,
                source_ref_id=source_ref_id,
                metadata={"method": _clean(method) or None},
            )
        )

    api_surface = _clean(product_spec.get("api_surface"))
    if api_surface and not endpoints:
        endpoints.append(
            _surface_ref(
                prefix="api",
                index=1,
                kind="api_surface",
                label="Discovered API surface",
                location=api_surface,
                source_ref_id=source_ref_id,
            )
        )
    return endpoints


def _data_entities(product_spec: Mapping[str, Any], source_ref_id: str | None) -> list[SurfaceRef]:
    entities: list[SurfaceRef] = []
    for index, item in enumerate(_as_list(product_spec.get("key_entities")), start=1):
        entity = _as_mapping(item)
        entities.append(
            _surface_ref(
                prefix="entity",
                index=index,
                kind="data_entity",
                label=entity.get("name"),
                location=entity.get("evidence_source"),
                source_ref_id=source_ref_id,
            )
        )
    return entities


def _deployment_boundaries(
    product_spec: Mapping[str, Any],
    source_ref_id: str | None,
) -> list[SurfaceRef]:
    hosting = _clean(product_spec.get("hosting_environment"))
    if not hosting:
        return []
    return [
        SurfaceRef(
            surface_id="deployment_hosting_environment",
            kind="deployment",
            label="Hosting environment",
            location=hosting,
            source_ref_id=source_ref_id,
        )
    ]


def _ci_boundaries(
    context_variables: Mapping[str, Any] | Any | None,
    source_ref_id: str | None,
) -> list[SurfaceRef]:
    raw = _as_list(_context_get(context_variables, "ci_boundaries"))
    boundaries: list[SurfaceRef] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            boundaries.append(
                _surface_ref(
                    prefix="ci",
                    index=index,
                    kind="ci",
                    label=item,
                    location=item,
                    source_ref_id=source_ref_id,
                )
            )
    return boundaries


def _integration_inventory(product_spec: Mapping[str, Any]) -> list[IntegrationInventory]:
    integrations: list[IntegrationInventory] = []
    for item in _as_list(product_spec.get("detected_connectors")):
        connector = _as_mapping(item)
        integration_id = _slug(
            connector.get("provider_id") or connector.get("package_or_import") or "integration"
        )
        secret_required = bool(_as_list(connector.get("likely_secret_envs")))
        config_required = bool(connector)
        readiness = (
            IntegrationReadinessStatus.SECRET_REQUIRED
            if secret_required
            else IntegrationReadinessStatus.CONFIG_REQUIRED
            if config_required
            else IntegrationReadinessStatus.UNKNOWN
        )
        integrations.append(
            IntegrationInventory(
                integration_id=integration_id,
                provider_type=_clean(connector.get("category") or connector.get("provider_id"))
                or "unknown",
                config_required=config_required,
                secret_required=secret_required,
                frontend_safe_config={},
                readiness_status=readiness,
                owner=OwnershipClass.EXTERNAL_SYSTEM.value,
            )
        )
    return integrations


def _unknowns(discovery_artifact: Mapping[str, Any]) -> list[UnknownItem]:
    unknowns: list[UnknownItem] = []
    for index, item in enumerate(_as_list(discovery_artifact.get("unresolved_questions")), start=1):
        question = _as_mapping(item)
        description = _clean(question.get("question") or question.get("description"))
        if not description:
            continue
        unknowns.append(
            UnknownItem(
                item_id=_stable_id("unknown", index, description[:32]),
                description=description,
                evidence=[_clean(question.get("context"))] if _clean(question.get("context")) else [],
                priority=_clean(question.get("priority")) or "medium",
            )
        )
    return unknowns


def _ownership_boundaries(
    *,
    product_spec: Mapping[str, Any],
    capability_specs: list[Any],
    augmentation_plan: Mapping[str, Any],
    decomposition_plan: Mapping[str, Any] | None,
    default_source_ref: str | None,
) -> list[OwnershipBoundary]:
    boundaries: list[OwnershipBoundary] = []

    def add_read_only(path_or_artifact: Any, notes: str | None = None) -> None:
        path = _clean(path_or_artifact)
        if not path:
            return
        boundaries.append(
            OwnershipBoundary(
                path_or_artifact=path,
                ownership=OwnershipClass.READ_ONLY_DISCOVERED,
                source_ref=default_source_ref,
                allowed_operations=[
                    AllowedOperation.INSPECT,
                    AllowedOperation.INDEX,
                    AllowedOperation.EXPLAIN,
                ],
                requires_review=True,
                notes=notes,
            )
        )

    def add_overlay(path_or_artifact: Any, operation: AllowedOperation, notes: str | None = None) -> None:
        path = _clean(path_or_artifact)
        if not path:
            return
        boundaries.append(
            OwnershipBoundary(
                path_or_artifact=path,
                ownership=OwnershipClass.GENERATED_OVERLAY,
                source_ref=default_source_ref,
                allowed_operations=[operation],
                requires_review=True,
                notes=notes,
            )
        )

    for service in _as_list(product_spec.get("service_surfaces")):
        surface = _as_mapping(service)
        add_read_only(surface.get("location") or surface.get("name"), "Existing service surface")
    for route in _as_list(product_spec.get("route_surfaces")):
        surface = _as_mapping(route)
        add_read_only(surface.get("path") or surface.get("module"), "Existing route surface")
    for connector in _as_list(product_spec.get("detected_connectors")):
        for source_file in _as_list(_as_mapping(connector).get("source_files")):
            add_read_only(source_file, "Connector source evidence")
    for capability in capability_specs:
        for entrypoint in _as_list(_as_mapping(capability).get("entrypoints")):
            add_read_only(entrypoint, "Capability entrypoint evidence")

    for workflow in _as_list(augmentation_plan.get("initial_workflows")):
        add_overlay(f"workflow:{workflow}", AllowedOperation.GENERATE_OVERLAY, "Recommended workflow")
    for adapter in _as_list(augmentation_plan.get("new_adapters_required")):
        add_overlay(f"adapter:{adapter}", AllowedOperation.GENERATE_ADAPTER, "Recommended adapter")

    if decomposition_plan:
        for module in _as_list(decomposition_plan.get("proposed_modules")):
            module_id = _as_mapping(module).get("module_id")
            add_overlay(
                f"module:{module_id}",
                AllowedOperation.PROPOSE_MIGRATION,
                "Modernization candidate from historical decomposition evidence",
            )
        for page in _as_list(decomposition_plan.get("proposed_pages")):
            page_id = _as_mapping(page).get("page_id") or _as_mapping(page).get("route")
            add_overlay(
                f"page:{page_id}",
                AllowedOperation.GENERATE_OVERLAY,
                "Overlay candidate from historical decomposition evidence",
            )
        for adapter in _as_list(decomposition_plan.get("proposed_adapters")):
            provider_id = _as_mapping(adapter).get("provider_id")
            add_overlay(
                f"adapter:{provider_id}",
                AllowedOperation.GENERATE_ADAPTER,
                "Adapter candidate from historical decomposition evidence",
            )

    return _dedupe_boundaries(boundaries)


def _dedupe_boundaries(boundaries: list[OwnershipBoundary]) -> list[OwnershipBoundary]:
    seen: set[tuple[str, str]] = set()
    result: list[OwnershipBoundary] = []
    for boundary in boundaries:
        key = (boundary.path_or_artifact, boundary.ownership.value)
        if key in seen:
            continue
        seen.add(key)
        result.append(boundary)
    return result


def _risk_report(
    *,
    product_spec: Mapping[str, Any],
    capability_specs: list[Any],
    augmentation_plan: Mapping[str, Any],
    context_variables: Mapping[str, Any] | Any | None,
    unknowns: list[UnknownItem],
) -> RiskReport:
    risks: list[RiskItem] = []
    if product_spec.get("storage_migration_required") or augmentation_plan.get("storage_migration_required"):
        risks.append(
            RiskItem(
                risk_id="risk_storage_migration_required",
                description="Storage changes require explicit migration planning before owned modules are created.",
                severity="high",
                mitigation="Review data ownership and migration validation before staging implementation work.",
            )
        )
    if _as_list(augmentation_plan.get("new_adapters_required")):
        risks.append(
            RiskItem(
                risk_id="risk_new_adapters_required",
                description="One or more integration adapters are not ready yet.",
                severity="medium",
                mitigation="Create reviewed adapter contracts before generated modules depend on them.",
            )
        )
    if any(_clean(_as_mapping(cap).get("confidence")) == "unverified" for cap in capability_specs):
        risks.append(
            RiskItem(
                risk_id="risk_unverified_capabilities",
                description="Some capabilities are based on unverified discovery evidence.",
                severity="medium",
                mitigation="Refresh discovery or request human confirmation before staging code changes.",
            )
        )

    blocked_paths = _unique_strings(_as_list(_context_get(context_variables, "blocked_paths")))
    secret_sensitive_paths = _unique_strings(
        _as_list(_context_get(context_variables, "secret_sensitive_paths"))
    )
    stale_warnings: list[str] = []
    if _clean(_context_get(context_variables, "preload_status")) not in ("", "ready"):
        stale_warnings.append("Discovery preload was incomplete or not ready.")

    next_steps = [
        "Review scan boundaries and ownership before staging changes.",
        "Confirm integration readiness before generated overlays call external systems.",
    ]
    if unknowns:
        next_steps.append("Resolve high-priority unknowns before promotion or pull request handoff.")
    if _parse_decomposition_plan(_context_get(context_variables, "module_decomposition_plan")):
        next_steps.append("Treat historical decomposition output as evidence feeding the adoption plan.")

    return RiskReport(
        risks=risks,
        unknowns=unknowns,
        blocked_paths=blocked_paths,
        secret_sensitive_paths=secret_sensitive_paths,
        stale_context_warnings=stale_warnings,
        recommended_next_steps=next_steps,
    )


def _adoption_plan(
    *,
    augmentation_plan: Mapping[str, Any],
    capability_specs: list[Any],
    unknowns: list[UnknownItem],
    context_variables: Mapping[str, Any] | Any | None = None,
) -> AdoptionPlan:
    legacy_level = _clean(augmentation_plan.get("adoption_level")).lower()
    recommended_path = _map_adoption_path(legacy_level)
    decomposition_plan = _parse_decomposition_plan(
        _context_get(context_variables, "module_decomposition_plan")
    )
    phases = [
        AdoptionPhase(
            phase_id="discovery_review",
            label="Discovery review",
            goals=["Confirm inventory, unknowns, and ownership boundaries."],
            actions=["Review ApplicationInventory, RiskReport, and OwnershipBoundary drafts."],
            required_human_decisions=["Approve scan scope before any staged change is proposed."],
        )
    ]
    if recommended_path in (AdoptionPath.AUGMENT, AdoptionPath.ADAPTER, AdoptionPath.OVERLAY):
        phases.append(
            AdoptionPhase(
                phase_id="overlay_or_adapter",
                label="Overlay or adapter",
                goals=["Add Mozaiks-owned augmentation without taking over existing source."],
                actions=["Generate reviewed overlays, adapters, or workflows where ownership allows."],
                required_human_decisions=["Approve which surfaces may be augmented."],
            )
        )
    if recommended_path is AdoptionPath.GRADUAL_MODERNIZATION:
        phases.append(
            AdoptionPhase(
                phase_id="modernization_candidate",
                label="Modernization candidate",
                goals=["Evaluate selective module or page migration candidates."],
                actions=["Convert historical decomposition evidence into reviewed migration candidates."],
                required_human_decisions=["Approve each transfer of ownership before implementation."],
            )
        )

    candidate_overlays = [f"workflow:{workflow}" for workflow in _as_list(augmentation_plan.get("initial_workflows"))]
    candidate_adapters = [f"adapter:{adapter}" for adapter in _as_list(augmentation_plan.get("new_adapters_required"))]
    candidate_migrations: list[str] = []

    for capability in capability_specs:
        cap = _as_mapping(capability)
        if _clean(cap.get("migration_priority")):
            candidate_migrations.append(f"capability:{cap.get('capability_id') or cap.get('label')}")

    if decomposition_plan:
        for module in _as_list(decomposition_plan.get("proposed_modules")):
            module_id = _as_mapping(module).get("module_id")
            if _clean(module_id):
                candidate_migrations.append(f"module:{module_id}")
        for page in _as_list(decomposition_plan.get("proposed_pages")):
            page_id = _as_mapping(page).get("page_id") or _as_mapping(page).get("route")
            if _clean(page_id):
                candidate_overlays.append(f"page:{page_id}")
        for adapter in _as_list(decomposition_plan.get("proposed_adapters")):
            provider_id = _as_mapping(adapter).get("provider_id")
            if _clean(provider_id):
                candidate_adapters.append(f"adapter:{provider_id}")

    human_decisions = [
        "Confirm which discovered surfaces remain read-only.",
        "Approve generated overlays or adapters before promotion.",
    ]
    if unknowns:
        human_decisions.append("Resolve high-priority unknowns before applying changes.")

    return AdoptionPlan(
        recommended_path=recommended_path,
        phases=phases,
        candidate_overlays=_unique_strings(candidate_overlays),
        candidate_adapters=_unique_strings(candidate_adapters),
        candidate_migrations=_unique_strings(candidate_migrations),
        human_decisions_required=human_decisions,
        not_in_scope=[
            "Automatic takeover or rewrite of existing source without explicit approval.",
            "Runtime routing decisions based on graph mirrors.",
        ],
    )


def _map_adoption_path(legacy_level: str) -> AdoptionPath:
    if legacy_level == "embed":
        return AdoptionPath.AUGMENT
    if legacy_level == "bridge":
        return AdoptionPath.ADAPTER
    if legacy_level == "ecosystem":
        return AdoptionPath.OVERLAY
    if legacy_level == "gradual_modernization":
        return AdoptionPath.GRADUAL_MODERNIZATION
    if legacy_level == "native_migration":
        return AdoptionPath.GRADUAL_MODERNIZATION
    return AdoptionPath.OBSERVE


def _parse_decomposition_plan(value: Any) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, Mapping) else None
    return None


def _scan_policy_ref(context_variables: Mapping[str, Any] | Any | None) -> str | None:
    scan_policy = _context_get(context_variables, "scan_policy_ref") or _context_get(
        context_variables, "scan_policy"
    )
    if isinstance(scan_policy, Mapping):
        return _clean(scan_policy.get("scan_policy_id") or scan_policy.get("id")) or None
    return _clean(scan_policy) or None


def _app_context_graph(
    *,
    app_id: str,
    source_refs: list[SourceRef],
    inventory: ApplicationInventory,
    capability_specs: list[Any],
    risk_report: RiskReport,
    ownership_boundaries: list[OwnershipBoundary],
    decomposition_plan: Mapping[str, Any] | None,
    chat_id: Any,
    indexed_at: datetime,
) -> AppContextGraph | None:
    if not source_refs:
        return None

    default_source_ref = source_refs[0].source_ref_id
    nodes_by_id: dict[str, AppContextGraphNode] = {}
    edges_by_key: dict[tuple[str, str, GraphEdgeType], AppContextGraphEdge] = {}

    def add_node(
        node_id: str,
        node_type: GraphNodeType,
        label: str | None,
        source_ref_id: str | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        clean_metadata = _clean_metadata(metadata)
        if node_id in nodes_by_id:
            if clean_metadata:
                node = nodes_by_id[node_id]
                merged_metadata = dict(node.metadata)
                merged_metadata.update(clean_metadata)
                nodes_by_id[node_id] = node.model_copy(update={"metadata": merged_metadata})
            return
        nodes_by_id[node_id] = (
            AppContextGraphNode(
                node_id=node_id,
                node_type=node_type,
                label=label,
                source_ref_id=source_ref_id or default_source_ref,
                stale_status=AppContextStaleStatus.UNKNOWN,
                metadata=clean_metadata,
            )
        )

    def add_edge(
        source_node_id: str,
        target_node_id: str,
        edge_type: GraphEdgeType,
        source_ref_id: str | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        key = (source_node_id, target_node_id, edge_type)
        if key in edges_by_key:
            return
        edges_by_key[key] = (
            AppContextGraphEdge(
                edge_id=_stable_id("edge", edge_type.value, source_node_id, target_node_id),
                edge_type=edge_type,
                source_node_id=source_node_id,
                target_node_id=target_node_id,
                source_ref_id=source_ref_id or default_source_ref,
                stale_status=AppContextStaleStatus.UNKNOWN,
                metadata=_clean_metadata(metadata),
            )
        )

    add_node(
        "app",
        GraphNodeType.APP,
        app_id,
        default_source_ref,
        {"provenance": "ExistingAppDiscovery", "advisory": True},
    )

    for source_ref in source_refs:
        node_type = GraphNodeType.REPO if source_ref.kind is SourceRefKind.REPO else GraphNodeType.SOURCE_REF
        add_node(
            source_ref.source_ref_id,
            node_type,
            source_ref.uri,
            source_ref.source_ref_id,
            {
                "provenance": "source_refs",
                "source_kind": source_ref.kind.value,
                "source_ref_id": source_ref.source_ref_id,
            },
        )
        add_edge(
            "app",
            source_ref.source_ref_id,
            GraphEdgeType.CONTAINS,
            source_ref.source_ref_id,
            {"provenance": "source_refs", "confidence": "explicit"},
        )

    route_nodes: list[tuple[SurfaceRef, str]] = []
    for surface in inventory.routes:
        node_id = f"route:{surface.surface_id}"
        route_nodes.append((surface, node_id))
        add_node(
            node_id,
            GraphNodeType.ROUTE,
            surface.label or surface.location,
            surface.source_ref_id,
            _surface_metadata(surface, "ApplicationInventory.routes"),
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            surface.source_ref_id,
            {"provenance": "ApplicationInventory.routes", "confidence": "discovered"},
        )

    page_nodes: list[tuple[SurfaceRef, str]] = []
    for page in inventory.pages:
        node_id = f"page:{page.surface_id}"
        page_nodes.append((page, node_id))
        add_node(
            node_id,
            GraphNodeType.COMPONENT,
            page.label or page.location,
            page.source_ref_id,
            _surface_metadata(page, "ApplicationInventory.pages"),
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            page.source_ref_id,
            {"provenance": "ApplicationInventory.pages", "confidence": "discovered"},
        )

    for route, route_node_id in route_nodes:
        for page, page_node_id in page_nodes:
            if _surface_matches(route, page):
                add_edge(
                    route_node_id,
                    page_node_id,
                    GraphEdgeType.RENDERS,
                    route.source_ref_id or page.source_ref_id,
                    {"provenance": "discovered_route_surface", "confidence": "discovered"},
                )

    api_nodes: list[tuple[SurfaceRef, str]] = []
    for endpoint in inventory.api_endpoints:
        node_id = f"api:{endpoint.surface_id}"
        api_nodes.append((endpoint, node_id))
        add_node(
            node_id,
            GraphNodeType.API_ENDPOINT,
            endpoint.label or endpoint.location,
            endpoint.source_ref_id,
            _surface_metadata(endpoint, "ApplicationInventory.api_endpoints"),
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            endpoint.source_ref_id,
            {"provenance": "ApplicationInventory.api_endpoints", "confidence": "discovered"},
        )

    for route, route_node_id in route_nodes:
        for endpoint, endpoint_node_id in api_nodes:
            if _surfaces_share_path_signal(route, endpoint):
                add_edge(
                    route_node_id,
                    endpoint_node_id,
                    GraphEdgeType.CALLS,
                    route.source_ref_id or endpoint.source_ref_id,
                    {"provenance": "discovered_surface_path_match", "confidence": "low"},
                )

    entity_nodes: list[tuple[SurfaceRef, str]] = []
    for entity in inventory.data_entities:
        node_id = f"entity:{entity.surface_id}"
        entity_nodes.append((entity, node_id))
        add_node(
            node_id,
            GraphNodeType.DATA_ENTITY,
            entity.label,
            entity.source_ref_id,
            _surface_metadata(entity, "ApplicationInventory.data_entities"),
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            entity.source_ref_id,
            {"provenance": "ApplicationInventory.data_entities", "confidence": "discovered"},
        )

    integration_nodes: list[tuple[IntegrationInventory, str]] = []
    for integration in inventory.integrations:
        node_id = f"integration:{integration.integration_id}"
        integration_nodes.append((integration, node_id))
        add_node(
            node_id,
            GraphNodeType.INTEGRATION,
            integration.integration_id,
            default_source_ref,
            {
                "provenance": "IntegrationInventory",
                "provider_type": integration.provider_type,
                "readiness_status": integration.readiness_status.value,
                "ownership": OwnershipClass.EXTERNAL_SYSTEM.value,
            },
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.USES_INTEGRATION,
            default_source_ref,
            {"provenance": "IntegrationInventory", "confidence": "discovered"},
        )

    capability_nodes: list[tuple[dict[str, Any], str]] = []
    for index, capability in enumerate(capability_specs, start=1):
        cap = _as_mapping(capability)
        capability_id = _clean(cap.get("capability_id") or cap.get("label") or f"capability_{index}")
        node_id = f"capability:{_stable_id(capability_id)}"
        capability_nodes.append((cap, node_id))
        add_node(
            node_id,
            GraphNodeType.CAPABILITY,
            _clean(cap.get("label")) or capability_id,
            default_source_ref,
            {
                "provenance": "CapabilitySpec",
                "capability_id": capability_id,
                "delivery_surface": _clean(cap.get("delivery_surface")) or None,
                "entrypoints": _unique_strings(_as_list(cap.get("entrypoints"))),
                "entity_refs": _unique_strings(_as_list(cap.get("entity_refs"))),
                "connector_requirements": _unique_strings(_as_list(cap.get("connector_requirements"))),
                "confidence": _clean(cap.get("confidence")) or None,
                "evidence_source": _clean(cap.get("evidence_source")) or None,
            },
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            default_source_ref,
            {"provenance": "CapabilitySpec", "confidence": "discovered"},
        )

        entrypoints = _as_list(cap.get("entrypoints"))
        connector_requirements = _as_list(cap.get("connector_requirements"))
        entity_refs = _as_list(cap.get("entity_refs"))
        for endpoint, endpoint_node_id in api_nodes:
            if _matches_any_evidence(endpoint, entrypoints):
                add_edge(
                    node_id,
                    endpoint_node_id,
                    GraphEdgeType.CALLS,
                    endpoint.source_ref_id,
                    {"provenance": "CapabilitySpec.entrypoints", "confidence": "explicit"},
                )
        for route, route_node_id in route_nodes:
            if _matches_any_evidence(route, entrypoints):
                add_edge(
                    node_id,
                    route_node_id,
                    GraphEdgeType.DEPENDS_ON,
                    route.source_ref_id,
                    {"provenance": "CapabilitySpec.entrypoints", "confidence": "explicit"},
                )
        for integration, integration_node_id in integration_nodes:
            if _matches_value(_clean(integration.integration_id), connector_requirements):
                add_edge(
                    node_id,
                    integration_node_id,
                    GraphEdgeType.USES_INTEGRATION,
                    default_source_ref,
                    {"provenance": "CapabilitySpec.connector_requirements", "confidence": "explicit"},
                )
        for entity, entity_node_id in entity_nodes:
            if _matches_any_evidence(entity, entity_refs):
                add_edge(
                    node_id,
                    entity_node_id,
                    GraphEdgeType.DEPENDS_ON,
                    entity.source_ref_id,
                    {"provenance": "CapabilitySpec.entity_refs", "confidence": "explicit"},
                )

    for boundary in ownership_boundaries:
        node_type = _ownership_node_type(boundary)
        node_id = f"ownership:{_stable_id(boundary.path_or_artifact, boundary.ownership.value)}"
        add_node(
            node_id,
            node_type,
            boundary.path_or_artifact,
            boundary.source_ref,
            _ownership_metadata(boundary),
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            boundary.source_ref,
            {"provenance": "OwnershipBoundary", "confidence": "discovered"},
        )
        if boundary.source_ref and boundary.source_ref in nodes_by_id:
            add_edge(
                node_id,
                boundary.source_ref,
                GraphEdgeType.PROTECTED_BY,
                boundary.source_ref,
                {"provenance": "OwnershipBoundary.source_ref", "confidence": "explicit"},
            )

    _add_decomposition_evidence_nodes(
        decomposition_plan=decomposition_plan,
        default_source_ref=default_source_ref,
        capability_nodes=capability_nodes,
        integration_nodes=integration_nodes,
        add_node=add_node,
        add_edge=add_edge,
    )

    for risk in risk_report.risks:
        node_id = f"risk:{risk.risk_id}"
        add_node(
            node_id,
            GraphNodeType.RISK,
            risk.description,
            default_source_ref,
            {
                "provenance": "RiskReport.risks",
                "severity": risk.severity,
                "mitigation": risk.mitigation,
                "affected_refs": risk.affected_refs,
            },
        )
        add_edge(
            node_id,
            "app",
            GraphEdgeType.BLOCKS,
            default_source_ref,
            {"provenance": "RiskReport.risks", "confidence": "discovered"},
        )
        if risk.risk_id == "risk_storage_migration_required":
            for _, entity_node_id in entity_nodes:
                add_edge(
                    node_id,
                    entity_node_id,
                    GraphEdgeType.BLOCKS,
                    default_source_ref,
                    {"provenance": "RiskReport.risks", "confidence": "discovered"},
                )
        if risk.risk_id == "risk_new_adapters_required":
            for _, integration_node_id in integration_nodes:
                add_edge(
                    node_id,
                    integration_node_id,
                    GraphEdgeType.BLOCKS,
                    default_source_ref,
                    {"provenance": "RiskReport.risks", "confidence": "discovered"},
                )
        if risk.risk_id == "risk_unverified_capabilities":
            for capability, capability_node_id in capability_nodes:
                if _clean(capability.get("confidence")) == "unverified":
                    add_edge(
                        node_id,
                        capability_node_id,
                        GraphEdgeType.BLOCKS,
                        default_source_ref,
                        {"provenance": "RiskReport.risks", "confidence": "discovered"},
                    )

    for unknown in risk_report.unknowns:
        node_id = f"unknown:{unknown.item_id}"
        add_node(
            node_id,
            GraphNodeType.UNKNOWN,
            unknown.description,
            default_source_ref,
            {
                "provenance": "RiskReport.unknowns",
                "priority": unknown.priority,
                "evidence": unknown.evidence,
            },
        )
        add_edge(
            node_id,
            "app",
            GraphEdgeType.BLOCKS,
            default_source_ref,
            {"provenance": "RiskReport.unknowns", "confidence": "discovered"},
        )
        unknown_text = " ".join([unknown.description, *_as_list(unknown.evidence)])
        for capability, capability_node_id in capability_nodes:
            if _capability_text_matches(capability, unknown_text):
                add_edge(
                    node_id,
                    capability_node_id,
                    GraphEdgeType.BLOCKS,
                    default_source_ref,
                    {"provenance": "RiskReport.unknowns", "confidence": "discovered"},
                )

    nodes = list(nodes_by_id.values())
    edges = list(edges_by_key.values())
    graph_hash = _hash_payload(
        {
            "app_id": app_id,
            "nodes": [node.model_dump(mode="json") for node in nodes],
            "edges": [edge.model_dump(mode="json") for edge in edges],
        }
    )
    return AppContextGraph(
        graph_id=_stable_id("graph", app_id, chat_id or "draft"),
        app_id=app_id,
        source_refs=source_refs,
        indexed_at=indexed_at,
        nodes=nodes,
        edges=edges,
        stale_status=AppContextStaleStatus.UNKNOWN,
        graph_hash=f"sha256:{graph_hash}",
    )


def _clean_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        if value in (None, "", [], {}):
            continue
        result[key] = value
    return result


def _surface_metadata(surface: SurfaceRef, provenance: str) -> dict[str, Any]:
    metadata = dict(surface.metadata)
    if surface.location:
        metadata["location"] = surface.location
    if surface.kind:
        metadata["surface_kind"] = surface.kind
    if surface.source_ref_id:
        metadata["source_ref_id"] = surface.source_ref_id
    metadata["provenance"] = provenance
    return metadata


def _surface_matches(left: SurfaceRef, right: SurfaceRef) -> bool:
    left_values = [left.surface_id, left.label, left.location]
    right_values = [right.surface_id, right.label, right.location]
    return any(_matches_value(_clean(value), right_values) for value in left_values if _clean(value))


def _surfaces_share_path_signal(left: SurfaceRef, right: SurfaceRef) -> bool:
    left_path = _clean(left.location or left.label)
    right_path = _clean(right.location or right.label)
    if not left_path or not right_path:
        return False
    return _path_signal(left_path) == _path_signal(right_path)


def _path_signal(value: str) -> str:
    clean = _clean(value).lower()
    clean = clean.removeprefix("/api/")
    clean = clean.removeprefix("api/")
    clean = clean.removeprefix("/")
    return _slug(clean)


def _matches_any_evidence(surface: SurfaceRef, evidence_values: list[Any]) -> bool:
    targets = [
        surface.surface_id,
        surface.label,
        surface.location,
        surface.metadata.get("operation_id"),
        surface.metadata.get("method"),
    ]
    return any(_matches_value(_clean(target), evidence_values) for target in targets if _clean(target))


def _matches_value(value: str, evidence_values: list[Any]) -> bool:
    clean_value = _clean(value).lower()
    if not clean_value:
        return False
    value_slug = _slug(clean_value)
    for evidence in evidence_values:
        clean_evidence = _clean(evidence).lower()
        if not clean_evidence:
            continue
        evidence_slug = _slug(clean_evidence)
        if clean_evidence == clean_value or evidence_slug == value_slug:
            return True
        if clean_value in clean_evidence or clean_evidence in clean_value:
            return True
        if value_slug and evidence_slug and (value_slug in evidence_slug or evidence_slug in value_slug):
            return True
    return False


def _ownership_node_type(boundary: OwnershipBoundary) -> GraphNodeType:
    value = boundary.path_or_artifact
    if value.startswith("module:"):
        return GraphNodeType.MODULE
    if value.startswith("page:"):
        return GraphNodeType.COMPONENT
    if value.startswith("workflow:"):
        return GraphNodeType.WORKFLOW
    if value.startswith("adapter:"):
        return GraphNodeType.INTEGRATION
    if boundary.ownership is OwnershipClass.GENERATED_OVERLAY:
        return GraphNodeType.GENERATED_OVERLAY
    if _safe_relative_path(value):
        return GraphNodeType.FILE
    if value.startswith("/"):
        return GraphNodeType.ROUTE
    return GraphNodeType.SOURCE_REF


def _ownership_metadata(boundary: OwnershipBoundary) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "provenance": "OwnershipBoundary",
        "ownership": boundary.ownership.value,
        "ownership_class": boundary.ownership.value,
        "allowed_operations": [operation.value for operation in boundary.allowed_operations],
        "requires_review": boundary.requires_review,
        "notes": boundary.notes,
        "path_or_artifact": boundary.path_or_artifact,
    }
    safe_path = _safe_relative_path(boundary.path_or_artifact)
    if safe_path:
        metadata["path"] = safe_path
        metadata["source_path"] = safe_path
    return metadata


def _safe_relative_path(value: Any) -> str | None:
    path = _clean(value).replace("\\", "/")
    if not path or path.startswith("/") or ".." in path.split("/"):
        return None
    if re.match(r"^[a-zA-Z]:/", path):
        return None
    lowered = path.lower()
    unsafe_terms = (".env", "secret", "credential", "password", "token", "vault")
    if any(term in lowered for term in unsafe_terms):
        return None
    if ":" in path:
        return None
    if "/" not in path and "." not in path:
        return None
    return path


def _add_decomposition_evidence_nodes(
    *,
    decomposition_plan: Mapping[str, Any] | None,
    default_source_ref: str,
    capability_nodes: list[tuple[dict[str, Any], str]],
    integration_nodes: list[tuple[IntegrationInventory, str]],
    add_node: Any,
    add_edge: Any,
) -> None:
    if not decomposition_plan:
        return

    candidate_module_nodes: dict[str, str] = {}
    for module in _as_list(decomposition_plan.get("proposed_modules")):
        item = _as_mapping(module)
        module_id = _clean(item.get("module_id"))
        if not module_id:
            continue
        node_id = f"candidate_module:{_stable_id(module_id)}"
        candidate_module_nodes[_slug(module_id)] = node_id
        add_node(
            node_id,
            GraphNodeType.MODULE,
            module_id,
            default_source_ref,
            {
                "provenance": "internal_decomposition_evidence",
                "internal_evidence": True,
                "candidate_type": "module",
                "ownership": OwnershipClass.GENERATED_OVERLAY.value,
                "source_files": _unique_strings(_as_list(item.get("source_files"))),
            },
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            default_source_ref,
            {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
        )
        for capability_value in _as_list(item.get("source_capabilities")):
            for capability, capability_node_id in capability_nodes:
                if _capability_text_matches(capability, capability_value):
                    add_edge(
                        node_id,
                        capability_node_id,
                        GraphEdgeType.IMPLEMENTS_CAPABILITY,
                        default_source_ref,
                        {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
                    )

    for page in _as_list(decomposition_plan.get("proposed_pages")):
        item = _as_mapping(page)
        page_id = _clean(item.get("page_id") or item.get("route"))
        if not page_id:
            continue
        node_id = f"candidate_page:{_stable_id(page_id)}"
        add_node(
            node_id,
            GraphNodeType.COMPONENT,
            page_id,
            default_source_ref,
            {
                "provenance": "internal_decomposition_evidence",
                "internal_evidence": True,
                "candidate_type": "page",
                "ownership": OwnershipClass.GENERATED_OVERLAY.value,
                "route": _clean(item.get("route")) or None,
            },
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            default_source_ref,
            {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
        )
        for binding in _as_list(item.get("module_bindings")):
            module_node_id = candidate_module_nodes.get(_slug(binding))
            if module_node_id:
                add_edge(
                    node_id,
                    module_node_id,
                    GraphEdgeType.CALLS,
                    default_source_ref,
                    {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
                )

    for adapter in _as_list(decomposition_plan.get("proposed_adapters")):
        item = _as_mapping(adapter)
        provider_id = _clean(item.get("provider_id"))
        if not provider_id:
            continue
        node_id = f"candidate_adapter:{_stable_id(provider_id)}"
        add_node(
            node_id,
            GraphNodeType.INTEGRATION,
            provider_id,
            default_source_ref,
            {
                "provenance": "internal_decomposition_evidence",
                "internal_evidence": True,
                "candidate_type": "adapter",
                "ownership": OwnershipClass.GENERATED_OVERLAY.value,
            },
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            default_source_ref,
            {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
        )
        for integration, integration_node_id in integration_nodes:
            if _matches_value(integration.integration_id, [provider_id]):
                add_edge(
                    node_id,
                    integration_node_id,
                    GraphEdgeType.WRAPS,
                    default_source_ref,
                    {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
                )

    for workflow in _as_list(decomposition_plan.get("proposed_workflows")):
        item = _as_mapping(workflow)
        workflow_id = _clean(item.get("workflow_id") or item.get("name"))
        if not workflow_id:
            continue
        node_id = f"candidate_workflow:{_stable_id(workflow_id)}"
        add_node(
            node_id,
            GraphNodeType.WORKFLOW,
            workflow_id,
            default_source_ref,
            {
                "provenance": "internal_decomposition_evidence",
                "internal_evidence": True,
                "candidate_type": "workflow",
                "ownership": OwnershipClass.GENERATED_OVERLAY.value,
            },
        )
        add_edge(
            "app",
            node_id,
            GraphEdgeType.CONTAINS,
            default_source_ref,
            {"provenance": "internal_decomposition_evidence", "internal_evidence": True},
        )


def _capability_text_matches(capability: Mapping[str, Any], text: Any) -> bool:
    values = [
        capability.get("capability_id"),
        capability.get("label"),
        capability.get("description"),
    ]
    return any(_matches_value(_clean(value), [text]) for value in values if _clean(value))


def _hash_payload(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "APP_CONTEXT_ARTIFACT_KINDS",
    "ExistingAppContextArtifactDrafts",
    "build_existing_app_context_artifacts",
]
