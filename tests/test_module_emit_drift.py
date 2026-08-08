"""Regression tests for emit_drift.py — service.py / module.yaml emits alignment.

These tests cover the two contract drift classes that ModuleLoader does NOT
enforce at startup:

Lane 3 — emits ↔ service.py alignment:
  service.py calls ctx.emit("event_A") but event_A is absent from
  module.yaml action.emits[]. The loader only checks the declaration
  direction (module.yaml → events.yaml) — it does not read service.py.

Lane 4 — contracts/events.yaml ↔ service.py alignment:
  Same gap viewed from events.yaml: since the loader already enforces
  module.yaml emits[] → events.yaml (hard error), an orphan in service.py
  implies a missing declaration in BOTH module.yaml and events.yaml.

The fix for both lanes is the same: every ctx.emit() string literal in
service.py must be declared in module.yaml action.emits[].

These tests prove the AST scanner catches real drift so generated app
drift-guard tests can rely on the same pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mozaiksai.core.runtime.app.emit_drift import (
    check_orphaned_emits,
    load_declared_emits,
    scan_service_emit_literals,
    scan_service_emit_literals_with_lines,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_module(
    root: Path,
    *,
    declared_emits: list[str] | None = None,
    service_src: str | None = None,
) -> Path:
    """Write a minimal module directory under root/modules/tasks/."""
    module_dir = root / "modules" / "tasks"
    (module_dir / "backend").mkdir(parents=True, exist_ok=True)

    emits_yaml = (
        "    emits: [" + ", ".join(declared_emits) + "]"
        if declared_emits
        else "    emits: []"
    )

    module_dir.joinpath("module.yaml").write_text(
        f"""
schema_version: mozaiks.module.v1
module:
  id: tasks
  display_name: Tasks
  version: 1.0.0
  description: Task management
  handler: backend.handler:TasksModule
permissions:
  - id: tasks.write
    description: Create and update tasks
actions:
  - id: create
    description: Create a task
    handler_method: create_task
    permissions: [tasks.write]
{emits_yaml}
""".lstrip(),
        encoding="utf-8",
    )

    if service_src is not None:
        module_dir.joinpath("backend", "service.py").write_text(
            service_src, encoding="utf-8"
        )

    return module_dir


# ---------------------------------------------------------------------------
# scan_service_emit_literals — basic scanner behaviour
# ---------------------------------------------------------------------------


def test_scan_finds_string_literal_emit(tmp_path: Path) -> None:
    """Scanner must detect ctx.emit('event_type', ...) string literals."""
    service_py = tmp_path / "service.py"
    service_py.write_text(
        """
async def create_task(self, ctx, *, title):
    await ctx.emit("domain.tasks.task_created", {"title": title})
    return {"task_id": "t1"}
""",
        encoding="utf-8",
    )

    result = scan_service_emit_literals(service_py)
    assert result == {"domain.tasks.task_created"}


def test_scan_finds_multiple_emits(tmp_path: Path) -> None:
    """Scanner must collect all unique string-literal event types."""
    service_py = tmp_path / "service.py"
    service_py.write_text(
        """
async def run_migration(self, ctx, *, migration_id):
    try:
        await ctx.emit("hosted.schema_migrations.migration.applied", {})
    except Exception:
        await ctx.emit("hosted.schema_migrations.migration.failed", {})
""",
        encoding="utf-8",
    )

    result = scan_service_emit_literals(service_py)
    assert result == {
        "hosted.schema_migrations.migration.applied",
        "hosted.schema_migrations.migration.failed",
    }


def test_scan_ignores_dynamic_emit_variable(tmp_path: Path) -> None:
    """Dynamic ctx.emit(variable, ...) calls must NOT be captured.

    Variables cannot be statically resolved to a declaration and would
    produce false positives. The scanner only captures string literals.
    """
    service_py = tmp_path / "service.py"
    service_py.write_text(
        """
async def do_thing(self, ctx, *, event_name):
    await ctx.emit(event_name, {})
""",
        encoding="utf-8",
    )

    result = scan_service_emit_literals(service_py)
    assert result == set()


def test_scan_ignores_f_string_emit(tmp_path: Path) -> None:
    """f-string emit arguments must NOT be captured (not a string Constant)."""
    service_py = tmp_path / "service.py"
    service_py.write_text(
        """
async def do_thing(self, ctx, *, ns):
    await ctx.emit(f"domain.{ns}.created", {})
""",
        encoding="utf-8",
    )

    result = scan_service_emit_literals(service_py)
    assert result == set()


def test_scan_returns_empty_when_no_service_py(tmp_path: Path) -> None:
    """Scanner must return empty set when the file does not exist."""
    result = scan_service_emit_literals(tmp_path / "nonexistent.py")
    assert result == set()


def test_scan_returns_empty_for_service_with_no_emits(tmp_path: Path) -> None:
    """Scanner must return empty set for a service that never calls ctx.emit."""
    service_py = tmp_path / "service.py"
    service_py.write_text(
        """
async def create_task(self, ctx, *, title):
    return {"task_id": "t1"}
""",
        encoding="utf-8",
    )

    result = scan_service_emit_literals(service_py)
    assert result == set()


def test_scan_with_lines_returns_line_numbers(tmp_path: Path) -> None:
    """scan_service_emit_literals_with_lines must include line numbers."""
    service_py = tmp_path / "service.py"
    service_py.write_text(
        """async def run(self, ctx):
    await ctx.emit("domain.tasks.task_created", {})
    await ctx.emit("domain.tasks.task_failed", {})
""",
        encoding="utf-8",
    )

    result = scan_service_emit_literals_with_lines(service_py)
    event_types = [e for e, _ in result]
    assert "domain.tasks.task_created" in event_types
    assert "domain.tasks.task_failed" in event_types
    # All line numbers must be positive integers
    assert all(ln > 0 for _, ln in result)


# ---------------------------------------------------------------------------
# load_declared_emits — module.yaml parsing
# ---------------------------------------------------------------------------


def test_load_declared_emits_from_module_yaml(tmp_path: Path) -> None:
    module_yaml = tmp_path / "module.yaml"
    module_yaml.write_text(
        """
schema_version: mozaiks.module.v1
module:
  id: tasks
  display_name: Tasks
  version: 1.0.0
  description: x
  handler: backend.handler:TasksModule
actions:
  - id: create
    description: Create
    handler_method: create_task
    emits: [domain.tasks.task_created]
  - id: delete
    description: Delete
    handler_method: delete_task
    emits:
      - domain.tasks.task_deleted
      - domain.tasks.task_archived
""".lstrip(),
        encoding="utf-8",
    )

    result = load_declared_emits(module_yaml)
    assert result == {
        "domain.tasks.task_created",
        "domain.tasks.task_deleted",
        "domain.tasks.task_archived",
    }


def test_load_declared_emits_returns_empty_for_no_emits(tmp_path: Path) -> None:
    module_yaml = tmp_path / "module.yaml"
    module_yaml.write_text(
        """
schema_version: mozaiks.module.v1
module:
  id: tasks
  display_name: Tasks
  version: 1.0.0
  description: x
  handler: backend.handler:TasksModule
actions:
  - id: create
    description: Create
    handler_method: create_task
    emits: []
""".lstrip(),
        encoding="utf-8",
    )

    result = load_declared_emits(module_yaml)
    assert result == set()


def test_load_declared_emits_returns_empty_when_file_absent(tmp_path: Path) -> None:
    result = load_declared_emits(tmp_path / "nonexistent.yaml")
    assert result == set()


# ---------------------------------------------------------------------------
# check_orphaned_emits — end-to-end drift detection
# ---------------------------------------------------------------------------


def test_check_orphaned_emits_returns_empty_when_no_service_py(
    tmp_path: Path,
) -> None:
    """No service.py → no orphans to report."""
    module_dir = _write_module(tmp_path, declared_emits=["domain.tasks.task_created"])
    # service.py intentionally not created
    assert check_orphaned_emits(module_dir) == []


def test_check_orphaned_emits_returns_empty_when_emits_match(
    tmp_path: Path,
) -> None:
    """service.py emits exactly the declared event → no drift."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created"],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    await ctx.emit("domain.tasks.task_created", {"title": title})\n'
            '    return {"task_id": "t1"}\n'
        ),
    )

    assert check_orphaned_emits(module_dir) == []


def test_check_orphaned_emits_catches_undeclared_emit(tmp_path: Path) -> None:
    """service.py emits an event NOT in module.yaml → drift detected.

    Regression guard for Lane 3: this exact pattern appeared in
    mozaikspay_merchant where enable_mozaikspay never called ctx.emit()
    even though the event was declared — the inverse (calling emit for an
    undeclared event) is equally dangerous and equally invisible to the loader.
    """
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created"],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    await ctx.emit("domain.tasks.task_created", {"title": title})\n'
            '    # This event was removed from module.yaml but the call was not cleaned up\n'
            '    await ctx.emit("domain.tasks.task_indexed", {"title": title})\n'
            '    return {"task_id": "t1"}\n'
        ),
    )

    orphans = check_orphaned_emits(module_dir)
    assert len(orphans) == 1
    event_type, line_no = orphans[0]
    assert event_type == "domain.tasks.task_indexed"
    assert line_no > 0


def test_check_orphaned_emits_catches_all_undeclared_emits(tmp_path: Path) -> None:
    """All orphaned emits must be reported, not just the first."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=[],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    await ctx.emit("domain.tasks.created", {})\n'
            '    await ctx.emit("domain.tasks.failed", {})\n'
        ),
    )

    orphans = check_orphaned_emits(module_dir)
    orphaned_types = {e for e, _ in orphans}
    assert orphaned_types == {"domain.tasks.created", "domain.tasks.failed"}


def test_check_orphaned_emits_ignores_dynamic_calls(tmp_path: Path) -> None:
    """Dynamic ctx.emit(var, ...) in service.py must not produce false positives."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=[],
        service_src=(
            'async def do_thing(self, ctx, *, event_name):\n'
            '    await ctx.emit(event_name, {})\n'
        ),
    )

    assert check_orphaned_emits(module_dir) == []


def test_check_orphaned_emits_returns_empty_when_no_emits_anywhere(
    tmp_path: Path,
) -> None:
    """Service with no emits and module.yaml with no declared emits → no drift."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=[],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    return {"task_id": "t1"}\n'
        ),
    )

    assert check_orphaned_emits(module_dir) == []


# ---------------------------------------------------------------------------
# Integration: drift-guard pattern for a generated app module
# ---------------------------------------------------------------------------


def test_drift_guard_pattern_used_by_generated_app_tests(tmp_path: Path) -> None:
    """The drift-guard pattern from module_archetypes.yaml drift_guard_test_guidance
    must correctly catch orphaned emits in a freshly-generated module.

    This test mimics exactly what AppGenerator scaffolds into
    tests/test_{module_id}_module_drift.py for every generated module.
    It proves the pattern works end-to-end, not just as advisory YAML text.
    """
    # Simulate a newly-generated module where the developer added a
    # ctx.emit call to service.py but forgot to declare it in module.yaml.
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created"],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    await ctx.emit("domain.tasks.task_created", {"title": title})\n'
            '    # Whoops — forgot to declare this in module.yaml\n'
            '    await ctx.emit("domain.tasks.task_queued_for_indexing", {})\n'
            '    return {"task_id": "t1"}\n'
        ),
    )

    # This is the exact assertion a generated drift-guard test would make:
    orphans = check_orphaned_emits(module_dir)
    assert orphans != [], (
        "Expected the drift-guard to catch the undeclared emit; "
        "the pattern in module_archetypes.yaml drift_guard_test_guidance is broken"
    )
    orphaned_types = {e for e, _ in orphans}
    assert "domain.tasks.task_queued_for_indexing" in orphaned_types
