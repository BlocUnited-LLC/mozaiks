"""Cross-pack drift guards: emits declarations and reactions handler_method alignment.

Two complementary contracts are verified:

1. **Emits alignment** — every event type string declared in an action's
   ``emits:`` list in module.yaml must appear as a literal in the module's
   ``backend/service.py``. If the string drifts (typo, rename without updating
   the declaration), all downstream reactions.yaml subscribers and notification
   contracts silently stop firing — no startup error, no warning.

2. **Reactions handler_method alignment** — every reaction with
   ``target.kind: handler`` and a ``handler_method`` key must resolve to a
   method definition (sync or async) in the module's ``backend/base_handler.py``
   or ``backend/service.py``. If the method name drifts, the event is consumed
   but the handler dispatch raises AttributeError at reaction time — a silent
   data loss for async event processing.

Tests are parametrized so each case produces a named failure that identifies
the exact pack, module, action/reaction, and mismatched string.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _service_source(module_dir: Path) -> str | None:
    """Return the combined source of all handler files for a module directory."""
    sources: list[str] = []
    for name in ("service.py", "base_handler.py", "handler.py"):
        f = module_dir / "backend" / name
        if f.exists():
            sources.append(f.read_text(encoding="utf-8"))
    return "\n".join(sources) if sources else None


# ---------------------------------------------------------------------------
# Emits alignment
# ---------------------------------------------------------------------------

def _collect_emits_cases() -> list[tuple[str, str, str, str]]:
    """Return (pack_id, module_id, action_id, event_type) for every emits declaration."""
    cases: list[tuple[str, str, str, str]] = []
    for module_yaml in sorted(BUILD_CONTEXT.rglob("module.yaml")):
        if "__pycache__" in module_yaml.parts:
            continue
        try:
            pack_id = module_yaml.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue
        data = _read_yaml(module_yaml)
        module_id = data.get("module", {}).get("id", module_yaml.parent.name)
        for action in data.get("actions") or []:
            for evt in action.get("emits") or []:
                cases.append((pack_id, module_id, action["id"], evt))
    return cases


_EMITS_CASES = _collect_emits_cases()


@pytest.mark.parametrize(
    "pack_id,module_id,action_id,event_type",
    _EMITS_CASES,
    ids=[f"{p}/{m}/action={a}/emits={e}" for p, m, a, e in _EMITS_CASES],
)
def test_emits_event_type_appears_in_service(
    pack_id: str, module_id: str, action_id: str, event_type: str
) -> None:
    """Every event type declared in module.yaml emits: must be a literal in service.py.

    If the string drifts from the actual emit call, downstream reactions and
    notification subscriptions silently stop firing.
    """
    module_dirs = list(
        (BUILD_CONTEXT / pack_id / "templates" / "modules").glob(f"*{module_id}*")
    )
    exact = [d for d in module_dirs if d.name == module_id]
    module_dir = (exact or module_dirs)[0] if (exact or module_dirs) else None

    assert module_dir is not None and module_dir.is_dir(), (
        f"Could not locate module directory for {pack_id}/{module_id}"
    )

    src = _service_source(module_dir)
    assert src is not None, (
        f"{pack_id}/{module_id}: action {action_id!r} declares emits={event_type!r} "
        f"but no service.py / base_handler.py / handler.py found"
    )

    assert event_type in src, (
        f"{pack_id}/{module_id}: action {action_id!r} declares emits={event_type!r} "
        f"but that string does not appear in the module's backend service files. "
        f"Either update the emits declaration or fix the emit call."
    )


# ---------------------------------------------------------------------------
# Reactions handler_method alignment
# ---------------------------------------------------------------------------

def _collect_reactions_handler_cases() -> list[tuple[str, str, str, str]]:
    """Return (pack_id, reactions_file, reaction_id, handler_method) for handler reactions."""
    cases: list[tuple[str, str, str, str]] = []
    for reactions_yaml in sorted(BUILD_CONTEXT.rglob("reactions.yaml")):
        if "__pycache__" in reactions_yaml.parts:
            continue
        try:
            pack_id = reactions_yaml.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue
        data = _read_yaml(reactions_yaml)
        rel = str(reactions_yaml.relative_to(BUILD_CONTEXT))
        for rxn in data.get("reactions") or []:
            target = rxn.get("target") or {}
            if target.get("kind") != "handler":
                continue
            method = target.get("handler_method")
            if method:
                cases.append((pack_id, rel, rxn.get("id", "?"), method))
    return cases


_REACTIONS_CASES = _collect_reactions_handler_cases()


@pytest.mark.parametrize(
    "pack_id,reactions_file,reaction_id,handler_method",
    _REACTIONS_CASES,
    ids=[f"{p}/reaction={r}/method={m}" for p, _, r, m in _REACTIONS_CASES],
)
def test_reactions_handler_method_exists_in_backend(
    pack_id: str, reactions_file: str, reaction_id: str, handler_method: str
) -> None:
    """Every handler_method in a reactions.yaml target must be defined in the backend.

    The reactions runtime dispatches to the declared method by name. If the method
    is renamed or missing, the event is silently consumed with no handler executed.
    """
    reactions_path = BUILD_CONTEXT / reactions_file
    # Module dir is two levels up from contracts/reactions.yaml
    module_dir = reactions_path.parent.parent

    src = _service_source(module_dir)
    assert src is not None, (
        f"{pack_id}: reaction {reaction_id!r} declares handler_method={handler_method!r} "
        f"but no service.py / base_handler.py / handler.py found at {module_dir}"
    )

    # Match sync or async def
    found = f"def {handler_method}(" in src or f"async def {handler_method}(" in src
    assert found, (
        f"{pack_id}: reaction {reaction_id!r} declares handler_method={handler_method!r} "
        f"but that method is not defined in the module's backend files. "
        f"Either add the method or fix the reaction declaration."
    )
