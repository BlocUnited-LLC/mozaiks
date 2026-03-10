from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, Optional

from logs.logging_config import get_core_logger
from mozaiksai.core.orchestration.change_classifier import ChangeType, get_change_classifier

logger = get_core_logger("universal_orchestrator")


# Canonical module-level defaults for route configuration. These remain empty
# until application startup wires concrete routes into the singleton instance.
EVENT_ROUTE_MAP: Dict[str, str] = {}
CHANGE_TYPE_ROUTE_MAP: Dict[ChangeType, str] = {}


@dataclass(frozen=True)
class RouteResult:
    status: str
    route: str
    detail: Optional[Dict[str, Any]] = None


RouteHandler = Callable[[Dict[str, Any]], Awaitable[RouteResult]]


class UniversalOrchestrator:
    """Runtime event router for structured and free-text triggers.

    Route maps are empty by default.  Call ``configure_routes`` at application
    startup (or load from a config file) to wire event types to workflow targets.
    Individual handlers can always be registered via ``register_handler``.
    """

    def __init__(self) -> None:
        self._handlers: Dict[str, RouteHandler] = {}
        # Keyed by raw event_type string → route target (e.g. "workflow.run:MyWorkflow")
        self._event_routes: Dict[str, str] = dict(EVENT_ROUTE_MAP)
        # Keyed by ChangeType → route target
        self._change_type_routes: Dict[ChangeType, str] = dict(CHANGE_TYPE_ROUTE_MAP)

    def configure_routes(
        self,
        *,
        event_routes: Optional[Dict[str, str]] = None,
        change_type_routes: Optional[Dict[ChangeType, str]] = None,
    ) -> None:
        """Merge event-to-route mappings into this orchestrator instance.

        Typical call-site: application startup or test setup.
        """
        if event_routes:
            self._event_routes.update(event_routes)
        if change_type_routes:
            self._change_type_routes.update(change_type_routes)

    def register_handler(self, route: str, handler: RouteHandler) -> None:
        self._handlers[str(route)] = handler

    async def handle_structured_event(self, payload: Dict[str, Any]) -> RouteResult:
        event_type = str(payload.get("event_type") or payload.get("type") or "").strip()
        if not event_type:
            return RouteResult(status="ignored", route="", detail={"reason": "missing event_type"})

        route = self._event_routes.get(event_type)
        if not route:
            return RouteResult(status="ignored", route="", detail={"reason": f"no route for {event_type}"})
        return await self._dispatch(route=route, payload=payload)

    async def handle_free_text_event(self, payload: Dict[str, Any]) -> RouteResult:
        text = str(payload.get("text") or payload.get("message") or "").strip()
        if not text:
            return RouteResult(status="ignored", route="", detail={"reason": "missing text"})

        classifier = get_change_classifier()
        classification = await classifier.classify(
            text=text,
            context=payload.get("context") if isinstance(payload.get("context"), dict) else None,
        )
        route = self._change_type_routes.get(
            classification.change_type,
            self._change_type_routes.get(ChangeType.UNKNOWN),
        )
        if not route:
            return RouteResult(
                status="ignored",
                route="",
                detail={"reason": f"no route configured for change_type {classification.change_type.value}"},
            )
        enriched = dict(payload)
        enriched["classification"] = {
            "change_type": classification.change_type.value,
            "rationale": classification.rationale,
            "confidence": classification.confidence,
        }
        return await self._dispatch(route=route, payload=enriched)

    async def handle_event(self, payload: Dict[str, Any]) -> RouteResult:
        if payload.get("event_type") or payload.get("type"):
            return await self.handle_structured_event(payload)
        return await self.handle_free_text_event(payload)

    async def _dispatch(self, *, route: str, payload: Dict[str, Any]) -> RouteResult:
        handler = self._handlers.get(route)
        if handler is not None:
            return await handler(payload)

        if route.startswith("workflow.run:"):
            workflow_name = route.split(":", 1)[1].strip()
            return await self._dispatch_workflow_run(workflow_name=workflow_name, payload=payload)

        if route.startswith("workflow.resume:"):
            workflow_name = route.split(":", 1)[1].strip()
            return await self._dispatch_workflow_resume(workflow_name=workflow_name, payload=payload)

        if route.startswith("process.run:"):
            return RouteResult(
                status="accepted",
                route=route,
                detail={
                    "kind": "process",
                    "target": route.split(":", 1)[1].strip(),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )

        return RouteResult(status="ignored", route=route, detail={"reason": "unhandled route"})

    async def _dispatch_workflow_run(self, *, workflow_name: str, payload: Dict[str, Any]) -> RouteResult:
        app_id = str(payload.get("app_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip()
        chat_id = str(payload.get("chat_id") or f"chat_{uuid.uuid4().hex[:12]}").strip()
        message = payload.get("message") if isinstance(payload.get("message"), str) else payload.get("text")
        override_workflow = str(payload.get("workflow_name") or "").strip()
        target_workflow = override_workflow or workflow_name

        if not target_workflow or not app_id or not user_id:
            return RouteResult(
                status="invalid",
                route=f"workflow.run:{workflow_name}",
                detail={"reason": "app_id, user_id, and workflow_name are required"},
            )

        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            if transport is None:
                return RouteResult(
                    status="failed",
                    route=f"workflow.run:{target_workflow}",
                    detail={"reason": "transport unavailable"},
                )

            await transport.handle_user_input_from_api(
                chat_id=chat_id,
                user_id=user_id,
                workflow_name=target_workflow,
                message=message if isinstance(message, str) else None,
                app_id=app_id,
                initial_agent_name_override=None,
            )
            return RouteResult(
                status="accepted",
                route=f"workflow.run:{target_workflow}",
                detail={"chat_id": chat_id, "workflow_name": target_workflow},
            )
        except Exception as exc:
            logger.warning("[UNIVERSAL] workflow.run dispatch failed route=%s: %s", target_workflow, exc)
            return RouteResult(
                status="failed",
                route=f"workflow.run:{target_workflow}",
                detail={"reason": str(exc)},
            )

    async def _dispatch_workflow_resume(self, *, workflow_name: str, payload: Dict[str, Any]) -> RouteResult:
        app_id = str(payload.get("app_id") or "").strip()
        user_id = str(payload.get("user_id") or "").strip()
        chat_id = str(payload.get("chat_id") or "").strip()
        message = payload.get("message") if isinstance(payload.get("message"), str) else payload.get("text")
        target_workflow = str(payload.get("workflow_name") or workflow_name).strip()

        if not chat_id or not app_id or not user_id or not target_workflow:
            return RouteResult(
                status="invalid",
                route=f"workflow.resume:{workflow_name}",
                detail={"reason": "chat_id, app_id, user_id, and workflow_name are required"},
            )

        try:
            from mozaiksai.core.transport.simple_transport import SimpleTransport

            transport = await SimpleTransport.get_instance()
            if transport is None:
                return RouteResult(
                    status="failed",
                    route=f"workflow.resume:{target_workflow}",
                    detail={"reason": "transport unavailable"},
                )

            await transport.handle_user_input_from_api(
                chat_id=chat_id,
                user_id=user_id,
                workflow_name=target_workflow,
                message=message if isinstance(message, str) else None,
                app_id=app_id,
                initial_agent_name_override=None,
            )
            return RouteResult(
                status="accepted",
                route=f"workflow.resume:{target_workflow}",
                detail={"chat_id": chat_id, "workflow_name": target_workflow},
            )
        except Exception as exc:
            logger.warning("[UNIVERSAL] workflow.resume dispatch failed route=%s: %s", target_workflow, exc)
            return RouteResult(
                status="failed",
                route=f"workflow.resume:{target_workflow}",
                detail={"reason": str(exc)},
            )


_orchestrator: Optional[UniversalOrchestrator] = None


def get_universal_orchestrator() -> UniversalOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = UniversalOrchestrator()
    return _orchestrator


__all__ = [
    "EVENT_ROUTE_MAP",
    "CHANGE_TYPE_ROUTE_MAP",
    "RouteResult",
    "UniversalOrchestrator",
    "get_universal_orchestrator",
]
