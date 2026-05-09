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

from mozaiksai.core.artifacts import ArtifactStore
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import CodingWorkerRequest, CodingWorkerResult, HarnessDecision
from mozaiksai.control_plane.runtime import ControlPlaneCheckpointRuntime
from mozaiksai.control_plane.invalidation import (
    ArtifactInvalidationService,
    get_artifact_invalidation_service,
)

from .coding_worker import ScopedRefinementCodingWorker, get_coding_worker
from .refinement_router import (
    RefinementRequest,
    RefinementRoutingDecision,
    RefinementTriggerRouteResolver,
    get_refinement_trigger_route_resolver,
)
from .harness_decision import FirstPartyHarnessDecisionPolicy, get_harness_decision_policy
from .scope_proposer import ArtifactScopeProposer, get_scope_proposer


class OrchestrationControlHarness:
    """Control-plane harness for builder-session routing.

    The harness owns builder-context interception and delegates to narrower
    analyzers or routers. It should stay above workflow-local AG2 execution.
    """

    def __init__(
        self,
        *,
        checkpoint_runtime: Optional[ControlPlaneCheckpointRuntime] = None,
        refinement_resolver: Optional[RefinementTriggerRouteResolver] = None,
        coding_worker: Optional[ScopedRefinementCodingWorker] = None,
        decision_policy: Optional[FirstPartyHarnessDecisionPolicy] = None,
        scope_proposer: Optional[ArtifactScopeProposer] = None,
        artifact_invalidation: Optional[ArtifactInvalidationService] = None,
        config_loader: Any = load_control_plane_config,
    ) -> None:
        self._checkpoint_runtime = checkpoint_runtime
        classifier = self._get_checkpoint_handler("request_submitted")
        self._refinement_resolver = refinement_resolver or self._bind_checkpoint_handler(
            "route_requested",
            classifier=classifier,
        ) or get_refinement_trigger_route_resolver()
        self._coding_worker = coding_worker or self._get_checkpoint_handler("coding_requested") or get_coding_worker()
        self._decision_policy = (
            decision_policy or self._get_checkpoint_handler("decision_requested") or get_harness_decision_policy()
        )
        self._scope_proposer = scope_proposer or self._get_checkpoint_handler("scope_requested") or get_scope_proposer()
        self._artifact_invalidation = artifact_invalidation or get_artifact_invalidation_service()
        self._config_loader = config_loader

    def _get_checkpoint_handler(self, event_name: str) -> Any:
        if self._checkpoint_runtime is None or not self._checkpoint_runtime.has_checkpoint(event_name):
            return None
        return self._checkpoint_runtime.get_checkpoint(event_name)

    def _bind_checkpoint_handler(self, event_name: str, /, **dependencies: Any) -> Any:
        if self._checkpoint_runtime is None or not self._checkpoint_runtime.has_checkpoint(event_name):
            return None
        return self._checkpoint_runtime.bind_checkpoint(event_name, **dependencies)

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
        user_id: Optional[str] = None,
        requested_workflow_id: Optional[str] = None,
        default_source_surface: Optional[str] = None,
    ) -> Optional[RefinementRequest]:
        """Normalize a builder refinement payload into the typed request contract."""

        return self._refinement_resolver.request_from_payload(
            payload=payload,
            app_id=app_id,
            user_id=user_id,
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

    def build_harness_decision(self, routing_decision: RefinementRoutingDecision) -> HarnessDecision:
        return self._decision_policy.for_workflow_route(routing_decision)

    def build_coding_request(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        payload: Optional[dict[str, Any]] = None,
    ) -> Optional[CodingWorkerRequest]:
        if not self.coding_enabled():
            return None
        if routing_decision.change_intent.change_class.value != "patch":
            return None
        raw = dict(payload or {})
        files = raw.get("files")
        safe_files = files if isinstance(files, dict) else {}
        return CodingWorkerRequest(
            app_id=str(refinement_request.app_id or "").strip(),
            user_id=str(refinement_request.user_id or "").strip() or None,
            artifact_kind=refinement_request.artifact_kind,
            artifact_key=refinement_request.normalized_artifact_key(),
            artifact_version_id=refinement_request.artifact_version_id,
            requested_workflow_id=routing_decision.workflow_id,
            raw_user_request=refinement_request.raw_user_request,
            source_surface=refinement_request.source_surface,
            change_class=routing_decision.change_intent.change_class.value,
            files={str(path): str(content) for path, content in safe_files.items()},
            validation_strategy=(
                str(raw.get("validation_strategy") or "").strip().lower() or None
            ),
            start_preview=bool(raw.get("start_preview", False)),
            context_seed={
                **dict(routing_decision.context_seed or {}),
                "refinement_request": refinement_request.model_dump(mode="python"),
                "routing_decision": routing_decision.model_dump(mode="python"),
            },
            metadata={
                "selected_file_paths": sorted(safe_files.keys()),
                "explicit_file_count": len(safe_files),
                "change_intent": routing_decision.change_intent.model_dump(mode="python"),
                "impact_set": routing_decision.impact_set.model_dump(mode="python"),
            },
        )

    async def prepare_coding_request(
        self,
        request: CodingWorkerRequest,
    ) -> tuple[Optional[CodingWorkerRequest], HarnessDecision]:
        if not self.coding_enabled():
            raise RuntimeError("Control-plane coding worker is disabled in app/config/ai.json")
        refinement_payload = request.context_seed.get("refinement_request")
        routing_payload = request.context_seed.get("routing_decision")
        if not isinstance(refinement_payload, dict) or not isinstance(routing_payload, dict):
            raise RuntimeError("Coding worker request is missing refinement routing context.")

        refinement_request = RefinementRequest.model_validate(refinement_payload)
        routing_decision = RefinementRoutingDecision.model_validate(routing_payload)
        confirmed_action = None
        maybe_action = refinement_request.extra.get("harness_action")
        if isinstance(maybe_action, dict):
            confirmed_action = str(maybe_action.get("action_id") or "").strip() or None
        if request.files:
            explicit_paths = sorted(request.files.keys())
            decision = self._decision_policy.for_scope_resolution(
                routing_decision=routing_decision,
                proposal=None,
                selected_paths=explicit_paths,
                explicit_scope=True,
            )
            metadata = dict(request.metadata or {})
            metadata["selected_file_paths"] = explicit_paths
            metadata["explicit_file_count"] = len(request.files)
            return request.model_copy(update={"metadata": metadata}), decision

        proposal = await self._scope_proposer.propose(
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            payload={"selected_file_paths": list((request.metadata or {}).get("selected_file_paths") or [])},
        )
        resolved_files: dict[str, str] = {}
        if proposal.resolution == "scoped_files":
            resolved_files = await self._scope_proposer.materialize_files(
                refinement_request=refinement_request,
                selected_paths=proposal.selected_paths,
            )
        decision = self._decision_policy.for_scope_resolution(
            routing_decision=routing_decision,
            proposal=proposal,
            selected_paths=sorted(resolved_files.keys()),
            explicit_scope=False,
        )
        metadata = dict(request.metadata or {})
        metadata["scope_proposal"] = proposal.model_dump(mode="python")
        metadata["selected_file_paths"] = sorted(resolved_files.keys())
        metadata["explicit_file_count"] = len(resolved_files)
        if decision.decision_type != "auto_patch":
            if confirmed_action == "apply_proposed_scope" and resolved_files:
                metadata["confirmed_scope_action"] = confirmed_action
                return request.model_copy(update={"files": resolved_files, "metadata": metadata}), decision
            return None, decision
        return request.model_copy(update={"files": resolved_files, "metadata": metadata}), decision

    async def execute_coding_request(self, request: CodingWorkerRequest) -> CodingWorkerResult:
        if not self.coding_enabled():
            raise RuntimeError("Control-plane coding worker is disabled in app/config/ai.json")
        return await self._coding_worker.execute(request)

    async def persist_revision_invalidation(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        change_request_id: Optional[str],
        artifact_store: Optional[ArtifactStore] = None,
    ) -> dict[str, object]:
        return await self._artifact_invalidation.invalidate_for_change_request(
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            change_request_id=change_request_id,
            artifact_store=artifact_store,
        )

    def build_coding_result_decision(
        self,
        request: CodingWorkerRequest,
    ) -> HarnessDecision:
        routing_payload = request.context_seed.get("routing_decision")
        if not isinstance(routing_payload, dict):
            raise RuntimeError("Coding worker request is missing refinement routing context.")
        routing_decision = RefinementRoutingDecision.model_validate(routing_payload)
        return self._decision_policy.for_coding_result(
            routing_decision=routing_decision,
            selected_paths=list((request.metadata or {}).get("selected_file_paths") or []),
        )

_harness: Optional[OrchestrationControlHarness] = None


def get_orchestration_control_harness() -> OrchestrationControlHarness:
    global _harness
    if _harness is None:
        _harness = OrchestrationControlHarness()
    return _harness
