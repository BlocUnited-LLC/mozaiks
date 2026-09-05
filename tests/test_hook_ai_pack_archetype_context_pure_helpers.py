"""
Pure helper unit tests for:
  factory_app/workflows/AgentGenerator/tools/hook_ai_pack_archetype_context.py

Covers:

  _to_pascal:
    - single word → capitalized
    - hyphenated name → each part capitalized, joined
    - empty string → empty string
    - "proposals-review-workflow" → "ProposalsReviewWorkflow"
    - multiple hyphens all processed
    - already-pascal-case word → still correct

  _detect_ai_workflow_surfaces:
    - no design_surface_map → []
    - design_surface_map not a dict → []
    - surfaces is not a list → []
    - surface without workflow surface_kind → not detected
    - surface with workflow surface_kind but no matching trigger → not detected
    - surface with review workflow trigger → detected with correct suffix/archetype
    - surface with analysis workflow trigger → detected with correct suffix/archetype
    - surface with extraction workflow trigger → detected with correct suffix/archetype
    - module_id derived by stripping suffix from trigger
    - multiple triggers on one surface → all matching detected

  _build_pattern_body:
    - empty surfaces list → header rules rendered, no per-surface blocks
    - single review surface → capability_id in output
    - archetype in output
    - workflow_startup_mode: BackendOnly in output
    - result_action in output
    - callback_endpoint includes module_id and result_action
    - extraction surface includes task_batches_required block
    - analysis surface does NOT include task_batches_required

  _build_callback_body:
    - always includes backend_request instruction
    - non-empty result for any non-empty ai_surfaces list
    - returns string
"""
from __future__ import annotations

from typing import Any

from factory_app.workflows.AgentGenerator.tools.hook_ai_pack_archetype_context import (
    _build_callback_body,
    _build_pattern_body,
    _detect_ai_workflow_surfaces,
    _to_pascal,
)

# ---------------------------------------------------------------------------
# 1. _to_pascal
# ---------------------------------------------------------------------------

class TestToPascal:
    def test_single_word(self):
        assert _to_pascal("proposals") == "Proposals"

    def test_hyphenated_name(self):
        assert _to_pascal("proposals-review-workflow") == "ProposalsReviewWorkflow"

    def test_empty_string(self):
        assert _to_pascal("") == ""

    def test_analysis_workflow(self):
        assert _to_pascal("orders-analysis-workflow") == "OrdersAnalysisWorkflow"

    def test_extraction_workflow(self):
        assert _to_pascal("items-extraction-workflow") == "ItemsExtractionWorkflow"

    def test_multiple_hyphens_all_capitalized(self):
        result = _to_pascal("a-b-c-d")
        assert result == "ABCD"

    def test_already_capitalized_word(self):
        # capitalize() lowercases rest, so "Already" → "Already"
        result = _to_pascal("already")
        assert result == "Already"


# ---------------------------------------------------------------------------
# 2. _detect_ai_workflow_surfaces
# ---------------------------------------------------------------------------

def _make_ctx(surface_map: Any) -> dict:
    return {"design_surface_map": surface_map}


class TestDetectAiWorkflowSurfaces:
    def test_no_design_surface_map_returns_empty(self):
        assert _detect_ai_workflow_surfaces({}) == []

    def test_none_context_returns_empty(self):
        assert _detect_ai_workflow_surfaces(None) == []

    def test_surface_map_not_dict_returns_empty(self):
        ctx = _make_ctx("not-a-dict")
        assert _detect_ai_workflow_surfaces(ctx) == []

    def test_non_workflow_surface_not_detected(self):
        ctx = _make_ctx({
            "surfaces": [{"surface_kind": "page", "workflow_triggers": ["orders-review-workflow"]}]
        })
        assert _detect_ai_workflow_surfaces(ctx) == []

    def test_workflow_surface_no_matching_trigger(self):
        ctx = _make_ctx({
            "surfaces": [{"surface_kind": "workflow", "workflow_triggers": ["generic-workflow"]}]
        })
        assert _detect_ai_workflow_surfaces(ctx) == []

    def test_review_workflow_trigger_detected(self):
        ctx = _make_ctx({
            "surfaces": [{
                "surface_kind": "workflow",
                "workflow_triggers": ["proposals-review-workflow"]
            }]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert len(result) == 1
        assert result[0]["capability_id"] == "proposals-review-workflow"

    def test_review_workflow_module_id_derived(self):
        ctx = _make_ctx({
            "surfaces": [{
                "surface_kind": "workflow",
                "workflow_triggers": ["proposals-review-workflow"]
            }]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert result[0]["module_id"] == "proposals"

    def test_analysis_workflow_trigger_detected(self):
        ctx = _make_ctx({
            "surfaces": [{
                "surface_kind": "workflow",
                "workflow_triggers": ["docs-analysis-workflow"]
            }]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert len(result) == 1
        assert result[0]["archetype"] == "ai_analysis"

    def test_extraction_workflow_trigger_detected(self):
        ctx = _make_ctx({
            "surfaces": [{
                "surface_kind": "workflow",
                "workflow_triggers": ["invoices-extraction-workflow"]
            }]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert len(result) == 1
        assert result[0]["archetype"] == "ai_extraction"

    def test_multiple_triggers_all_matched(self):
        ctx = _make_ctx({
            "surfaces": [{
                "surface_kind": "workflow",
                "workflow_triggers": [
                    "proposals-review-workflow",
                    "items-analysis-workflow",
                ]
            }]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert len(result) == 2

    def test_non_dict_surface_skipped(self):
        ctx = _make_ctx({
            "surfaces": ["not-a-dict", {"surface_kind": "workflow", "workflow_triggers": ["p-review-workflow"]}]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert len(result) == 1

    def test_extraction_has_task_batches_required(self):
        ctx = _make_ctx({
            "surfaces": [{
                "surface_kind": "workflow",
                "workflow_triggers": ["items-extraction-workflow"]
            }]
        })
        result = _detect_ai_workflow_surfaces(ctx)
        assert result[0].get("task_batches_required") is True


# ---------------------------------------------------------------------------
# 3. _build_pattern_body
# ---------------------------------------------------------------------------

def _review_surface() -> dict:
    return {
        "capability_id": "proposals-review-workflow",
        "module_id": "proposals",
        "archetype": "ai_review",
        "pattern": "Feedback Loop",
        "workflow_startup_mode": "BackendOnly",
        "agents": "IntakeAgent → ReviewerAgent → ResultAgent",
        "result_action": "record_review_result",
        "result_agent": "ResultAgent",
    }


class TestBuildPatternBody:
    def test_empty_surfaces_renders_header_rules(self):
        result = _build_pattern_body([])
        assert "RULES FOR AI-NATIVE WORKFLOWS" in result

    def test_capability_id_in_output(self):
        result = _build_pattern_body([_review_surface()])
        assert "proposals-review-workflow" in result

    def test_archetype_in_output(self):
        result = _build_pattern_body([_review_surface()])
        assert "ai_review" in result

    def test_backend_only_in_output(self):
        result = _build_pattern_body([_review_surface()])
        assert "BackendOnly" in result

    def test_result_action_in_output(self):
        result = _build_pattern_body([_review_surface()])
        assert "record_review_result" in result

    def test_callback_endpoint_format(self):
        result = _build_pattern_body([_review_surface()])
        assert "/api/modules/proposals/record_review_result" in result

    def test_extraction_includes_task_batches(self):
        surface = {
            **_review_surface(),
            "capability_id": "items-extraction-workflow",
            "module_id": "items",
            "archetype": "ai_extraction",
            "pattern": "Triage with Tasks",
            "agents": "TriageAgent → ExtractionWorkerAgent → SynthesisAgent",
            "result_action": "store_extraction_results",
            "result_agent": "SynthesisAgent",
            "task_batches_required": True,
        }
        result = _build_pattern_body([surface])
        assert "task_batches" in result

    def test_review_surface_does_not_include_task_batches(self):
        result = _build_pattern_body([_review_surface()])
        assert "task_batches_required" not in result

    def test_returns_string(self):
        assert isinstance(_build_pattern_body([]), str)


# ---------------------------------------------------------------------------
# 4. _build_callback_body
# ---------------------------------------------------------------------------

class TestBuildCallbackBody:
    def test_includes_backend_request_instruction(self):
        result = _build_callback_body([_review_surface()])
        assert "backend_request" in result

    def test_non_empty_for_any_surfaces(self):
        result = _build_callback_body([_review_surface()])
        assert len(result) > 0

    def test_empty_surfaces_still_returns_contract_text(self):
        result = _build_callback_body([])
        # The header/intro text is always present
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_string(self):
        assert isinstance(_build_callback_body([_review_surface()]), str)
