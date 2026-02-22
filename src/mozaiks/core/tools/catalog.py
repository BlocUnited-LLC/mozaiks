from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    handler: ToolHandler
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCatalog:
    _items: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec, *, overwrite: bool = False) -> None:
        key = spec.name.strip()
        if not key:
            raise ValueError("Tool name must be non-empty")
        if key in self._items and not overwrite:
            raise ValueError(f"Tool '{key}' is already registered")
        self._items[key] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._items.get(name.strip())

    def list(self) -> list[ToolSpec]:
        return [self._items[key] for key in sorted(self._items.keys())]

    def list_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "metadata": dict(spec.metadata),
            }
            for spec in self.list()
        ]

    async def execute(self, name: str, payload: dict[str, Any]) -> dict[str, Any]:
        spec = self.get(name)
        if spec is None:
            raise KeyError(f"Unknown tool '{name}'")
        output = spec.handler(payload)
        if inspect.isawaitable(output):
            result = await output
        else:
            result = output
        if not isinstance(result, dict):
            return {"result": result}
        return result
