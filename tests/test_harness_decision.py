from __future__ import annotations

from mozaiksai.control_plane import (
    ArtifactKind,
    ChangeClass,
    ChangeIntent,
    FirstPartyHarnessDecisionPolicy,
    ImpactSet,
    RefinementRequest,
    RefinementRoutingDecision,
    ScopeProposal,
)


def _routing_decision(change_class: ChangeClass) -> RefinementRoutingDecision:
    request = RefinementRequest(
        request_kind="refinement",
        artifact_kind=ArtifactKind.APP_BUNDLE,
        artifact_key="app_bundle",
        artifact_version_id="av_123",
        raw_user_request="Change the dashboard",
        source_surface="app_build",
        app_id="app_1",
        requested_workflow_id="AppGenerator",
    )
    return RefinementRoutingDecision(
        workflow_id="ValueEngine" if change_class == ChangeClass.CORE else "AppGenerator",
        refinement_request=request,
        change_intent=ChangeIntent(
            change_class=change_class,
            source="llm",
            signals=["test_signal"],
            rationale="test rationale",
            confidence=0.9,
            requires_concept_revision=(change_class == ChangeClass.CORE),
            touches_app_bundle=True,
            touches_workflow_bundle=(change_class == ChangeClass.CORE),
            touches_design_docs=(change_class == ChangeClass.CORE),
            touches_concept=(change_class == ChangeClass.CORE),
        ),
        impact_set=ImpactSet(
            affected_workflows=["ValueEngine"] if change_class == ChangeClass.CORE else ["AppGenerator"],
            affected_bundle_paths=[],
            affected_declarative_families=["app_bundle"],
            requires_replanning=(change_class != ChangeClass.PATCH),
            requires_rebuild=True,
            restart_from="ValueEngine" if change_class == ChangeClass.CORE else "AppGenerator",
            scope_summary="test scope",
        ),
        context_seed={},
        explanation="test explanation",
        is_full_restart=(change_class == ChangeClass.CORE),
    )


def test_decision_policy_requires_confirmation_for_core_restart() -> None:
    policy = FirstPartyHarnessDecisionPolicy()
    decision = policy.for_workflow_route(_routing_decision(ChangeClass.CORE))

    assert decision.decision_type == "core_restart"
    assert decision.requires_confirmation is True
    assert decision.recommended_workflow_id == "ValueEngine"
    assert decision.actions[0].action_id == "confirm_recommended_workflow"


def test_decision_policy_clarifies_low_confidence_scope() -> None:
    policy = FirstPartyHarnessDecisionPolicy()
    decision = policy.for_scope_resolution(
        routing_decision=_routing_decision(ChangeClass.PATCH),
        proposal=ScopeProposal(
            resolution="clarify",
            selected_paths=["app/ui/pages/Dashboard.jsx"],
            rationale="The request might affect more than one dashboard surface.",
            confidence=0.42,
            clarification_question="Do you mean the dashboard page or the export panel?",
            signals=["ambiguous_scope"],
        ),
        selected_paths=["app/ui/pages/Dashboard.jsx"],
        explicit_scope=False,
    )

    assert decision.decision_type == "clarify_scope"
    assert decision.clarification_question == "Do you mean the dashboard page or the export panel?"


def test_decision_policy_auto_patch_for_high_confidence_scope() -> None:
    policy = FirstPartyHarnessDecisionPolicy()
    decision = policy.for_scope_resolution(
        routing_decision=_routing_decision(ChangeClass.PATCH),
        proposal=ScopeProposal(
            resolution="scoped_files",
            selected_paths=["app/ui/pages/Dashboard.jsx"],
            rationale="Dashboard is the primary surface.",
            confidence=0.93,
            clarification_question=None,
            signals=["dashboard_surface"],
        ),
        selected_paths=["app/ui/pages/Dashboard.jsx"],
        explicit_scope=False,
    )

    assert decision.decision_type == "auto_patch"
    assert decision.selected_paths == ["app/ui/pages/Dashboard.jsx"]


def test_decision_policy_requires_confirmation_for_multi_file_scope() -> None:
    policy = FirstPartyHarnessDecisionPolicy()
    decision = policy.for_scope_resolution(
        routing_decision=_routing_decision(ChangeClass.PATCH),
        proposal=ScopeProposal(
            resolution="scoped_files",
            selected_paths=[
                "app/ui/pages/Dashboard.jsx",
                "app/ui/components/ExportPanel.jsx",
            ],
            rationale="Both the dashboard page and export panel appear to be affected.",
            confidence=0.91,
            clarification_question=None,
            signals=["multi_file_scope"],
        ),
        selected_paths=[
            "app/ui/pages/Dashboard.jsx",
            "app/ui/components/ExportPanel.jsx",
        ],
        explicit_scope=False,
    )

    assert decision.decision_type == "clarify_scope"
    assert decision.actions[0].action_id == "apply_proposed_scope"
