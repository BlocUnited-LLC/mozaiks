"""Shared test fixtures and helpers.

Tests that require an active app workspace (PLATFORM_PATH or
MOZAIKS_APP_WORKSPACE_PATH) are skipped automatically when neither env var
is set.  This allows the framework CI to run without a bundled product app.

To run workspace-dependent tests locally:

    MOZAIKS_APP_WORKSPACE_PATH=/path/to/mozaiks-app pytest
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest


def _resolve_active_app_root() -> Path | None:
    """Return the active app root from env vars, or None if not configured."""
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
