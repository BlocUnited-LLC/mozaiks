"""Shared test fixtures and helpers.

Tests that require an active app workspace resolve it explicitly:
``PLATFORM_PATH`` or ``MOZAIKS_APP_WORKSPACE_PATH`` win when set, and the
repo's first-party ``factory_app/app`` bundle is the deterministic fallback in
a repo checkout. The fallback keeps the resolution order-independent — it must
never depend on whether an earlier test happened to import a host module.
Only when neither an env var nor the repo bundle resolves (framework CI
without a bundled app) are workspace-dependent tests skipped.

To run workspace-dependent tests against another workspace locally:

    MOZAIKS_APP_WORKSPACE_PATH=/path/to/mozaiks-app pytest
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _repo_factory_app_bundle() -> Path:
    """The first-party factory app bundle, honoring MOZAIKS_FACTORY_APP_PATH.

    The host resolves the factory root through ``mozaiksai.resources``, which
    consults that env var; matching it here keeps tests and host agreeing about
    the active workspace in relocated or installed-package checkouts.
    """
    override = os.environ.get("MOZAIKS_FACTORY_APP_PATH", "").strip()
    if override:
        return (Path(override) / "app").resolve()
    return (Path(__file__).resolve().parents[1] / "factory_app" / "app").resolve()


def _resolve_active_app_root() -> Path | None:
    """Return the active app root: env vars first, then the repo factory bundle."""
    platform_path = os.environ.get("PLATFORM_PATH", "").strip()
    if platform_path:
        candidate = Path(platform_path)
        if (candidate / "app.json").exists():
            return candidate.resolve()
        nested = candidate / "app"
        if (nested / "app.json").exists():
            return nested.resolve()
        return candidate.resolve()

    workspace_path = os.environ.get("MOZAIKS_APP_WORKSPACE_PATH", "").strip()
    if workspace_path:
        candidate = Path(workspace_path)
        nested = candidate / "app"
        if (nested / "app.json").exists():
            return nested.resolve()
        if (candidate / "app.json").exists():
            return candidate.resolve()

    factory_bundle = _repo_factory_app_bundle()
    if (factory_bundle / "app.json").exists():
        return factory_bundle

    return None


def active_app_root() -> Path:
    """Return the active app root. Skips the test if not configured."""
    root = _resolve_active_app_root()
    if root is None:
        pytest.skip(
            "No active app workspace configured. "
            "Set MOZAIKS_APP_WORKSPACE_PATH or PLATFORM_PATH to run this test."
        )
    return root


@pytest.fixture
def app_root() -> Path:
    """Pytest fixture: active app workspace root. Skips if not configured."""
    return active_app_root()


@pytest.fixture(autouse=True)
def _isolate_execution_authority_state(monkeypatch: pytest.MonkeyPatch):
    """Reset process-global chat-lock and workflow-admission state per test.

    The lock module keeps process-global state (configured mode, held local
    locks, the lease registry). A test that runs host startup pins `required`
    mode, and an abandoned background task can leave a local lock held for a
    reused chat id — either would silently change the behavior of every later
    test. Entering each test with clean lock state keeps tests
    order-independent.
    """
    from mozaiksai.core.runtime.execution_admission import reset_local_workflow_admission
    from mozaiksai.core.runtime.persistence.distributed_lock import reset_chat_lock_state
    from mozaiksai.core.workflow.queue import reset_workflow_admission_state

    reset_chat_lock_state()
    reset_workflow_admission_state()
    reset_local_workflow_admission()
    # Tests and embedded transports are explicitly single-process unless a
    # focused admission test opts into required mode. This prevents a
    # developer .env MONGO_URI from silently changing unrelated test meaning.
    monkeypatch.setenv("MOZAIKS_WORKFLOW_ADMISSION_MODE", "local")
    yield
    reset_chat_lock_state()
    reset_workflow_admission_state()
    reset_local_workflow_admission()

