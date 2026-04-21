# ==============================================================================
# FILE: mozaiksai/core/events/ag2_events.py
# DESCRIPTION: AG2-native custom events for mozaiksai runtime
# ==============================================================================

"""
AG2-Native Custom Events for mozaiksai

This module defines mozaiksai domain events using AG2's native event system.
These events flow through AG2's IOStream and can be yielded via a_run_group_chat_iter().

Architecture:
    1. Events defined here use @wrap_event (AG2's event decorator)
    2. Events are emitted via IOStream.get_default().send()
    3. Events are consumed via yield_on=[...] in a_run_group_chat_iter()
    4. EventStreamProcessor routes them to UnifiedEventDispatcher

Forward Compatibility (AG2 Beta):
    - AG2 beta uses similar patterns: BaseEvent + send() + subscribe()
    - When migrating to beta, these events become Stream subscribers
    - The event taxonomy stays the same; only the transport changes

Event Taxonomy (aligned with docs/architecture/foundations/event-system.md):
    - App domain events: business facts from app backend
    - Automation control events: AI runtime decisions
    - Workflow runtime events: live execution stream
"""

from typing import Any, Dict, Optional

from autogen.events.base_event import BaseEvent, wrap_event
from autogen.io.base import IOStream


# =============================================================================
# AUTOMATION CONTROL EVENTS
# These describe AI runtime decisions (not business facts)
# =============================================================================

@wrap_event
class WorkflowTriggeredEvent(BaseEvent):
    """Emitted when a domain event triggers a workflow."""
    workflow_name: str
    trigger_event: str  # The domain event that triggered this
    chat_id: str
    app_id: str
    user_id: Optional[str] = None

    def print(self, f=None):
        print(f"[WORKFLOW_TRIGGERED] {self.workflow_name} triggered by {self.trigger_event}", file=f)


@wrap_event
class HandoffRequestedEvent(BaseEvent):
    """Emitted when an agent requests handoff to another agent or user."""
    from_agent: str
    to_agent: str  # Can be "user" for user handoff
    reason: str
    chat_id: str
    context_snapshot: Dict[str, Any] = None

    def print(self, f=None):
        print(f"[HANDOFF] {self.from_agent} -> {self.to_agent}: {self.reason}", file=f)


@wrap_event
class PlanCreatedEvent(BaseEvent):
    """Emitted when the AI creates an execution plan."""
    plan_id: str
    workflow_name: str
    chat_id: str
    steps: list  # List of planned step descriptions
    estimated_turns: int = 0

    def print(self, f=None):
        print(f"[PLAN_CREATED] {self.plan_id} with {len(self.steps)} steps", file=f)


@wrap_event
class PrerequisitesRequiredEvent(BaseEvent):
    """Emitted when workflow execution requires user input or external data."""
    workflow_name: str
    chat_id: str
    required_inputs: list  # List of required input names
    blocking: bool = True

    def print(self, f=None):
        print(f"[PREREQUISITES] {self.workflow_name} needs: {self.required_inputs}", file=f)


# =============================================================================
# WORKFLOW RUNTIME EVENTS
# These describe live execution state (not stored as business facts)
# =============================================================================

@wrap_event
class AgentThinkingEvent(BaseEvent):
    """Emitted when an agent starts processing (for UI thinking indicators)."""
    agent_name: str
    chat_id: str
    thinking_type: str = "default"  # "default", "tool_call", "handoff"

    def print(self, f=None):
        print(f"[THINKING] {self.agent_name} is thinking ({self.thinking_type})", file=f)


@wrap_event
class StructuredOutputEvent(BaseEvent):
    """Emitted when an agent produces validated structured output."""
    agent_name: str
    chat_id: str
    output_type: str  # Schema name from structured_outputs.yaml
    output_data: Dict[str, Any]
    validation_passed: bool = True

    def print(self, f=None):
        status = "valid" if self.validation_passed else "INVALID"
        print(f"[STRUCTURED_OUTPUT] {self.agent_name} produced {status} {self.output_type}", file=f)


@wrap_event
class DecompositionPlannedEvent(BaseEvent):
    """Emitted when a workflow-local decomposition plan is ready for MFJ fan-out."""
    agent_name: str
    chat_id: str
    workflow_name: str
    model_name: str
    structured_data: Dict[str, Any]
    context: Dict[str, Any]

    def print(self, f=None):
        workflow_count = len(self.structured_data.get("workflows", []) or [])
        print(
            f"[DECOMPOSITION] {self.agent_name} planned {workflow_count} workflow(s) for {self.workflow_name}",
            file=f,
        )


@wrap_event
class ArtifactUpdatedEvent(BaseEvent):
    """Emitted when a workflow artifact is created or updated."""
    artifact_id: str
    artifact_type: str  # "document", "image", "code", etc.
    chat_id: str
    workflow_name: str
    action: str = "updated"  # "created", "updated", "deleted"
    artifact_version_id: Optional[str] = None

    def print(self, f=None):
        print(f"[ARTIFACT] {self.artifact_type} {self.artifact_id} {self.action}", file=f)


@wrap_event
class ArtifactReadyEvent(BaseEvent):
    """Emitted when a workflow artifact version is validated and ready for downstream consumers."""
    artifact_id: str
    artifact_type: str
    chat_id: str
    workflow_name: str
    artifact_version_id: Optional[str] = None

    def print(self, f=None):
        print(f"[ARTIFACT_READY] {self.artifact_type} {self.artifact_id}", file=f)


@wrap_event
class UIToolRequestedEvent(BaseEvent):
    """Emitted when an agent requests a UI component to be rendered."""
    ui_tool_id: str
    agent_name: str
    chat_id: str
    workflow_name: str
    display_mode: str = "inline"  # "inline", "modal", "artifact"
    payload: Dict[str, Any] = None

    def print(self, f=None):
        print(f"[UI_TOOL] {self.agent_name} requests {self.ui_tool_id} ({self.display_mode})", file=f)


@wrap_event
class ContextUpdatedEvent(BaseEvent):
    """Emitted when workflow context variables change."""
    chat_id: str
    variable_name: str
    old_value: Any = None
    new_value: Any = None
    source: str = "agent"  # "agent", "tool", "user", "system"

    def print(self, f=None):
        print(f"[CONTEXT] {self.variable_name} updated by {self.source}", file=f)


# =============================================================================
# MID-FLIGHT JOURNEY EVENTS
# These track child workflow orchestration
# =============================================================================

@wrap_event
class JourneyStartedEvent(BaseEvent):
    """Emitted when a mid-flight journey (child workflow) begins."""
    parent_chat_id: str
    child_chat_id: str
    child_workflow_name: str
    decomposition_agent: str

    def print(self, f=None):
        print(f"[MFJ_START] {self.child_workflow_name} spawned from {self.parent_chat_id}", file=f)


@wrap_event
class JourneyCompletedEvent(BaseEvent):
    """Emitted when a mid-flight journey completes and returns to parent."""
    parent_chat_id: str
    child_chat_id: str
    child_workflow_name: str
    result_summary: Optional[str] = None

    def print(self, f=None):
        print(f"[MFJ_COMPLETE] {self.child_workflow_name} returned to parent", file=f)


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def emit_ag2_event(event: BaseEvent) -> bool:
    """
    Emit an AG2-native event via IOStream.

    Events emitted here flow through AG2's event system and can be:
    1. Yielded via yield_on=[EventType] in a_run_group_chat_iter()
    2. Observed by any IOStream subscriber

    Returns True if event was sent, False if no default IOStream exists.
    """
    stream = IOStream.get_default()
    if stream is None:
        return False

    stream.send(event)
    return True


def emit_handoff_requested(
    from_agent: str,
    to_agent: str,
    reason: str,
    chat_id: str,
    context_snapshot: Optional[Dict[str, Any]] = None,
) -> bool:
    """Convenience function to emit handoff request."""
    return emit_ag2_event(HandoffRequestedEvent(
        from_agent=from_agent,
        to_agent=to_agent,
        reason=reason,
        chat_id=chat_id,
        context_snapshot=context_snapshot or {},
    ))


def emit_structured_output(
    agent_name: str,
    chat_id: str,
    output_type: str,
    output_data: Dict[str, Any],
    validation_passed: bool = True,
) -> bool:
    """Convenience function to emit validated structured output."""
    return emit_ag2_event(StructuredOutputEvent(
        agent_name=agent_name,
        chat_id=chat_id,
        output_type=output_type,
        output_data=output_data,
        validation_passed=validation_passed,
    ))


def emit_decomposition_planned(
    agent_name: str,
    chat_id: str,
    workflow_name: str,
    model_name: str,
    structured_data: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> bool:
    """Convenience function to emit an explicit decomposition checkpoint."""
    return emit_ag2_event(DecompositionPlannedEvent(
        agent_name=agent_name,
        chat_id=chat_id,
        workflow_name=workflow_name,
        model_name=model_name,
        structured_data=structured_data,
        context=context or {},
    ))


def emit_ui_tool_requested(
    ui_tool_id: str,
    agent_name: str,
    chat_id: str,
    workflow_name: str,
    display_mode: str = "inline",
    payload: Optional[Dict[str, Any]] = None,
) -> bool:
    """Convenience function to emit UI tool request."""
    return emit_ag2_event(UIToolRequestedEvent(
        ui_tool_id=ui_tool_id,
        agent_name=agent_name,
        chat_id=chat_id,
        workflow_name=workflow_name,
        display_mode=display_mode,
        payload=payload or {},
    ))


def emit_artifact_updated(
    artifact_id: str,
    artifact_type: str,
    chat_id: str,
    workflow_name: str,
    *,
    action: str = "updated",
    artifact_version_id: Optional[str] = None,
) -> bool:
    return emit_ag2_event(ArtifactUpdatedEvent(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        chat_id=chat_id,
        workflow_name=workflow_name,
        action=action,
        artifact_version_id=artifact_version_id,
    ))


def emit_artifact_ready(
    artifact_id: str,
    artifact_type: str,
    chat_id: str,
    workflow_name: str,
    *,
    artifact_version_id: Optional[str] = None,
) -> bool:
    return emit_ag2_event(ArtifactReadyEvent(
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        chat_id=chat_id,
        workflow_name=workflow_name,
        artifact_version_id=artifact_version_id,
    ))


# =============================================================================
# EVENT TYPE REGISTRY
# Used by a_run_group_chat_iter(yield_on=[...])
# =============================================================================

MOZAIKSAI_CONTROL_EVENTS = [
    WorkflowTriggeredEvent,
    HandoffRequestedEvent,
    PlanCreatedEvent,
    PrerequisitesRequiredEvent,
]

MOZAIKSAI_RUNTIME_EVENTS = [
    AgentThinkingEvent,
    StructuredOutputEvent,
    DecompositionPlannedEvent,
    ArtifactUpdatedEvent,
    ArtifactReadyEvent,
    UIToolRequestedEvent,
    ContextUpdatedEvent,
]

MOZAIKSAI_JOURNEY_EVENTS = [
    JourneyStartedEvent,
    JourneyCompletedEvent,
]

# All custom events for yield_on parameter
ALL_MOZAIKSAI_EVENTS = (
    MOZAIKSAI_CONTROL_EVENTS +
    MOZAIKSAI_RUNTIME_EVENTS +
    MOZAIKSAI_JOURNEY_EVENTS
)
