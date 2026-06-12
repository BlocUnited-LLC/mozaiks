"""Hydrate AppGenerator workflow integration context from workflow_bundle artifacts."""

from __future__ import annotations

from typing import Any

from factory_app.workflows._shared.workflow_integration import (
    hydrate_workflow_integration_context_from_latest_artifact,
)


async def hydrate_workflow_integration_context(context_variables: Any = None) -> dict[str, Any]:
    return await hydrate_workflow_integration_context_from_latest_artifact(context_variables)


__all__ = ["hydrate_workflow_integration_context"]
