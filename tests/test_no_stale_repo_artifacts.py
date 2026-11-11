from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

RETIRED_LAUNCHERS = {
    "scripts/startapp.ps1",
    "scripts/cleanse.ps1",
    "start-dev.ps1",
}

FORBIDDEN_EXACT_TRACKED_PATHS = {
    ".pytest_live_smoke.err.txt",
    "package-lock.json",
    "runtime_tool_call_debug.txt",
    "test_chat.html",
    "valueengine_debug.txt",
}

FORBIDDEN_TRACKED_PREFIXES = (
    ".tmp/",
    "generated/",
    "generated_artifacts/",
    "logs/agent_outputs/",
    "logs/logs/",
    "logs/workflow_converter/",
    "site/",
    "tmp/",
    "web_shell/playwright/tmp-generated",
)

FORBIDDEN_TRACKED_PARTS = {
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
}

FORBIDDEN_TRACKED_PATTERNS = (
    "*.log",
    "*.pyc",
    "*.pyo",
    "*_debug.txt",
    ".pytest_live_smoke*.txt",
)


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def _stale_artifact_reason(path: str) -> str | None:
    if path in RETIRED_LAUNCHERS:
        return "retired dev launcher"
    if path in FORBIDDEN_EXACT_TRACKED_PATHS:
        return "one-off debug or root-local artifact"
    if path.startswith(FORBIDDEN_TRACKED_PREFIXES):
        return "generated output directory"
    if any(part in FORBIDDEN_TRACKED_PARTS for part in path.split("/")):
        return "cache or dependency directory"
    if path.startswith(("chat-ui/dist/", "web_shell/dist/")):
        return "frontend build output"
    if any(fnmatch.fnmatch(path, pattern) for pattern in FORBIDDEN_TRACKED_PATTERNS):
        return "generated log/cache artifact"
    return None


def test_local_generated_artifacts_are_not_tracked() -> None:
    hits = [
        f"{path}: {_stale_artifact_reason(path)}"
        for path in _tracked_paths()
        if _stale_artifact_reason(path)
    ]

    assert not hits, "Stale local artifacts are tracked:\n" + "\n".join(hits[:200])


@pytest.mark.parametrize(
    "path",
    [
        *sorted(RETIRED_LAUNCHERS),
        "generated/app_intelligence/sample_app/20260913035003/artifact.zip",
        "generated/apps/example/build/app/app.json",
        "generated/workflows/example/build/Example/orchestrator.yaml",
        "generated_artifacts/workspace_app_intelligence_acceptance/app_context_graph-app_context_graph/artifact.zip",
        "generated_artifacts/workspace_app_intelligence_acceptance/source_context_bundle-source_context_bundle/artifact.zip",
        "generated_artifacts/example/new-run/artifact.zip",
    ],
)
def test_retired_launchers_and_generated_output_are_rejected(path: str) -> None:
    assert _stale_artifact_reason(path) is not None


@pytest.mark.parametrize(
    "path",
    [
        "scripts/run-studio.ps1",
        "scripts/run-backend.ps1",
        "scripts/run-frontend.ps1",
        "scripts/clean-runtime-artifacts.ps1",
        "scripts/clear_collections.py",
        "start-app.ps1",
        "tests/fixtures/generated/app.json",
        "tests/fixtures/generated_artifacts/artifact.zip",
        "web_shell/playwright/fixtures/generated-app/app/app.json",
        "docs/reviews/retired-launchers-and-generated-snapshots.md",
    ],
)
def test_supported_source_and_intentional_fixtures_are_allowed(path: str) -> None:
    assert _stale_artifact_reason(path) is None


def test_generated_output_roots_are_gitignored() -> None:
    ignored = {
        "generated/app_intelligence/sample_app/new-run/artifact.zip",
        "generated/apps/example/build/app/app.json",
        "generated/workflows/example/build/Example/orchestrator.yaml",
        "generated_artifacts/workspace_app_intelligence_acceptance/new-run/artifact.zip",
    }
    source = {
        "tests/fixtures/generated/app.json",
        "tests/fixtures/generated_artifacts/artifact.zip",
        "web_shell/playwright/fixtures/generated-app/app/app.json",
        "scripts/run-studio.ps1",
        "start-app.ps1",
    }
    result = subprocess.run(
        ["git", "-c", "core.excludesFile=", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=ROOT,
        input=("\0".join(sorted(ignored | source)) + "\0").encode(),
        check=True,
        capture_output=True,
    )
    assert set(result.stdout.decode().rstrip("\0").split("\0")) == ignored


@pytest.mark.parametrize(
    "path",
    [
        "scripts/run-studio.ps1",
        "scripts/run-backend.ps1",
        "scripts/run-frontend.ps1",
        "scripts/clean-runtime-artifacts.ps1",
        "start-app.ps1",
    ],
)
def test_supported_launchers_do_not_depend_on_retired_chain(path: str) -> None:
    source = (ROOT / path).read_text(encoding="utf-8").lower()
    for retired in RETIRED_LAUNCHERS:
        assert Path(retired).name.lower() not in source
