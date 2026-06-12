"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_ai_pack_workflow_context.py

Covers:

  _context_get:
    - None context → default returned
    - dict context with key → value returned
    - dict context missing key → default returned
    - dict context with None value → None returned (dict.get returns value, not default)
    - object with no .get and no .data → default returned

  _detect_ai_packs:
    - empty context → []
    - app_build_plan with known pack_type → detected
    - app_build_plan with non-AI pack → not detected
    - concept_blueprint with AI hint → detected
    - concept_blueprint with unknown hint → not detected
    - pack present in both plan and blueprint → deduplicated
    - non-dict pack items in capability_packs → skipped
    - multiple distinct packs → all returned

  _pack_instructions:
    - ai_review_pack → non-empty string containing "ai_review_pack"
    - ai_analysis_pack → non-empty string containing "ai_analysis_pack"
    - ai_extraction_pack → non-empty string containing "ai_extraction_pack"
    - unknown pack → empty string
    - review pack includes "HARD CONSTRAINTS"
    - review pack includes "reactions.yaml"
    - analysis pack mentions BackendOnly
    - extraction pack mentions "task_batches"

  _build_body:
    - always includes capability_id naming convention header
    - detected pack instructions included in output
    - unknown pack produces no separator block
    - multiple packs → all instruction blocks included
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_ai_pack_workflow_context import (
    _build_body,
    _context_get,
    _detect_ai_packs,
    _pack_instructions,
)

# ---------------------------------------------------------------------------
# 1. _context_get
# ---------------------------------------------------------------------------

class TestContextGet:
    def test_none_context_returns_default(self):
        assert _context_get(None, "key") is None
        assert _context_get(None, "key", "fallback") == "fallback"

    def test_dict_key_found(self):
        assert _context_get({"x": 42}, "x") == 42

    def test_dict_key_missing_returns_default(self):
        assert _context_get({}, "missing", "default") == "default"

    def test_dict_none_value_returns_none(self):
        # dict.get(key, default) returns None when key is present with value None
        result = _context_get({"key": None}, "key", "fallback")
        # The standard getter(key, default) call: dict.get("key", "fallback") = None
        assert result is None

    def test_non_dict_non_none_returns_default(self):
        assert _context_get(42, "key", "fallback") == "fallback"

    def test_string_context_returns_default(self):
        assert _context_get("not-a-dict", "key", "default") == "default"


# ---------------------------------------------------------------------------
# 2. _detect_ai_packs
# ---------------------------------------------------------------------------

class TestDetectAiPacks:
    def test_empty_context_returns_empty(self):
        assert _detect_ai_packs({}) == []

    def test_none_context_returns_empty(self):
        assert _detect_ai_packs(None) == []

    def test_ai_review_pack_in_build_plan(self):
        ctx = {
            "app_build_plan": {
                "capability_packs": [{"pack_type": "ai_review_pack"}]
            }
        }
        result = _detect_ai_packs(ctx)
        assert "ai_review_pack" in result

    def test_non_ai_pack_not_detected(self):
        ctx = {
            "app_build_plan": {
                "capability_packs": [{"pack_type": "wallet_pack"}]
            }
        }
        result = _detect_ai_packs(ctx)
        assert result == []

    def test_concept_blueprint_ai_hint(self):
        ctx = {
            "concept_blueprint": {
                "capability_pack_hints": ["ai_analysis_pack"]
            }
        }
        result = _detect_ai_packs(ctx)
        assert "ai_analysis_pack" in result

    def test_unknown_hint_not_detected(self):
        ctx = {
            "concept_blueprint": {
                "capability_pack_hints": ["unknown_pack"]
            }
        }
        result = _detect_ai_packs(ctx)
        assert result == []

    def test_pack_in_both_plan_and_blueprint_deduplicated(self):
        ctx = {
            "app_build_plan": {
                "capability_packs": [{"pack_type": "ai_review_pack"}]
            },
            "concept_blueprint": {
                "capability_pack_hints": ["ai_review_pack"]
            }
        }
        result = _detect_ai_packs(ctx)
        assert result.count("ai_review_pack") == 1

    def test_non_dict_pack_items_skipped(self):
        ctx = {
            "app_build_plan": {
                "capability_packs": ["not-a-dict", {"pack_type": "ai_review_pack"}]
            }
        }
        result = _detect_ai_packs(ctx)
        assert "ai_review_pack" in result

    def test_multiple_distinct_packs_all_returned(self):
        ctx = {
            "app_build_plan": {
                "capability_packs": [
                    {"pack_type": "ai_review_pack"},
                    {"pack_type": "ai_analysis_pack"},
                ]
            }
        }
        result = _detect_ai_packs(ctx)
        assert "ai_review_pack" in result
        assert "ai_analysis_pack" in result

    def test_extraction_pack_detected(self):
        ctx = {
            "concept_blueprint": {
                "capability_pack_hints": ["ai_extraction_pack"]
            }
        }
        result = _detect_ai_packs(ctx)
        assert "ai_extraction_pack" in result

    def test_no_capability_packs_key_returns_empty(self):
        ctx = {"app_build_plan": {}}
        assert _detect_ai_packs(ctx) == []


# ---------------------------------------------------------------------------
# 3. _pack_instructions
# ---------------------------------------------------------------------------

class TestPackInstructions:
    def test_review_pack_returns_non_empty(self):
        result = _pack_instructions("ai_review_pack")
        assert result != ""

    def test_analysis_pack_returns_non_empty(self):
        result = _pack_instructions("ai_analysis_pack")
        assert result != ""

    def test_extraction_pack_returns_non_empty(self):
        result = _pack_instructions("ai_extraction_pack")
        assert result != ""

    def test_unknown_pack_returns_empty(self):
        assert _pack_instructions("unknown_pack") == ""

    def test_empty_string_pack_returns_empty(self):
        assert _pack_instructions("") == ""

    def test_review_pack_includes_hard_constraints(self):
        result = _pack_instructions("ai_review_pack")
        assert "HARD CONSTRAINTS" in result

    def test_review_pack_includes_reactions_yaml(self):
        result = _pack_instructions("ai_review_pack")
        assert "reactions.yaml" in result

    def test_review_pack_mentions_ai_review_pack(self):
        result = _pack_instructions("ai_review_pack")
        assert "ai_review_pack" in result

    def test_analysis_pack_mentions_backend_only(self):
        result = _pack_instructions("ai_analysis_pack")
        assert "BackendOnly" in result

    def test_extraction_pack_mentions_task_batches(self):
        result = _pack_instructions("ai_extraction_pack")
        assert "task_batches" in result

    def test_review_pack_mentions_workflow_surface_constraint(self):
        result = _pack_instructions("ai_review_pack")
        assert "surface_kind" in result

    def test_extraction_pack_mentions_batch(self):
        result = _pack_instructions("ai_extraction_pack")
        assert "batch" in result.lower()


# ---------------------------------------------------------------------------
# 4. _build_body
# ---------------------------------------------------------------------------

class TestBuildBody:
    def test_always_includes_capability_id_naming(self):
        result = _build_body(["ai_review_pack"])
        assert "capability_id" in result or "naming" in result.lower()

    def test_review_pack_block_included(self):
        result = _build_body(["ai_review_pack"])
        assert "ai_review_pack" in result

    def test_analysis_pack_block_included(self):
        result = _build_body(["ai_analysis_pack"])
        assert "ai_analysis_pack" in result

    def test_extraction_pack_block_included(self):
        result = _build_body(["ai_extraction_pack"])
        assert "ai_extraction_pack" in result

    def test_multiple_packs_all_included(self):
        result = _build_body(["ai_review_pack", "ai_analysis_pack"])
        assert "ai_review_pack" in result
        assert "ai_analysis_pack" in result

    def test_empty_packs_list_no_separator_blocks(self):
        result = _build_body([])
        assert "---" not in result

    def test_separator_line_for_known_pack(self):
        result = _build_body(["ai_review_pack"])
        assert "--- ai_review_pack ---" in result

    def test_header_includes_agentgenerator_context(self):
        result = _build_body(["ai_review_pack"])
        assert "AgentGenerator" in result
