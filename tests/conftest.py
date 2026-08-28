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

_REPO_FACTORY_APP_BUNDLE = Path(__file__).resolve().parents[1] / "factory_app" / "app"


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

    if (_REPO_FACTORY_APP_BUNDLE / "app.json").exists():
        return _REPO_FACTORY_APP_BUNDLE.resolve()

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

