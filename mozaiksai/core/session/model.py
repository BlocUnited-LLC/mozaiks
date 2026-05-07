from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, Optional


class SessionLifecycle(str, Enum):
    INITIAL = "initial"
    ACTIVE = "active"
    AWAITING_TRANSITION = "awaiting_transition"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    STALE = "stale"


@dataclass
class UnmetDependency:
    workflow_id: str
    blocked_workflow_id: str
    reason: str
    scope: str = "app"


@dataclass
class SessionState:
    session_id: str
    app_id: str
    user_id: str
    lifecycle_state: SessionLifecycle = SessionLifecycle.INITIAL
    current_workflow_id: Optional[str] = None
    current_chat_id: Optional[str] = None
    journey_instance_id: Optional[str] = None
    journey_key: Optional[str] = None
    journey_position: int = 0
    journey_total_steps: int = 0
    pending_transition_id: Optional[str] = None
    pending_approval_id: Optional[str] = None
    last_trigger_source: Optional[str] = None
    last_requested_workflow_id: Optional[str] = None
    last_route_explanation: Optional[str] = None
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
