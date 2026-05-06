from __future__ import annotations

"""Builder-session orchestration control harness.

This is the control-plane entrypoint for build-affecting requests that arrive
through Studio or other builder surfaces. It is intentionally *not* a global
prompt or a workflow-local AG2 handoff graph.

Today the harness delegates refinement-classified requests to the authoritative
LLM-backed refinement router. Future builder-session analyzers can plug into the
same harness without changing the SessionRouter contract.
"""

from typing import Any, Optional

from mozaiksai.core.control_plane import ControlPlaneConfig, load_control_plane_config
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution

from .refinement_router import (
    RefinementRequest,
    RefinementRoutingDecision,
    RefinementTriggerRouteResolver,
    get_refinement_trigger_route_resolver,
)


class OrchestrationControlHarness:
    """Control-plane harness for builder-session routing.

    The harness owns builder-context interception and delegates to narrower
    analyzers or routers. It should stay above workflow-local AG2 execution.
    """

    def __init__(
        self,
        *,
        refinement_resolver: Optional[RefinementTriggerRouteResolver] = None,
        config_loader: Any = load_control_plane_config,
    ) -> None:
        self._refinement_resolver = refinement_resolver or get_refinement_trigger_route_resolver()
        self._config_loader = config_loader

    def current_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    def enabled(self) -> bool:
        return bool(self.current_config().enabled)

    def coding_enabled(self) -> bool:
        return bool(self.current_config().coding_enabled())

    async def resolve(self, trigger: TriggerInput) -> Optional[TriggerRoutingContribution]:
        """Resolve builder-session routing contributions for a trigger.

        Current supported path:
        - `trigger_source == "refinement"` -> LLM-backed change classification
          and refinement routing.

        All other trigger sources fall through to the normal SessionRouter flow.
        """

        if not self.enabled():
            return None
        return await self._refinement_resolver.resolve(trigger)

    def request_from_payload(
        self,
        *,
        payload: dict,
        app_id: Optional[str] = None,
        requested_workflow_id: Optional[str] = None,
        default_source_surface: Optional[str] = None,
    ) -> Optional[RefinementRequest]:
        """Normalize a builder refinement payload into the typed request contract."""

        return self._refinement_resolver.request_from_payload(
            payload=payload,
            app_id=app_id,
            requested_workflow_id=requested_workflow_id,
            default_source_surface=default_source_surface,
        )

    async def route_refinement_request(
        self,
        request: RefinementRequest,
    ) -> RefinementRoutingDecision:
        """Run authoritative refinement classification + routing for one request."""

        if not self.enabled():
            raise RuntimeError("Control-plane harness is disabled in app/config/ai.json")
        return await self._refinement_resolver.route(request)

    @staticmethod
    def supported_trigger_sources() -> list[str]:
        return ["refinement"]

    @staticmethod
    def harness_scope() -> str:
        return "builder_session_control_plane"


_harness: Optional[OrchestrationControlHarness] = None


def get_orchestration_control_harness() -> OrchestrationControlHarness:
    global _harness
    if _harness is None:
        _harness = OrchestrationControlHarness()
    return _harness
