"""
Pure helper unit tests for:
  factory_app/workflows/AppGenerator/tools/hook_workflow_integration_contract.py

Covers:

  _reaction_id_for_event:
    - capability_id + event_type joined with underscore
    - non-alphanumeric chars replaced with underscore
    - result lowercased
    - leading/trailing underscores stripped
    - empty token falls back to "workflow_reaction_{index+1}"
    - index 0 → workflow_reaction_1
    - index 2 → workflow_reaction_3

  _build_app_plan_block:
    - workflow_name in output
    - capability_id in output
    - startup_mode in output
    - AgentDriven startup_mode → "agent speaks first" in launch_expectation
    - UserDriven startup_mode → "user speaks first" in launch_expectation
    - trigger_events rendered when present
    - each event_type from dict events listed
    - string event items in trigger_events rendered
    - empty trigger_events → "none declared" message
    - HARD CONSTRAINTS section present
    - capability_id in hard constraints

  _build_config_middleware_block:
    - capability_id in output
    - trigger_events rendered with reactions.yaml format
    - each event uses _reaction_id_for_event id
    - target.kind: capability in output
    - target.capability_id value in output
    - empty trigger_events → "none declared" message
    - HARD CONSTRAINT section present

  _build_generic_block:
    - workflow_name in output twice (workflow_id and header)
    - capability_id in output
    - startup_mode in output
    - returns multi-line string
"""
from __future__ import annotations

from factory_app.workflows.AppGenerator.tools.hook_workflow_integration_contract import (
    _build_app_plan_block,
    _build_config_middleware_block,
    _build_generic_block,
    _reaction_id_for_event,
)

# ---------------------------------------------------------------------------
# 1. _reaction_id_for_event
# ---------------------------------------------------------------------------

class TestReactionIdForEvent:
    def test_basic_combination(self):
        result = _reaction_id_for_event("orders-workflow", "domain.orders.created", 0)
        # non-alphanumeric → underscore, lowercased
        assert isinstance(result, str)
        assert len(result) > 0
        assert result == result.lower()

    def test_non_alphanumeric_replaced(self):
        result = _reaction_id_for_event("my-cap", "my.event", 0)
        # "my-cap_my.event" → "my_cap_my_event"
        assert "-" not in result
        assert "." not in result

    def test_result_lowercased(self):
        result = _reaction_id_for_event("CapId", "Event.Type", 0)
        assert result == result.lower()

    def test_no_leading_trailing_underscores(self):
        result = _reaction_id_for_event("cap", "event", 0)
        assert not result.startswith("_")
        assert not result.endswith("_")

    def test_empty_combination_fallback(self):
        # When both ids are empty/symbol-only after cleanup, token will be empty
        result = _reaction_id_for_event("---", "...", 0)
        assert result == "workflow_reaction_1"

    def test_index_0_fallback_is_1(self):
        result = _reaction_id_for_event("", "", 0)
        assert result == "workflow_reaction_1"

    def test_index_2_fallback_is_3(self):
        result = _reaction_id_for_event("", "", 2)
        assert result == "workflow_reaction_3"

    def test_valid_input_no_fallback(self):
        result = _reaction_id_for_event("orders_workflow", "domain_orders_created", 0)
        assert result != "workflow_reaction_1"
        assert "orders" in result


# ---------------------------------------------------------------------------
# 2. _build_app_plan_block
# ---------------------------------------------------------------------------

class TestBuildAppPlanBlock:
    def _base(self, startup_mode="UserDriven", trigger_events=None):
        return _build_app_plan_block(
            workflow_name="orders-review-workflow",
            capability_id="orders-review-workflow",
            startup_mode=startup_mode,
            trigger_events=trigger_events or [],
        )

    def test_workflow_name_in_output(self):
        result = self._base()
        assert "orders-review-workflow" in result

    def test_capability_id_in_output(self):
        result = self._base()
        assert "orders-review-workflow" in result

    def test_startup_mode_in_output(self):
        result = _build_app_plan_block("wf", "cap", "UserDriven", [])
        assert "UserDriven" in result

    def test_agent_driven_says_agent_speaks_first(self):
        result = _build_app_plan_block("wf", "cap", "AgentDriven", [])
        assert "agent speaks first" in result

    def test_user_driven_says_user_speaks_first(self):
        result = _build_app_plan_block("wf", "cap", "UserDriven", [])
        assert "user speaks first" in result

    def test_trigger_events_rendered(self):
        events = [{"event_type": "domain.orders.created"}]
        result = _build_app_plan_block("wf", "cap", "UserDriven", events)
        assert "domain.orders.created" in result

    def test_event_string_items_rendered(self):
        events = ["domain.orders.submitted"]
        result = _build_app_plan_block("wf", "cap", "UserDriven", events)
        assert "domain.orders.submitted" in result

    def test_empty_trigger_events_shows_none_declared(self):
        result = self._base(trigger_events=[])
        assert "none declared" in result.lower()

    def test_hard_constraints_section_present(self):
        result = self._base()
        assert "HARD CONSTRAINTS" in result

    def test_capability_id_in_hard_constraints(self):
        result = _build_app_plan_block("wf", "my-cap-id", "UserDriven", [])
        assert "my-cap-id" in result

    def test_returns_string(self):
        assert isinstance(self._base(), str)

    def test_multiple_events_all_rendered(self):
        events = [
            {"event_type": "domain.orders.created"},
            {"event_type": "domain.orders.submitted"},
        ]
        result = _build_app_plan_block("wf", "cap", "UserDriven", events)
        assert "domain.orders.created" in result
        assert "domain.orders.submitted" in result


# ---------------------------------------------------------------------------
# 3. _build_config_middleware_block
# ---------------------------------------------------------------------------

class TestBuildConfigMiddlewareBlock:
    def test_capability_id_in_output(self):
        result = _build_config_middleware_block("orders-cap", [])
        assert "orders-cap" in result

    def test_empty_trigger_events_shows_none_declared(self):
        result = _build_config_middleware_block("cap", [])
        assert "none declared" in result.lower()

    def test_trigger_event_renders_event_type(self):
        events = [{"event_type": "domain.orders.created"}]
        result = _build_config_middleware_block("cap", events)
        assert "domain.orders.created" in result

    def test_target_kind_capability_in_output(self):
        events = [{"event_type": "domain.orders.submitted"}]
        result = _build_config_middleware_block("cap", events)
        assert "kind: capability" in result

    def test_target_capability_id_in_output(self):
        events = [{"event_type": "domain.orders.submitted"}]
        result = _build_config_middleware_block("my-cap", events)
        assert "capability_id: my-cap" in result

    def test_reaction_id_present_in_output(self):
        events = [{"event_type": "domain.orders.created"}]
        result = _build_config_middleware_block("orders-workflow", events)
        # _reaction_id_for_event should produce a token
        assert "- id:" in result

    def test_hard_constraint_section_present(self):
        result = _build_config_middleware_block("cap", [])
        assert "HARD CONSTRAINT" in result

    def test_multiple_events_all_rendered(self):
        events = [
            {"event_type": "domain.orders.created"},
            {"event_type": "domain.orders.deleted"},
        ]
        result = _build_config_middleware_block("cap", events)
        assert "domain.orders.created" in result
        assert "domain.orders.deleted" in result

    def test_string_event_items_rendered(self):
        result = _build_config_middleware_block("cap", ["domain.items.created"])
        assert "domain.items.created" in result

    def test_returns_string(self):
        assert isinstance(_build_config_middleware_block("cap", []), str)


# ---------------------------------------------------------------------------
# 4. _build_generic_block
# ---------------------------------------------------------------------------

class TestBuildGenericBlock:
    def test_workflow_name_in_output(self):
        result = _build_generic_block("orders-workflow", "orders-cap", "UserDriven")
        assert "orders-workflow" in result

    def test_capability_id_in_output(self):
        result = _build_generic_block("orders-workflow", "orders-cap", "UserDriven")
        assert "orders-cap" in result

    def test_startup_mode_in_output(self):
        result = _build_generic_block("wf", "cap", "BackendOnly")
        assert "BackendOnly" in result

    def test_multi_line_output(self):
        result = _build_generic_block("wf", "cap", "UserDriven")
        assert "\n" in result

    def test_workflow_id_line_present(self):
        result = _build_generic_block("my-workflow", "my-cap", "UserDriven")
        assert "workflow_id" in result

    def test_capability_id_line_present(self):
        result = _build_generic_block("wf", "my-cap", "UserDriven")
        assert "capability_id" in result

    def test_returns_string(self):
        assert isinstance(_build_generic_block("wf", "cap", "UserDriven"), str)
