from __future__ import annotations

from typing import Any

from mozaiks.core.registries.decorators import plugin


@plugin("noop", description="No-op plugin placeholder to validate plugin wiring.")
async def noop_plugin(payload: dict[str, Any]) -> dict[str, Any]:
    return payload
