from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from logs.logging_config import get_core_logger

from mozaiksai.core.data.persistence.persistence_manager import AG2PersistenceManager
from mozaiksai.core.workflow.pack.config import get_transition, load_global_pack_graph
from mozaiksai.core.workflow.pack.schema import WorkflowTransition

from .model import RoutingDecision, TriggerInput

logger = get_core_logger("session_launcher")
_PERSISTENCE_MANAGER = AG2PersistenceManager()


@dataclass(slots=True)
class PreparedWorkflowLaunch:
    app_id: str
    user_id: str
    trigger_source: str
    workflow_id: str
    requested_workflow_id: Optional[str]
    validated_context: Dict[str, Any]
    trigger_meta: Dict[str, Any]
    routing_decision: RoutingDecision
    session_router: Any
    journey_id: Optional[str] = None


@dataclass(slots=True)
class WorkflowLaunchResult:
    chat_id: str
    app_id: str
    user_id: str
    workflow_id: str
    requested_workflow_id: Optional[str]
    websocket_url: str
    trigger_source: str
    routing_explanation: str
    rerouted_by_dependency: bool
    validated_context: Dict[str, Any]
    trigger_meta: Dict[str, Any]
    routing_decision: RoutingDecision


@dataclass(slots=True)
class TransitionLaunchResult:
    resolution_type: str
    transition_id: str
    option_id: Optional[str]
    context_variables: Dict[str, Any]
    next_transition_id: Optional[str] = None
    transition: Optional[WorkflowTransition] = None
    workflow_launch: Optional[WorkflowLaunchResult] = None


def validate_context_for_workflow(workflow_id: str, merged_context: Dict[str, Any]) -> Dict[str, Any]:
    validated_context: Dict[str, Any] = {}
    if not merged_context:
        return validated_context

    try:
        from mozaiksai.core.workflow.workflow_manager import workflow_manager

        wf_cfg = workflow_manager.get_config(workflow_id) or {}
        declared_keys = set((wf_cfg.get("context_variables") or {}).get("definitions", {}).keys())
    except Exception:
        declared_keys = set()

    for key, value in merged_context.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if declared_keys and key not in declared_keys:
            logger.warning("SESSION_LAUNCH_CONTEXT_KEY_REJECTED: key=%s workflow=%s", key, workflow_id)
            continue
        validated_context[key] = value
    return validated_context


async def create_routed_chat_session(
    *,
    workflow_id: str,
    app_id: str,
    user_id: str,
    context_variables: Dict[str, Any],
    trigger_meta: Dict[str, Any],
    session_router: Optional[Any] = None,
    journey_id: Optional[str] = None,
) -> str:
    chat_id = str(uuid4())
    extra_fields: Dict[str, Any] = {"trigger_meta": trigger_meta}
    extra_fields.update(context_variables)

    await _PERSISTENCE_MANAGER.create_chat_session(
        chat_id=chat_id,
        app_id=app_id,
        workflow_name=workflow_id,
        user_id=user_id,
        extra_fields=extra_fields or None,
    )

    if session_router is not None:
        try:
            await session_router.bind_workflow_session(
                app_id=app_id,
                user_id=user_id,
                workflow_id=workflow_id,
                chat_id=chat_id,
                journey_id=journey_id,
            )
        except Exception as exc:
            logger.warning("Failed to bind SessionRouter chat session: %s", exc)
    return chat_id


def _build_trigger_meta(
    *,
    trigger_source: str,
    routing_decision: RoutingDecision,
    extra_trigger_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    trigger_meta: Dict[str, Any] = {
        "trigger_source": trigger_source,
        "requested_workflow_id": routing_decision.requested_workflow_id,
        "resolved_workflow_id": routing_decision.workflow_id,
        "rerouted_by_dependency": bool(routing_decision.rerouted_by_dependency),
    }
    for key, value in (extra_trigger_meta or {}).items():
        if value is None:
            continue
        trigger_meta[key] = value
    return trigger_meta


async def prepare_routed_workflow_launch(
    *,
    workflow_id: Optional[str],
    app_id: str,
    user_id: str,
    trigger_source: str = "chat",
    context_variables: Optional[Dict[str, Any]] = None,
    trigger_payload: Optional[Dict[str, Any]] = None,
    session_router: Optional[Any] = None,
    extra_trigger_meta: Optional[Dict[str, Any]] = None,
) -> PreparedWorkflowLaunch:
    from .router import get_session_router

    router = session_router or get_session_router()
    routing_decision = await router.route_trigger(
        TriggerInput(
            app_id=app_id,
            user_id=user_id,
            trigger_source=trigger_source,
            workflow_id=workflow_id,
            context_variables=context_variables or {},
            trigger_payload=trigger_payload or {},
        )
    )
    resolved_workflow_id = routing_decision.workflow_id
    merged_context = {**dict(routing_decision.context_seed), **dict(context_variables or {})}
    validated_context = validate_context_for_workflow(resolved_workflow_id, merged_context)
    trigger_meta = _build_trigger_meta(
        trigger_source=trigger_source,
        routing_decision=routing_decision,
        extra_trigger_meta=extra_trigger_meta,
    )
    return PreparedWorkflowLaunch(
        app_id=app_id,
        user_id=user_id,
        trigger_source=trigger_source,
        workflow_id=resolved_workflow_id,
        requested_workflow_id=routing_decision.requested_workflow_id,
        validated_context=validated_context,
        trigger_meta=trigger_meta,
        routing_decision=routing_decision,
        session_router=router,
    )


async def launch_prepared_workflow(launch: PreparedWorkflowLaunch) -> WorkflowLaunchResult:
    chat_id = await create_routed_chat_session(
        workflow_id=launch.workflow_id,
        app_id=launch.app_id,
        user_id=launch.user_id,
        context_variables=launch.validated_context,
        trigger_meta=launch.trigger_meta,
        session_router=launch.session_router,
        journey_id=launch.journey_id,
    )
    return WorkflowLaunchResult(
        chat_id=chat_id,
        app_id=launch.app_id,
        user_id=launch.user_id,
        workflow_id=launch.workflow_id,
        requested_workflow_id=launch.requested_workflow_id,
        websocket_url=f"/ws/{launch.workflow_id}/{launch.app_id}/{chat_id}/{launch.user_id}",
        trigger_source=launch.trigger_source,
        routing_explanation=launch.routing_decision.explanation,
        rerouted_by_dependency=bool(launch.routing_decision.rerouted_by_dependency),
        validated_context=dict(launch.validated_context),
        trigger_meta=dict(launch.trigger_meta),
        routing_decision=launch.routing_decision,
    )


async def launch_routed_workflow(
    *,
    workflow_id: Optional[str],
    app_id: str,
    user_id: str,
    trigger_source: str = "chat",
    context_variables: Optional[Dict[str, Any]] = None,
    trigger_payload: Optional[Dict[str, Any]] = None,
    session_router: Optional[Any] = None,
    extra_trigger_meta: Optional[Dict[str, Any]] = None,
) -> WorkflowLaunchResult:
    launch = await prepare_routed_workflow_launch(
        workflow_id=workflow_id,
        app_id=app_id,
        user_id=user_id,
        trigger_source=trigger_source,
        context_variables=context_variables,
        trigger_payload=trigger_payload,
        session_router=session_router,
        extra_trigger_meta=extra_trigger_meta,
    )
    return await launch_prepared_workflow(launch)


async def launch_transition(
    *,
    app_id: str,
    user_id: str,
    transition_id: str,
    option_id: Optional[str] = None,
    context_variables: Optional[Dict[str, Any]] = None,
    session_router: Optional[Any] = None,
    extra_trigger_meta: Optional[Dict[str, Any]] = None,
) -> TransitionLaunchResult:
    from .router import get_session_router

    router = session_router or get_session_router()
    resolution = await router.resolve_transition(
        app_id=app_id,
        user_id=user_id,
        transition_id=transition_id,
        option_id=option_id,
        context_seed=context_variables or {},
    )

    if resolution.resolution_type == "transition":
        pack = load_global_pack_graph()
        next_transition = get_transition(pack, resolution.target_id) if pack is not None else None
        if next_transition is None:
            raise ValueError(
                f"Transition '{resolution.target_id}' could not be loaded after resolution"
            )
        return TransitionLaunchResult(
            resolution_type="transition",
            transition_id=transition_id,
            option_id=resolution.option_id,
            next_transition_id=resolution.target_id,
            transition=next_transition,
            context_variables=dict(resolution.context_seed),
        )

    route_decision = resolution.routing_decision
    if route_decision is None:
        raise ValueError("Workflow transition resolution is missing routing decision")

    workflow_launch = await launch_routed_workflow(
        workflow_id=route_decision.requested_workflow_id,
        app_id=app_id,
        user_id=user_id,
        trigger_source="transition",
        context_variables=dict(resolution.context_seed),
        session_router=router,
        extra_trigger_meta={
            "transition_id": transition_id,
            "option_id": resolution.option_id,
            **(extra_trigger_meta or {}),
        },
    )
    return TransitionLaunchResult(
        resolution_type="workflow",
        transition_id=transition_id,
        option_id=resolution.option_id,
        context_variables=dict(resolution.context_seed),
        workflow_launch=workflow_launch,
    )


async def emit_workflow_launch_navigation(
    *,
    source_chat_id: Optional[str],
    workflow_launch: WorkflowLaunchResult,
) -> bool:
    chat_id = str(source_chat_id or "").strip()
    if not chat_id:
        return False

    try:
        from mozaiksai.core.transport.simple_transport import SimpleTransport

        transport = await SimpleTransport.get_instance()
        await transport.send_event_to_ui(
            {
                "type": "chat.navigate",
                "data": {
                    "chat_id": workflow_launch.chat_id,
                    "workflow_name": workflow_launch.workflow_id,
                    "requested_workflow_name": workflow_launch.requested_workflow_id
                    or workflow_launch.workflow_id,
                    "app_id": workflow_launch.app_id,
                    "websocket_url": workflow_launch.websocket_url,
                },
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            chat_id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "Failed to emit workflow launch navigation from source chat %s to launched chat %s: %s",
            chat_id,
            workflow_launch.chat_id,
            exc,
        )
        return False


__all__ = [
    "emit_workflow_launch_navigation",
    "PreparedWorkflowLaunch",
    "TransitionLaunchResult",
    "WorkflowLaunchResult",
    "create_routed_chat_session",
    "launch_prepared_workflow",
    "launch_routed_workflow",
    "launch_transition",
    "prepare_routed_workflow_launch",
    "validate_context_for_workflow",
]