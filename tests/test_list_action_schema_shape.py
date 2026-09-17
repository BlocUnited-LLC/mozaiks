"""A collection-returning action must be an object wrapping an array.

A proving run for the read-operation repair (#634) failed the whole build:

    task batch 'app_build_tasks' failed at task '2':
    Only object schema contracts may declare properties or required fields

The repair had declared `list_habits` in that exact task. Its instruction asked
for "a read action returning the Habit records" and said nothing about shape, so
the contract agent wrote the obvious thing - a top-level `type: "array"` with
properties - and the renderer rejected it.

Two layers were wrong. The repair invited the shape, and nothing in the schema
rules said a non-object schema may not declare properties, so this was reachable
by any collection-returning action. Earlier bundles never had one: the missing
read is why. Fixing the missing read is what first produced a list action, and
the list action is what found this.
"""

from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_missing_read_operation,
)
from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
)
from tests.test_plan_read_operation import _context, _plan

AGENTS = "factory_app/workflows/AppGenerator/agents.yaml"


def _array_with_properties() -> dict:
    return {
        "type": "array",
        "description": "The habits the caller may see.",
        "items_type": "object",
        "properties": [
            {"name": "habit_id", "type": "string", "description": "id",
             "required": True, "enum_values": [], "items_type": None}
        ],
        "required": ["habit_id"],
    }


def _object_wrapping_an_array() -> dict:
    return {
        "type": "object",
        "description": "The habits the caller may see.",
        "items_type": None,
        "properties": [
            {"name": "habits", "type": "array", "description": "records",
             "required": True, "enum_values": [], "items_type": "object"}
        ],
        "required": ["habits"],
    }


def test_the_shape_the_agent_wrote_is_still_rejected() -> None:
    """The guard is correct and is not being relaxed to accommodate the repair."""
    with pytest.raises(ValueError, match="Only object schema contracts"):
        _materialize_schema_contract(_array_with_properties())


def test_the_shape_the_instruction_now_asks_for_materializes() -> None:
    rendered = _materialize_schema_contract(_object_wrapping_an_array())

    assert rendered["type"] == "object"
    assert rendered["properties"]["habits"]["type"] == "array"
    assert rendered["required"] == ["habits"]


def test_the_repair_tells_the_agent_which_shape_to_write() -> None:
    plan = _plan(["create_habit"])

    _repair_missing_read_operation(plan, _context())

    message = plan["build_tasks"][0]["initial_message"]
    assert "list_habits" in message
    assert "not a bare" in message and 'type: "array"' in message


def test_the_rule_is_stated_for_every_collection_action_not_only_repaired_ones() -> None:
    """A hand-planned list action reaches the same renderer."""
    text = open(AGENTS, encoding="utf-8").read()

    assert "An action returning a collection declares" in text
    assert 'never `type: "array"` at the top level' in text
