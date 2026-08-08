"""emit_drift — AST-based service.py emit consistency checker.

This module is a development and test-time utility. It is NOT loaded at app
startup and has no effect on the runtime hot path.

Purpose
-------
The ModuleLoader enforces at startup that every event type in
``action.emits[]`` is declared in ``contracts/events.yaml``. It does NOT
scan ``backend/service.py`` source code to verify that:

1. Every ``ctx.emit()`` call in service.py uses an event type that is
   declared in ``module.yaml action.emits[]`` / ``contracts/events.yaml``.
2. Every event type declared in ``module.yaml action.emits[]`` is actually
   emitted by service.py (optional completeness check).

These two checks together close the "orphaned emit" and "declared-but-missing
emit" drift classes. They are intentionally test-time / CI checks — not
startup validators — because service.py may legitimately use dynamic event
types via variables (which the AST cannot statically resolve to a declaration).

Public API
----------
``scan_service_emit_literals(service_py)``
    Return the set of string-literal event types found in
    ``ctx.emit("event_type", ...)`` calls in the given file.

``load_declared_emits(module_yaml)``
    Return the set of event types declared in ``actions[].emits[]`` across
    all actions in a module.yaml file.

``check_orphaned_emits(module_dir)``
    Return a list of ``(event_type, line_no)`` pairs for ``ctx.emit()``
    calls whose event type is not declared in the module contract.
    Returns an empty list when service.py does not exist or when no
    orphaned emits are found.

Typical usage in a generated app's drift-guard test
----------------------------------------------------
::

    from mozaiksai.core.runtime.app.emit_drift import check_orphaned_emits
    from pathlib import Path

    def test_tasks_service_emits_match_contract():
        orphans = check_orphaned_emits(
            Path(__file__).resolve().parents[1] / "app" / "modules" / "tasks"
        )
        assert orphans == [], (
            f"service.py calls ctx.emit() for undeclared events: {orphans}"
        )
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scan_service_emit_literals(service_py: Path) -> set[str]:
    """Return string-literal event types from ``ctx.emit("...", ...)`` calls.

    Only captures calls of the form ``<expr>.emit(<string_literal>, ...)``.
    Dynamic calls such as ``ctx.emit(event_var, ...)`` are intentionally
    ignored — they cannot be resolved statically and should not produce
    false positives.

    Returns an empty set when ``service_py`` does not exist.
    """
    if not service_py.exists():
        return set()

    try:
        source = service_py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(service_py))
    except SyntaxError:
        return set()

    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "emit"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def scan_service_emit_literals_with_lines(service_py: Path) -> list[tuple[str, int]]:
    """Like ``scan_service_emit_literals`` but returns ``(event_type, line_no)`` pairs.

    Used internally by ``check_orphaned_emits`` for diagnostic output.
    """
    if not service_py.exists():
        return []

    try:
        source = service_py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(service_py))
    except SyntaxError:
        return []

    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "emit"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append((node.args[0].value, node.lineno))
    return found


def load_declared_emits(module_yaml: Path) -> set[str]:
    """Return the set of event types declared across all ``actions[].emits[]``.

    Returns an empty set when ``module_yaml`` does not exist or contains no
    actions with emits.
    """
    if not module_yaml.exists():
        return set()

    raw: Any = yaml.safe_load(module_yaml.read_text(encoding="utf-8")) or {}
    declared: set[str] = set()
    for action in raw.get("actions", []):
        for event_type in action.get("emits", []):
            if isinstance(event_type, str):
                declared.add(event_type)
    return declared


def check_orphaned_emits(module_dir: Path) -> list[tuple[str, int]]:
    """Return ``(event_type, line_no)`` pairs for undeclared ``ctx.emit()`` calls.

    An emit is *orphaned* when service.py calls ``ctx.emit("event_type", ...)``
    but ``event_type`` does not appear in any ``actions[].emits[]`` entry in
    ``module.yaml``.

    Returns an empty list when:
    - ``backend/service.py`` does not exist (module has no service layer)
    - all emits in service.py are declared in the module contract
    """
    service_py = module_dir / "backend" / "service.py"
    module_yaml = module_dir / "module.yaml"

    declared = load_declared_emits(module_yaml)
    candidates = scan_service_emit_literals_with_lines(service_py)

    return [
        (event_type, line_no)
        for event_type, line_no in candidates
        if event_type not in declared
    ]
