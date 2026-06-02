"""
Hook: Inject AI Pack Surface Context

Fires as an update_agent_state hook on DesignDocsAgent.

When the concept_blueprint includes AI-native capability packs
(ai_review_pack, ai_analysis_pack, ai_extraction_pack), this hook injects an
[AI PACK SURFACE DECLARATIONS] block into DesignDocsAgent's system message.

The block clarifies:
  - The hard constraint "no AI/LLM backend logic" means: don't implement LLM
    calls in backend docs. It does NOT mean: ignore AI-native pack surface declarations.
  - For each AI-native pack, DesignDocs must declare BOTH:
      (a) a module surface for the entity that owns the data
      (b) a workflow surface for the AI orchestration lane
  - The workflow surface must populate workflow_triggers with the canonical
    capability_id so AgentGenerator knows which bundle to generate.
  - DesignDocs declares the INTERFACE, not the implementation.

This hook is conditional — it no-ops when no AI-native packs are detected.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from factory_app.workflows._shared.hook_utils import update_agent_section

logger = logging.getLogger(__name__)

_AI_NATIVE_PACKS = frozenset({"ai_review_pack", "ai_analysis_pack", "ai_extraction_pack"})
_HEADER = "[AI PACK SURFACE DECLARATIONS]"


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _detect_ai_packs(context_variables: Any) -> list[str]:
    """Return AI-native pack ids present in concept_blueprint.capability_pack_hints."""
    detected: list[str] = []
    blueprint = _context_get(context_variables, "concept_blueprint") or {}
    if isinstance(blueprint, dict):
        for hint in blueprint.get("capability_pack_hints") or []:
            hint_str = str(hint or "").strip()
            if hint_str in _AI_NATIVE_PACKS and hint_str not in detected:
                detected.append(hint_str)
    return detected


def _pack_surface_guidance(pack_id: str) -> str:
    if pack_id == "ai_review_pack":
        return (
            "ai_review_pack detected.\n"
            "\n"
            "Create TWO surface_map entries:\n"
            "\n"
            "1. MODULE surface — the entity that gets reviewed (e.g. proposals, submissions):\n"
            "   surface_kind: module\n"
            "   owner: app\n"
            "   source_capability_packs: [ai_review_pack]\n"
            "   owned_mutations: [submit_for_review, record_review_result, ...]\n"
            "   events_emitted: [domain.{module_id}.submitted]\n"
            "   workflow_triggers: [\"{module_id}-review-workflow\"]\n"
            "\n"
            "2. WORKFLOW surface — the AI review orchestration lane:\n"
            "   surface_id: {module_id}_review_workflow\n"
            "   surface_kind: workflow\n"
            "   owner: app\n"
            "   source_capability_packs: [ai_review_pack]\n"
            "   label: {Entity} Review Workflow\n"
            "   summary: BackendOnly autonomous review workflow triggered by submission events.\n"
            "   workflow_triggers: [\"{module_id}-review-workflow\"]\n"
            "\n"
            "IMPORTANT: The workflow surface declares the CAPABILITY ID the AgentGenerator run "
            "must generate. Do NOT implement LLM logic in the backend doc. Do NOT add LLM "
            "endpoints or prompt-serving APIs. Just declare the surface boundary.\n"
            "The backend doc must document:\n"
            "  - The module actions: submit_for_review, record_review_result\n"
            "  - The domain event: domain.{module_id}.submitted\n"
            "  - The reactions.yaml wiring: routes event to {module_id}-review-workflow\n"
        )

    if pack_id == "ai_analysis_pack":
        return (
            "ai_analysis_pack detected.\n"
            "\n"
            "Create TWO surface_map entries:\n"
            "\n"
            "1. MODULE surface — the entity that gets analyzed (e.g. documents, records):\n"
            "   surface_kind: module\n"
            "   owner: app\n"
            "   source_capability_packs: [ai_analysis_pack]\n"
            "   owned_mutations: [create_{entity}, store_analysis_result, ...]\n"
            "   events_emitted: [domain.{module_id}.created]\n"
            "   workflow_triggers: [\"{module_id}-analysis-workflow\"]\n"
            "\n"
            "2. WORKFLOW surface — the AI analysis orchestration lane:\n"
            "   surface_id: {module_id}_analysis_workflow\n"
            "   surface_kind: workflow\n"
            "   owner: app\n"
            "   source_capability_packs: [ai_analysis_pack]\n"
            "   label: {Entity} Analysis Workflow\n"
            "   summary: BackendOnly autonomous analysis workflow triggered on entity creation.\n"
            "   workflow_triggers: [\"{module_id}-analysis-workflow\"]\n"
            "\n"
            "IMPORTANT: No workflow_touchpoint for this workflow — it runs BackendOnly with no "
            "user-initiated launch. The creation event triggers it automatically.\n"
            "The backend doc must document:\n"
            "  - The module actions: create_{entity}, store_analysis_result\n"
            "  - The domain event: domain.{module_id}.created\n"
            "  - The reactions.yaml wiring: routes creation event to {module_id}-analysis-workflow\n"
        )

    if pack_id == "ai_extraction_pack":
        return (
            "ai_extraction_pack detected.\n"
            "\n"
            "Create TWO surface_map entries:\n"
            "\n"
            "1. MODULE surface — the entity that holds batch records (e.g. batches, imports):\n"
            "   surface_kind: module\n"
            "   owner: app\n"
            "   source_capability_packs: [ai_extraction_pack]\n"
            "   owned_mutations: [process_batch, store_extraction_results, ...]\n"
            "   events_emitted: [domain.{module_id}.batch_submitted]\n"
            "   workflow_triggers: [\"{module_id}-extraction-workflow\"]\n"
            "\n"
            "2. WORKFLOW surface — the AI extraction orchestration lane:\n"
            "   surface_id: {module_id}_extraction_workflow\n"
            "   surface_kind: workflow\n"
            "   owner: app\n"
            "   source_capability_packs: [ai_extraction_pack]\n"
            "   label: {Entity} Extraction Workflow\n"
            "   summary: BackendOnly batch extraction workflow with parallel worker task_batches.\n"
            "   workflow_triggers: [\"{module_id}-extraction-workflow\"]\n"
            "\n"
            "The backend doc must document:\n"
            "  - The module actions: process_batch, store_extraction_results\n"
            "  - The domain event: domain.{module_id}.batch_submitted\n"
            "  - The reactions.yaml wiring: routes batch event to {module_id}-extraction-workflow\n"
        )

    return ""


def _build_body(detected_packs: list[str]) -> str:
    parts: list[str] = [
        "AI-native capability packs are in scope for this app. "
        "These packs require you to declare paired surface boundaries — "
        "a MODULE surface and a WORKFLOW surface — for each AI-native pack.\n",
        "CLARIFICATION on the [HARD CONSTRAINTS] rule "
        '"Do NOT implement AI/LLM backend logic":\n'
        "  - Do NOT add LLM API calls, embeddings, vector DBs, or prompt-serving "
        "routes to the backend doc.\n"
        "  - DO declare the workflow surface boundary with its capability_id in "
        "surface_map.workflow_triggers.\n"
        "  - DO document the deterministic module actions the AI workflow will call "
        "(submit_for_review, record_review_result, store_analysis_result, etc.).\n"
        "  - The workflow surface is an interface declaration, not an implementation.\n",
        "capability_id naming convention:\n"
        "  ai_review_pack    → {module_id}-review-workflow\n"
        "  ai_analysis_pack  → {module_id}-analysis-workflow\n"
        "  ai_extraction_pack → {module_id}-extraction-workflow\n"
        "These capability_ids are consumed by AgentGenerator PatternAgent to generate "
        "the corresponding BackendOnly workflow bundles.\n",
    ]

    for pack_id in detected_packs:
        guidance = _pack_surface_guidance(pack_id)
        if guidance:
            parts.append(f"--- {pack_id} ---\n{guidance}")

    return "\n".join(parts)


def inject_ai_pack_surface_context(
    agent: Any,
    messages: List[Dict[str, Any]],
) -> None:
    """
    update_agent_state hook for DesignDocsAgent.

    Detects AI-native capability packs in concept_blueprint.capability_pack_hints.
    When found, injects [AI PACK SURFACE DECLARATIONS] with guidance on creating
    paired module + workflow surface_map entries.

    No-ops when no AI-native packs are detected.
    """
    agent_name = getattr(agent, "name", "")
    if agent_name != "DesignDocsAgent":
        return

    context_variables = getattr(agent, "context_variables", None)

    try:
        detected = _detect_ai_packs(context_variables)
        if not detected:
            return

        body = _build_body(detected)
        update_agent_section(agent, _HEADER, body)

        logger.info(
            "[%s] Injected AI pack surface context for packs: %s",
            agent_name,
            ", ".join(detected),
        )

    except Exception as exc:
        logger.error(
            "[%s] Failed to inject AI pack surface context: %s",
            agent_name,
            exc,
        )


__all__ = ["inject_ai_pack_surface_context"]
