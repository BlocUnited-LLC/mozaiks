from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from mozaiksai.control_plane import (
    SourceImportRequest,
    public_source_import_result,
    resolve_source_import,
    source_import_scan_policy,
)
from mozaiksai.core.app_context import default_context_graph_scan_policy, select_source_file_map


def test_resolve_local_workspace_selects_monorepo_subpath_and_ignores(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    selected = workspace / "apps" / "web"
    selected.mkdir(parents=True)

    result = resolve_source_import(
        app_id="app_1",
        request=SourceImportRequest(
            source_kind="local_workspace",
            workspace_root=str(workspace),
            monorepo_path="apps/web",
            ignored_paths=["docs/archive", "../unsafe"],
        ),
    )

    assert result.source_kind == "local_workspace"
    assert result.workspace_root == workspace.resolve().as_posix()
    assert result.selected_root == selected.resolve().as_posix()
    assert result.monorepo_path == "apps/web"
    assert result.ignored_paths == ["docs/archive"]


def test_source_import_scan_policy_excludes_import_ignored_paths() -> None:
    policy = default_context_graph_scan_policy(
        source_import_scan_policy({"max_files": 50}, ignored_paths=["docs/archive", "fixtures/generated"])
    )

    selected = select_source_file_map(
        {
            "src/App.tsx": "export default function App() { return null }",
            "docs/archive/old.md": "old",
            "fixtures/generated/out.ts": "export const x = 1",
        },
        policy=policy,
    )

    assert sorted(selected.file_map) == ["src/App.tsx"]
    assert selected.health["skipped"]["excluded_path"] == 2
    assert "context_graph_excluded_paths_skipped:2" in selected.warnings


def test_resolve_git_repository_rejects_embedded_credentials(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="embedded credentials"):
        resolve_source_import(
            app_id="app_1",
            request=SourceImportRequest(
                source_kind="git_repository",
                repo_url="https://token@example.com/org/repo.git",
            ),
            import_root=tmp_path,
        )


def test_resolve_git_repository_shapes_clone_command(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def fake_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[:2] == ["git", "clone"]:
            Path(args[-1]).mkdir(parents=True)
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="abc123\n", stderr="")

    result = resolve_source_import(
        app_id="app_1",
        request=SourceImportRequest(
            source_kind="git_repository",
            repo_url="https://github.com/org/repo.git",
            branch="main",
        ),
        import_root=tmp_path,
        run_git_command=fake_git,
    )

    assert commands[0][:5] == ["git", "clone", "--depth", "1", "--branch"]
    assert commands[0][5] == "main"
    assert commands[0][6] == "https://github.com/org/repo.git"
    assert commands[1][0:2] == ["git", "-C"]
    assert result.commit_sha == "abc123"
    assert public_source_import_result(result)["workspace_root_present"] is True
