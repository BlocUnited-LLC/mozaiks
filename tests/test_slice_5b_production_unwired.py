from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUBSTRATE_MODULES = {
    "mozaiksai.core.workflow.assignment_admission",
    "mozaiksai.core.workflow.assignment_artifacts",
    "mozaiksai.core.semantics.composition_ledger",
}
PRODUCTION_SURFACES = (
    ROOT / "factory_app/workflows/AppGenerator",
    ROOT / "mozaiksai/core/workflow/task_batches.py",
    ROOT / "mozaiksai/core/workflow/orchestration_patterns.py",
    ROOT / "mozaiksai/core/adapters",
    ROOT / "mozaiksai/core/semantics/materialization.py",
    ROOT / "mozaiksai/core/runtime/persistence",
    ROOT / "mozaiksai/control_plane",
)


def _python_files(path: Path) -> tuple[Path, ...]:
    return tuple(path.rglob("*.py")) if path.is_dir() else (path,)


def test_slice_5b_substrate_has_no_production_importer() -> None:
    importers: list[str] = []
    for surface in PRODUCTION_SURFACES:
        for path in _python_files(surface):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imported = None
                if isinstance(node, ast.ImportFrom):
                    imported = node.module
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in SUBSTRATE_MODULES:
                            imported = alias.name
                if imported in SUBSTRATE_MODULES:
                    importers.append(str(path.relative_to(ROOT)))
    assert not importers


def test_obsolete_worker_framework_and_runtime_identity_fields_are_absent() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "mozaiksai").rglob("*.py")
    )
    for retired in (
        "class WorkAssignment",
        "class WorkResult",
        "class IntegrationResult",
        "WorkAssignmentExecutionContext",
        "make_work_assignment",
        "allowed_agent_ids",
    ):
        assert retired not in source
    for runtime_identity in (
        "agent_id",
        "task_id",
        "passport",
        "resume_token",
        "channel_id",
    ):
        for module in SUBSTRATE_MODULES:
            path = ROOT / (module.replace(".", "/") + ".py")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            fields = {
                target.id
                for node in ast.walk(tree)
                if isinstance(node, ast.AnnAssign)
                and isinstance((target := node.target), ast.Name)
            }
            assert runtime_identity not in fields


def test_appbuildplan_remains_explicit_production_authority() -> None:
    task_batches = (ROOT / "mozaiksai/core/workflow/task_batches.py").read_text(
        encoding="utf-8"
    )
    agents = (
        ROOT / "factory_app/workflows/AppGenerator/agents.yaml"
    ).read_text(encoding="utf-8")
    assert 'base_context.get("app_build_plan")' in task_batches
    assert "authoritative persistent page inventory" in agents
