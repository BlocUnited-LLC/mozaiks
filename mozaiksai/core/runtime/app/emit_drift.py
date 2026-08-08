"""emit_drift — AST-based service.py emit consistency checker.

This module is a development and test-time utility. It is NOT loaded at app
startup and has no effect on the runtime hot path.

Purpose
-------
The ModuleLoader enforces at startup that every event type in
``action.emits[]`` is declared in ``contracts/events.yaml``. It does NOT
scan ``backend/service.py`` source code to verify that:

1. Every ``ctx.emit()`` call in service.py uses an event type that is
   declared in ``module.yaml action.emits[]`` / ``contracts/events.yaml``
   (Lane 3 — orphaned emits).
2. Every event type declared in ``module.yaml action.emits[]`` and
   ``contracts/events.yaml`` is actually emitted somewhere in service.py
   (Lane 4 — missing emits / contracts/events.yaml ↔ service.py).

Both checks close drift classes that are invisible to the loader. They are
intentionally test-time / CI checks — not startup validators — because
service.py may use dynamic event types via variables that cannot be resolved
statically, and because a module might legitimately emit a declared event from
a background worker rather than service.py directly.

Public API
----------
``scan_service_emit_literals(service_py)``
    Return the set of string-literal event types found in
    ``ctx.emit("event_type", ...)`` calls in the given file.

``load_declared_emits(module_yaml)``
    Return the set of event types declared in ``actions[].emits[]`` across
    all actions in a module.yaml file.

``load_declared_events_from_yaml(events_yaml)``
    Return the set of event types declared in a ``contracts/events.yaml``
    file. Complements ``load_declared_emits``; both sets are equal for a
    well-formed module because the loader enforces
    ``module.yaml emits[] ⊆ events.yaml`` as a hard startup error.

``check_orphaned_emits(module_dir)``
    Lane 3. Return ``(event_type, line_no)`` pairs for ``ctx.emit()`` calls
    in service.py whose event type is absent from ``module.yaml emits[]``.
    Returns an empty list when service.py does not exist or all emits are
    declared.

``check_missing_emits(module_dir)``
    Lane 4. Return a sorted list of event types declared in
    ``module.yaml emits[]`` (backed by ``contracts/events.yaml``) that are
    absent from service.py ``ctx.emit()`` string literals. Returns an empty
    list when events.yaml does not exist, when no events are declared, or
    when all declared events have a matching emit in service.py.

Typical usage in a generated app's drift-guard tests
-----------------------------------------------------
::

    from mozaiksai.core.runtime.app.emit_drift import (
        check_orphaned_emits,
        check_missing_emits,
    )
    from pathlib import Path

    MODULE_DIR = Path(__file__).resolve().parents[1] / "app" / "modules" / "tasks"

    def test_tasks_service_emits_match_contract():
        orphans = check_orphaned_emits(MODULE_DIR)
        assert orphans == [], f"undeclared emits: {orphans}"

    def test_tasks_declared_events_are_emitted():
        missing = check_missing_emits(MODULE_DIR)
        assert missing == [], f"declared but never emitted: {missing}"
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


def load_declared_events_from_yaml(events_yaml: Path) -> set[str]:
    """Return the set of event types declared in ``contracts/events.yaml``.

    The loader enforces ``module.yaml emits[] ⊆ events.yaml`` at startup, so
    this set equals ``load_declared_emits(module.yaml)`` for any well-formed
    module.  Use this function when you want to read directly from the events
    catalog rather than through module.yaml.

    Returns an empty set when ``events_yaml`` does not exist or contains no
    events.
    """
    if not events_yaml.exists():
        return set()

    raw: Any = yaml.safe_load(events_yaml.read_text(encoding="utf-8")) or {}
    declared: set[str] = set()
    for event in raw.get("events", []):
        if isinstance(event, dict):
            event_type = event.get("type")
            if isinstance(event_type, str) and event_type:
                declared.add(event_type)
    return declared


def check_missing_emits(module_dir: Path) -> list[str]:
    """Return event types declared in module.yaml but absent from service.py.

    Lane 4 — contracts/events.yaml ↔ service.py alignment.

    An event is *missing* when ``module.yaml action.emits[]`` declares it
    (meaning it is also declared in ``contracts/events.yaml``, enforced by the
    loader) but ``backend/service.py`` contains no ``ctx.emit("event_type",
    ...)`` string-literal call for it.

    This catches "declared but never emitted" drift — e.g. an event added to
    module.yaml and events.yaml during design but whose ctx.emit() call was
    never written, or an emit that was removed from service.py without cleaning
    up the declaration.

    Returns an empty list when:
    - ``module.yaml`` declares no emits (nothing to check)
    - ``backend/service.py`` does not exist and no events are declared
    - all declared events have a matching ctx.emit() literal in service.py

    When ``backend/service.py`` does not exist but events are declared, all
    declared events are returned as missing — they cannot be emitted by a
    non-existent service layer.

    Note: only string-literal ``ctx.emit("event_type", ...)`` calls are
    detected. Dynamic emit calls using variables are not captured, so a
    declared event emitted dynamically will appear as missing. In that case,
    suppress the check for that event by excluding it from the assertion or by
    adding a ``# emit: event_type`` marker comment as documentation.
    """
    module_yaml = module_dir / "module.yaml"
    service_py = module_dir / "backend" / "service.py"

    declared = load_declared_emits(module_yaml)
    if not declared:
        return []

    actual = scan_service_emit_literals(service_py)
    return sorted(declared - actual)


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
