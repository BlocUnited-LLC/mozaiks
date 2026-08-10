from __future__ import annotations

from .app.definition import (
    AppDefinition,
    AppFeatureFlags,
    ExecutionMode,
    ModuleRef,
    PageRef,
    WorkflowRef,
)
from .app.loader import AppLoader, AppLoadError, AppLoadResult
from .app.module_loader import (
    ActionDef,
    LoadedModule,
    ModuleDefinition,
    ModuleLoader,
    ModuleLoadError,
)
from .composition.executor_registry import Executor, ExecutorRegistry, ExecutorType
from .composition.extensions import (
    get_workflow_lifecycle_hooks,
    mount_declared_routers,
    start_declared_services,
    stop_services,
)
from .composition.module_authority import (
    ModuleDispatchAudit,
    ModuleDispatchAuthority,
    ModuleDispatchProvenance,
    ModuleEntitlementCheck,
    ModuleExecutionPolicyDecision,
    ModuleExecutionPolicyInput,
    ModulePermissionCheck,
)
from .composition.module_context import ModuleContext
from .composition.module_executor import ModuleExecutor, ModuleRequest, ModuleResult
from .composition.platform_hooks import PlatformHookRegistry, get_platform_hooks
from .readiness import (
    EnvReader,
    EnvValidator,
    ReadinessCheck,
    checks_from_readiness_requirements,
    env_present,
    evaluate_readiness_checks,
    evaluate_readiness_requirements,
    non_false_env,
    readiness_score,
    summarize_readiness_categories,
    truthy_env,
)

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
    "ModuleDispatchAudit",
    "ModuleDispatchAuthority",
    "ModuleDispatchProvenance",
    "ModuleEntitlementCheck",
    "ModuleExecutionPolicyDecision",
    "ModuleExecutionPolicyInput",
    "ModulePermissionCheck",
    "ModuleExecutor",
    "ModuleRequest",
    "ModuleResult",
    "EnvReader",
    "EnvValidator",
    "ReadinessCheck",
    "checks_from_readiness_requirements",
    "env_present",
    "evaluate_readiness_checks",
    "evaluate_readiness_requirements",
    "non_false_env",
    "readiness_score",
    "summarize_readiness_categories",
    "truthy_env",
]
