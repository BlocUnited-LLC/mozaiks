from __future__ import annotations

import ast
from pathlib import Path

from mozaiksai.core.semantics.artifact_revision import (
    ApplicationPublication,
    ArtifactRevision,
)

ROOT = Path(__file__).resolve().parents[1]
SUBSTRATE_MODULES = {
    "mozaiksai.core.semantics.artifact_revision",
    "mozaiksai.core.artifacts.revision_store",
}
PRODUCTION_SURFACES = (
    ROOT / "factory_app/workflows/AppGenerator",
    ROOT / "factory_app/refinement_harness",
    ROOT / "mozaiksai/hosts/studio.py",
    ROOT / "mozaiksai/core/artifacts/store.py",
    ROOT / "mozaiksai/core/app_context",
    ROOT / "mozaiksai/control_plane",
    ROOT / "mozaiksai/core/workflow/task_batches.py",
    ROOT / "mozaiksai/core/adapters",
    ROOT / "mozaiksai/core/semantics/materialization.py",
)


def _python_files(path: Path) -> tuple[Path, ...]:
    return tuple(path.rglob("*.py")) if path.is_dir() else (path,)


def test_slice_5c_has_no_production_importer() -> None:
    importers: list[str] = []
    for surface in PRODUCTION_SURFACES:
        for path in _python_files(surface):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in SUBSTRATE_MODULES:
                    importers.append(str(path.relative_to(ROOT)))
                elif isinstance(node, ast.Import):
                    if any(alias.name in SUBSTRATE_MODULES for alias in node.names):
                        importers.append(str(path.relative_to(ROOT)))
    assert importers == []


def test_package_roots_do_not_activate_offline_revision_store() -> None:
    artifact_root = (ROOT / "mozaiksai/core/artifacts/__init__.py").read_text(encoding="utf-8")
    semantic_root = (ROOT / "mozaiksai/core/semantics/__init__.py").read_text(encoding="utf-8")
    assert "revision_store" not in artifact_root
    assert "artifact_revision import" not in semantic_root


def test_slice_5c_contract_has_no_ag2_or_runtime_execution_identity() -> None:
    paths = (
        ROOT / "mozaiksai/core/semantics/artifact_revision.py",
        ROOT / "mozaiksai/core/artifacts/revision_store.py",
    )
    fields: set[str] = set()
    imports: set[str] = set()
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                fields.add(node.target.id.casefold())
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.casefold())
            elif isinstance(node, ast.Import):
                imports.update(alias.name.casefold() for alias in node.names)
    legacy_agent_package = "".join(("auto", "gen"))
    assert not any(
        "ag2" in module or legacy_agent_package in module for module in imports
    )
    for forbidden in (
        "agent_id",
        "task_id",
        "passport",
        "resume_token",
        "channel_id",
        "runtime_attempt",
    ):
        assert forbidden not in fields


def test_appbuildplan_and_buildrecord_remain_production_authority() -> None:
    task_batches = (ROOT / "mozaiksai/core/workflow/task_batches.py").read_text(encoding="utf-8")
    build_store = (ROOT / "mozaiksai/core/artifacts/store.py").read_text(encoding="utf-8")
    studio = (ROOT / "mozaiksai/hosts/studio.py").read_text(encoding="utf-8")
    assert 'base_context.get("app_build_plan")' in task_batches
    assert "async def create_build_record" in build_store
    assert "accept_build_record" in studio


def test_revision_and_publication_identity_are_git_independent() -> None:
    source_control_fields = {
        "repo_url",
        "repository_id",
        "github_repository_id",
        "branch",
        "commit_sha",
        "pull_request",
        "git_provider",
        "ide",
        "editor",
    }
    assert source_control_fields.isdisjoint(ArtifactRevision.model_fields)
    assert source_control_fields.isdisjoint(ApplicationPublication.model_fields)

    for relative in (
        "mozaiksai/core/semantics/artifact_revision.py",
        "mozaiksai/core/artifacts/revision_store.py",
        "mozaiksai/core/artifacts/content_store.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8").casefold()
        assert "import git" not in source
        assert "from git" not in source
        assert "github" not in source
