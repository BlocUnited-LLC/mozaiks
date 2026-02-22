from __future__ import annotations

from typing import Any

from mozaiks.core.tools.catalog import ToolCatalog, ToolSpec


async def _echo_tool(payload: dict[str, Any]) -> dict[str, Any]:
    return {"echo": payload}


def register_builtin_tools(catalog: ToolCatalog) -> None:
    catalog.register(
        ToolSpec(
            name="echo",
            handler=_echo_tool,
            description="Echoes the input payload for smoke testing.",
            metadata={"kind": "python-callable", "version": "1.0.0"},
        ),
        overwrite=True,
    )
