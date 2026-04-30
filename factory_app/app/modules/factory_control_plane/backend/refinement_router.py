from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from mozaiksai.core.session.model import SessionLifecycle, TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution

_logger = logging.getLogger("factory_app.app.modules.factory_control_plane.backend.refinement_router")


class ChangeClass(str, Enum):
    PATCH = "patch"
    DESIGN = "design"
    FEATURE = "feature"
    CORE = "core"


class ArtifactKind(str, Enum):
    APP_BUNDLE = "app_bundle"
    WORKFLOW_BUNDLE = "workflow_bundle"
    DESIGN_DOCS = "design_docs"
    CONCEPT = "concept"


@dataclass
class ChangeRequest:
    change_class: ChangeClass
    artifact_kind: ArtifactKind
    artifact_version_id: Optional[str] = None
    raw_user_request: Optional[str] = None
    app_id: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefinementRoutingDecision:
    workflow_id: str
    context_seed: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    is_full_restart: bool = False


@dataclass(frozen=True)
class ArtifactRoutePolicy:
    owner_workflow_id: str
    design_workflow_id: str
    root_workflow_id: str


_ARTIFACT_ROUTE_POLICIES: dict[ArtifactKind, ArtifactRoutePolicy] = {
    ArtifactKind.APP_BUNDLE: ArtifactRoutePolicy(
        owner_workflow_id="AppGenerator",
        design_workflow_id="DesignDocs",
        root_workflow_id="ValueEngine",
    ),
    ArtifactKind.WORKFLOW_BUNDLE: ArtifactRoutePolicy(
        owner_workflow_id="AgentGenerator",
        design_workflow_id="AgentGenerator",
        root_workflow_id="ValueEngine",
    ),
    ArtifactKind.DESIGN_DOCS: ArtifactRoutePolicy(
        owner_workflow_id="DesignDocs",
        design_workflow_id="DesignDocs",
        root_workflow_id="ValueEngine",
    ),
    ArtifactKind.CONCEPT: ArtifactRoutePolicy(
        owner_workflow_id="ValueEngine",
        design_workflow_id="ValueEngine",
        root_workflow_id="ValueEngine",
    ),
}


class RefinementTriggerRouteResolver:
    @staticmethod
    def _artifact_label(artifact_kind: ArtifactKind) -> str:
        return artifact_kind.value.replace("_", " ")

    def _policy_for(self, artifact_kind: ArtifactKind) -> ArtifactRoutePolicy:
        policy = _ARTIFACT_ROUTE_POLICIES.get(artifact_kind)
        if policy is not None:
            return policy
        _logger.warning(
            "No artifact routing policy for kind=%s, falling back to %s",
            artifact_kind,
            ArtifactKind.APP_BUNDLE.value,
        )
        return _ARTIFACT_ROUTE_POLICIES[ArtifactKind.APP_BUNDLE]

    def _derive_route(self, request: ChangeRequest) -> RefinementRoutingDecision:
        policy = self._policy_for(request.artifact_kind)
        label = self._artifact_label(request.artifact_kind)

        if request.change_class == ChangeClass.CORE:
            return RefinementRoutingDecision(
                workflow_id=policy.root_workflow_id,
                explanation=f"Core concept change detected for {label}; restarting from {policy.root_workflow_id}.",
                is_full_restart=True,
            )

        if request.change_class == ChangeClass.DESIGN:
            return RefinementRoutingDecision(
                workflow_id=policy.design_workflow_id,
                explanation=f"Re-entering {policy.design_workflow_id} to revise design-owned aspects of the {label}.",
                is_full_restart=False,
            )

        if request.change_class == ChangeClass.FEATURE:
            return RefinementRoutingDecision(
                workflow_id=policy.owner_workflow_id,
                explanation=f"Re-entering {policy.owner_workflow_id} to extend the {label} within the current concept.",
                is_full_restart=False,
            )

        return RefinementRoutingDecision(
            workflow_id=policy.owner_workflow_id,
            explanation=f"Re-entering {policy.owner_workflow_id} to apply a scoped patch to the {label}.",
            is_full_restart=False,
        )

    def route(self, request: ChangeRequest) -> RefinementRoutingDecision:
        decision = self._derive_route(request)
        context_seed = {
            "refinement_mode": request.change_class != ChangeClass.CORE,
            "change_class": request.change_class.value,
            "artifact_kind": request.artifact_kind.value,
        }
        if request.artifact_version_id:
            context_seed["artifact_version_id"] = request.artifact_version_id
        if request.raw_user_request:
            context_seed["refinement_request"] = request.raw_user_request

        _logger.info(
            "Routing decision: change_class=%s artifact_kind=%s -> workflow=%s restart=%s",
            request.change_class,
            request.artifact_kind,
            decision.workflow_id,
            decision.is_full_restart,
        )

        decision.context_seed = context_seed
        return decision

    def resolve(self, trigger: TriggerInput) -> Optional[TriggerRoutingContribution]:
        trigger_source = str(trigger.trigger_source or "").strip().lower()
        payload = dict(trigger.trigger_payload or {})
        change_class_value = str(payload.get("change_class") or "").strip()
        if trigger_source != "refinement" or not change_class_value:
            return None

        decision = self.route(
            ChangeRequest(
                change_class=ChangeClass(change_class_value),
                artifact_kind=ArtifactKind(
                    str(payload.get("artifact_kind") or ArtifactKind.APP_BUNDLE.value)
                ),
                artifact_version_id=str(payload.get("artifact_version_id") or "").strip() or None,
                raw_user_request=str(payload.get("raw_user_request") or "").strip() or None,
                app_id=trigger.app_id,
            )
        )
        return TriggerRoutingContribution(
            workflow_id=decision.workflow_id,
            context_seed=decision.context_seed,
            explanation=decision.explanation,
            is_full_restart=decision.is_full_restart,
            lifecycle_state=SessionLifecycle.STALE if decision.is_full_restart else SessionLifecycle.ACTIVE,
        )

    def supported_artifact_kinds(self) -> list[str]:
        return sorted(policy_kind.value for policy_kind in _ARTIFACT_ROUTE_POLICIES)

    def supported_change_classes(self) -> list[str]:
        return sorted(change_class.value for change_class in ChangeClass)


_resolver: Optional[RefinementTriggerRouteResolver] = None


def get_refinement_trigger_route_resolver() -> RefinementTriggerRouteResolver:
    global _resolver
    if _resolver is None:
        _resolver = RefinementTriggerRouteResolver()
    return _resolver