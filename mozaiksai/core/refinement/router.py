# ==============================================================================
# FILE: mozaiksai/core/refinement/router.py
# DESCRIPTION: Lightweight refinement re-entry policy helper.
#
# Maps (change_class, artifact_kind) → re-entry workflow and seeded context.
#
# This is intentionally engine-agnostic and platform-agnostic, but it is not a
# full control plane. SessionRouter should call into this helper when refinement
# re-entry is needed.
#
# Declaration model:
# - no YAML routing table
# - no platform-specific override layer
# - only canonical artifact ownership is declared here in code
# - routing is then derived generically:
#     patch / feature -> artifact owner
#     design          -> artifact design owner
#     core            -> root owner (restart)
#
# Architecture layer: runtime (not workflow, not adapter).
# ==============================================================================

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

_logger = logging.getLogger("mozaiksai.refinement.router")

# ---------------------------------------------------------------------------
# Domain types
# ---------------------------------------------------------------------------


class ChangeClass(str, Enum):
    """The four change classes from the Refinement Control Plane spec."""

    PATCH = "patch"
    DESIGN = "design"
    FEATURE = "feature"
    CORE = "core"


class ArtifactKind(str, Enum):
    """Artifact kinds that can be refined."""

    APP_BUNDLE = "app_bundle"
    WORKFLOW_BUNDLE = "workflow_bundle"
    DESIGN_DOCS = "design_docs"
    CONCEPT = "concept"


@dataclass
class ChangeRequest:
    """A classified refinement request before routing."""

    change_class: ChangeClass
    artifact_kind: ArtifactKind
    artifact_version_id: Optional[str] = None
    raw_user_request: Optional[str] = None
    app_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingDecision:
    """The router's decision: which workflow to invoke and what context to seed."""

    workflow_id: str
    context_seed: Dict[str, Any] = field(default_factory=dict)
    # Human-readable explanation for the UI and audit log
    explanation: str = ""
    # Whether this is a full restart (core) or a targeted re-entry
    is_full_restart: bool = False


# ---------------------------------------------------------------------------
# Canonical artifact ownership
#
# This is the only declaration the runtime needs for refinement re-entry.
# The route itself is derived from change class:
#   patch / feature -> owner_workflow_id
#   design          -> design_workflow_id
#   core            -> root_workflow_id + full restart
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ArtifactRoutePolicy:
    owner_workflow_id: str
    design_workflow_id: str
    root_workflow_id: str


_ARTIFACT_ROUTE_POLICIES: Dict[ArtifactKind, ArtifactRoutePolicy] = {
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


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class RefinementRouter:
    """Maps a ChangeRequest to a RoutingDecision.

    This helper intentionally stays simple:
    - artifact ownership is declared once in code
    - routing is derived from the change class
    - SessionRouter should own the surrounding lifecycle logic
    """

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

    def _derive_route(self, request: ChangeRequest) -> RoutingDecision:
        policy = self._policy_for(request.artifact_kind)
        label = self._artifact_label(request.artifact_kind)

        if request.change_class == ChangeClass.CORE:
            return RoutingDecision(
                workflow_id=policy.root_workflow_id,
                explanation=f"Core concept change detected for {label}; restarting from {policy.root_workflow_id}.",
                is_full_restart=True,
            )

        if request.change_class == ChangeClass.DESIGN:
            return RoutingDecision(
                workflow_id=policy.design_workflow_id,
                explanation=f"Re-entering {policy.design_workflow_id} to revise design-owned aspects of the {label}.",
                is_full_restart=False,
            )

        if request.change_class == ChangeClass.FEATURE:
            return RoutingDecision(
                workflow_id=policy.owner_workflow_id,
                explanation=f"Re-entering {policy.owner_workflow_id} to extend the {label} within the current concept.",
                is_full_restart=False,
            )

        return RoutingDecision(
            workflow_id=policy.owner_workflow_id,
            explanation=f"Re-entering {policy.owner_workflow_id} to apply a scoped patch to the {label}.",
            is_full_restart=False,
        )

    def route(self, request: ChangeRequest) -> RoutingDecision:
        """Return the routing decision for a change request."""
        decision = self._derive_route(request)
        context_seed = {
            "refinement_mode": request.change_class != ChangeClass.CORE,
            "change_class": request.change_class.value,
            "artifact_kind": request.artifact_kind.value,
        }

        # Always include the artifact_version_id so the re-entry workflow can
        # load the correct artifact version from persistence.
        if request.artifact_version_id:
            context_seed["artifact_version_id"] = request.artifact_version_id

        # Include the raw user request so refinement agents can read the intent.
        if request.raw_user_request:
            context_seed["refinement_request"] = request.raw_user_request

        _logger.info(
            "Routing decision: change_class=%s artifact_kind=%s → workflow=%s restart=%s",
            request.change_class,
            request.artifact_kind,
            decision.workflow_id,
            decision.is_full_restart,
        )

        decision.context_seed = context_seed
        return decision

    def supported_artifact_kinds(self) -> list[str]:
        return sorted(policy_kind.value for policy_kind in _ARTIFACT_ROUTE_POLICIES)

    def supported_change_classes(self) -> list[str]:
        return sorted(change_class.value for change_class in ChangeClass)


# Module-level singleton — lazy-initialised on first import
_router: Optional[RefinementRouter] = None


def get_refinement_router() -> RefinementRouter:
    global _router
    if _router is None:
        _router = RefinementRouter()
    return _router
