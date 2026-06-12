"""
Workflow helper contract tests.

Covers:
  orchestration_utils._normalize_human_in_the_loop:
    - bool passthrough (True/False)
    - truthy string values ("true","yes","1","on","always")
    - falsy string values ("false","no","0","of","never")
    - unknown string defaults to False
    - numeric 1/0
    - None defaults to False

  contract_validation._context_expression_refs:
    - extracts ${var} references from expression string
    - multiple references
    - no references returns empty set
    - duplicate refs deduplicated

  contract_validation.validate_workflow_context_contract:
    - passes when all context variables declared
    - raises ValueError when agent view references undeclared variable
    - raises ValueError when transition_graph context_equals references undeclared variable
    - raises ValueError when context_expression references undeclared variable
    - skips task_batches when file does not exist (no error)

  transition_ui_catalog.get_transition_ui_primitives:
    - returns non-empty tuple
    - all entries are TransitionUIPrimitive instances
    - contains expected primitive IDs (LauncherScreen, ConfirmScreen, etc.)

  transition_ui_catalog.format_transition_ui_catalog_guidance:
    - returns non-empty string
    - contains LauncherScreen
    - contains rules section
"""
from __future__ import annotations

import pytest

from mozaiksai.core.workflow.contract_validation import (
    _context_expression_refs,
    validate_workflow_context_contract,
)
from mozaiksai.core.workflow.orchestration_utils import _normalize_human_in_the_loop
from mozaiksai.core.workflow.transition_ui_catalog import (
    TransitionUIPrimitive,
    format_transition_ui_catalog_guidance,
    get_transition_ui_primitives,
)

# ---------------------------------------------------------------------------
# 1. _normalize_human_in_the_loop
# ---------------------------------------------------------------------------

class TestNormalizeHumanInTheLoop:
    def test_true_bool(self):
        assert _normalize_human_in_the_loop(True) is True

    def test_false_bool(self):
        assert _normalize_human_in_the_loop(False) is False

    @pytest.mark.parametrize("value", ["true", "yes", "1", "on", "always"])
    def test_truthy_strings(self, value):
        assert _normalize_human_in_the_loop(value) is True

    @pytest.mark.parametrize("value", ["false", "no", "0", "of", "never"])
    def test_falsy_strings(self, value):
        assert _normalize_human_in_the_loop(value) is False

    def test_unknown_string_defaults_false(self):
        assert _normalize_human_in_the_loop("maybe") is False

    def test_numeric_one(self):
        assert _normalize_human_in_the_loop(1) is True

    def test_numeric_zero(self):
        assert _normalize_human_in_the_loop(0) is False

    def test_none_defaults_false(self):
        assert _normalize_human_in_the_loop(None) is False

    def test_case_insensitive_TRUE(self):
        assert _normalize_human_in_the_loop("TRUE") is True

    def test_case_insensitive_FALSE(self):
        assert _normalize_human_in_the_loop("FALSE") is False

    def test_whitespace_stripped_from_string(self):
        assert _normalize_human_in_the_loop("  true  ") is True


# ---------------------------------------------------------------------------
# 2. _context_expression_refs
# ---------------------------------------------------------------------------

class TestContextExpressionRefs:
    def test_single_ref(self):
        result = _context_expression_refs("${my_var} == 'done'")
        assert result == {"my_var"}

    def test_multiple_refs(self):
        result = _context_expression_refs("${a} and ${b}")
        assert result == {"a", "b"}

    def test_no_refs_returns_empty(self):
        result = _context_expression_refs("status == 'active'")
        assert result == set()

    def test_duplicate_refs_deduplicated(self):
        result = _context_expression_refs("${x} == ${x}")
        assert result == {"x"}

    def test_empty_string_returns_empty(self):
        assert _context_expression_refs("") == set()

    def test_refs_stripped_of_whitespace(self):
        result = _context_expression_refs("${ var_name }")
        assert "var_name" in result


# ---------------------------------------------------------------------------
# 3. validate_workflow_context_contract
# ---------------------------------------------------------------------------

def _make_config(
    definitions: list[str] | None = None,
    agent_views: dict | None = None,
    transition_rules: list | None = None,
) -> dict:
    ctx: dict = {}
    if definitions is not None or agent_views is not None:
        ctx_block: dict = {}
        if definitions is not None:
            ctx_block["definitions"] = {var: {} for var in definitions}
        if agent_views is not None:
            ctx_block["agents"] = agent_views
        ctx["context_variables"] = ctx_block
    if transition_rules is not None:
        ctx["transition_graph"] = {"transition_rules": transition_rules}
    return ctx


class TestValidateWorkflowContextContract:
    def test_passes_when_all_variables_declared(self, tmp_path):
        config = _make_config(
            definitions=["my_var"],
            agent_views={"AgentA": {"variables": ["my_var"]}},
        )
        # Should not raise
        validate_workflow_context_contract(
            workflow_name="TestWorkflow",
            workflow_config=config,
            workflow_path=tmp_path,
        )

    def test_raises_when_agent_view_references_undeclared_variable(self, tmp_path):
        config = _make_config(
            definitions=["declared_var"],
            agent_views={"AgentA": {"variables": ["undeclared_var"]}},
        )
        with pytest.raises(ValueError, match="undeclared context variable"):
            validate_workflow_context_contract(
                workflow_name="TestWorkflow",
                workflow_config=config,
                workflow_path=tmp_path,
            )

    def test_raises_when_context_equals_references_undeclared(self, tmp_path):
        config = _make_config(
            definitions=["known"],
            transition_rules=[
                {
                    "condition_type": "context_equals",
                    "condition_key": "unknown_var",
                    "source_agent": "AgentA",
                    "target_agent": "AgentB",
                }
            ],
        )
        with pytest.raises(ValueError, match="undeclared context variable"):
            validate_workflow_context_contract(
                workflow_name="TestWorkflow",
                workflow_config=config,
                workflow_path=tmp_path,
            )

    def test_raises_when_context_expression_references_undeclared(self, tmp_path):
        config = _make_config(
            definitions=["known"],
            transition_rules=[
                {
                    "condition_type": "context_expression",
                    "context_expression": "${missing_var} == 'done'",
                    "source_agent": "AgentA",
                    "target_agent": "AgentB",
                }
            ],
        )
        with pytest.raises(ValueError, match="undeclared context variable"):
            validate_workflow_context_contract(
                workflow_name="TestWorkflow",
                workflow_config=config,
                workflow_path=tmp_path,
            )

    def test_no_error_when_no_context_variables_section(self, tmp_path):
        # Empty config — no context_variables or transition_graph
        validate_workflow_context_contract(
            workflow_name="TestWorkflow",
            workflow_config={},
            workflow_path=tmp_path,
        )

    def test_skips_task_batches_when_file_absent(self, tmp_path):
        # task_batches.yaml does not exist → no error
        config = _make_config(definitions=["x"])
        validate_workflow_context_contract(
            workflow_name="TestWorkflow",
            workflow_config=config,
            workflow_path=tmp_path,
        )

    def test_context_equals_with_declared_variable_passes(self, tmp_path):
        config = _make_config(
            definitions=["status"],
            transition_rules=[
                {
                    "condition_type": "context_equals",
                    "condition_key": "status",
                    "source_agent": "A",
                    "target_agent": "B",
                }
            ],
        )
        validate_workflow_context_contract(
            workflow_name="TestWorkflow",
            workflow_config=config,
            workflow_path=tmp_path,
        )


# ---------------------------------------------------------------------------
# 4. get_transition_ui_primitives
# ---------------------------------------------------------------------------

class TestGetTransitionUIPrimitives:
    def test_returns_non_empty_tuple(self):
        primitives = get_transition_ui_primitives()
        assert len(primitives) > 0

    def test_all_are_transition_ui_primitive_instances(self):
        for entry in get_transition_ui_primitives():
            assert isinstance(entry, TransitionUIPrimitive)

    def test_contains_launcher_screen(self):
        ids = {e.primitive_id for e in get_transition_ui_primitives()}
        assert "LauncherScreen" in ids

    def test_contains_confirm_screen(self):
        ids = {e.primitive_id for e in get_transition_ui_primitives()}
        assert "ConfirmScreen" in ids

    def test_contains_transition_choice_panel(self):
        ids = {e.primitive_id for e in get_transition_ui_primitives()}
        assert "TransitionChoicePanel" in ids

    def test_all_have_non_empty_import_path(self):
        for entry in get_transition_ui_primitives():
            assert entry.import_path


# ---------------------------------------------------------------------------
# 5. format_transition_ui_catalog_guidance
# ---------------------------------------------------------------------------

class TestFormatTransitionUICatalogGuidance:
    def test_returns_non_empty_string(self):
        result = format_transition_ui_catalog_guidance()
        assert isinstance(result, str)
        assert len(result) > 50

    def test_contains_launcher_screen(self):
        result = format_transition_ui_catalog_guidance()
        assert "LauncherScreen" in result

    def test_contains_rules_section(self):
        result = format_transition_ui_catalog_guidance()
        assert "Rules:" in result

    def test_contains_shell_ownership_reference(self):
        result = format_transition_ui_catalog_guidance()
        assert "shell" in result.lower()
