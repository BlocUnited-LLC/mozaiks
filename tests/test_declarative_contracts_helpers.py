"""
mozaiksai/core/workflow/declarative/contracts.py pure helper unit tests.

Covers standalone pure functions:
  _required_text:
    - valid string → stripped string returned
    - empty string → ValueError raised
    - whitespace only → ValueError raised
    - None → ValueError raised
    - non-string value → stringified and checked

  _optional_text:
    - None → None
    - empty string → None
    - whitespace only → None
    - valid string → stripped string
    - non-string value → str(value) stripped

  _normalize_string_list:
    - empty list → []
    - strings stripped
    - empty strings excluded
    - duplicates excluded (first kept)
    - order preserved

Pydantic validators tested via model construction:

  OrchestratorTriggerSpec._validate_trigger:
    - no type/event/endpoint → ValidationError
    - type only → accepted
    - event only → accepted
    - endpoint only → accepted

  OrchestratorConfig._validate_max_turns:
    - max_turns < 1 → ValidationError
    - max_turns > 500 → ValidationError
    - valid range → accepted

  AgentSpec._validate_prompt_shape:
    - no prompt_sections and no system_message → ValidationError
    - prompt_sections present → accepted
    - system_message present → accepted

  AgentSpec._validate_max_auto_reply:
    - max_consecutive_auto_reply < 0 → ValidationError
    - 0 is valid

  AgentsConfig._validate_unique_names:
    - duplicate agent names → ValidationError
    - unique names → accepted

  PromptSectionSpec._validate_content:
    - empty heading → ValidationError
    - empty content → ValidationError
    - valid → accepted

  OrchestratorConfig._required_text_fields:
    - empty workflow_name → ValidationError
    - empty orchestration_pattern → ValidationError
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow.declarative.contracts import (
    AgentsConfig,
    AgentSpec,
    OrchestratorConfig,
    OrchestratorTriggerSpec,
    PromptSectionSpec,
    _normalize_string_list,
    _optional_text,
    _required_text,
)

# ---------------------------------------------------------------------------
# 1. _required_text
# ---------------------------------------------------------------------------

class TestRequiredText:
    def test_valid_string_returned(self):
        assert _required_text("hello", field_name="name") == "hello"

    def test_string_stripped(self):
        assert _required_text("  hello  ", field_name="name") == "hello"

    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _required_text("", field_name="name")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _required_text("   ", field_name="name")

    def test_none_raises(self):
        with pytest.raises(ValueError, match="non-empty"):
            _required_text(None, field_name="name")

    def test_falsy_int_raises(self):
        # str(0 or "") = str("") = "" → raises
        with pytest.raises(ValueError, match="non-empty"):
            _required_text(0, field_name="count")

    def test_non_zero_int_stringified(self):
        result = _required_text(42, field_name="version")
        assert result == "42"

    def test_field_name_in_error_message(self):
        with pytest.raises(ValueError, match="my_field"):
            _required_text("", field_name="my_field")


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

    def test_valid_string_stripped(self):
        assert _optional_text("  hello  ") == "hello"

    def test_valid_string_returned(self):
        assert _optional_text("hello") == "hello"

    def test_non_string_stringified(self):
        result = _optional_text(42)
        assert result == "42"

    def test_zero_stringified_and_returned(self):
        # str(0).strip() = "0" → returned (non-empty)
        assert _optional_text(0) == "0"

    def test_true_stringified(self):
        assert _optional_text(True) == "True"


# ---------------------------------------------------------------------------
# 3. _normalize_string_list
# ---------------------------------------------------------------------------

class TestNormalizeStringList:
    def test_empty_list_returns_empty(self):
        assert _normalize_string_list([]) == []

    def test_strings_stripped(self):
        result = _normalize_string_list(["  a  ", "  b  "])
        assert result == ["a", "b"]

    def test_empty_strings_excluded(self):
        result = _normalize_string_list(["a", "", "b"])
        assert "" not in result
        assert result == ["a", "b"]

    def test_duplicates_removed(self):
        result = _normalize_string_list(["a", "b", "a"])
        assert result == ["a", "b"]

    def test_order_preserved(self):
        result = _normalize_string_list(["c", "a", "b"])
        assert result == ["c", "a", "b"]

    def test_whitespace_only_excluded(self):
        result = _normalize_string_list(["  ", "valid"])
        assert result == ["valid"]

    def test_none_items_excluded(self):
        # str(None or "").strip() = "" → excluded
        result = _normalize_string_list([None, "valid"])
        assert result == ["valid"]


# ---------------------------------------------------------------------------
# 4. OrchestratorTriggerSpec._validate_trigger
# ---------------------------------------------------------------------------

class TestOrchestratorTriggerSpecValidate:
    def test_no_type_event_endpoint_raises(self):
        with pytest.raises(ValidationError, match="at least one of"):
            OrchestratorTriggerSpec()

    def test_type_only_accepted(self):
        t = OrchestratorTriggerSpec(type="chat")
        assert t.type == "chat"

    def test_event_only_accepted(self):
        t = OrchestratorTriggerSpec(event="task.created")
        assert t.event == "task.created"

    def test_endpoint_only_accepted(self):
        t = OrchestratorTriggerSpec(endpoint="/api/start")
        assert t.endpoint == "/api/start"

    def test_whitespace_type_becomes_none_then_raises(self):
        with pytest.raises(ValidationError, match="at least one of"):
            OrchestratorTriggerSpec(type="   ")


# ---------------------------------------------------------------------------
# 5. OrchestratorConfig._validate_max_turns
# ---------------------------------------------------------------------------

class TestOrchestratorConfigMaxTurns:
    def _valid_config(self, **kwargs):
        defaults = {
            "workflow_name": "TestWorkflow",
            "workflow_startup_mode": "UserDriven",
        }
        defaults.update(kwargs)
        return OrchestratorConfig(**defaults)

    def test_max_turns_below_1_raises(self):
        with pytest.raises(ValidationError, match=">= 1"):
            self._valid_config(max_turns=0)

    def test_max_turns_above_500_raises(self):
        with pytest.raises(ValidationError, match="<= 500"):
            self._valid_config(max_turns=501)

    def test_max_turns_1_accepted(self):
        config = self._valid_config(max_turns=1)
        assert config.max_turns == 1

    def test_max_turns_500_accepted(self):
        config = self._valid_config(max_turns=500)
        assert config.max_turns == 500

    def test_default_max_turns_50(self):
        config = self._valid_config()
        assert config.max_turns == 50


# ---------------------------------------------------------------------------
# 6. OrchestratorConfig._required_text_fields
# ---------------------------------------------------------------------------

class TestOrchestratorConfigRequiredText:
    def test_empty_workflow_name_raises(self):
        with pytest.raises(ValidationError):
            OrchestratorConfig(workflow_name="", workflow_startup_mode="UserDriven")

    def test_whitespace_workflow_name_raises(self):
        with pytest.raises(ValidationError):
            OrchestratorConfig(workflow_name="   ", workflow_startup_mode="UserDriven")

    def test_empty_orchestration_pattern_raises(self):
        with pytest.raises(ValidationError):
            OrchestratorConfig(
                workflow_name="TestWorkflow",
                workflow_startup_mode="UserDriven",
                orchestration_pattern="",
            )


# ---------------------------------------------------------------------------
# 7. AgentSpec — prompt_shape and max_auto_reply
# ---------------------------------------------------------------------------

class TestAgentSpecPromptShape:
    def test_no_prompt_sections_and_no_system_message_raises(self):
        with pytest.raises(ValidationError, match="must provide"):
            AgentSpec(name="MyAgent")

    def test_system_message_alone_accepted(self):
        agent = AgentSpec(name="MyAgent", system_message="You are a helpful agent.")
        assert agent.system_message == "You are a helpful agent."

    def test_prompt_sections_alone_accepted(self):
        agent = AgentSpec(
            name="MyAgent",
            prompt_sections=[PromptSectionSpec(heading="Role", content="You are a helper.")],
        )
        assert len(agent.prompt_sections) == 1

    def test_empty_name_raises(self):
        with pytest.raises(ValidationError):
            AgentSpec(name="", system_message="Hello")

    def test_whitespace_name_raises(self):
        with pytest.raises(ValidationError):
            AgentSpec(name="   ", system_message="Hello")


class TestAgentSpecMaxAutoReply:
    def test_negative_raises(self):
        with pytest.raises(ValidationError, match=">= 0"):
            AgentSpec(name="A", system_message="Hi", max_consecutive_auto_reply=-1)

    def test_zero_accepted(self):
        agent = AgentSpec(name="A", system_message="Hi", max_consecutive_auto_reply=0)
        assert agent.max_consecutive_auto_reply == 0

    def test_positive_accepted(self):
        agent = AgentSpec(name="A", system_message="Hi", max_consecutive_auto_reply=5)
        assert agent.max_consecutive_auto_reply == 5


# ---------------------------------------------------------------------------
# 8. AgentsConfig._validate_unique_names
# ---------------------------------------------------------------------------

class TestAgentsConfigUniqueNames:
    def test_duplicate_names_raises(self):
        with pytest.raises(ValidationError, match="duplicate"):
            AgentsConfig(agents=[
                AgentSpec(name="Agent", system_message="Hi"),
                AgentSpec(name="Agent", system_message="There"),
            ])

    def test_unique_names_accepted(self):
        config = AgentsConfig(agents=[
            AgentSpec(name="AgentA", system_message="Hi"),
            AgentSpec(name="AgentB", system_message="There"),
        ])
        assert len(config.agents) == 2

    def test_empty_agents_accepted(self):
        config = AgentsConfig()
        assert config.agents == []


# ---------------------------------------------------------------------------
# 9. PromptSectionSpec._validate_content
# ---------------------------------------------------------------------------

class TestPromptSectionSpecValidate:
    def test_empty_heading_raises(self):
        with pytest.raises(ValidationError):
            PromptSectionSpec(heading="", content="Some content")

    def test_empty_content_raises(self):
        with pytest.raises(ValidationError):
            PromptSectionSpec(heading="Role", content="")

    def test_whitespace_heading_raises(self):
        with pytest.raises(ValidationError):
            PromptSectionSpec(heading="   ", content="content")

    def test_valid_section_accepted(self):
        section = PromptSectionSpec(heading="Role", content="You are a helpful agent.")
        assert section.heading == "Role"
        assert section.content == "You are a helpful agent."

    def test_optional_id_none_by_default(self):
        section = PromptSectionSpec(heading="Role", content="content")
        assert section.id is None

    def test_whitespace_id_normalised_to_none(self):
        section = PromptSectionSpec(id="   ", heading="Role", content="content")
        assert section.id is None

    def test_valid_id_accepted(self):
        section = PromptSectionSpec(id="role_section", heading="Role", content="content")
        assert section.id == "role_section"
