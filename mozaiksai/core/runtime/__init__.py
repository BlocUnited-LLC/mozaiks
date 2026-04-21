from __future__ import annotations

from .app.definition import AppDefinition, AppFeatureFlags, OperationRef, ExecutionMode, WorkflowRef, PageRef
from .app.loader import AppLoader, AppLoadError, AppLoadResult
from .app.module_loader import OperationLoader, OperationLoadError, LoadedOperation, OperationDefinition, ActionDef
from .composition.extensions import (
    mount_declared_routers,
    start_declared_services,
    stop_services,
    get_workflow_lifecycle_hooks,
    get_workflow_api_router,
)
from .composition.platform_hooks import get_platform_hooks, PlatformHookRegistry
from .composition.executor_registry import Executor, ExecutorRegistry, ExecutorType
from .composition.module_context import OperationContext
from .composition.module_executor import OperationExecutor, OperationRequest, OperationResult

__all__ = [
    "AppDefinition",
    "AppFeatureFlags",
    "AppLoadError",
    "AppLoadResult",
    "AppLoader",
    "ExecutionMode",
    "WorkflowRef",
    "OperationRef",
    "PageRef",
    "OperationLoader",
    "OperationLoadError",
    "LoadedOperation",
    "OperationDefinition",
    "ActionDef",
    "mount_declared_routers",
    "start_declared_services",
    "stop_services",
    "get_workflow_lifecycle_hooks",
    "get_workflow_api_router",
    "get_platform_hooks",
    "PlatformHookRegistry",
    "Executor",
    "ExecutorRegistry",
    "ExecutorType",
    "OperationContext",
    "OperationExecutor",
    "OperationRequest",
    "OperationResult",
]
