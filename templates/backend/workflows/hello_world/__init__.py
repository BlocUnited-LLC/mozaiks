from __future__ import annotations

from typing import Any

from mozaiks.core.registries.decorators import workflow


@workflow(
    "hello_world",
    description="Starter workflow — returns a greeting. Rename this folder and the name field to match your use case.",
)
async def hello_world_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    name = payload.get("name", "world")
    return {
        "message": f"Hello, {name}!",
        "workflow": "hello_world",
    }
