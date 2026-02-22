from __future__ import annotations

from mozaiks.core.registries.decorators import install_plugins, install_workflows
from mozaiks.core.registries.plugins import InMemoryPluginRegistry
from mozaiks.core.registries.workflows import InMemoryWorkflowRegistry
from mozaiks.core.tools import ToolCatalog, register_builtin_tools


def register_runtime_components(
    *,
    workflow_registry: InMemoryWorkflowRegistry,
    plugin_registry: InMemoryPluginRegistry,
    tool_catalog: ToolCatalog,
) -> None:
    # Explicit registration imports keep startup deterministic and avoid filesystem discovery.
    from mozaiks.core.plugins import builtin as _plugins_builtin  # noqa: F401
    from mozaiks.core.workflows import builtin as _workflows_builtin  # noqa: F401

    install_workflows(workflow_registry)
    install_plugins(plugin_registry)
    register_builtin_tools(tool_catalog)
