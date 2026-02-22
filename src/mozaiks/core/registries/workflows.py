from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


WorkflowHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
DEFAULT_WORKFLOW_VERSION = "1.0.0"


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Workflow name must be non-empty")
    return normalized


def _normalize_version(version: str | None) -> str:
    candidate = (version or DEFAULT_WORKFLOW_VERSION).strip()
    return candidate or DEFAULT_WORKFLOW_VERSION


def _version_key(version: str) -> tuple[str, ...]:
    parts: list[str] = []
    for part in version.split("."):
        if part.isdigit():
            parts.append(f"{int(part):08d}")
        else:
            parts.append(f"z{part}")
    return tuple(parts)


@dataclass(frozen=True)
class WorkflowDefinition:
    name: str
    version: str
    handler: WorkflowHandler
    description: str = ""
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkflowSpec:
    name: str
    version: str
    description: str
    tags: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass
class InMemoryWorkflowRegistry:
    _items: dict[tuple[str, str], WorkflowDefinition] = field(default_factory=dict)

    def register(self, definition: WorkflowDefinition, *, overwrite: bool = False) -> None:
        key = (_normalize_name(definition.name), _normalize_version(definition.version))
        if key in self._items and not overwrite:
            raise ValueError(f"Workflow '{key[0]}' version '{key[1]}' is already registered")
        self._items[key] = WorkflowDefinition(
            name=key[0],
            version=key[1],
            handler=definition.handler,
            description=definition.description,
            tags=tuple(definition.tags),
            metadata=dict(definition.metadata),
        )

    def get(self, name: str) -> WorkflowDefinition | None:
        normalized = _normalize_name(name)
        matches = [item for (workflow_name, _), item in self._items.items() if workflow_name == normalized]
        if not matches:
            return None
        return sorted(matches, key=lambda item: _version_key(item.version), reverse=True)[0]

    def get_version(self, name: str, version: str) -> WorkflowDefinition | None:
        key = (_normalize_name(name), _normalize_version(version))
        return self._items.get(key)

    def list_specs(self) -> list[WorkflowSpec]:
        specs: list[WorkflowSpec] = []
        for (name, version), definition in sorted(
            self._items.items(),
            key=lambda item: (item[0][0], _version_key(item[0][1])),
        ):
            specs.append(
                WorkflowSpec(
                    name=name,
                    version=version,
                    description=definition.description,
                    tags=definition.tags,
                    metadata=dict(definition.metadata),
                )
            )
        return specs
