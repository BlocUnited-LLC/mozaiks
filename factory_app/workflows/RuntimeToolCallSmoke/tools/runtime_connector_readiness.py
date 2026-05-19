from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.generator_support.connector_request import (
    collect_missing_connector_needs,
)


async def collect_runtime_connector_readiness(context_variables: Any = None) -> dict[str, Any]:
    """Exercise shared connector readiness through the runtime UI-tool path."""

    return await collect_missing_connector_needs(
        context_variables=context_variables,
        required_at=["build_time", "validation_time", "runtime"],
        prompt=True,
    )


__all__ = ["collect_runtime_connector_readiness"]
