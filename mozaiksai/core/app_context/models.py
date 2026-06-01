from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AppContextMode(str, Enum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"
    HYBRID = "hybrid"


class AppContextStaleStatus(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    PARTIALLY_STALE = "partially_stale"
    UNSAFE = "unsafe"
    UNKNOWN = "unknown"


class AppContextReviewState(str, Enum):
    DRAFT = "draft"
    REVIEW_PENDING = "review_pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class AppContextPromotionState(str, Enum):
    NOT_PROMOTED = "not_promoted"
    PR_READY = "pr_ready"
    PROMOTION_READY = "promotion_ready"
    PROMOTED = "promoted"
    APPLIED_EXTERNALLY = "applied_externally"


class SourceRefKind(str, Enum):
    REPO = "repo"
    APP_ROOT = "app_root"
    GENERATED_BUNDLE = "generated_bundle"
    ARTIFACT_VERSION = "artifact_version"
    DISCOVERY_SNAPSHOT = "discovery_snapshot"
    API_SPEC = "api_spec"
    MANUAL_EVIDENCE = "manual_evidence"
    EXTERNAL_SYSTEM = "external_system"


class OwnershipClass(str, Enum):
    READ_ONLY_DISCOVERED = "read_only_discovered"
    GENERATED_OVERLAY = "generated_overlay"
    STAGED_PATCH = "staged_patch"
    MIGRATED_OWNED = "migrated_owned"
    EXTERNAL_SYSTEM = "external_system"


class AllowedOperation(str, Enum):
    INSPECT = "inspect"
    INDEX = "index"
    EXPLAIN = "explain"
    STAGE_PATCH = "stage_patch"
    GENERATE_OVERLAY = "generate_overlay"
    GENERATE_ADAPTER = "generate_adapter"
    PROPOSE_MIGRATION = "propose_migration"
    PROMOTE = "promote"
    OPEN_PR = "open_pr"
    IGNORE = "ignore"


class GraphNodeType(str, Enum):
    APP = "app"
    REPO = "repo"
    SOURCE_REF = "source_ref"
    ARTIFACT = "artifact"
    FILE = "file"
    ROUTE = "route"
    PAGE = "page"
    COMPONENT = "component"
    API_ENDPOINT = "api_endpoint"
    DATA_ENTITY = "data_entity"
    INTEGRATION = "integration"
    CAPABILITY = "capability"
    MODULE = "module"
    WORKFLOW = "workflow"
    AGENT = "agent"
    TOOL = "tool"
    SYMBOL = "symbol"
    CONFIG = "config"
    PROMPT = "prompt"
    GENERATED_OVERLAY = "generated_overlay"
    STAGED_PATCH = "staged_patch"
    RISK = "risk"
    UNKNOWN = "unknown"


class GraphEdgeType(str, Enum):
    CONTAINS = "contains"
    DECLARES = "declares"
    DEFINES = "defines"
    IMPORTS = "imports"
    RENDERS = "renders"
    CALLS = "calls"
    READS = "reads"
    WRITES = "writes"
    REFERENCES = "references"
    USES_INTEGRATION = "uses_integration"
    PROTECTED_BY = "protected_by"
    IMPLEMENTS_CAPABILITY = "implements_capability"
    GENERATED_FROM = "generated_from"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    TRIGGERS = "triggers"
    STAGED_PATCH_TOUCHES = "staged_patch_touches"
    WRAPS = "wraps"
    REPLACES = "replaces"
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"


class IntegrationReadinessStatus(str, Enum):
    UNKNOWN = "unknown"
    READY = "ready"
    CONFIG_REQUIRED = "config_required"
    SECRET_REQUIRED = "secret_required"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"


class AdoptionPath(str, Enum):
    OBSERVE = "observe"
    AUGMENT = "augment"
    OVERLAY = "overlay"
    ADAPTER = "adapter"
    GRADUAL_MODERNIZATION = "gradual_modernization"
    DEFER = "defer"


class BrownfieldRegistrationStatus(str, Enum):
    PENDING = "pending"
    REGISTERED = "registered"
    NEEDS_REVIEW = "needs_review"
    STALE = "stale"
    ARCHIVED = "archived"


class SourceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_ref_id: str = Field(min_length=1)
    kind: SourceRefKind
    uri: str = Field(min_length=1)
    ref: str | None = None
    checksum: str | None = None
    indexed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_kind: str = Field(min_length=1)
    artifact_key: str | None = None
    artifact_version_id: str = Field(min_length=1)
    lifecycle_status: str | None = None
    source_ref_id: str | None = None


class ValidationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_failures: list[str] = Field(default_factory=list)


class SurfaceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    surface_id: str = Field(min_length=1)
    label: str | None = None
    kind: str = Field(min_length=1)
    location: str | None = None
    source_ref_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class UnknownItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    priority: str = "medium"


class OwnershipBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path_or_artifact: str = Field(min_length=1)
    ownership: OwnershipClass
    source_ref: str | None = None
    allowed_operations: list[AllowedOperation] = Field(default_factory=list)
    requires_review: bool = True
    notes: str | None = None


class IntegrationInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    integration_id: str = Field(min_length=1)
    provider_type: str = Field(min_length=1)
    config_required: bool = False
    secret_required: bool = False
    frontend_safe_config: dict[str, Any] = Field(default_factory=dict)
    readiness_status: IntegrationReadinessStatus = IntegrationReadinessStatus.UNKNOWN
    owner: str | None = None

    @field_validator("frontend_safe_config")
    @classmethod
    def _reject_secret_marked_frontend_config(cls, value: dict[str, Any]) -> dict[str, Any]:
        secret_terms = ("secret", "password", "credential", "token", "private")
        unsafe_keys = [
            key
            for key in value
            if any(term in str(key).lower() for term in secret_terms)
        ]
        if unsafe_keys:
            raise ValueError("frontend_safe_config must not include secret-sensitive keys")
        return value


class RiskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    severity: str = "medium"
    affected_refs: list[str] = Field(default_factory=list)
    mitigation: str | None = None


class RiskReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risks: list[RiskItem] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)
    blocked_paths: list[str] = Field(default_factory=list)
    secret_sensitive_paths: list[str] = Field(default_factory=list)
    stale_context_warnings: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


class AdoptionPhase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    goals: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    required_human_decisions: list[str] = Field(default_factory=list)


class AdoptionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommended_path: AdoptionPath
    phases: list[AdoptionPhase] = Field(default_factory=list)
    candidate_overlays: list[str] = Field(default_factory=list)
    candidate_adapters: list[str] = Field(default_factory=list)
    candidate_migrations: list[str] = Field(default_factory=list)
    human_decisions_required: list[str] = Field(default_factory=list)
    not_in_scope: list[str] = Field(default_factory=list)


class ApplicationInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    stacks: list[str] = Field(default_factory=list)
    services: list[SurfaceRef] = Field(default_factory=list)
    routes: list[SurfaceRef] = Field(default_factory=list)
    api_endpoints: list[SurfaceRef] = Field(default_factory=list)
    pages: list[SurfaceRef] = Field(default_factory=list)
    components: list[SurfaceRef] = Field(default_factory=list)
    data_entities: list[SurfaceRef] = Field(default_factory=list)
    modules: list[SurfaceRef] = Field(default_factory=list)
    workflows: list[SurfaceRef] = Field(default_factory=list)
    integrations: list[IntegrationInventory] = Field(default_factory=list)
    deployment_boundaries: list[SurfaceRef] = Field(default_factory=list)
    ci_boundaries: list[SurfaceRef] = Field(default_factory=list)
    unknowns: list[UnknownItem] = Field(default_factory=list)


class AppContextSurfaceIndexes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routes: list[SurfaceRef] = Field(default_factory=list)
    pages: list[SurfaceRef] = Field(default_factory=list)
    components: list[SurfaceRef] = Field(default_factory=list)
    api_endpoints: list[SurfaceRef] = Field(default_factory=list)
    data_entities: list[SurfaceRef] = Field(default_factory=list)
    integrations: list[IntegrationInventory] = Field(default_factory=list)
    modules: list[SurfaceRef] = Field(default_factory=list)
    workflows: list[SurfaceRef] = Field(default_factory=list)
    events: list[SurfaceRef] = Field(default_factory=list)
    deployment_boundaries: list[SurfaceRef] = Field(default_factory=list)


class AppContextGraphNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    node_type: GraphNodeType
    label: str | None = None
    source_ref_id: str | None = None
    artifact_version_id: str | None = None
    checksum: str | None = None
    stale_status: AppContextStaleStatus = AppContextStaleStatus.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppContextGraphEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str | None = None
    edge_type: GraphEdgeType
    source_node_id: str = Field(min_length=1)
    target_node_id: str = Field(min_length=1)
    source_ref_id: str | None = None
    artifact_version_id: str | None = None
    checksum: str | None = None
    stale_status: AppContextStaleStatus = AppContextStaleStatus.UNKNOWN
    metadata: dict[str, Any] = Field(default_factory=dict)


class AppContextGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    graph_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    indexed_at: datetime = Field(default_factory=_utc_now)
    nodes: list[AppContextGraphNode] = Field(default_factory=list)
    edges: list[AppContextGraphEdge] = Field(default_factory=list)
    stale_status: AppContextStaleStatus = AppContextStaleStatus.UNKNOWN
    checksum: str | None = None
    graph_hash: str | None = None

    @model_validator(mode="after")
    def _validate_graph_references(self) -> AppContextGraph:
        node_ids = [node.node_id for node in self.nodes]
        duplicate_node_ids = {node_id for node_id in node_ids if node_ids.count(node_id) > 1}
        if duplicate_node_ids:
            raise ValueError("AppContextGraph node_id values must be unique")

        known_node_ids = set(node_ids)
        dangling_edges = [
            edge
            for edge in self.edges
            if edge.source_node_id not in known_node_ids
            or edge.target_node_id not in known_node_ids
        ]
        if dangling_edges:
            raise ValueError("AppContextGraph edges must reference existing node_id values")
        return self


class AppContextVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context_version_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    mode: AppContextMode
    source_refs: list[SourceRef] = Field(default_factory=list)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    graph_snapshot_ref: str | None = None
    ownership_boundaries: list[OwnershipBoundary] = Field(default_factory=list)
    surface_indexes: AppContextSurfaceIndexes = Field(default_factory=AppContextSurfaceIndexes)
    indexed_at: datetime = Field(default_factory=_utc_now)
    stale_status: AppContextStaleStatus = AppContextStaleStatus.UNKNOWN
    stale_reasons: list[str] = Field(default_factory=list)
    validation_summary: ValidationSummary = Field(default_factory=ValidationSummary)
    review_state: AppContextReviewState = AppContextReviewState.DRAFT
    promotion_state: AppContextPromotionState = AppContextPromotionState.NOT_PROMOTED


class BrownfieldRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registration_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    context_version_id: str = Field(min_length=1)
    scan_policy_ref: str | None = None
    status: BrownfieldRegistrationStatus = BrownfieldRegistrationStatus.PENDING
    created_at: datetime = Field(default_factory=_utc_now)
