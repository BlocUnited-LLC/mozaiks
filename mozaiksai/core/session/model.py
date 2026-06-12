from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class SessionLifecycle(str, Enum):
    INITIAL = "initial"
    ACTIVE = "active"
    AWAITING_TRANSITION = "awaiting_transition"
    AWAITING_DECISION = "awaiting_decision"
    COMPLETED = "completed"
    STALE = "stale"


class SequenceStatus(str, Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    STALE = "stale"
    REVISING = "revising"


@dataclass
class UnmetDependency:
    workflow_id: str
    blocked_workflow_id: str
    reason: str
    scope: str = "app"


@dataclass
class RevisionEntry:
    revision_id: str
    change_request_id: str | None = None
    scope: str | None = None
    origin_workflow: str | None = None
    target_workflow: str | None = None
    from_version_refs: dict[str, str] = field(default_factory=dict)
    to_version_refs: dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PendingDecisionAction:
    action_id: str
    label: str
    action_type: str = "run_workflow"
    workflow_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingHarnessDecision:
    decision_id: str
    decision_type: str
    message: str
    rationale: str
    confidence: float = 0.0
    recommended_workflow_id: str | None = None
    selected_paths: list[str] = field(default_factory=list)
    clarification_question: str | None = None
    change_request_id: str | None = None
    revision_id: str | None = None
    requires_confirmation: bool = False
    trigger_source: str = "refinement"
    requested_workflow_id: str | None = None
    journey_id: str | None = None
    context_variables: dict[str, Any] = field(default_factory=dict)
    trigger_payload: dict[str, Any] = field(default_factory=dict)
    actions: list[PendingDecisionAction] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SessionState:
    session_id: str
    app_id: str
    user_id: str
    sequence_status: SequenceStatus = SequenceStatus.IN_PROGRESS
    sequence_completed_at: datetime | None = None
    active_revision_id: str | None = None
    active_change_request_id: str | None = None
    current_revision_scope: str | None = None
    revision_origin_workflow: str | None = None
    restart_from_workflow: str | None = None
    lifecycle_state: SessionLifecycle = SessionLifecycle.INITIAL
    current_workflow_id: str | None = None
    current_chat_id: str | None = None
    journey_instance_id: str | None = None
    journey_key: str | None = None
    journey_position: int = 0
    journey_total_steps: int = 0
    pending_transition_id: str | None = None
    pending_harness_decision: PendingHarnessDecision | None = None
    last_trigger_source: str | None = None
    last_requested_workflow_id: str | None = None
    last_route_explanation: str | None = None
    artifact_version_refs: dict[str, str] = field(default_factory=dict)
    stale_layers: dict[str, str] = field(default_factory=dict)
    revision_history: list[RevisionEntry] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TriggerInput:
    app_id: str
    user_id: str
    trigger_source: str
    workflow_id: str | None = None
    journey_id: str | None = None
    context_variables: dict[str, Any] = field(default_factory=dict)
    trigger_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    workflow_id: str
    requested_workflow_id: str | None
    journey_id: str | None = None
    context_seed: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    is_full_restart: bool = False
    rerouted_by_dependency: bool = False
    unmet_dependency: UnmetDependency | None = None
    lifecycle_state: SessionLifecycle = SessionLifecycle.ACTIVE


@dataclass
class TransitionResolution:
    resolution_type: str  # "transition" | "workflow"
    transition_id: str
    target_id: str
    route_type: str  # "transition" | "workflow"
    journey_id: str | None = None
    context_seed: dict[str, Any] = field(default_factory=dict)
    option_id: str | None = None
    routing_decision: RoutingDecision | None = None


@dataclass
class JourneyAdvanceDecision:
    journey_instance_id: str
    journey_key: str
    current_group_index: int
    journey_total_steps: int
    next_group_index: int | None = None
    next_workflows: list[str] = field(default_factory=list)
    next_transition_id: str | None = None
    context_seed: dict[str, Any] = field(default_factory=dict)
    completed: bool = False
