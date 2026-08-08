"""Regression tests for emit_drift.py — service.py / module.yaml emits alignment.

These tests cover the two contract drift classes that ModuleLoader does NOT
enforce at startup:

Lane 3 — emits ↔ service.py alignment:
  service.py calls ctx.emit("event_A") but event_A is absent from
  module.yaml action.emits[]. Covered by check_orphaned_emits.

Lane 4 — contracts/events.yaml ↔ service.py alignment:
  module.yaml declares event_A in action.emits[] (backed by events.yaml)
  but service.py never calls ctx.emit("event_A"). Covered by
  check_missing_emits. This catches "declared but dead" events — added to
  the contract during design but whose emit call was never written, or an
  emit removed from service.py without cleaning up the declaration.

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
    check_missing_emits,
    check_orphaned_emits,
    load_declared_emits,
    load_declared_events_from_yaml,
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
    write_events_yaml: bool = False,
) -> Path:
    """Write a minimal module directory under root/modules/tasks/.

    When ``write_events_yaml=True``, a ``contracts/events.yaml`` is created
    that mirrors ``declared_emits`` so the module is well-formed with respect
    to the loader's events.yaml requirement.
    """
    module_dir = root / "modules" / "tasks"
    (module_dir / "backend").mkdir(parents=True, exist_ok=True)
    (module_dir / "contracts").mkdir(parents=True, exist_ok=True)

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

    if write_events_yaml and declared_emits:
        events_entries = "\n".join(
            f"  - type: {et}\n    version: 1\n    producer: tasks\n    payload_schema: {{}}"
            for et in declared_emits
        )
        module_dir.joinpath("contracts", "events.yaml").write_text(
            f"schema_version: mozaiks.events.v1\nevents:\n{events_entries}\n",
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


# ---------------------------------------------------------------------------
# load_declared_events_from_yaml — contracts/events.yaml reader
# ---------------------------------------------------------------------------


def test_load_declared_events_from_yaml_reads_event_types(tmp_path: Path) -> None:
    events_yaml = tmp_path / "events.yaml"
    events_yaml.write_text(
        """
schema_version: mozaiks.events.v1
events:
  - type: domain.tasks.task_created
    version: 1
    producer: tasks
    payload_schema: {}
  - type: domain.tasks.task_deleted
    version: 1
    producer: tasks
    payload_schema: {}
""".lstrip(),
        encoding="utf-8",
    )

    result = load_declared_events_from_yaml(events_yaml)
    assert result == {"domain.tasks.task_created", "domain.tasks.task_deleted"}


def test_load_declared_events_from_yaml_returns_empty_when_absent(
    tmp_path: Path,
) -> None:
    result = load_declared_events_from_yaml(tmp_path / "nonexistent.yaml")
    assert result == set()


def test_load_declared_events_from_yaml_returns_empty_for_no_events(
    tmp_path: Path,
) -> None:
    events_yaml = tmp_path / "events.yaml"
    events_yaml.write_text(
        "schema_version: mozaiks.events.v1\nevents: []\n", encoding="utf-8"
    )
    assert load_declared_events_from_yaml(events_yaml) == set()


def test_load_declared_events_matches_load_declared_emits_for_well_formed_module(
    tmp_path: Path,
) -> None:
    """For a well-formed module (loader invariant holds), the event types in
    events.yaml and module.yaml emits[] must be the same set.

    The loader enforces module.yaml emits[] ⊆ events.yaml as a hard error, so
    for any module that loads successfully, these two sets are equal.
    """
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created", "domain.tasks.task_deleted"],
        write_events_yaml=True,
    )
    from_module_yaml = load_declared_emits(module_dir / "module.yaml")
    from_events_yaml = load_declared_events_from_yaml(
        module_dir / "contracts" / "events.yaml"
    )
    assert from_module_yaml == from_events_yaml


# ---------------------------------------------------------------------------
# Lane 4: check_missing_emits — contracts/events.yaml ↔ service.py alignment
# ---------------------------------------------------------------------------


def test_check_missing_emits_returns_empty_when_no_declared_emits(
    tmp_path: Path,
) -> None:
    """No declared emits in module.yaml → nothing can be missing."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=[],
        service_src="async def create_task(self, ctx, *, title):\n    return {}\n",
    )
    assert check_missing_emits(module_dir) == []


def test_check_missing_emits_returns_empty_when_service_emits_all_declared(
    tmp_path: Path,
) -> None:
    """service.py emits every declared event → no missing emits."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created"],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    await ctx.emit("domain.tasks.task_created", {"title": title})\n'
            '    return {"task_id": "t1"}\n'
        ),
    )
    assert check_missing_emits(module_dir) == []


def test_check_missing_emits_catches_declared_event_never_emitted(
    tmp_path: Path,
) -> None:
    """Declared event absent from service.py → reported as missing.

    Regression guard for Lane 4: this exact pattern appeared in the App Zero
    sweep where module.yaml and events.yaml declared an event (e.g.
    hosted.wallet.payout.failed) but service.py never called ctx.emit() for it.
    The loader cannot catch this because it only enforces the declaration
    direction; the check requires reading service.py.
    """
    module_dir = _write_module(
        tmp_path,
        declared_emits=[
            "domain.tasks.task_created",
            "domain.tasks.task_failed",  # declared but never emitted
        ],
        service_src=(
            'async def create_task(self, ctx, *, title):\n'
            '    await ctx.emit("domain.tasks.task_created", {"title": title})\n'
            '    return {"task_id": "t1"}\n'
            '    # NOTE: task_failed is declared but the emit call was never written\n'
        ),
    )

    missing = check_missing_emits(module_dir)
    assert missing == ["domain.tasks.task_failed"]


def test_check_missing_emits_reports_all_missing_events(tmp_path: Path) -> None:
    """All missing events are returned, not just the first."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=[
            "domain.tasks.task_created",
            "domain.tasks.task_deleted",
            "domain.tasks.task_archived",
        ],
        service_src="async def noop(self, ctx): return {}\n",
    )

    missing = check_missing_emits(module_dir)
    assert set(missing) == {
        "domain.tasks.task_created",
        "domain.tasks.task_deleted",
        "domain.tasks.task_archived",
    }
    # Result must be sorted for deterministic output
    assert missing == sorted(missing)


def test_check_missing_emits_returns_all_when_no_service_py(tmp_path: Path) -> None:
    """No service.py + declared emits → all declared events are missing."""
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created"],
        # service_src intentionally absent
    )
    assert check_missing_emits(module_dir) == ["domain.tasks.task_created"]


def test_check_missing_emits_ignores_dynamic_emits_as_non_matching(
    tmp_path: Path,
) -> None:
    """Dynamic ctx.emit(variable, ...) does not count as covering a declared event.

    A declared event emitted dynamically still appears as missing. The
    developer must either use a string literal or suppress the assertion.
    """
    module_dir = _write_module(
        tmp_path,
        declared_emits=["domain.tasks.task_created"],
        service_src=(
            'async def create_task(self, ctx, *, event_name):\n'
            '    await ctx.emit(event_name, {})\n'  # dynamic — not captured
        ),
    )

    missing = check_missing_emits(module_dir)
    assert "domain.tasks.task_created" in missing


def test_check_missing_emits_returns_empty_when_no_module_yaml(
    tmp_path: Path,
) -> None:
    """No module.yaml → no declared emits → nothing to report."""
    module_dir = tmp_path / "modules" / "tasks"
    (module_dir / "backend").mkdir(parents=True, exist_ok=True)
    # module.yaml intentionally absent
    assert check_missing_emits(module_dir) == []


# ---------------------------------------------------------------------------
# Integration: drift-guard pattern for contracts/events.yaml ↔ service.py
# ---------------------------------------------------------------------------


def test_drift_guard_pattern_for_events_yaml_service_alignment(tmp_path: Path) -> None:
    """The events_yaml_service_alignment pattern from module_archetypes.yaml
    drift_guard_test_guidance must correctly catch declared-but-never-emitted
    events in a freshly-generated module.

    This test mimics what AppGenerator scaffolds into
    tests/test_{module_id}_module_drift.py for the Lane 4 check.
    """
    # Simulate a module where events.yaml and module.yaml declare an event
    # that was added during design but whose ctx.emit() call was never written.
    module_dir = _write_module(
        tmp_path,
        declared_emits=[
            "hosted.schema_migrations.migration.applied",
            "hosted.schema_migrations.migration.failed",  # declared but missing
        ],
        service_src=(
            'async def run_migration(self, ctx, *, migration_id):\n'
            '    await ctx.emit("hosted.schema_migrations.migration.applied", {})\n'
            '    # migration.failed was declared in module.yaml but the\n'
            '    # except branch and its ctx.emit were never implemented\n'
            '    return {"success": True}\n'
        ),
    )

    # Exact assertion a generated drift-guard test would make:
    missing = check_missing_emits(module_dir)
    assert missing != [], (
        "Expected the drift-guard to catch the declared-but-missing emit; "
        "the events_yaml_service_alignment pattern is broken"
    )
    assert "hosted.schema_migrations.migration.failed" in missing
