from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mozaiksai.core.session.model import SessionLifecycle, TriggerInput
from mozaiksai.core.session.trigger_routing import TriggerRoutingContribution
from ..loader import load_selected_control_plane_pack
from ..schema import (
    ControlPlaneArtifactRoutingManifest,
    ControlPlaneChangeRouteManifest,
    LoadedControlPlanePack,
)
from .change_classifier import get_change_classifier

_logger = logging.getLogger("mozaiksai.control_plane.implementations.refinement_router")


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
    artifact_kind: str
    artifact_key: Optional[str] = None
    artifact_version_id: Optional[str] = None
    raw_user_request: str = ""
    source_surface: Optional[str] = None
    app_id: Optional[str] = None
    user_id: Optional[str] = None
    requested_workflow_id: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_kind")
    @classmethod
    def _normalize_artifact_kind(cls, value: Any) -> str:
        if isinstance(value, Enum):
            value = value.value
        normalized = str(value or "").strip().lower()
        if not normalized:
            raise ValueError("artifact_kind is required")
        return normalized

    def normalized_artifact_key(self) -> str:
        return str(self.artifact_key or self.artifact_kind).strip() or self.artifact_kind


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
    artifact_kind: str
    label: str
    patch: ControlPlaneChangeRouteManifest
    design: ControlPlaneChangeRouteManifest
    feature: ControlPlaneChangeRouteManifest
    core: ControlPlaneChangeRouteManifest

    def route_for(self, change_class: ChangeClass) -> ControlPlaneChangeRouteManifest:
        return getattr(self, change_class.value)

class RefinementTriggerRouteResolver:
    def __init__(self, *, classifier=None, pack_loader=load_selected_control_plane_pack) -> None:
        self._classifier = classifier or get_change_classifier()
        self._pack_loader = pack_loader

    @staticmethod
    def _artifact_label(artifact_kind: str, policy: Optional[ArtifactRoutePolicy] = None) -> str:
        return str(policy.label if policy is not None else artifact_kind).replace("_", " ")

    def _load_pack(self) -> LoadedControlPlanePack:
        loaded = self._pack_loader()
        return loaded if isinstance(loaded, LoadedControlPlanePack) else LoadedControlPlanePack.model_validate(loaded)

    def _policy_for(self, artifact_kind: str) -> ArtifactRoutePolicy:
        pack = self._load_pack()
        policy = pack.routing_for_artifact(artifact_kind)
        if policy is None:
            configured = ", ".join(sorted(artifact.artifact_kind for artifact in pack.manifest.routing.artifacts))
            raise RuntimeError(
                "No control-plane routing is configured for "
                f"artifact_kind '{artifact_kind}'. Add it to control_plane.yaml routing.artifacts. "
                f"Configured kinds: {configured or 'none'}."
            )
        return self._to_policy(policy)

    @staticmethod
    def _to_policy(policy: ControlPlaneArtifactRoutingManifest) -> ArtifactRoutePolicy:
        return ArtifactRoutePolicy(
            artifact_kind=policy.artifact_kind,
            label=str(policy.label or policy.artifact_kind).strip() or policy.artifact_kind,
            patch=policy.routes.patch,
            design=policy.routes.design,
            feature=policy.routes.feature,
            core=policy.routes.core,
        )

    @staticmethod
    def _families_for_route(route: ControlPlaneChangeRouteManifest, artifact_kind: str) -> list[str]:
        families = [str(item).strip() for item in route.affected_declarative_families if str(item).strip()]
        return families or [artifact_kind]

    async def _derive_change_intent(self, request: RefinementRequest) -> ChangeIntent:
        classification = await self._classifier.classify(
            artifact_kind=request.artifact_kind,
            artifact_key=request.artifact_key,
            raw_user_request=request.raw_user_request,
            declared_change_class=request.declared_change_class.value if request.declared_change_class else None,
            artifact_version_id=request.artifact_version_id,
            source_surface=request.source_surface,
            app_id=request.app_id,
            user_id=request.user_id,
            requested_workflow_id=request.requested_workflow_id,
            extra=request.extra,
        )
        change_class = ChangeClass(classification.change_class)
        source = "llm"
        signals = [str(signal).strip() for signal in classification.signals if str(signal).strip()]
        policy = self._policy_for(request.artifact_kind)
        route = policy.route_for(change_class)
        families = set(self._families_for_route(route, request.artifact_kind))
        label = self._artifact_label(request.artifact_kind, policy)

        if change_class == ChangeClass.CORE:
            return ChangeIntent(
                change_class=change_class,
                source=source,
                signals=signals,
                rationale=str(classification.rationale).strip()
                or f"Core revision requested for the {label}; reopen the upstream workflow and downstream dependent outputs.",
                confidence=classification.confidence,
                requires_concept_revision=True,
                touches_app_bundle="app_bundle" in families,
                touches_workflow_bundle="workflow_bundle" in families,
                touches_design_docs="design_docs" in families,
                touches_concept="concept" in families,
            )

        touches_app_bundle = "app_bundle" in families
        touches_workflow_bundle = "workflow_bundle" in families
        touches_design_docs = "design_docs" in families
        touches_concept = "concept" in families

        if change_class == ChangeClass.DESIGN:
            rationale = str(classification.rationale).strip() or (
                f"Design-scoped revision requested for the {label}; preserve the current concept while revisiting the structured owning workflow."
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
            requires_concept_revision=(change_class == ChangeClass.CORE) or (touches_concept and change_class != ChangeClass.PATCH),
            touches_app_bundle=touches_app_bundle,
            touches_workflow_bundle=touches_workflow_bundle,
            touches_design_docs=touches_design_docs,
            touches_concept=touches_concept,
        )

    def _derive_impact_set(self, request: RefinementRequest, intent: ChangeIntent) -> ImpactSet:
        policy = self._policy_for(request.artifact_kind)
        route = policy.route_for(intent.change_class)
        families = self._families_for_route(route, request.artifact_kind)
        label = self._artifact_label(request.artifact_kind, policy)
        affected_workflows = [str(item).strip() for item in route.affected_workflows if str(item).strip()] or [route.route_to]
        if intent.change_class == ChangeClass.CORE:
            scope_summary = route.scope_summary or (
                f"Restart from {route.route_to} and invalidate downstream outputs that depend on the {label}."
            )
        elif intent.change_class == ChangeClass.DESIGN:
            scope_summary = route.scope_summary or (
                f"Reopen structured planning surfaces for the {label} and rebuild the affected downstream outputs."
            )
        elif intent.change_class == ChangeClass.FEATURE:
            scope_summary = route.scope_summary or (
                f"Extend the existing {label} within the approved concept using {route.route_to}."
            )
        else:
            scope_summary = route.scope_summary or (
                f"Apply a local patch to the current {label} without widening upstream scope."
            )
        return ImpactSet(
            affected_workflows=affected_workflows,
            affected_declarative_families=families,
            requires_replanning=route.requires_replanning,
            requires_rebuild=route.requires_rebuild,
            restart_from=route.route_to,
            scope_summary=scope_summary,
        )

    def _derive_route(
        self,
        request: RefinementRequest,
        *,
        change_intent: ChangeIntent,
        impact_set: ImpactSet,
    ) -> RefinementRoutingDecision:
        policy = self._policy_for(request.artifact_kind)
        route = policy.route_for(change_intent.change_class)
        label = self._artifact_label(request.artifact_kind, policy)

        if change_intent.change_class == ChangeClass.CORE:
            return RefinementRoutingDecision(
                workflow_id=route.route_to,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Core concept change detected for {label}; restarting from {route.route_to}.",
                is_full_restart=True,
            )

        if change_intent.change_class == ChangeClass.DESIGN:
            return RefinementRoutingDecision(
                workflow_id=route.route_to,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Re-entering {route.route_to} to revise design-owned aspects of the {label}.",
                is_full_restart=False,
            )

        if change_intent.change_class == ChangeClass.FEATURE:
            return RefinementRoutingDecision(
                workflow_id=route.route_to,
                refinement_request=request,
                change_intent=change_intent,
                impact_set=impact_set,
                explanation=f"Re-entering {route.route_to} to extend the {label} within the current concept.",
                is_full_restart=False,
            )

        return RefinementRoutingDecision(
            workflow_id=route.route_to,
            refinement_request=request,
            change_intent=change_intent,
            impact_set=impact_set,
            explanation=f"Re-entering {route.route_to} to apply a scoped patch to the {label}.",
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
            "build_mode": "revision",
            "revision_scope": change_intent.change_class.value,
            "artifact_kind": request.artifact_kind,
            "refinement_request": request.raw_user_request,
            "refinement_request_meta": request.model_dump(mode="python"),
            "screen": request.source_surface,
            "change_intent": change_intent.model_dump(mode="python"),
            "impact_set": impact_set.model_dump(mode="python"),
            "revision_origin_workflow": request.requested_workflow_id,
        }
        if request.artifact_version_id:
            context_seed["artifact_version_id"] = request.artifact_version_id
        change_request_id = request.extra.get("change_request_id")
        if isinstance(change_request_id, str) and change_request_id.strip():
            context_seed["change_request_id"] = change_request_id.strip()
        revision_id = request.extra.get("revision_id")
        if isinstance(revision_id, str) and revision_id.strip():
            context_seed["revision_id"] = revision_id.strip()
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
        user_id: Optional[str] = None,
        requested_workflow_id: Optional[str] = None,
        default_source_surface: Optional[str] = None,
    ) -> Optional[RefinementRequest]:
        nested_request = payload.get("refinement_request")
        if not isinstance(nested_request, dict):
            return None
        request_payload = dict(nested_request)
        request_payload.setdefault("extra", {})
        if not isinstance(request_payload["extra"], dict):
            request_payload["extra"] = {}
        harness_action = payload.get("harness_action")
        if isinstance(harness_action, dict):
            request_payload["extra"]["harness_action"] = dict(harness_action)
        change_request_id = payload.get("change_request_id")
        if isinstance(change_request_id, str) and change_request_id.strip():
            request_payload["extra"]["change_request_id"] = change_request_id.strip()
        revision_id = payload.get("revision_id")
        if isinstance(revision_id, str) and revision_id.strip():
            request_payload["extra"]["revision_id"] = revision_id.strip()

        default_artifact_kind = self._load_pack().manifest.routing.default_artifact_kind
        request_payload.setdefault("artifact_kind", default_artifact_kind)
        request_payload["artifact_key"] = (
            str(request_payload.get("artifact_key") or request_payload.get("artifact_kind") or default_artifact_kind)
            .strip()
            or default_artifact_kind
        )
        request_payload["app_id"] = str(app_id or "").strip() or None
        request_payload["user_id"] = str(user_id or "").strip() or None
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
            user_id=trigger.user_id,
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
        pack = self._load_pack()
        configured = [artifact.artifact_kind for artifact in pack.manifest.routing.artifacts]
        return sorted(configured or [ArtifactKind.APP_BUNDLE.value])

    def supported_change_classes(self) -> list[str]:
        return sorted(change_class.value for change_class in ChangeClass)


_resolver: Optional[RefinementTriggerRouteResolver] = None


def get_refinement_trigger_route_resolver() -> RefinementTriggerRouteResolver:
    global _resolver
    if _resolver is None:
        _resolver = RefinementTriggerRouteResolver()
    return _resolver
