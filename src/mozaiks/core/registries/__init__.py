from mozaiks.core.registries.decorators import (
    install_plugins,
    install_workflows,
    plugin,
    workflow,
)
from mozaiks.core.registries.plugins import InMemoryPluginRegistry, PluginSpec
from mozaiks.core.registries.workflows import InMemoryWorkflowRegistry, WorkflowSpec

__all__ = [
    "InMemoryWorkflowRegistry",
    "WorkflowSpec",
    "InMemoryPluginRegistry",
    "PluginSpec",
    "workflow",
    "plugin",
    "install_workflows",
    "install_plugins",
]
