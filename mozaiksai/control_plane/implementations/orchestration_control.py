"""Builder-session Refinement Engine harness.

This is the Refinement Engine entrypoint for build-affecting requests that
arrive through Studio or other builder surfaces. It is intentionally *not* a
global prompt or a workflow-local AG2 handoff graph.

Today the harness delegates refinement-classified requests to the authoritative
LLM-backed refinement router. Future builder-session analyzers can plug into the
same harness without changing the SessionRouter contract.
"""

from __future__ import annotations

from typing import Any

from mozaiksai.control_plane.config import ControlPlaneConfig, load_control_plane_config
from mozaiksai.control_plane.contracts import (
    CodingWorkerRequest,
    CodingWorkerResult,
    ContractSurfacePlan,
    HarnessDecision,
    SurfacePlanExecutionResult,
)
from mozaiksai.control_plane.invalidation import (
    ArtifactInvalidationService,
    get_artifact_invalidation_service,
)
from mozaiksai.control_plane.metrics import (
    ControlPlaneBuildTimer,
    check_token_usage,
    log_build_outcome,
)
from mozaiksai.control_plane.refinement_tracking import record_refinement_event
from mozaiksai.control_plane.runtime import ControlPlaneCheckpointRuntime
from mozaiksai.core.artifacts import ArtifactStore
from mozaiksai.core.session.model import TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution

from .coding_worker import ScopedRefinementCodingWorker, get_coding_worker
from .contract_surface_planner import ContractSurfacePlanner, get_contract_surface_planner
from .harness_decision import FirstPartyHarnessDecisionPolicy, get_harness_decision_policy
from .refinement_router import (
    RefinementRequest,
    RefinementRoutingDecision,
    RefinementTriggerRouteResolver,
)
from .scope_proposer import ArtifactScopeProposer, get_scope_proposer
from .surface_regeneration_worker import SurfaceRegenerationWorker, get_surface_regeneration_worker


class OrchestrationControlHarness:
    """Refinement Engine harness for builder-session routing.

    The harness owns builder-context interception and delegates to narrower
    analyzers or routers. It should stay above workflow-local AG2 execution.
    """

    def __init__(
        self,
        *,
        checkpoint_runtime: ControlPlaneCheckpointRuntime | None = None,
        refinement_resolver: RefinementTriggerRouteResolver | None = None,
        coding_worker: ScopedRefinementCodingWorker | None = None,
        decision_policy: FirstPartyHarnessDecisionPolicy | None = None,
        scope_proposer: ArtifactScopeProposer | None = None,
        contract_surface_planner: ContractSurfacePlanner | None = None,
        surface_regeneration_worker: SurfaceRegenerationWorker | None = None,
        artifact_invalidation: ArtifactInvalidationService | None = None,
        config_loader: Any = load_control_plane_config,
    ) -> None:
        self._checkpoint_runtime = checkpoint_runtime
        classifier = self._get_checkpoint_handler("request_submitted")
        self._refinement_resolver = (
            refinement_resolver
            or self._bind_checkpoint_handler(
                "route_requested",
                classifier=classifier,
            )
            or RefinementTriggerRouteResolver(classifier=classifier)
        )
        self._coding_worker = coding_worker or self._get_checkpoint_handler("coding_requested") or get_coding_worker()
        self._decision_policy = (
            decision_policy or self._get_checkpoint_handler("decision_requested") or get_harness_decision_policy()
        )
        self._scope_proposer = scope_proposer or self._get_checkpoint_handler("scope_requested") or get_scope_proposer()
        self._contract_surface_planner = (
            contract_surface_planner
            or self._get_checkpoint_handler("contract_surface_requested")
            or get_contract_surface_planner()
        )
        self._surface_regeneration_worker = surface_regeneration_worker or get_surface_regeneration_worker()
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

    def contract_surface_enabled(self) -> bool:
        config = self.current_config()
        return bool(config.enabled and config.contract_surface.enabled)

    async def resolve(self, trigger: TriggerInput) -> TriggerRoutingContribution | None:
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
        app_id: str | None = None,
        user_id: str | None = None,
        requested_workflow_id: str | None = None,
        default_source_surface: str | None = None,
    ) -> RefinementRequest | None:
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
            raise RuntimeError("Refinement Engine is disabled in app/config/refinement_policy.yaml")

        _app_id = str(request.app_id or "")
        await record_refinement_event(
            event_kind="request_received",
            request_id=request.request_id,
            app_id=_app_id,
            intent=str(request.raw_user_request or ""),
        )

        try:
            with ControlPlaneBuildTimer(
                "route_refinement",
                request_id=request.request_id,
                app_id=request.app_id,
            ):
                decision = await self._refinement_resolver.route(request)
        except Exception as exc:
            await record_refinement_event(
                event_kind="failed",
                request_id=request.request_id,
                app_id=_app_id,
                outcome="error",
                error=str(exc),
            )
            raise

        log_build_outcome(
            outcome="ok",
            request_id=request.request_id,
            app_id=request.app_id,
            change_class=decision.change_class,
            workflow_sequence=decision.workflow_sequence,
        )
        await record_refinement_event(
            event_kind="classified",
            request_id=request.request_id,
            app_id=_app_id,
            change_class=decision.change_class,
            workflow_sequence=decision.workflow_sequence,
            outcome="ok",
        )
        return decision

    async def prepare_contract_surface_request(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        context_graph_catalog: dict[str, Any] | None = None,
    ) -> tuple[ContractSurfacePlan | None, HarnessDecision]:
        """Run contract surface planning for feature/design changes on app_bundle artifacts.

        This is the contract-aware refinement path:
        - Eligible: feature or design change class on app_bundle or workflow_bundle
        - The planner identifies which contract surfaces are affected and in what order
        - The decision policy shapes this into a targeted_regeneration decision
        - Falls back to workflow_reentry when confidence is low or scope is too broad

        Returns (plan, decision). plan is None when the fallback path is taken.
        """

        if not self.contract_surface_enabled():
            raise RuntimeError("Contract surface planning is disabled in app/config/refinement_policy.yaml")

        plan: ContractSurfacePlan = await self._contract_surface_planner.propose(
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            context_graph_catalog=context_graph_catalog,
        )

        decision: HarnessDecision = self._decision_policy.for_contract_surface_plan(
            routing_decision=routing_decision,
            plan=plan,
        )
        return (None if plan.fallback_to_workflow else plan), decision

    async def execute_surface_plan(
        self,
        *,
        plan: ContractSurfacePlan,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        workspace_files: dict[str, Any] | None = None,
    ) -> SurfacePlanExecutionResult:
        """Execute a ContractSurfacePlan surface by surface.

        Each surface in the plan is executed in dependency order (data_schema
        before module_action, module_action before page_binding, etc.).
        File outputs accumulate across surfaces so later surfaces see the
        updated content from earlier ones.

        workspace_files supplies the current content of workspace files keyed
        by relative path. Absent paths are treated as new files (empty string).

        Raises RuntimeError when contract_surface is disabled.
        """
        if not self.contract_surface_enabled():
            raise RuntimeError("Contract surface planning is disabled in app/config/refinement_policy.yaml")

        return await self._surface_regeneration_worker.execute_plan(
            plan=plan,
            refinement_request=refinement_request,
            routing_decision=routing_decision,
            workspace_files={str(k): str(v) for k, v in (workspace_files or {}).items()},
        )

    def build_harness_decision(self, routing_decision: RefinementRoutingDecision) -> HarnessDecision:
        return self._decision_policy.for_workflow_route(routing_decision)

    def build_coding_request(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        payload: dict[str, Any] | None = None,
    ) -> CodingWorkerRequest | None:
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
            build_family=refinement_request.build_family,
            build_key=refinement_request.normalized_build_key(),
            build_record_id=refinement_request.build_record_id,
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
    ) -> tuple[CodingWorkerRequest | None, HarnessDecision]:
        if not self.coding_enabled():
            raise RuntimeError("Refinement coding worker is disabled in app/config/refinement_policy.yaml")
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
            raise RuntimeError("Refinement coding worker is disabled in app/config/refinement_policy.yaml")

        request_id: str | None = None
        try:
            seed = request.context_seed or {}
            refinement_payload = seed.get("refinement_request")
            if isinstance(refinement_payload, dict):
                request_id = str(refinement_payload.get("request_id") or "").strip() or None
        except Exception:
            pass

        try:
            with ControlPlaneBuildTimer(
                "coding_worker",
                request_id=request_id,
                app_id=request.app_id,
            ):
                result = await self._coding_worker.execute(request)
        except Exception as exc:
            await record_refinement_event(
                event_kind="failed",
                request_id=request_id or "unknown",
                app_id=request.app_id,
                change_class=request.change_class,
                outcome="error",
                error=str(exc),
            )
            raise

        check_token_usage(
            stage="coding_worker",
            token_count=getattr(result, "token_count", None),
            app_id=request.app_id,
        )
        await record_refinement_event(
            event_kind="completed",
            request_id=request_id or "unknown",
            app_id=request.app_id,
            change_class=request.change_class,
            outcome="ok",
            metadata={"token_count": getattr(result, "token_count", None)},
        )
        return result

    async def persist_revision_invalidation(
        self,
        *,
        refinement_request: RefinementRequest,
        routing_decision: RefinementRoutingDecision,
        change_request_id: str | None,
        artifact_store: ArtifactStore | None = None,
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

_harness: OrchestrationControlHarness | None = None


def get_orchestration_control_harness() -> OrchestrationControlHarness:
    global _harness
    if _harness is None:
        _harness = OrchestrationControlHarness()
    return _harness
