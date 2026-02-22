from __future__ import annotations

from typing import Any

from mozaiks.core.registries.decorators import workflow


@workflow(
    "echo",
    version="1.0.0",
    description="Deterministic baseline workflow for runtime validation.",
    tags=("core", "sample"),
)
async def echo_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    return {"echo": payload}
