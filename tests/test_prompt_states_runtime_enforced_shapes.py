"""Vocabularies the runtime enforces must appear in the prompt that writes them.

Three defects in one day shared a shape: the runtime enforces a precise
contract, the prompt describes the intent, and the agent writes something
reasonable that cannot load.

    collection-returning schema   object wrapping an array   prompt said nothing
    typed contract field          field set when file emitted prompt said the inverse
    account-data handler          __init__(db), keyword-only  prompt said intent only

Each cost a live run of roughly twenty minutes plus a backend restart to find.
An audit of the runtime's enforced shapes against the prompts found twelve more
candidates; the ones an agent is actually likely to hit are now stated.

These tests read the vocabularies out of the runtime rather than restating them,
so adding a profile panel kind or a reaction target kind fails here until the
prompt that generates it is updated too. The point is to stop finding this class
one live run at a time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "factory_app/workflows/AppGenerator/agents.yaml"
LOADER = ROOT / "mozaiksai/core/runtime/app/module_loader.py"


def _prompt() -> str:
    return AGENTS.read_text(encoding="utf-8")


def _loader() -> str:
    return LOADER.read_text(encoding="utf-8")


def _literal_set(name: str) -> set[str]:
    match = re.search(rf"{name}\s*=\s*\{{([^}}]*)\}}", _loader())
    assert match, f"{name} is no longer defined in module_loader.py"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def test_every_profile_panel_kind_is_named_in_the_prompt() -> None:
    for kind in _literal_set("_PROFILE_PANEL_KINDS"):
        assert f"`{kind}`" in _prompt(), f"profile panel kind {kind!r} is enforced but unstated"


def test_every_profile_field_type_is_named_in_the_prompt() -> None:
    for field_type in _literal_set("_PROFILE_FIELD_TYPES"):
        assert f"`{field_type}`" in _prompt(), f"profile field type {field_type!r} is enforced but unstated"


def _kind_literals() -> list[list[str]]:
    return [
        re.findall(r'"([^"]+)"', match.group(1))
        for match in re.finditer(r"kind:\s*Literal\[([^\]]*)\]", _loader())
    ]


def test_every_kind_vocabulary_the_loader_enforces_is_named_in_the_prompt() -> None:
    """Every `kind: Literal[...]` in the loader, not just the one being fixed.

    Written narrowly for reaction targets first, this immediately caught a set
    the hand audit had missed: capabilities[].kind accepts `transition` and
    `hosted`, and the prompt named neither. Checking all of them is the point -
    a new kind added to any of these fails here until the prompt says so.
    """
    prompt = _prompt()
    unstated = [
        kind
        for kinds in _kind_literals()
        for kind in kinds
        if f"`{kind}`" not in prompt
    ]

    assert not unstated, f"enforced kind value(s) never stated in the prompt: {unstated}"


def test_the_reaction_target_kinds_are_among_them() -> None:
    """service_adapter was a real fourth kind the prompt listed only three of."""
    assert ["handler", "capability", "notification", "service_adapter"] in _kind_literals()


@pytest.mark.parametrize(
    "rule",
    [
        "target.handler_method",
        "target.capability_id",
        "target.notification_id",
        "target.adapter_method",
    ],
)
def test_each_reaction_kind_declares_its_own_field(rule: str) -> None:
    assert f"`{rule}`" in _prompt()


def test_the_uniqueness_rules_are_stated() -> None:
    """A duplicate fails module load rather than taking the last one."""
    prompt = _prompt()

    assert "Ids are unique within their file" in prompt
    for enforced in ("events[].type", "capabilities[].capability_id", "panels[].id"):
        assert f"`{enforced}`" in prompt


def test_the_admin_renderer_rules_are_stated() -> None:
    prompt = _prompt()

    assert "`renderer: schema` declares `sections`" in prompt
    assert "`renderer: custom_component` declares `component`" in prompt


def test_the_runtime_still_enforces_what_the_prompt_now_promises() -> None:
    """If an enforcement is dropped, the prompt text becomes stale - catch that too."""
    loader = _loader()

    assert "admin panel 'page' must reference a non-empty page id" in loader
    assert "custom_component admin panels must declare component" in loader
    assert "api_router runtime extension prefix must start with /" in loader
    assert "service_adapter reactions must declare target.adapter and target.adapter_method" in loader
