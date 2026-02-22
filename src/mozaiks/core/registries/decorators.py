from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from mozaiks.core.registries.plugins import InMemoryPluginRegistry, PluginDefinition
from mozaiks.core.registries.workflows import (
    DEFAULT_WORKFLOW_VERSION,
    InMemoryWorkflowRegistry,
    WorkflowDefinition,
)


WorkflowHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
PluginHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class WorkflowRegistration:
    name: str
    version: str
    handler: WorkflowHandler
    description: str
    tags: tuple[str, ...]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PluginRegistration:
    name: str
    handler: PluginHandler
    description: str
    metadata: dict[str, Any]


_REGISTERED_WORKFLOWS: list[WorkflowRegistration] = []
_REGISTERED_PLUGINS: list[PluginRegistration] = []


def workflow(
    name: str,
    *,
    version: str = DEFAULT_WORKFLOW_VERSION,
    description: str = "",
    tags: tuple[str, ...] = (),
    metadata: dict[str, Any] | None = None,
) -> Callable[[WorkflowHandler], WorkflowHandler]:
    def _decorator(func: WorkflowHandler) -> WorkflowHandler:
        _REGISTERED_WORKFLOWS.append(
            WorkflowRegistration(
                name=name,
                version=version,
                handler=func,
                description=description,
                tags=tags,
                metadata=metadata or {},
            )
        )
        return func

    return _decorator


def plugin(
    name: str,
    *,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> Callable[[PluginHandler], PluginHandler]:
    def _decorator(func: PluginHandler) -> PluginHandler:
        _REGISTERED_PLUGINS.append(
            PluginRegistration(
                name=name,
                handler=func,
                description=description,
                metadata=metadata or {},
            )
        )
        return func

    return _decorator


def install_workflows(registry: InMemoryWorkflowRegistry) -> None:
    for item in _REGISTERED_WORKFLOWS:
        combined_metadata = dict(item.metadata)
        registry.register(
            WorkflowDefinition(
                name=item.name,
                version=item.version,
                handler=item.handler,
                description=item.description,
                tags=item.tags,
                metadata=combined_metadata,
            ),
            overwrite=True,
        )


def install_plugins(registry: InMemoryPluginRegistry) -> None:
    for item in _REGISTERED_PLUGINS:
        registry.register(
            PluginDefinition(
                name=item.name,
                handler=item.handler,
                description=item.description,
                metadata=dict(item.metadata),
            ),
            overwrite=True,
        )
