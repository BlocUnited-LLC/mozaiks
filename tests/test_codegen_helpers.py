"""
Code generation pure helper unit tests.

Covers helpers from:
  - refinement_harness_codegen.py: _dump_yaml, _safe_prompt_id, _safe_prompt_path
  - app_backend_admin_codegen.py: _indent_block, _render_admin_config_module
  - orchestration_patterns.py: _next_agent_after_trigger

  _dump_yaml:
    - dict with keys → valid YAML string
    - sort_keys=False preserved (insertion order)
    - allow_unicode=True (unicode chars pass through)

  _safe_prompt_id:
    - valid id unchanged
    - non-alphanumeric replaced with underscore
    - leading/trailing underscores stripped
    - empty string after sanitization → ValueError

  _safe_prompt_path:
    - no raw path → default path constructed from prompt_id
    - valid path under refinement_harness/prompts/ → returned
    - absolute path → ValueError
    - path with ".." traversal → ValueError
    - path not under refinement_harness/prompts/ → ValueError
    - path without .yaml suffix → ValueError

  _indent_block:
    - 0 spaces → unchanged
    - positive spaces → each line prefixed
    - empty lines not prefixed (blank lines preserved)
    - multiline text → all lines prefixed

  _render_admin_config_module:
    - returns string starting with "_ADMIN_CONFIG ="
    - includes "def get_admin_config():" function
    - payload dict appears in output
    - returns module as a string (not bytes)

  _next_agent_after_trigger:
    - no matching rule → None
    - matching rule → target_agent returned
    - matching rule with "terminate" target → skipped
    - multiple rules, first non-terminate returned
    - empty rules list → None
"""
from __future__ import annotations

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.app_backend_admin_codegen import (
    _indent_block,
    _render_admin_config_module,
)
from factory_app.workflows.AppGenerator.tools.refinement_harness_codegen import (
    _dump_yaml,
    _safe_prompt_id,
    _safe_prompt_path,
)
from mozaiksai.core.workflow.orchestration_patterns import (
    _next_agent_after_trigger,
)

# ---------------------------------------------------------------------------
# 1. _dump_yaml
# ---------------------------------------------------------------------------

class TestDumpYaml:
    def test_dict_produces_yaml_string(self):
        result = _dump_yaml({"key": "value"})
        parsed = yaml.safe_load(result)
        assert parsed == {"key": "value"}

    def test_result_is_string(self):
        assert isinstance(_dump_yaml({"key": "val"}), str)

    def test_empty_dict_returns_braces(self):
        result = _dump_yaml({})
        assert "{}" in result

    def test_unicode_passes_through(self):
        result = _dump_yaml({"name": "café"})
        assert "café" in result

    def test_nested_dict_serialized(self):
        data = {"outer": {"inner": "value"}}
        result = _dump_yaml(data)
        parsed = yaml.safe_load(result)
        assert parsed == data


# ---------------------------------------------------------------------------
# 2. _safe_prompt_id
# ---------------------------------------------------------------------------

class TestSafePromptId:
    def test_valid_id_unchanged(self):
        assert _safe_prompt_id("my_prompt") == "my_prompt"

    def test_spaces_replaced_with_underscore(self):
        result = _safe_prompt_id("my prompt")
        assert result == "my_prompt"

    def test_special_chars_replaced(self):
        result = _safe_prompt_id("my.prompt!")
        # dot and ! become underscores, consecutive → single
        assert "." not in result
        assert "!" not in result

    def test_leading_trailing_underscores_stripped(self):
        result = _safe_prompt_id("  !!my_prompt!!  ")
        assert not result.startswith("_")
        assert not result.endswith("_")
        assert "my_prompt" in result

    def test_empty_after_sanitization_raises(self):
        with pytest.raises(ValueError, match="safe character"):
            _safe_prompt_id("!!!")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _safe_prompt_id("")

    def test_dashes_preserved(self):
        assert _safe_prompt_id("my-prompt") == "my-prompt"

    def test_alphanumeric_preserved(self):
        assert _safe_prompt_id("prompt123") == "prompt123"


# ---------------------------------------------------------------------------
# 3. _safe_prompt_path
# ---------------------------------------------------------------------------

class TestSafePromptPath:
    def test_no_raw_path_uses_default(self):
        result = _safe_prompt_path(None, "my_prompt")
        assert result == "refinement_harness/prompts/my_prompt.yaml"

    def test_empty_raw_path_uses_default(self):
        result = _safe_prompt_path("", "my_prompt")
        assert result == "refinement_harness/prompts/my_prompt.yaml"

    def test_valid_path_returned(self):
        result = _safe_prompt_path(
            "refinement_harness/prompts/refinement.yaml",
            "refinement",
        )
        assert result == "refinement_harness/prompts/refinement.yaml"

    def test_backslash_normalized(self):
        result = _safe_prompt_path(
            "refinement_harness\\prompts\\my_prompt.yaml",
            "my_prompt",
        )
        assert result == "refinement_harness/prompts/my_prompt.yaml"

    def test_absolute_path_raises(self):
        with pytest.raises(ValueError, match="refinement_harness/prompts"):
            _safe_prompt_path("/absolute/path.yaml", "prompt")

    def test_dotdot_traversal_raises(self):
        with pytest.raises(ValueError):
            _safe_prompt_path("refinement_harness/prompts/../secrets.yaml", "prompt")

    def test_wrong_directory_raises(self):
        with pytest.raises(ValueError):
            _safe_prompt_path("app/config/prompt.yaml", "prompt")

    def test_non_yaml_extension_raises(self):
        with pytest.raises(ValueError):
            _safe_prompt_path("refinement_harness/prompts/my_prompt.txt", "my_prompt")

    def test_prompt_id_sanitized_in_default(self):
        result = _safe_prompt_path(None, "my prompt!!")
        # Should sanitize the prompt_id before using in default path
        assert result.startswith("refinement_harness/prompts/")
        assert result.endswith(".yaml")


# ---------------------------------------------------------------------------
# 4. _indent_block
# ---------------------------------------------------------------------------

class TestIndentBlock:
    def test_zero_spaces_unchanged(self):
        assert _indent_block("line1\nline2", 0) == "line1\nline2"

    def test_four_spaces_added(self):
        result = _indent_block("line1\nline2", 4)
        assert result == "    line1\n    line2"

    def test_empty_lines_not_prefixed(self):
        result = _indent_block("line1\n\nline2", 4)
        lines = result.split("\n")
        assert lines[1] == ""  # empty line stays empty

    def test_single_line(self):
        assert _indent_block("hello", 2) == "  hello"

    def test_empty_string(self):
        assert _indent_block("", 4) == ""


# ---------------------------------------------------------------------------
# 5. _render_admin_config_module
# ---------------------------------------------------------------------------

class TestRenderAdminConfigModule:
    def test_result_is_string(self):
        result = _render_admin_config_module({"title": "Admin"})
        assert isinstance(result, str)

    def test_starts_with_admin_config_assignment(self):
        result = _render_admin_config_module({"title": "Admin"})
        assert result.startswith("_ADMIN_CONFIG = ")

    def test_includes_get_admin_config_function(self):
        result = _render_admin_config_module({"title": "Admin"})
        assert "def get_admin_config():" in result
        assert "return _ADMIN_CONFIG" in result

    def test_payload_in_output(self):
        result = _render_admin_config_module({"title": "My Admin Panel"})
        assert "My Admin Panel" in result

    def test_nested_payload_in_output(self):
        result = _render_admin_config_module({"section": {"key": "value"}})
        assert "section" in result
        assert "value" in result


# ---------------------------------------------------------------------------
# 6. _next_agent_after_trigger
# ---------------------------------------------------------------------------

class TestNextAgentAfterTrigger:
    def _rules(self, source, target):
        return [{"source_agent": source, "target_agent": target}]

    def test_no_rules_returns_none(self):
        result = _next_agent_after_trigger(transition_rules=[], trigger_agent="EntryAgent")
        assert result is None

    def test_matching_rule_returns_target(self):
        result = _next_agent_after_trigger(
            transition_rules=self._rules("EntryAgent", "PlannerAgent"),
            trigger_agent="EntryAgent",
        )
        assert result == "PlannerAgent"

    def test_no_matching_source_returns_none(self):
        result = _next_agent_after_trigger(
            transition_rules=self._rules("OtherAgent", "PlannerAgent"),
            trigger_agent="EntryAgent",
        )
        assert result is None

    def test_terminate_target_skipped(self):
        rules = [
            {"source_agent": "EntryAgent", "target_agent": "terminate"},
        ]
        result = _next_agent_after_trigger(
            transition_rules=rules,
            trigger_agent="EntryAgent",
        )
        assert result is None

    def test_first_non_terminate_returned(self):
        rules = [
            {"source_agent": "EntryAgent", "target_agent": "terminate"},
            {"source_agent": "EntryAgent", "target_agent": "PlannerAgent"},
        ]
        result = _next_agent_after_trigger(
            transition_rules=rules,
            trigger_agent="EntryAgent",
        )
        assert result == "PlannerAgent"

    def test_whitespace_stripped_from_source(self):
        rules = [{"source_agent": "  EntryAgent  ", "target_agent": "NextAgent"}]
        result = _next_agent_after_trigger(
            transition_rules=rules,
            trigger_agent="EntryAgent",
        )
        assert result == "NextAgent"

    def test_empty_target_skipped(self):
        rules = [
            {"source_agent": "EntryAgent", "target_agent": ""},
            {"source_agent": "EntryAgent", "target_agent": "ValidAgent"},
        ]
        result = _next_agent_after_trigger(
            transition_rules=rules,
            trigger_agent="EntryAgent",
        )
        assert result == "ValidAgent"
