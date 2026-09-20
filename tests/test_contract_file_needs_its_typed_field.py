"""A contract file in code_files must have its typed module_contract field set.

A live build died on this after three identical attempts:

    module_contract.events_yaml is null but raw output emits
    modules/habit_management_module/contracts/events.yaml

The guard is load-bearing, not a consistency nicety. Typed fields are
materialized - action schemas compiled by _materialize_schema_contract, event
entries normalized - and a raw contracts/*.yaml entry in code_files bypasses
every bit of that. Accepting the raw file would ship a contract that never went
through the typed pipeline.

It carried no comment saying so, which is why it read as arbitrary. The prompt
had the same gap from the other direction: it said "do not mirror null
manifests into code_files" and, separately, "plus code_files for the corrected
YAML files only". Neither states the invariant the guard enforces, which is
directional - a file in code_files requires its field, not merely the reverse.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mozaiksai.core.workflow.generator_support.code_files import (
    extract_code_file_map_from_payload,
)

ROOT = Path(__file__).resolve().parents[1]


def _payload(events_yaml, include_raw_file: bool) -> dict:
    payload: dict = {
        "module_contract": {
            "module_id": "habits",
            "module_yaml": {"module": {"id": "habits"}},
            "events_yaml": events_yaml,
        }
    }
    if include_raw_file:
        payload["code_files"] = [
            {"path": "modules/habits/contracts/events.yaml", "content": "events: []\n"}
        ]
    return payload


def test_a_raw_contract_file_with_a_null_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="is null but raw output emits"):
        extract_code_file_map_from_payload(_payload(None, include_raw_file=True))


def test_the_error_says_what_to_do_instead_of_only_what_is_wrong() -> None:
    """Three identical retries suggest the message was not actionable."""
    with pytest.raises(ValueError) as error:
        extract_code_file_map_from_payload(_payload(None, include_raw_file=True))

    message = str(error.value)
    assert "Set module_contract.events_yaml" in message
    assert "skips schema materialization" in message
    # A module with no events is told to set the field, which its prompt says
    # to leave null. Without the second branch it retries the same output until
    # the budget ends - a live build did exactly that. The advice must say what
    # the null case does instead.
    assert "when module_contract.events_yaml is null" in message
    assert "omit contracts/events.yaml from code_files" in message
    assert "a null field emits nothing" in message


def test_the_typed_field_alone_is_accepted() -> None:
    files = extract_code_file_map_from_payload(
        _payload({"events": []}, include_raw_file=False)
    )

    assert "modules/habits/contracts/events.yaml" in files


def test_a_null_field_with_no_raw_file_is_fine() -> None:
    """A module with no events declares null and emits nothing."""
    files = extract_code_file_map_from_payload(_payload(None, include_raw_file=False))

    assert "modules/habits/contracts/events.yaml" not in files


def test_the_guard_records_why_it_exists() -> None:
    """It read as arbitrary for want of one comment."""
    source = (ROOT / "mozaiksai/core/workflow/generator_support/code_files.py").read_text(
        encoding="utf-8"
    )

    assert "bypasses every bit of that" in source


def test_the_prompt_states_the_invariant_in_the_direction_agents_get_wrong() -> None:
    text = (ROOT / "factory_app/workflows/AppGenerator/agents.yaml").read_text(encoding="utf-8")

    assert "A contract file in `code_files` requires its `module_contract` field to be set." in text


def test_an_absent_field_is_treated_as_null() -> None:
    """The strict structured output always carries every key, so only a
    hand-built bundle can omit one. A raw companion file must not slip past the
    guard on that alone - once the companion is an optional owned path, nothing
    downstream would catch it."""
    payload = {
        "module_contract": {"module_id": "habits", "module_yaml": {"module": {"id": "habits"}}},
        "code_files": [{"path": "modules/habits/contracts/events.yaml", "content": "events: []\n"}],
    }
    with pytest.raises(ValueError, match="is null but raw output emits"):
        extract_code_file_map_from_payload(payload)
