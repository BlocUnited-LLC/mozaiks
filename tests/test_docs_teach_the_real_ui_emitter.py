"""Guidance must name an API that exists.

CLAUDE.md and .claude/rules/frontend.md both taught
`transport.send_ui_tool_event(...)`. No such function exists anywhere in
mozaiksai, and `tests/test_workflow_ui_tool_contracts.py` actively rejects that
call in factory tools — so an agent following the documented pattern wrote code
that could not run and would fail review. See #381.

Docs are read as authority, which makes a wrong example worse than a missing
one: it is confidently actionable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUIDANCE = [
    ROOT / "CLAUDE.md",
    ROOT / ".claude" / "rules" / "frontend.md",
]


@pytest.mark.parametrize("path", GUIDANCE, ids=lambda p: p.name)
def test_guidance_does_not_teach_a_function_that_does_not_exist(path: Path) -> None:
    assert "send_ui_tool_event" not in path.read_text(encoding="utf-8"), (
        f"{path.name} documents send_ui_tool_event, which does not exist in mozaiksai"
    )


@pytest.mark.parametrize("path", GUIDANCE, ids=lambda p: p.name)
def test_guidance_names_the_real_emitter(path: Path) -> None:
    assert "emit_ui_surface" in path.read_text(encoding="utf-8")


def test_the_documented_emitter_actually_exists() -> None:
    """The point of the exercise: the named symbol must be importable."""
    from mozaiksai.core.workflow.ui_tools import emit_ui_surface, use_ui_tool

    assert callable(emit_ui_surface)
    assert callable(use_ui_tool)


def test_the_documented_signature_matches_the_implementation() -> None:
    """A correct name with wrong arguments is the same failure one step later."""
    import inspect

    from mozaiksai.core.workflow.ui_tools import emit_ui_surface

    params = inspect.signature(emit_ui_surface).parameters
    assert list(params)[:2] == ["tool_id", "payload"], (
        "docs show tool_id and payload positionally"
    )
    for keyword in ("chat_id", "workflow_name", "display"):
        assert keyword in params, f"docs pass {keyword}= but the function does not accept it"


def test_no_guidance_file_reintroduces_a_transport_call() -> None:
    """Factory tools are required to use emit_ui_surface, so guidance must not
    point at the transport underneath it."""
    for path in GUIDANCE:
        text = path.read_text(encoding="utf-8")
        assert "transport.send_" not in text, f"{path.name} reaches past the workflow API"
