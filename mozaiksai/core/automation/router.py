from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

from mozaiksai.core.automation.config import load_automation_config, reload_automation_config
from mozaiksai.core.automation.contracts import (
    AutomationConfigBundle,
    AutomationDecision,
    AutomationDecisionStatus,
    AutomationEffectKind,
    AutomationRoute,
    SubstrateEventEnvelope,
)
from mozaiksai.core.orchestration.universal_orchestrator import (
    RouteResult,
    UniversalOrchestrator,
    get_universal_orchestrator,
)

logger = logging.getLogger("mozaiksai.automation.router")

_TEMPLATE_PATTERN = re.compile(r"\{([^{}]+)\}")


def _lookup_path(data: Dict[str, Any], path: str) -> Any:
    current: Any = data
    for segment in str(path or "").split("."):
        segment = segment.strip()
        if not segment:
            continue
        if not isinstance(current, dict) or segment not in current:
            return None
        current = current.get(segment)
    return current


def _render_template(template: str, event_data: Dict[str, Any]) -> str:
    def replacer(match: re.Match[str]) -> str:
        value = _lookup_path(event_data, match.group(1).strip())
        return "" if value is None else str(value)

    return _TEMPLATE_PATTERN.sub(replacer, template)


class AutomationRouter:
    def __init__(self, config: Optional[AutomationConfigBundle] = None) -> None:
        self._config = config or load_automation_config()
        self._known_event_types = {entry.event_type for entry in self._config.events}

    def reload_from_disk(self) -> AutomationConfigBundle:
        self._config = reload_automation_config()
        self._known_event_types = {entry.event_type for entry in self._config.events}
        return self._config

    def known_event_types(self) -> set[str]:
        return set(self._known_event_types)

    def _route_matches(self, route: AutomationRoute, event_data: Dict[str, Any]) -> bool:
        if not route.enabled or route.event_type != event_data.get("event_type"):
            return False
        for path, expected in route.when.items():
            actual = _lookup_path(event_data, path)
            if actual != expected:
                return False
        return True

    def _build_dispatch_payload(
        self,
        route: AutomationRoute,
        event: SubstrateEventEnvelope,
        event_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "event_type": event.event_type,
            "workflow_name": route.effect.workflow,
            "automation_route_id": route.route_id,
            "automation_event": event_data,
        }

        for key, source_path in route.bindings.items():
            value = _lookup_path(event_data, source_path)
            if value is not None:
                payload[key] = value

        payload.setdefault("app_id", _lookup_path(event_data, "tenant.app_id"))
        payload.setdefault("user_id", _lookup_path(event_data, "tenant.user_id"))
        payload.setdefault("chat_id", _lookup_path(event_data, "tenant.chat_id"))

        if route.effect.message_template:
            rendered = _render_template(route.effect.message_template, event_data).strip()
            if rendered:
                payload["message"] = rendered

        if "message" not in payload:
            message = _lookup_path(event_data, "payload.message")
            if message is None:
                message = _lookup_path(event_data, "payload.text")
            if isinstance(message, str) and message.strip():
                payload["message"] = message.strip()

        return payload

    def evaluate(self, event: SubstrateEventEnvelope) -> AutomationDecision:
        if event.event_type not in self._known_event_types:
            return AutomationDecision(
                status=AutomationDecisionStatus.INVALID,
                detail={"reason": f"unknown event_type '{event.event_type}'"},
            )

        event_data = event.model_dump(mode="json")
        for route in self._config.routes:
            if not self._route_matches(route, event_data):
                continue

            effect = route.effect
            if effect.kind is AutomationEffectKind.NONE:
                return AutomationDecision(
                    status=AutomationDecisionStatus.IGNORED,
                    route_id=route.route_id,
                    detail={"reason": "effect.kind=none"},
                )

            if effect.kind not in {
                AutomationEffectKind.WORKFLOW_RUN,
                AutomationEffectKind.WORKFLOW_RESUME,
            }:
                return AutomationDecision(
                    status=AutomationDecisionStatus.INVALID,
                    route_id=route.route_id,
                    detail={"reason": f"unsupported effect.kind '{effect.kind.value}'"},
                )

            route_target = f"{effect.kind.value}:{effect.workflow}"
            return AutomationDecision(
                status=AutomationDecisionStatus.MATCHED,
                route_id=route.route_id,
                route=route_target,
                payload=self._build_dispatch_payload(route, event, event_data),
                detail={"surface": effect.surface or "background"},
            )

        return AutomationDecision(
            status=AutomationDecisionStatus.IGNORED,
            detail={"reason": f"no automation route matched '{event.event_type}'"},
        )

    async def dispatch(
        self,
        event: SubstrateEventEnvelope,
        *,
        orchestrator: Optional[UniversalOrchestrator] = None,
    ) -> Tuple[AutomationDecision, Optional[RouteResult]]:
        decision = self.evaluate(event)
        if decision.status is not AutomationDecisionStatus.MATCHED or not decision.route:
            return decision, None

        resolved_orchestrator = orchestrator or get_universal_orchestrator()
        result = await resolved_orchestrator.dispatch_route(
            route=decision.route,
            payload=decision.payload,
        )
        return decision, result


_automation_router: Optional[AutomationRouter] = None


def get_automation_router() -> AutomationRouter:
    global _automation_router
    if _automation_router is None:
        _automation_router = AutomationRouter()
    return _automation_router


def reload_automation_router() -> AutomationRouter:
    global _automation_router
    _automation_router = AutomationRouter(reload_automation_config())
    return _automation_router


__all__ = [
    "AutomationRouter",
    "get_automation_router",
    "reload_automation_router",
]
