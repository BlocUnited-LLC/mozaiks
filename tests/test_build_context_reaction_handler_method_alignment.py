"""Cross-pack drift guard: reaction target.kind=handler methods must exist in handler.py.

When a reaction declares ``target.kind: handler`` with a ``handler_method``, the
module_event_router dispatches to that method on the owning module's handler instance at
event time.  If the method is absent the router silently skips the reaction
(``HANDLER_TARGET_SKIPPED`` warning) — no startup error, no type error.

This test file walks every pack template that declares handler-kind reactions and
asserts each ``handler_method`` appears as a method definition in the corresponding
``backend/base_handler.py`` (preferred) or ``backend/handler.py``.

Tests are parametrized so a single mismatch surfaces as a named failure identifying
the exact pack, reaction id, and missing method — not a bulk assertion error.

Runtime reference:
    mozaiksai/core/runtime/composition/module_event_router.py _dispatch_handler()
    — calls getattr(handler, handler_method, None); missing method → "skipped".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _handler_file(module_dir: Path) -> Path | None:
    """Return the primary handler implementation file for a module directory."""
    base = module_dir / "backend" / "base_handler.py"
    if base.exists():
        return base
    direct = module_dir / "backend" / "handler.py"
    if direct.exists():
        return direct
    return None


def _collect_cases() -> list[tuple[str, str, str, str]]:
    """Return (pack_id, module_id, reaction_id, method_name) for every reaction handler_method."""
    cases: list[tuple[str, str, str, str]] = []
    for reactions_yaml in sorted(BUILD_CONTEXT.rglob("reactions.yaml")):
        if "__pycache__" in reactions_yaml.parts:
            continue
        try:
            rel = reactions_yaml.relative_to(BUILD_CONTEXT)
            pack_id = rel.parts[0]
        except ValueError:
            continue
        data = _read_yaml(reactions_yaml)
        # The module dir owns the reactions.yaml via contracts/ subdirectory
        module_dir = reactions_yaml.parent.parent
        module_id = module_dir.name
        for reaction in data.get("reactions") or []:
            target = reaction.get("target") or {}
            if target.get("kind") != "handler":
                continue
            method = target.get("handler_method")
            if method:
                cases.append((pack_id, module_id, reaction.get("id", "?"), method))
    return cases


_CASES = _collect_cases()


@pytest.mark.parametrize(
    "pack_id,module_id,reaction_id,method_name",
    _CASES,
    ids=[f"{p}/{m}/reaction={r}/{n}" for p, m, r, n in _CASES],
)
def test_reaction_handler_method_exists(
    pack_id: str, module_id: str, reaction_id: str, method_name: str
) -> None:
    """Every reaction target.kind=handler method must be defined in the owning handler.

    A missing method causes the module_event_router to silently skip the reaction
    at event dispatch time — the domain side-effect is lost with no startup error.
    """
    module_dir = BUILD_CONTEXT / pack_id / "templates" / "modules" / module_id
    # Also try subdirectory matches when module_id is a suffix of the actual dir name
    if not module_dir.is_dir():
        candidates = list((BUILD_CONTEXT / pack_id / "templates" / "modules").glob(f"*{module_id}*"))
        exact = [d for d in candidates if d.name == module_id]
        module_dir = (exact or candidates)[0] if (exact or candidates) else module_dir

    assert module_dir.is_dir(), (
        f"{pack_id}/{module_id}: cannot locate module directory. "
        f"Expected {module_dir.relative_to(BUILD_CONTEXT)}"
    )

    handler = _handler_file(module_dir)
    assert handler is not None, (
        f"{pack_id}/{module_id}: reaction {reaction_id!r} declares "
        f"target.kind=handler with handler_method={method_name!r} but no "
        f"base_handler.py or handler.py exists in the module's backend/. "
        f"Add the handler file and implement the method."
    )

    source = handler.read_text(encoding="utf-8")
    pattern = re.compile(rf"\bdef {re.escape(method_name)}\s*\(")
    assert pattern.search(source), (
        f"{pack_id}/{module_id}: reaction {reaction_id!r} declares "
        f"target.handler_method={method_name!r} but that method is not defined "
        f"in {handler.name}. "
        f"Add the method or correct the reaction declaration."
    )


def test_all_handler_reactions_have_handler_files() -> None:
    """Every module with a handler-kind reaction must ship a handler implementation.

    A module with handler reactions but no handler file would silently skip all
    those reactions at runtime — there is no handler instance to dispatch to.
    """
    for reactions_yaml in sorted(BUILD_CONTEXT.rglob("reactions.yaml")):
        if "__pycache__" in reactions_yaml.parts:
            continue
        data = _read_yaml(reactions_yaml)
        has_handler_reaction = any(
            (r.get("target") or {}).get("kind") == "handler"
            for r in (data.get("reactions") or [])
        )
        if not has_handler_reaction:
            continue
        module_dir = reactions_yaml.parent.parent
        handler = _handler_file(module_dir)
        assert handler is not None, (
            f"{reactions_yaml.relative_to(BUILD_CONTEXT)}: declares handler-kind "
            f"reactions but no base_handler.py or handler.py exists in "
            f"{module_dir.relative_to(BUILD_CONTEXT)}/backend/. "
            f"Add the handler file."
        )
