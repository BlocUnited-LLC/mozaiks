from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


PluginHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Plugin name must be non-empty")
    return normalized


@dataclass(frozen=True)
class PluginDefinition:
    name: str
    handler: PluginHandler
    description: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginSpec:
    name: str
    description: str
    metadata: dict[str, Any]


@dataclass
class InMemoryPluginRegistry:
    _items: dict[str, PluginDefinition] = field(default_factory=dict)

    def register(self, definition: PluginDefinition, *, overwrite: bool = False) -> None:
        key = _normalize_name(definition.name)
        if key in self._items and not overwrite:
            raise ValueError(f"Plugin '{key}' is already registered")
        self._items[key] = PluginDefinition(
            name=key,
            handler=definition.handler,
            description=definition.description,
            metadata=dict(definition.metadata),
        )

    def get(self, name: str) -> PluginDefinition | None:
        key = _normalize_name(name)
        return self._items.get(key)

    def list_specs(self) -> list[PluginSpec]:
        return [
            PluginSpec(name=name, description=item.description, metadata=dict(item.metadata))
            for name, item in sorted(self._items.items(), key=lambda item: item[0])
        ]
