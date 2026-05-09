from __future__ import annotations

from .app.definition import AppDefinition, AppFeatureFlags, ModuleRef, ExecutionMode, WorkflowRef, PageRef
from .app.loader import AppLoader, AppLoadError, AppLoadResult
from .app.module_loader import ModuleLoader, ModuleLoadError, LoadedModule, ModuleDefinition, ActionDef
from .composition.extensions import (
    mount_declared_routers,
    start_declared_services,
    stop_services,
    get_workflow_lifecycle_hooks,
)
from .composition.platform_hooks import get_platform_hooks, PlatformHookRegistry
from .composition.executor_registry import Executor, ExecutorRegistry, ExecutorType
from .composition.module_context import ModuleContext
from .composition.module_executor import ModuleExecutor, ModuleRequest, ModuleResult

__all__ = [
    "AppDefinition",
    "AppFeatureFlags",
    "AppLoadError",
    "AppLoadResult",
    "AppLoader",
    "ExecutionMode",
    "WorkflowRef",
    "ModuleRef",
    "PageRef",
    "ModuleLoader",
    "ModuleLoadError",
    "LoadedModule",
    "ModuleDefinition",
    "ActionDef",
    "mount_declared_routers",
    "start_declared_services",
    "stop_services",
    "get_workflow_lifecycle_hooks",
    "get_platform_hooks",
    "PlatformHookRegistry",
    "Executor",
    "ExecutorRegistry",
    "ExecutorType",
    "ModuleContext",
    "ModuleExecutor",
    "ModuleRequest",
    "ModuleResult",
]
