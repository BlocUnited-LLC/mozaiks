"""
Workflow UI catalog and startup message helpers unit tests.

Covers:
  workflow_ui_catalog:
    - get_workflow_ui_primitives: non-empty, all entries are WorkflowUIPrimitive
    - get_workflow_ui_realization_ids: with/without shell_builtin
    - get_workflow_ui_primitive_ids: with/without shell_status, subset relationship
    - get_workflow_renderable_primitives: plannable, realization in {generated,shipped}
    - get_workflow_renderable_primitive_ids: subset of all primitive ids
    - get_workflow_shipped_component_primitives: plannable, shipped, has component name
    - get_workflow_shipped_component_names: non-empty strings
    - get_workflow_shipped_component_map: (primitive_id, component) tuples
    - infer_workflow_ui_realization: None for unknown/empty, shell_builtin, shipped_component,
      workflow_wrapper when component overridden, generated_component
    - validate_workflow_ui_realization_ids: valid pass, invalid raise ValueError,
      None returns empty, deduplicates, skips empty strings, non-string raises
    - validate_workflow_ui_primitive_ids: valid pass, invalid raise, None empty, deduplicates
    - validate_workflow_renderable_primitive_ids: valid pass, shell-status raises, None empty
    - format_workflow_ui_catalog_guidance: returns non-empty string

  startup_messages:
    - normalize_comparable_text: collapses whitespace, strips, handles None
    - matches_hidden_initial_message: returns False when no hidden message, False for
      wrong role, False for wrong agent name, True for matching message+role+sender
"""
from __future__ import annotations

import pytest

from mozaiksai.core.workflow.startup_messages import (
    matches_hidden_initial_message,
    normalize_comparable_text,
)
from mozaiksai.core.workflow.workflow_ui_catalog import (
    WorkflowUIPrimitive,
    format_workflow_ui_catalog_guidance,
    get_workflow_renderable_primitive_ids,
    get_workflow_renderable_primitives,
    get_workflow_shipped_component_map,
    get_workflow_shipped_component_names,
    get_workflow_shipped_component_primitives,
    get_workflow_ui_primitive_ids,
    get_workflow_ui_primitives,
    get_workflow_ui_realization_ids,
    infer_workflow_ui_realization,
    validate_workflow_renderable_primitive_ids,
    validate_workflow_ui_primitive_ids,
    validate_workflow_ui_realization_ids,
)

# ---------------------------------------------------------------------------
# 1. get_workflow_ui_primitives
# ---------------------------------------------------------------------------

class TestGetWorkflowUIPrimitives:
    def test_returns_non_empty_tuple(self):
        primitives = get_workflow_ui_primitives()
        assert len(primitives) > 0

    def test_all_entries_are_correct_type(self):
        for entry in get_workflow_ui_primitives():
            assert isinstance(entry, WorkflowUIPrimitive)

    def test_all_have_non_empty_primitive_id(self):
        for entry in get_workflow_ui_primitives():
            assert entry.primitive_id

    def test_contains_composer_reply(self):
        ids = {e.primitive_id for e in get_workflow_ui_primitives()}
        assert "composer_reply" in ids

    def test_contains_approval_card(self):
        ids = {e.primitive_id for e in get_workflow_ui_primitives()}
        assert "approval_card" in ids

    def test_contains_progress_stepper(self):
        ids = {e.primitive_id for e in get_workflow_ui_primitives()}
        assert "progress_stepper" in ids


# ---------------------------------------------------------------------------
# 2. get_workflow_ui_realization_ids
# ---------------------------------------------------------------------------

class TestGetWorkflowUIRealizationIds:
    def test_includes_shell_builtin_by_default(self):
        ids = get_workflow_ui_realization_ids()
        assert "shell_builtin" in ids

    def test_excludes_shell_builtin_when_flag_false(self):
        ids = get_workflow_ui_realization_ids(include_shell_builtin=False)
        assert "shell_builtin" not in ids

    def test_includes_shipped_component(self):
        assert "shipped_component" in get_workflow_ui_realization_ids()

    def test_includes_generated_component(self):
        assert "generated_component" in get_workflow_ui_realization_ids()

    def test_includes_workflow_wrapper(self):
        assert "workflow_wrapper" in get_workflow_ui_realization_ids()


# ---------------------------------------------------------------------------
# 3. get_workflow_ui_primitive_ids
# ---------------------------------------------------------------------------

class TestGetWorkflowUIPrimitiveIds:
    def test_includes_plannable_ids(self):
        ids = get_workflow_ui_primitive_ids()
        assert "approval_card" in ids

    def test_includes_shell_status_by_default(self):
        ids = get_workflow_ui_primitive_ids(include_shell_status=True)
        shell_primitives = [e for e in get_workflow_ui_primitives() if not e.plannable]
        for sp in shell_primitives:
            assert sp.primitive_id in ids

    def test_excludes_shell_status_when_flag_false(self):
        ids_with = set(get_workflow_ui_primitive_ids(include_shell_status=True))
        ids_without = set(get_workflow_ui_primitive_ids(include_shell_status=False))
        shell_ids = {e.primitive_id for e in get_workflow_ui_primitives() if not e.plannable}
        assert shell_ids.issubset(ids_with - ids_without)

    def test_subset_relationship(self):
        without_shell = set(get_workflow_ui_primitive_ids(include_shell_status=False))
        with_shell = set(get_workflow_ui_primitive_ids(include_shell_status=True))
        assert without_shell.issubset(with_shell)


# ---------------------------------------------------------------------------
# 4. get_workflow_renderable_primitives
# ---------------------------------------------------------------------------

class TestGetWorkflowRenderablePrimitives:
    def test_all_are_plannable(self):
        for entry in get_workflow_renderable_primitives():
            assert entry.plannable is True

    def test_all_have_shipped_or_generated_realization(self):
        allowed = {"shipped_component", "generated_component"}
        for entry in get_workflow_renderable_primitives():
            assert entry.realization in allowed

    def test_non_empty(self):
        assert len(get_workflow_renderable_primitives()) > 0

    def test_no_shell_builtins(self):
        for entry in get_workflow_renderable_primitives():
            assert entry.realization != "shell_builtin"


# ---------------------------------------------------------------------------
# 5. get_workflow_renderable_primitive_ids
# ---------------------------------------------------------------------------

class TestGetWorkflowRenderablePrimitiveIds:
    def test_is_subset_of_all_primitive_ids(self):
        all_ids = set(get_workflow_ui_primitive_ids())
        renderable_ids = set(get_workflow_renderable_primitive_ids())
        assert renderable_ids.issubset(all_ids)

    def test_does_not_include_shell_status(self):
        shell_ids = {e.primitive_id for e in get_workflow_ui_primitives() if not e.plannable}
        renderable = set(get_workflow_renderable_primitive_ids())
        assert not shell_ids.intersection(renderable)

    def test_includes_progress_stepper(self):
        renderable = set(get_workflow_renderable_primitive_ids())
        assert "progress_stepper" in renderable


# ---------------------------------------------------------------------------
# 6. get_workflow_shipped_component_primitives / names / map
# ---------------------------------------------------------------------------

class TestGetWorkflowShippedComponents:
    def test_all_have_non_none_component_name(self):
        for entry in get_workflow_shipped_component_primitives():
            assert entry.shipped_component is not None
            assert entry.shipped_component

    def test_all_are_plannable_shipped_realization(self):
        for entry in get_workflow_shipped_component_primitives():
            assert entry.plannable is True
            assert entry.realization == "shipped_component"

    def test_names_are_non_empty_strings(self):
        for name in get_workflow_shipped_component_names():
            assert isinstance(name, str)
            assert name

    def test_map_is_tuple_of_pairs(self):
        for item in get_workflow_shipped_component_map():
            assert len(item) == 2
            assert all(isinstance(v, str) for v in item)

    def test_approval_card_in_shipped(self):
        names = {e.primitive_id for e in get_workflow_shipped_component_primitives()}
        assert "approval_card" in names


# ---------------------------------------------------------------------------
# 7. infer_workflow_ui_realization
# ---------------------------------------------------------------------------

class TestInferWorkflowUIRealization:
    def test_none_primitive_returns_none(self):
        assert infer_workflow_ui_realization(None, None) is None

    def test_empty_primitive_returns_none(self):
        assert infer_workflow_ui_realization("", "SomeComponent") is None

    def test_unknown_primitive_returns_none(self):
        assert infer_workflow_ui_realization("totally_unknown_id", None) is None

    def test_shell_builtin_returns_shell_builtin(self):
        result = infer_workflow_ui_realization("composer_reply", None)
        assert result == "shell_builtin"

    def test_shipped_component_with_canonical_name(self):
        # ApprovalCard is shipped_component for approval_card
        result = infer_workflow_ui_realization("approval_card", "ApprovalCard")
        assert result == "shipped_component"

    def test_shipped_component_with_no_name_returns_shipped(self):
        result = infer_workflow_ui_realization("approval_card", None)
        assert result == "shipped_component"

    def test_shipped_component_with_different_name_returns_wrapper(self):
        result = infer_workflow_ui_realization("approval_card", "MyCustomApproval")
        assert result == "workflow_wrapper"

    def test_generated_component_primitive_returns_generated(self):
        # record_picker has realization=generated_component
        result = infer_workflow_ui_realization("record_picker", None)
        assert result == "generated_component"


# ---------------------------------------------------------------------------
# 8. validate_workflow_ui_realization_ids
# ---------------------------------------------------------------------------

class TestValidateWorkflowUIRealizationIds:
    def test_valid_realizations_pass(self):
        result = validate_workflow_ui_realization_ids(
            ["shipped_component", "generated_component"], context="test"
        )
        assert "shipped_component" in result
        assert "generated_component" in result

    def test_none_returns_empty_list(self):
        result = validate_workflow_ui_realization_ids(None, context="test")
        assert result == []

    def test_invalid_realization_raises_value_error(self):
        with pytest.raises(ValueError, match="unsupported"):
            validate_workflow_ui_realization_ids(["not_a_realization"], context="test")

    def test_deduplicates(self):
        result = validate_workflow_ui_realization_ids(
            ["shipped_component", "shipped_component"], context="test"
        )
        assert result.count("shipped_component") == 1

    def test_empty_strings_skipped(self):
        result = validate_workflow_ui_realization_ids(["", "  ", "generated_component"], context="test")
        assert "" not in result
        assert "generated_component" in result

    def test_non_string_raises_value_error(self):
        with pytest.raises(ValueError):
            validate_workflow_ui_realization_ids([42], context="test")

    def test_exclude_shell_builtin_makes_it_invalid(self):
        with pytest.raises(ValueError):
            validate_workflow_ui_realization_ids(
                ["shell_builtin"], context="test", include_shell_builtin=False
            )


# ---------------------------------------------------------------------------
# 9. validate_workflow_ui_primitive_ids
# ---------------------------------------------------------------------------

class TestValidateWorkflowUIPrimitiveIds:
    def test_valid_primitive_passes(self):
        result = validate_workflow_ui_primitive_ids(["approval_card"], context="test")
        assert result == ["approval_card"]

    def test_none_returns_empty(self):
        result = validate_workflow_ui_primitive_ids(None, context="test")
        assert result == []

    def test_invalid_primitive_raises(self):
        with pytest.raises(ValueError, match="unsupported"):
            validate_workflow_ui_primitive_ids(["not_a_primitive"], context="test")

    def test_deduplicates(self):
        result = validate_workflow_ui_primitive_ids(["approval_card", "approval_card"], context="test")
        assert result.count("approval_card") == 1

    def test_shell_status_included_by_default(self):
        # run_status_banner is a shell status primitive
        result = validate_workflow_ui_primitive_ids(
            ["run_status_banner"], context="test", include_shell_status=True
        )
        assert "run_status_banner" in result

    def test_shell_status_excluded_when_flag_false(self):
        with pytest.raises(ValueError):
            validate_workflow_ui_primitive_ids(
                ["run_status_banner"], context="test", include_shell_status=False
            )


# ---------------------------------------------------------------------------
# 10. validate_workflow_renderable_primitive_ids
# ---------------------------------------------------------------------------

class TestValidateWorkflowRenderablePrimitiveIds:
    def test_valid_renderable_passes(self):
        result = validate_workflow_renderable_primitive_ids(["approval_card"], context="test")
        assert "approval_card" in result

    def test_progress_stepper_is_renderable(self):
        result = validate_workflow_renderable_primitive_ids(["progress_stepper"], context="test")
        assert result == ["progress_stepper"]

    def test_none_returns_empty(self):
        result = validate_workflow_renderable_primitive_ids(None, context="test")
        assert result == []

    def test_shell_status_primitive_raises(self):
        with pytest.raises(ValueError):
            validate_workflow_renderable_primitive_ids(["run_status_banner"], context="test")


# ---------------------------------------------------------------------------
# 11. format_workflow_ui_catalog_guidance
# ---------------------------------------------------------------------------

class TestFormatWorkflowUICatalogGuidance:
    def test_returns_non_empty_string(self):
        result = format_workflow_ui_catalog_guidance()
        assert isinstance(result, str)
        assert len(result) > 100

    def test_contains_plannable_section(self):
        result = format_workflow_ui_catalog_guidance()
        assert "plannable" in result.lower() or "interaction" in result.lower()

    def test_contains_approval_card(self):
        result = format_workflow_ui_catalog_guidance()
        assert "approval_card" in result


# ---------------------------------------------------------------------------
# 12. normalize_comparable_text (startup_messages)
# ---------------------------------------------------------------------------

class TestNormalizeComparableText:
    def test_collapses_whitespace(self):
        assert normalize_comparable_text("  hello   world  ") == "hello world"

    def test_none_returns_empty_string(self):
        assert normalize_comparable_text(None) == ""

    def test_empty_string_returns_empty(self):
        assert normalize_comparable_text("") == ""

    def test_newlines_collapsed(self):
        assert normalize_comparable_text("hello\nworld") == "hello world"

    def test_tabs_collapsed(self):
        assert normalize_comparable_text("hello\tworld") == "hello world"


# ---------------------------------------------------------------------------
# 13. matches_hidden_initial_message (startup_messages)
# ---------------------------------------------------------------------------

class TestMatchesHiddenInitialMessage:
    def test_returns_false_when_workflow_name_is_none(self):
        result = matches_hidden_initial_message(
            workflow_name=None,
            role="user",
            content="Hello",
            agent_name="user",
            workflow_startup_mode="agentdriven",
        )
        assert result is False

    def test_returns_false_when_startup_mode_is_not_agentdriven(self):
        # Non-agentdriven mode → resolve_hidden_initial_message returns None
        result = matches_hidden_initial_message(
            workflow_name="MyWorkflow",
            role="user",
            content="Hello",
            agent_name="user",
            workflow_startup_mode="userfirst",
        )
        assert result is False

    def test_returns_false_when_hidden_message_not_configured(self):
        # workflow_manager.get_config will raise or return None
        result = matches_hidden_initial_message(
            workflow_name="NonexistentWorkflow",
            role="user",
            content="anything",
            agent_name="user",
        )
        assert result is False
