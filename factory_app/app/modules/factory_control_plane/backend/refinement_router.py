from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from mozaiksai.core.session.model import SessionLifecycle, TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from .change_classifier import get_change_classifier

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


class RefinementRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_kind: str = "refinement"
    declared_change_class: Optional[ChangeClass] = None
    artifact_kind: ArtifactKind
    artifact_key: Optional[str] = None
    artifact_version_id: Optional[str] = None
    raw_user_request: str = ""
    source_surface: Optional[str] = None
    app_id: Optional[str] = None
    requested_workflow_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)

    def normalized_artifact_key(self) -> str:
        return str(self.artifact_key or self.artifact_kind.value).strip() or self.artifact_kind.value


class ChangeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_class: ChangeClass
    source: str = "llm"
    signals: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    requires_concept_revision: bool = False
    touches_app_bundle: bool = False
    touches_workflow_bundle: bool = False
    touches_design_docs: bool = False
    touches_concept: bool = False


class ImpactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    affected_workflows: list[str] = Field(default_factory=list)
    affected_bundle_paths: list[str] = Field(default_factory=list)
    affected_declarative_families: list[str] = Field(default_factory=list)
    requires_replanning: bool = False
    requires_rebuild: bool = True
    restart_from: Optional[str] = None
    scope_summary: str = ""


class RefinementRoutingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    refinement_request: RefinementRequest
    change_intent: ChangeIntent
    impact_set: ImpactSet
    context_seed: dict[str, Any] = Field(default_factory=dict)
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

_ARTIFACT_FAMILY_MAP: dict[ArtifactKind, list[str]] = {
    ArtifactKind.APP_BUNDLE: ["app_bundle"],
    ArtifactKind.WORKFLOW_BUNDLE: ["workflow_bundle"],
    ArtifactKind.DESIGN_DOCS: ["design_docs"],
    ArtifactKind.CONCEPT: ["concept"],
}

_DOWNSTREAM_BUILD_SEQUENCE = ["ValueEngine", "DesignDocs", "AgentGenerator", "AppGenerator"]

class RefinementTriggerRouteResolver:
    def __init__(self) -> None:
        self._classifier = get_change_classifier()

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

    def _base_workflows_for(self, artifact_kind: ArtifactKind) -> list[str]:
        if artifact_kind == ArtifactKind.APP_BUNDLE:
            return ["AppGenerator"]
        if artifact_kind == ArtifactKind.WORKFLOW_BUNDLE:
            return ["AgentGenerator"]
        if artifact_kind == ArtifactKind.DESIGN_DOCS:
            return ["DesignDocs", "AgentGenerator", "AppGenerator"]
        return list(_DOWNSTREAM_BUILD_SEQUENCE)

    async def _derive_change_intent(self, request: RefinementRequest) -> ChangeIntent:
        classification = await self._classifier.classify(
            artifact_kind=request.artifact_kind.value,
            raw_user_request=request.raw_user_request,
            declared_change_class=request.declared_change_class.value if request.declared_change_class else None,
            artifact_version_id=request.artifact_version_id,
            source_surface=request.source_surface,
            app_id=request.app_id,
            requested_workflow_id=request.requested_workflow_id,
            extra=request.extra,
        )
        change_class = ChangeClass(classification.change_class)
        source = "llm"
        signals = [str(signal).strip() for signal in classification.signals if str(signal).strip()]
        artifact_kind = request.artifact_kind
        label = self._artifact_label(artifact_kind)

        if change_class == ChangeClass.CORE:
            return ChangeIntent(
                change_class=change_class,
                source=source,
                signals=signals,
                rationale=str(classification.rationale).strip()
                or f"Core revision requested for the {label}; canonical concept and downstream artifacts must be reopened.",
                confidence=classification.confidence,
                requires_concept_revision=True,
                touches_app_bundle=True,
                touches_workflow_bundle=True,
                touches_design_docs=True,
                touches_concept=True,
            )

        touches_app_bundle = artifact_kind == ArtifactKind.APP_BUNDLE
        touches_workflow_bundle = artifact_kind == ArtifactKind.WORKFLOW_BUNDLE
        touches_design_docs = artifact_kind == ArtifactKind.DESIGN_DOCS
        touches_concept = artifact_kind == ArtifactKind.CONCEPT

        if change_class == ChangeClass.DESIGN:
            touches_design_docs = True
            if artifact_kind == ArtifactKind.CONCEPT:
                touches_concept = True
            rationale = str(classification.rationale).strip() or (
                f"Design-scoped revision requested for the {label}; keep the current concept and reopen only design-owned surfaces."
            )
        elif change_class == ChangeClass.FEATURE:
            rationale = str(classification.rationale).strip() or (
                f"Feature extension requested for the {label}; preserve the current concept while widening the owned implementation scope."
            )
        else:
            rationale = str(classification.rationale).strip() or (
                f"Scoped patch requested for the {label}; stay within the current artifact boundary unless validation forces wider scope."
            )

        return ChangeIntent(
            change_class=change_class,
            source=source,
            signals=signals,
            rationale=rationale,
            confidence=classification.confidence,
            requires_concept_revision=touches_concept and change_class != ChangeClass.PATCH,
            touches_app_bundle=touches_app_bundle,
            touches_workflow_bundle=touches_workflow_bundle,
            touches_design_docs=touches_design_docs,
            touches_concept=touches_concept,
        )

    def _derive_impact_set(self, request: RefinementRequest, intent: ChangeIntent) -> ImpactSet:
        policy = self._policy_for(request.artifact_kind)
        artifact_kind = request.artifact_kind
        families = list(_ARTIFACT_FAMILY_MAP.get(artifact_kind, []))

        if intent.change_class == ChangeClass.CORE:
            return ImpactSet(
                affected_workflows=list(_DOWNSTREAM_BUILD_SEQUENCE),
                affected_declarative_families=["concept", "design_docs", "workflow_bundle", "app_bundle"],
                requires_replanning=True,
                requires_rebuild=True,
                restart_from=policy.root_workflow_id,
                scope_summary=f"Restart from {policy.root_workflow_id} and invalidate downstream concept, design, workflow, and app artifacts.",
            )

        if intent.change_class == ChangeClass.DESIGN:
            if artifact_kind == ArtifactKind.APP_BUNDLE:
                affected_workflows = [policy.design_workflow_id, policy.owner_workflow_id]
                families = ["design_docs", "app_bundle"]
            else:
                affected_workflows = self._base_workflows_for(artifact_kind)
            return ImpactSet(
                affected_workflows=affected_workflows,
                affected_declarative_families=families,
                requires_replanning=True,
                requires_rebuild=True,
                restart_from=policy.design_workflow_id,
                scope_summary=f"Reopen design-owned surfaces for the {self._artifact_label(artifact_kind)} and rebuild affected downstream outputs.",
            )

        if intent.change_class == ChangeClass.FEATURE:
            return ImpactSet(
                affected_workflows=self._base_workflows_for(artifact_kind),
                affected_declarative_families=families,
                requires_replanning=True,
                requires_rebuild=True,
                restart_from=policy.owner_workflow_id,
                scope_summary=f"Extend the existing {self._artifact_label(artifact_kind)} within the approved concept using the owning workflow.",
            )

        return ImpactSet(
            affected_workflows=[policy.owner_workflow_id],
            affected_declarative_families=families,
            requires_replanning=False,
            requires_rebuild=True,
            restart_from=policy.owner_workflow_id,
            scope_summary=f"Apply a local patch to the current {self._artifact_label(artifact_kind)} without widening upstream scope.",
        )

    def _derive_route(
        self,
        request: RefinementRequest,
        *,
        change_intent: ChangeIntent,
        impact_set: ImpactSet,
    ) -> RefinementRoutingDecision:
        policy = self._policy_for(request.artifact_kind)
        label = self._artifact_label(request.artifact_kind)

        if change_intent.change_class == ChangeClass.CORE:
            return RefinementRoutingDecision(
                workflow_id=policy.root_workflow_id,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Core concept change detected for {label}; restarting from {policy.root_workflow_id}.",
                is_full_restart=True,
            )

        if change_intent.change_class == ChangeClass.DESIGN:
            return RefinementRoutingDecision(
                workflow_id=policy.design_workflow_id,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Re-entering {policy.design_workflow_id} to revise design-owned aspects of the {label}.",
                is_full_restart=False,
            )

        if change_intent.change_class == ChangeClass.FEATURE:
            return RefinementRoutingDecision(
                workflow_id=policy.owner_workflow_id,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Re-entering {policy.owner_workflow_id} to extend the {label} within the current concept.",
                is_full_restart=False,
            )

        return RefinementRoutingDecision(
            workflow_id=policy.owner_workflow_id,
            refinement_request=request,
            change_intent=change_intent,
            impact_set=impact_set,
            explanation=f"Re-entering {policy.owner_workflow_id} to apply a scoped patch to the {label}.",
            is_full_restart=False,
        )

    def _build_context_seed(
        self,
        *,
        request: RefinementRequest,
        change_intent: ChangeIntent,
        impact_set: ImpactSet,
    ) -> dict[str, Any]:
        context_seed: dict[str, Any] = {
            "refinement_mode": change_intent.change_class != ChangeClass.CORE,
            "change_class": change_intent.change_class.value,
            "artifact_kind": request.artifact_kind.value,
            "refinement_request": request.raw_user_request,
            "refinement_request_meta": request.model_dump(mode="python"),
            "change_intent": change_intent.model_dump(mode="python"),
            "impact_set": impact_set.model_dump(mode="python"),
        }
        if request.artifact_version_id:
            context_seed["artifact_version_id"] = request.artifact_version_id
        return context_seed

    async def route(self, request: RefinementRequest) -> RefinementRoutingDecision:
        change_intent = await self._derive_change_intent(request)
        impact_set = self._derive_impact_set(request, change_intent)
        decision = self._derive_route(
            request,
            change_intent=change_intent,
            impact_set=impact_set,
        )
        decision.context_seed = self._build_context_seed(
            request=request,
            change_intent=change_intent,
            impact_set=impact_set,
        )

        _logger.info(
            "Routing decision: change_class=%s artifact_kind=%s -> workflow=%s restart=%s",
            change_intent.change_class,
            request.artifact_kind,
            decision.workflow_id,
            decision.is_full_restart,
        )
        return decision

    def request_from_payload(
        self,
        *,
        payload: dict[str, Any],
        app_id: Optional[str] = None,
        requested_workflow_id: Optional[str] = None,
        default_source_surface: Optional[str] = None,
    ) -> Optional[RefinementRequest]:
        nested_request = payload.get("refinement_request")
        if not isinstance(nested_request, dict):
            return None
        request_payload = dict(nested_request)

        request_payload.setdefault("artifact_kind", ArtifactKind.APP_BUNDLE.value)
        request_payload["artifact_key"] = (
            str(request_payload.get("artifact_key") or request_payload.get("artifact_kind") or ArtifactKind.APP_BUNDLE.value)
            .strip()
            or ArtifactKind.APP_BUNDLE.value
        )
        request_payload["app_id"] = str(app_id or "").strip() or None
        request_payload["requested_workflow_id"] = str(requested_workflow_id or "").strip() or None
        if default_source_surface and not request_payload.get("source_surface"):
            request_payload["source_surface"] = default_source_surface
        return RefinementRequest.model_validate(request_payload)

    def request_from_trigger(self, trigger: TriggerInput) -> Optional[RefinementRequest]:
        trigger_source = str(trigger.trigger_source or "").strip().lower()
        if trigger_source != "refinement":
            return None
        default_source_surface = None
        screen = trigger.context_variables.get("screen")
        if isinstance(screen, str) and screen.strip():
            default_source_surface = screen.strip()
        return self.request_from_payload(
            payload=dict(trigger.trigger_payload or {}),
            app_id=trigger.app_id,
            requested_workflow_id=trigger.workflow_id,
            default_source_surface=default_source_surface,
        )

    async def resolve(self, trigger: TriggerInput) -> Optional[TriggerRoutingContribution]:
        request = self.request_from_trigger(trigger)
        if request is None:
            return None

        decision = await self.route(request)
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
