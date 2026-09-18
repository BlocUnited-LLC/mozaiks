"""The account-data handler has a shape the runtime enforces and the prompt did not state.

A generated habit tracker reached module loading for the first time and failed:

    MODULE_LOAD_FAILED: habit_management - modules/habit_management/backend/
    account_data_handler.py: invalid account-data implementation: export one
    AccountDataHandler class with __init__(db) and async
    delete_user_data(*, app_id, user_id) / export_user_data(*, app_id, user_id)

What was generated:

    class AccountDataHandler:
        async def delete_user_data(self, app_id: str, user_id: str) -> dict:
            db = ...  # retrieve your db here

No __init__, positional parameters instead of keyword-only, and a literal
Ellipsis placeholder for the database handle.

The prompt said what to implement - "export and delete of this module's records
by app_id and user_id" - and never the signature. This is the third defect of
that shape today: the runtime enforces a precise contract, the prompt describes
the intent, and the agent writes something reasonable that cannot load.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "factory_app/workflows/AppGenerator/agents.yaml"
LOADER = ROOT / "mozaiksai/core/runtime/app/module_loader.py"


def test_the_prompt_states_the_constructor_the_loader_requires() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    assert "`__init__(self, db)`" in text


def test_the_prompt_states_that_the_arguments_are_keyword_only() -> None:
    """Positional args were what the generated handler actually used."""
    text = AGENTS.read_text(encoding="utf-8")

    assert "the `*` is required, positional parameters fail" in text


def test_the_prompt_forbids_the_placeholder_that_was_emitted() -> None:
    text = AGENTS.read_text(encoding="utf-8")

    assert "`db = ...`" in text


def test_the_prompt_and_the_loader_agree_on_the_method_names() -> None:
    """If the runtime contract moves, this should fail rather than drift."""
    prompt = AGENTS.read_text(encoding="utf-8")
    loader = LOADER.read_text(encoding="utf-8")

    for method in ("delete_user_data", "export_user_data"):
        assert method in loader, f"{method} is no longer the runtime contract"
        assert method in prompt, f"{method} is not stated in the prompt"


def test_the_loader_still_requires_exactly_one_handler_class() -> None:
    """The prompt says "one AccountDataHandler class"; pin that it is true."""
    loader = LOADER.read_text(encoding="utf-8")

    assert "if len(valid) != 1:" in loader
    assert "export one AccountDataHandler class with __init__(db)" in loader
