from __future__ import annotations

from typing import Optional

from mozaiksai.control_plane.contracts import ContractSurfacePlan, HarnessDecision, HarnessDecisionAction, ScopeProposal
from mozaiksai.control_plane.loader import load_selected_control_plane_pack

from .refinement_router import ChangeClass, RefinementRoutingDecision


class FirstPartyHarnessDecisionPolicy:
    """Deterministic first-party UX decision policy for the control-plane harness."""

    _AUTO_SCOPE_THRESHOLD = 0.78
    _CLARIFY_SCOPE_THRESHOLD = 0.55

    def __init__(self, *, pack_loader=load_selected_control_plane_pack) -> None:
        self._pack_loader = pack_loader

    def _scope_policy(self):
        return self._pack_loader().policies.scope

    def for_workflow_route(self, routing_decision: RefinementRoutingDecision) -> HarnessDecision:
        change_class = routing_decision.change_intent.change_class
        workflow_id = routing_decision.workflow_id
        confidence = float(routing_decision.change_intent.confidence or 0.0)
        rationale = str(routing_decision.change_intent.rationale or routing_decision.explanation).strip()

        if change_class == ChangeClass.CORE:
            return HarnessDecision(
                decision_type="core_restart",
                message=f"This looks like a concept-level change. Recommended route: {workflow_id}.",
                rationale=rationale,
                confidence=confidence,
                recommended_workflow_id=workflow_id,
                requires_confirmation=True,
                actions=[
                    HarnessDecisionAction(
                        action_id="confirm_recommended_workflow",
                        label=f"Run {workflow_id}",
                        action_type="confirm_workflow",
                        workflow_id=workflow_id,
                    )
                ],
                metadata={
                    "change_class": change_class.value,
                    "workflow_sequence": routing_decision.workflow_sequence,
                    "scope_summary": routing_decision.impact_set.scope_summary,
                },
            )

        if change_class in {ChangeClass.DESIGN, ChangeClass.FEATURE}:
            return HarnessDecision(
                decision_type="workflow_reentry",
                message=f"Recommended route: {workflow_id}.",
                rationale=rationale,
                confidence=confidence,
                recommended_workflow_id=workflow_id,
                requires_confirmation=False,
                actions=[
                    HarnessDecisionAction(
                        action_id="run_recommended_workflow",
                        label=f"Continue in {workflow_id}",
                        action_type="run_workflow",
                        workflow_id=workflow_id,
                    )
                ],
                metadata={
                    "change_class": change_class.value,
                    "workflow_sequence": routing_decision.workflow_sequence,
                    "scope_summary": routing_decision.impact_set.scope_summary,
                },
            )

        return HarnessDecision(
            decision_type="workflow_reentry",
            message=f"Scoped patch stays with {workflow_id}.",
            rationale=rationale,
            confidence=confidence,
            recommended_workflow_id=workflow_id,
            requires_confirmation=False,
            actions=[
                HarnessDecisionAction(
                    action_id="run_recommended_workflow",
                    label=f"Continue in {workflow_id}",
                    action_type="run_workflow",
                    workflow_id=workflow_id,
                )
            ],
            metadata={
                "change_class": change_class.value,
                "workflow_sequence": routing_decision.workflow_sequence,
                "scope_summary": routing_decision.impact_set.scope_summary,
            },
        )

    def for_scope_resolution(
        self,
        *,
        routing_decision: RefinementRoutingDecision,
        proposal: Optional[ScopeProposal],
        selected_paths: list[str],
        explicit_scope: bool,
    ) -> HarnessDecision:
        if explicit_scope:
            return HarnessDecision(
                decision_type="auto_patch",
                message="Scoped patch ready to apply.",
                rationale="The selected file scope was provided explicitly by the builder surface.",
                confidence=1.0,
                recommended_workflow_id=routing_decision.workflow_id,
                selected_paths=list(selected_paths),
                requires_confirmation=False,
                actions=[
                    HarnessDecisionAction(
                        action_id="review_patch",
                        label="Review patch",
                        action_type="review_patch",
                        workflow_id=routing_decision.workflow_id,
                    )
                ],
                metadata={"scope_origin": "explicit"},
            )

        if proposal is None:
            return HarnessDecision(
                decision_type="clarify_scope",
                message="The patch scope is missing.",
                rationale="No explicit files were supplied and no scope proposal was available.",
                confidence=0.0,
                recommended_workflow_id=routing_decision.workflow_id,
                clarification_question="Which file or surface should be changed first?",
                requires_confirmation=False,
                actions=[
                    HarnessDecisionAction(
                        action_id="clarify_scope",
                        label="Clarify scope",
                        action_type="clarify",
                        workflow_id=routing_decision.workflow_id,
                    )
                ],
                metadata={"scope_origin": "missing"},
            )

        if proposal.resolution == "workflow":
            return HarnessDecision(
                decision_type="fallback_workflow",
                message=f"This request is broader than a local patch. Recommended route: {routing_decision.workflow_id}.",
                rationale=proposal.rationale,
                confidence=float(proposal.confidence or 0.0),
                recommended_workflow_id=routing_decision.workflow_id,
                selected_paths=list(proposal.selected_paths),
                requires_confirmation=False,
                actions=[
                    HarnessDecisionAction(
                        action_id="run_recommended_workflow",
                        label=f"Continue in {routing_decision.workflow_id}",
                        action_type="run_workflow",
                        workflow_id=routing_decision.workflow_id,
                    )
                ],
                metadata={"scope_origin": "proposal", "signals": list(proposal.signals)},
            )

        if proposal.resolution == "clarify" or proposal.confidence < self._CLARIFY_SCOPE_THRESHOLD:
            return HarnessDecision(
                decision_type="clarify_scope",
                message="I need one more scope clarification before applying a patch.",
                rationale=proposal.rationale,
                confidence=float(proposal.confidence or 0.0),
                recommended_workflow_id=routing_decision.workflow_id,
                selected_paths=list(proposal.selected_paths),
                clarification_question=proposal.clarification_question or "Which file or surface should be changed first?",
                requires_confirmation=False,
                actions=self._clarify_actions(
                    workflow_id=routing_decision.workflow_id,
                    selected_paths=selected_paths,
                    include_apply=bool(selected_paths),
                ),
                metadata={"scope_origin": "proposal", "signals": list(proposal.signals)},
            )

        if not selected_paths:
            return HarnessDecision(
                decision_type="fallback_workflow",
                message=f"No safe file scope could be materialized. Recommended route: {routing_decision.workflow_id}.",
                rationale=proposal.rationale,
                confidence=float(proposal.confidence or 0.0),
                recommended_workflow_id=routing_decision.workflow_id,
                requires_confirmation=False,
                actions=[
                    HarnessDecisionAction(
                        action_id="run_recommended_workflow",
                        label=f"Continue in {routing_decision.workflow_id}",
                        action_type="run_workflow",
                        workflow_id=routing_decision.workflow_id,
                    )
                ],
                metadata={"scope_origin": "proposal", "signals": list(proposal.signals)},
            )

        scope_policy = self._scope_policy()
        if len(selected_paths) > scope_policy.auto_apply_max_paths:
            return HarnessDecision(
                decision_type="clarify_scope",
                message="This patch touches multiple files. Confirm the proposed scope before applying it.",
                rationale=proposal.rationale,
                confidence=float(proposal.confidence or 0.0),
                recommended_workflow_id=routing_decision.workflow_id,
                selected_paths=list(selected_paths),
                clarification_question=proposal.clarification_question
                or "Confirm these files are the right scope, or narrow the request further.",
                requires_confirmation=False,
                actions=self._clarify_actions(
                    workflow_id=routing_decision.workflow_id,
                    selected_paths=selected_paths,
                    include_apply=True,
                ),
                metadata={
                    "scope_origin": "proposal",
                    "signals": list(proposal.signals),
                    "scope_policy_auto_apply_max_paths": scope_policy.auto_apply_max_paths,
                },
            )

        if proposal.confidence < self._AUTO_SCOPE_THRESHOLD:
            return HarnessDecision(
                decision_type="clarify_scope",
                message="This looks like a local patch, but the inferred scope needs confirmation.",
                rationale=proposal.rationale,
                confidence=float(proposal.confidence or 0.0),
                recommended_workflow_id=routing_decision.workflow_id,
                selected_paths=list(selected_paths),
                clarification_question=proposal.clarification_question or "Confirm whether these files are the right scope.",
                requires_confirmation=False,
                actions=self._clarify_actions(
                    workflow_id=routing_decision.workflow_id,
                    selected_paths=selected_paths,
                    include_apply=True,
                ),
                metadata={"scope_origin": "proposal", "signals": list(proposal.signals)},
            )

        return HarnessDecision(
            decision_type="auto_patch",
            message="Scoped patch ready to apply.",
            rationale=proposal.rationale,
            confidence=float(proposal.confidence or 0.0),
            recommended_workflow_id=routing_decision.workflow_id,
            selected_paths=list(selected_paths),
            requires_confirmation=False,
            actions=[
                HarnessDecisionAction(
                    action_id="review_patch",
                    label="Review patch",
                    action_type="review_patch",
                    workflow_id=routing_decision.workflow_id,
                )
            ],
            metadata={"scope_origin": "proposal", "signals": list(proposal.signals)},
        )

    def for_coding_result(
        self,
        *,
        routing_decision: RefinementRoutingDecision,
        selected_paths: list[str],
    ) -> HarnessDecision:
        return HarnessDecision(
            decision_type="auto_patch",
            message="Scoped patch applied.",
            rationale=str(routing_decision.change_intent.rationale or routing_decision.explanation).strip(),
            confidence=float(routing_decision.change_intent.confidence or 0.0),
            recommended_workflow_id=routing_decision.workflow_id,
            selected_paths=list(selected_paths),
            requires_confirmation=False,
            actions=[
                HarnessDecisionAction(
                    action_id="review_patch",
                    label="Review patch",
                    action_type="review_patch",
                    workflow_id=routing_decision.workflow_id,
                )
            ],
            metadata={"scope_origin": "applied"},
        )

    def for_contract_surface_plan(
        self,
        *,
        routing_decision: RefinementRoutingDecision,
        plan: ContractSurfacePlan,
    ) -> HarnessDecision:
        """Shape the decision for a contract surface planning result.

        If the plan identifies specific surfaces and confidence is sufficient,
        returns a targeted_regeneration decision with the surface plan in
        metadata. Otherwise falls back to workflow_reentry.
        """
        workflow_id = routing_decision.workflow_id
        confidence = float(plan.confidence)
        rationale = str(routing_decision.change_intent.rationale or "").strip()

        if plan.fallback_to_workflow or not plan.surfaces:
            return HarnessDecision(
                decision_type="workflow_reentry",
                message=f"Change scope is too broad for targeted regeneration. Recommended route: {workflow_id}.",
                rationale=plan.fallback_reason or rationale or "falling back to workflow route",
                confidence=confidence,
                recommended_workflow_id=workflow_id,
                requires_confirmation=False,
                actions=[
                    HarnessDecisionAction(
                        action_id="run_recommended_workflow",
                        label=f"Continue in {workflow_id}",
                        action_type="run_workflow",
                        workflow_id=workflow_id,
                    )
                ],
                metadata={
                    "change_class": routing_decision.change_intent.change_class.value,
                    "workflow_sequence": routing_decision.workflow_sequence,
                    "contract_surface_fallback": True,
                    "fallback_reason": plan.fallback_reason,
                },
            )

        surface_labels = [
            f"{s.kind} ({s.target_id})" for s in plan.surfaces
        ]
        return HarnessDecision(
            decision_type="targeted_regeneration",
            message=plan.summary,
            rationale=rationale or f"Targeted update across {len(plan.surfaces)} contract surface(s).",
            confidence=confidence,
            recommended_workflow_id=workflow_id,
            requires_confirmation=False,
            actions=[
                HarnessDecisionAction(
                    action_id="review_surface_plan",
                    label="Review surface plan",
                    action_type="review_surface",
                    workflow_id=workflow_id,
                    metadata={
                        "surface_count": len(plan.surfaces),
                        "surfaces": surface_labels,
                    },
                ),
                HarnessDecisionAction(
                    action_id="apply_surface_plan",
                    label="Apply targeted update",
                    action_type="run_workflow",
                    workflow_id=workflow_id,
                ),
            ],
            metadata={
                "change_class": routing_decision.change_intent.change_class.value,
                "workflow_sequence": routing_decision.workflow_sequence,
                "contract_surface_plan": plan.model_dump(mode="python"),
                "surface_labels": surface_labels,
                "requires_schema_migration": plan.requires_schema_migration,
            },
        )

    @staticmethod
    def _clarify_actions(
        *,
        workflow_id: str,
        selected_paths: list[str],
        include_apply: bool,
    ) -> list[HarnessDecisionAction]:
        actions: list[HarnessDecisionAction] = []
        if include_apply and selected_paths:
            actions.append(
                HarnessDecisionAction(
                    action_id="apply_proposed_scope",
                    label="Apply proposed scope",
                    action_type="apply_scope",
                    workflow_id=workflow_id,
                )
            )
        actions.append(
            HarnessDecisionAction(
                action_id="clarify_scope",
                label="Clarify scope",
                action_type="clarify",
                workflow_id=workflow_id,
            )
        )
        return actions


_decision_policy: Optional[FirstPartyHarnessDecisionPolicy] = None


def get_harness_decision_policy() -> FirstPartyHarnessDecisionPolicy:
    global _decision_policy
    if _decision_policy is None:
        _decision_policy = FirstPartyHarnessDecisionPolicy()
    return _decision_policy
