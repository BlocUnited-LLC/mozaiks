"""Verify the app and workflow inputs in order before plan hydration."""

from __future__ import annotations

from typing import Any

from factory_app.workflows.AppGenerator.tools.hydrate_app_revision_context import (
    hydrate_app_revision_context,
)


async def hydrate_app_build_inputs(context_variables: Any = None) -> dict[str, Any]:
    return await hydrate_app_revision_context(context_variables, load_workflows=True)


__all__ = ["hydrate_app_build_inputs"]
