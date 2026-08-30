from __future__ import annotations

from types import SimpleNamespace

import pytest

from mozaiksai.control_plane import (
    ArtifactInvalidationService,
    ArtifactKind,
    ChangeClass,
    ChangeIntent,
    ImpactSet,
    RefinementRequest,
    RefinementRoutingDecision,
)


class _FakeSessionStore:
    async def load(self, *, app_id: str, user_id: str):
        return SimpleNamespace(
            artifact_version_refs={
                "concept": "av_concept_1",
                "design_docs": "av_design_1",
                "workflow_bundle": "av_workflow_1",
                "app_bundle": "av_app_1",
            }
        )


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.family_calls: list[dict] = []

    async def invalidate_artifact_version_refs(self, **kwargs):  # noqa: ANN003
        self.calls.append(dict(kwargs))
        return ["av_design_1", "av_app_1"]

    async def invalidate_artifact_family(self, **kwargs):  # noqa: ANN003
        self.family_calls.append(dict(kwargs))
        return 1  # simulate one version staled


def _request() -> RefinementRequest:
    return RefinementRequest(
        request_kind="refinement",
        artifact_kind=ArtifactKind.APP_BUNDLE,
        artifact_key="app_bundle",
        artifact_version_id="av_app_1",
        raw_user_request="Refresh the app information architecture.",
        source_surface="app_build",
        app_id="app_1",
        user_id="user_1",
        requested_workflow_id="AppGenerator",
    )


def _routing_decision() -> RefinementRoutingDecision:
    return RefinementRoutingDecision(
        workflow_id="DesignDocs",
        refinement_request=_request(),
        context_seed={},
        is_full_restart=False,
        explanation="Re-entering DesignDocs to revise app-surface structure.",
        change_intent=ChangeIntent(
            change_class=ChangeClass.DESIGN,
            source="llm",
            signals=["information_architecture"],
            rationale="The request changes the designed app surface.",
            confidence=0.9,
            requires_concept_revision=False,
            touches_app_bundle=True,
            touches_workflow_bundle=False,
            touches_design_docs=True,
            touches_concept=False,
        ),
        impact_set=ImpactSet(
            affected_workflows=["DesignDocs", "AppGenerator"],
            affected_bundle_paths=[],
            affected_declarative_families=["design_docs", "app_bundle"],
            requires_replanning=True,
            requires_rebuild=True,
            restart_from="DesignDocs",
            scope_summary="Reopen design and rebuild the app bundle.",
        ),
    )


@pytest.mark.asyncio
async def test_artifact_invalidation_service_uses_session_refs_and_affected_families() -> None:
    artifact_store = _FakeArtifactStore()
    service = ArtifactInvalidationService(
        session_store=_FakeSessionStore(),
        artifact_store=artifact_store,
    )

    result = await service.invalidate_for_change_request(
        refinement_request=_request(),
        routing_decision=_routing_decision(),
        change_request_id="cr_123",
    )

    assert result == {
        "change_request_id": "cr_123",
        "affected_build_families": ["design_docs", "app_bundle"],
        "invalidated_build_record_ids": ["av_design_1", "av_app_1"],
        "downstream_staled_families": ["subscription_contract", "workflow_bundle"],
    }
    assert artifact_store.calls == [
        {
            "app_id": "app_1",
            "artifact_version_refs": {
                "concept": "av_concept_1",
                "design_docs": "av_design_1",
                "workflow_bundle": "av_workflow_1",
                "app_bundle": "av_app_1",
            },
            "affected_artifact_kinds": ["design_docs", "app_bundle"],
            "reason": "change_request:cr_123",
        }
    ]
    # Downstream propagation should have staled design_docs dependents.
    assert artifact_store.family_calls == [
        {
            "app_id": "app_1",
            "artifact_kind": "subscription_contract",
            "artifact_key": "subscription_contract",
            "reason": "change_request:cr_123",
        },
        {
            "app_id": "app_1",
            "artifact_kind": "workflow_bundle",
            "artifact_key": "workflow_bundle",
            "reason": "change_request:cr_123",
        }
    ]

