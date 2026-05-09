from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, Optional


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
    change_request_id: Optional[str] = None
    scope: Optional[str] = None
    origin_workflow: Optional[str] = None
    target_workflow: Optional[str] = None
    from_version_refs: Dict[str, str] = field(default_factory=dict)
    to_version_refs: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PendingDecisionAction:
    action_id: str
    label: str
    action_type: str = "run_workflow"
    workflow_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingHarnessDecision:
    decision_id: str
    decision_type: str
    message: str
    rationale: str
    confidence: float = 0.0
    recommended_workflow_id: Optional[str] = None
    selected_paths: list[str] = field(default_factory=list)
    clarification_question: Optional[str] = None
    change_request_id: Optional[str] = None
    revision_id: Optional[str] = None
    requires_confirmation: bool = False
    trigger_source: str = "refinement"
    requested_workflow_id: Optional[str] = None
    journey_id: Optional[str] = None
    context_variables: Dict[str, Any] = field(default_factory=dict)
    trigger_payload: Dict[str, Any] = field(default_factory=dict)
    actions: list[PendingDecisionAction] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class SessionState:
    session_id: str
    app_id: str
    user_id: str
    sequence_status: SequenceStatus = SequenceStatus.IN_PROGRESS
    sequence_completed_at: Optional[datetime] = None
    active_revision_id: Optional[str] = None
    active_change_request_id: Optional[str] = None
    current_revision_scope: Optional[str] = None
    revision_origin_workflow: Optional[str] = None
    restart_from_workflow: Optional[str] = None
    lifecycle_state: SessionLifecycle = SessionLifecycle.INITIAL
    current_workflow_id: Optional[str] = None
    current_chat_id: Optional[str] = None
    journey_instance_id: Optional[str] = None
    journey_key: Optional[str] = None
    journey_position: int = 0
    journey_total_steps: int = 0
    pending_transition_id: Optional[str] = None
    pending_harness_decision: Optional[PendingHarnessDecision] = None
    last_trigger_source: Optional[str] = None
    last_requested_workflow_id: Optional[str] = None
    last_route_explanation: Optional[str] = None
    artifact_version_refs: Dict[str, str] = field(default_factory=dict)
    stale_layers: Dict[str, str] = field(default_factory=dict)
    revision_history: list[RevisionEntry] = field(default_factory=list)
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class TriggerInput:
    app_id: str
    user_id: str
    trigger_source: str
    workflow_id: Optional[str] = None
    journey_id: Optional[str] = None
    context_variables: Dict[str, Any] = field(default_factory=dict)
    trigger_payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    workflow_id: str
    requested_workflow_id: Optional[str]
    journey_id: Optional[str] = None
    context_seed: Dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    is_full_restart: bool = False
    rerouted_by_dependency: bool = False
    unmet_dependency: Optional[UnmetDependency] = None
    lifecycle_state: SessionLifecycle = SessionLifecycle.ACTIVE


@dataclass
class TransitionResolution:
    resolution_type: str  # "transition" | "workflow"
    transition_id: str
    target_id: str
    route_type: str  # "transition" | "workflow"
    journey_id: Optional[str] = None
    context_seed: Dict[str, Any] = field(default_factory=dict)
    option_id: Optional[str] = None
    routing_decision: Optional[RoutingDecision] = None


@dataclass
class JourneyAdvanceDecision:
    journey_instance_id: str
    journey_key: str
    current_group_index: int
    journey_total_steps: int
    next_group_index: Optional[int] = None
    next_workflows: list[str] = field(default_factory=list)
    next_transition_id: Optional[str] = None
    context_seed: Dict[str, Any] = field(default_factory=dict)
    completed: bool = False
