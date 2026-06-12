"""
Context variable schema pure unit tests.

Covers:
  _required_text:
    - non-empty string returned as stripped
    - None raises ValueError
    - empty string raises ValueError
    - whitespace-only raises ValueError

  _optional_text:
    - None → None
    - empty string → None
    - whitespace-only → None
    - non-empty string → stripped

  _normalize_string_list:
    - empty list → empty list
    - strips whitespace
    - filters empty strings
    - deduplicates preserving first-seen order
    - purely empty-after-strip entries removed

  ContextTriggerMatch:
    - equals accepted
    - contains accepted
    - regex accepted
    - all None → ValidationError
    - leading/trailing whitespace stripped

  ContextTriggerSpec:
    - agent_text requires agent and match
    - agent_text without agent → ValidationError
    - agent_text without match → ValidationError
    - user_text requires match
    - ui_response requires tool
    - ui_response without tool → ValidationError
    - valid agent_text

  ContextVariableSource:
    - config type with env_var
    - whitespace env_var normalized
    - empty env_var normalized to None
    - fields normalized as string list
    - inputs deduplicated

  ContextVariableDefinition:
    - valid with source
    - type optional
    - description whitespace stripped

  ContextAgentView:
    - empty variables accepted
    - variables deduplicated
    - whitespace entries stripped

  ContextVariablesPlan:
    - empty definitions and agents
    - definitions accepted
    - agent referencing undeclared variable → ValidationError
    - agent referencing declared variable → valid
    - empty-key definition name → ValidationError
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.context.schema import (
    ContextAgentView,
    ContextTriggerMatch,
    ContextTriggerSpec,
    ContextVariableDefinition,
    ContextVariableSource,
    ContextVariablesPlan,
    _normalize_string_list,
    _optional_text,
    _required_text,
)

# ---------------------------------------------------------------------------
# 1. _required_text
# ---------------------------------------------------------------------------

class TestRequiredText:
    def test_non_empty_string_returned(self):
        assert _required_text("hello", field_name="f") == "hello"

    def test_strips_whitespace(self):
        assert _required_text("  hello  ", field_name="f") == "hello"

    def test_none_raises(self):
        with pytest.raises(ValueError, match="f"):
            _required_text(None, field_name="f")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _required_text("", field_name="f")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError):
            _required_text("   ", field_name="f")

    def test_zero_as_value_raises(self):
        # implementation uses `str(value or "")`, so falsy 0 → "" → raises
        with pytest.raises(ValueError):
            _required_text(0, field_name="f")


# ---------------------------------------------------------------------------
# 2. _optional_text
# ---------------------------------------------------------------------------

class TestOptionalText:
    def test_none_returns_none(self):
        assert _optional_text(None) is None

    def test_empty_string_returns_none(self):
        assert _optional_text("") is None

    def test_whitespace_only_returns_none(self):
        assert _optional_text("   ") is None

    def test_non_empty_string_returned_stripped(self):
        assert _optional_text("  hello  ") == "hello"

    def test_plain_string_passthrough(self):
        assert _optional_text("world") == "world"


# ---------------------------------------------------------------------------
# 3. _normalize_string_list
# ---------------------------------------------------------------------------

class TestNormalizeStringList:
    def test_empty_list_returns_empty(self):
        assert _normalize_string_list([]) == []

    def test_strips_whitespace(self):
        assert _normalize_string_list(["  a  ", "b"]) == ["a", "b"]

    def test_filters_empty_strings(self):
        assert _normalize_string_list(["", "a", "  "]) == ["a"]

    def test_deduplicates_preserving_order(self):
        result = _normalize_string_list(["b", "a", "b", "c", "a"])
        assert result == ["b", "a", "c"]

    def test_all_empty_returns_empty(self):
        assert _normalize_string_list(["", "  ", "\t"]) == []

    def test_single_item(self):
        assert _normalize_string_list(["only"]) == ["only"]


# ---------------------------------------------------------------------------
# 4. ContextTriggerMatch
# ---------------------------------------------------------------------------

def _make_source(**kw):
    """Minimal valid ContextVariableSource."""
    defaults = {"type": "config"}
    defaults.update(kw)
    return defaults


class TestContextTriggerMatch:
    def test_equals_accepted(self):
        m = ContextTriggerMatch(equals="yes")
        assert m.equals == "yes"

    def test_contains_accepted(self):
        m = ContextTriggerMatch(contains="foo")
        assert m.contains == "foo"

    def test_regex_accepted(self):
        m = ContextTriggerMatch(regex="^foo")
        assert m.regex == "^foo"

    def test_all_none_rejected(self):
        with pytest.raises(ValidationError):
            ContextTriggerMatch(equals=None, contains=None, regex=None)

    def test_whitespace_stripped(self):
        m = ContextTriggerMatch(equals="  yes  ")
        assert m.equals == "yes"

    def test_whitespace_only_treated_as_none_rejects(self):
        # whitespace → None → still no match set → ValidationError
        with pytest.raises(ValidationError):
            ContextTriggerMatch(equals="   ")

    def test_multiple_fields_accepted(self):
        m = ContextTriggerMatch(equals="yes", contains="y")
        assert m.equals == "yes"
        assert m.contains == "y"


# ---------------------------------------------------------------------------
# 5. ContextTriggerSpec
# ---------------------------------------------------------------------------

class TestContextTriggerSpec:
    def _match(self):
        return ContextTriggerMatch(equals="yes")

    def test_agent_text_valid(self):
        spec = ContextTriggerSpec(type="agent_text", agent="MyAgent", match=self._match())
        assert spec.type == "agent_text"
        assert spec.agent == "MyAgent"

    def test_agent_text_missing_agent_raises(self):
        with pytest.raises(ValidationError, match="agent"):
            ContextTriggerSpec(type="agent_text", agent=None, match=self._match())

    def test_agent_text_missing_match_raises(self):
        with pytest.raises(ValidationError, match="match"):
            ContextTriggerSpec(type="agent_text", agent="MyAgent", match=None)

    def test_user_text_requires_match(self):
        with pytest.raises(ValidationError, match="match"):
            ContextTriggerSpec(type="user_text", match=None)

    def test_user_text_valid(self):
        spec = ContextTriggerSpec(type="user_text", match=self._match())
        assert spec.type == "user_text"

    def test_ui_response_requires_tool(self):
        with pytest.raises(ValidationError, match="tool"):
            ContextTriggerSpec(type="ui_response", tool=None)

    def test_ui_response_valid(self):
        spec = ContextTriggerSpec(type="ui_response", tool="my_tool")
        assert spec.tool == "my_tool"

    def test_agent_whitespace_stripped(self):
        spec = ContextTriggerSpec(type="agent_text", agent="  Agt  ", match=self._match())
        assert spec.agent == "Agt"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ContextTriggerSpec(type="unknown_type")


# ---------------------------------------------------------------------------
# 6. ContextVariableSource
# ---------------------------------------------------------------------------

class TestContextVariableSource:
    def test_config_type_minimal(self):
        src = ContextVariableSource(type="config")
        assert src.type == "config"

    def test_env_var_set(self):
        src = ContextVariableSource(type="config", env_var="MY_VAR")
        assert src.env_var == "MY_VAR"

    def test_env_var_whitespace_stripped(self):
        src = ContextVariableSource(type="config", env_var="  MY_VAR  ")
        assert src.env_var == "MY_VAR"

    def test_env_var_empty_becomes_none(self):
        src = ContextVariableSource(type="config", env_var="")
        assert src.env_var is None

    def test_fields_string_list_normalized(self):
        src = ContextVariableSource(type="data_reference", fields=["  a  ", "b", "a"])
        # deduplicated, stripped
        assert src.fields == ["a", "b"]

    def test_fields_all_empty_becomes_none(self):
        src = ContextVariableSource(type="data_reference", fields=["", "  "])
        assert src.fields is None

    def test_inputs_deduplicated(self):
        src = ContextVariableSource(type="computed", inputs=["x", "y", "x"])
        assert src.inputs == ["x", "y"]

    def test_data_entity_type_accepted(self):
        src = ContextVariableSource(type="data_entity")
        assert src.type == "data_entity"

    def test_invalid_type_rejected(self):
        with pytest.raises(ValidationError):
            ContextVariableSource(type="invalid_type")

    def test_refresh_strategy_once_accepted(self):
        src = ContextVariableSource(type="data_reference", refresh_strategy="once")
        assert src.refresh_strategy == "once"

    def test_invalid_refresh_strategy_rejected(self):
        with pytest.raises(ValidationError):
            ContextVariableSource(type="data_reference", refresh_strategy="always")

    def test_write_strategy_immediate_accepted(self):
        src = ContextVariableSource(type="data_entity", write_strategy="immediate")
        assert src.write_strategy == "immediate"

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ContextVariableSource(type="config", unknown_field="x")


# ---------------------------------------------------------------------------
# 7. ContextVariableDefinition
# ---------------------------------------------------------------------------

class TestContextVariableDefinition:
    def _src(self):
        return ContextVariableSource(type="config")

    def test_valid_construction(self):
        defn = ContextVariableDefinition(source=self._src())
        assert defn.source.type == "config"

    def test_type_optional(self):
        defn = ContextVariableDefinition(source=self._src())
        assert defn.type is None

    def test_description_whitespace_stripped(self):
        defn = ContextVariableDefinition(source=self._src(), description="  My var  ")
        assert defn.description == "My var"

    def test_description_whitespace_only_becomes_none(self):
        defn = ContextVariableDefinition(source=self._src(), description="   ")
        assert defn.description is None

    def test_type_set(self):
        defn = ContextVariableDefinition(source=self._src(), type="string")
        assert defn.type == "string"

    def test_type_whitespace_stripped(self):
        defn = ContextVariableDefinition(source=self._src(), type="  boolean  ")
        assert defn.type == "boolean"


# ---------------------------------------------------------------------------
# 8. ContextAgentView
# ---------------------------------------------------------------------------

class TestContextAgentView:
    def test_empty_variables_accepted(self):
        view = ContextAgentView()
        assert view.variables == []

    def test_variables_deduplicated(self):
        view = ContextAgentView(variables=["a", "b", "a"])
        assert view.variables == ["a", "b"]

    def test_whitespace_stripped_from_variables(self):
        view = ContextAgentView(variables=["  x  ", "y"])
        assert view.variables == ["x", "y"]

    def test_empty_strings_filtered(self):
        view = ContextAgentView(variables=["", "a", "  "])
        assert view.variables == ["a"]

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ContextAgentView(variables=[], unknown="x")


# ---------------------------------------------------------------------------
# 9. ContextVariablesPlan
# ---------------------------------------------------------------------------

def _defn():
    """Minimal valid ContextVariableDefinition as a dict."""
    return {"source": {"type": "config"}}


class TestContextVariablesPlan:
    def test_empty_plan_valid(self):
        plan = ContextVariablesPlan()
        assert plan.definitions == {}
        assert plan.agents == {}

    def test_definitions_accepted(self):
        plan = ContextVariablesPlan(definitions={"my_var": _defn()})
        assert "my_var" in plan.definitions

    def test_agent_referencing_undeclared_variable_rejected(self):
        with pytest.raises(ValidationError, match="undeclared"):
            ContextVariablesPlan(
                definitions={},
                agents={"MyAgent": {"variables": ["missing_var"]}},
            )

    def test_agent_referencing_declared_variable_valid(self):
        plan = ContextVariablesPlan(
            definitions={"my_var": _defn()},
            agents={"MyAgent": {"variables": ["my_var"]}},
        )
        assert plan.agents["MyAgent"].variables == ["my_var"]

    def test_agent_with_empty_variables_valid(self):
        plan = ContextVariablesPlan(
            definitions={"x": _defn()},
            agents={"MyAgent": {"variables": []}},
        )
        assert plan.agents["MyAgent"].variables == []

    def test_multiple_agents_validated(self):
        plan = ContextVariablesPlan(
            definitions={"a": _defn(), "b": _defn()},
            agents={
                "Agent1": {"variables": ["a"]},
                "Agent2": {"variables": ["b"]},
            },
        )
        assert len(plan.agents) == 2

    def test_one_agent_references_undeclared_rejects_all(self):
        with pytest.raises(ValidationError):
            ContextVariablesPlan(
                definitions={"a": _defn()},
                agents={
                    "Agent1": {"variables": ["a"]},
                    "Agent2": {"variables": ["missing"]},
                },
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            ContextVariablesPlan(definitions={}, unknown="x")
