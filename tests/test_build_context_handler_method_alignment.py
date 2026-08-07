"""Cross-pack drift guard: handler_method declarations must exist in base_handler.py.

Each generated module's module.yaml declares a ``handler_method`` per action.
The module_loader dispatches calls using these names. If a name drifts from the
actual implementation the action silently 404s or raises AttributeError at
runtime — no startup warning, no type error.

This test file walks every pack template that declares handler_methods and
asserts each one appears as a method definition in the corresponding
backend/base_handler.py (preferred) or backend/handler.py.

Tests are parametrized so a single mismatch surfaces as a named failure
identifying the exact pack, module, and method — not just a bulk assertion.
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


def _collect_cases() -> list[tuple[str, str, str]]:
    """Return (pack_id, module_id, method_name) for every declared handler_method."""
    cases: list[tuple[str, str, str]] = []
    for module_yaml in sorted(BUILD_CONTEXT.rglob("module.yaml")):
        if "__pycache__" in module_yaml.parts:
            continue
        # Derive pack_id from path: build_context/<pack_id>/templates/modules/<module_id>/module.yaml
        try:
            rel = module_yaml.relative_to(BUILD_CONTEXT)
            pack_id = rel.parts[0]
        except ValueError:
            continue
        data = _read_yaml(module_yaml)
        module_id = data.get("module", {}).get("id", module_yaml.parent.name)
        for action in data.get("actions") or []:
            method = action.get("handler_method")
            if method:
                cases.append((pack_id, module_id, method))
    return cases


_CASES = _collect_cases()


@pytest.mark.parametrize("pack_id,module_id,method_name", _CASES,
                         ids=[f"{p}/{m}/{n}" for p, m, n in _CASES])
def test_handler_method_exists_in_base_handler(
    pack_id: str, module_id: str, method_name: str
) -> None:
    """Every handler_method declared in module.yaml must be defined in base_handler.py.

    This prevents the module_loader from dispatching to a method that does not
    exist — an error that would only surface at request time, not at startup.
    """
    # Find the module directory that owns this module_id within the pack
    module_dirs = list(
        (BUILD_CONTEXT / pack_id / "templates" / "modules").glob(f"*{module_id}*")
    )
    # Prefer exact match
    exact = [d for d in module_dirs if d.name == module_id]
    module_dir = (exact or module_dirs)[0] if (exact or module_dirs) else None

    assert module_dir is not None and module_dir.is_dir(), (
        f"Could not locate module directory for {pack_id}/{module_id}"
    )

    handler = _handler_file(module_dir)
    assert handler is not None, (
        f"{pack_id}/{module_id}: no base_handler.py or handler.py found "
        f"but module.yaml declares handler_method={method_name!r}"
    )

    source = handler.read_text(encoding="utf-8")
    pattern = re.compile(rf"\bdef {re.escape(method_name)}\s*\(")
    assert pattern.search(source), (
        f"{pack_id}/{module_id}: handler_method {method_name!r} declared in "
        f"module.yaml but not found in {handler.name}. "
        f"Either add the method or fix the declaration."
    )


def test_all_packs_with_actions_have_handler_files() -> None:
    """Every pack module that declares actions must ship a base_handler.py or handler.py.

    A module with actions but no handler file would fail at load time with a
    confusing ImportError rather than a clear contract error.
    """
    for module_yaml in sorted(BUILD_CONTEXT.rglob("module.yaml")):
        if "__pycache__" in module_yaml.parts:
            continue
        data = _read_yaml(module_yaml)
        actions = data.get("actions") or []
        if not any(a.get("handler_method") for a in actions):
            continue  # no handler_method declarations — skip
        module_dir = module_yaml.parent
        handler = _handler_file(module_dir)
        assert handler is not None, (
            f"{module_yaml.relative_to(BUILD_CONTEXT)}: module declares actions with "
            f"handler_method but no base_handler.py or handler.py exists"
        )
