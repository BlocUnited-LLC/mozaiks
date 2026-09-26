"""Collection-returning actions require an object wrapping an array."""

from __future__ import annotations

import pytest

from mozaiksai.core.workflow.generator_support.code_files import (
    _materialize_schema_contract,
)

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


def test_the_rule_is_stated_for_every_collection_action_not_only_repaired_ones() -> None:
    """A hand-planned list action reaches the same renderer."""
    text = open(AGENTS, encoding="utf-8").read()

    assert "An action returning a collection declares" in text
    assert 'never `type: "array"` at the top level' in text
