from __future__ import annotations

"""Executor protocol and registry.

Executors are the active handlers for a given runtime surface:
  - WorkflowExecutor — handles AI workflow execution (AG2 runtime, already exists)
  - ModuleExecutor   — handles CRUD module execution (Phase 2)

The ExecutorRegistry is the runtime's single source of truth for which
executors are available. Phase 1 only registers WorkflowExecutor.
Phase 2 adds ModuleExecutor when modules are discovered from
modules/*/module.yaml.

The Executor protocol defines the minimal interface both implementations
must satisfy so the runtime's request dispatcher can treat them uniformly.
"""

from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("executor_registry")


class ExecutorType(StrEnum):
    """Identifies which kind of executor is registered."""
    WORKFLOW = "workflow"  # AI workflow execution
    MODULE = "module"      # CRUD module execution (Phase 2)


@runtime_checkable
class Executor(Protocol):
    """Minimal interface all executors must implement.

    The runtime dispatcher calls `execute()` after resolving which executor
    owns the incoming request. Each executor handles its own request format.
    """

    executor_type: ExecutorType

    async def execute(self, request: Any, context: Any) -> Any:
        """Execute a request within the given context.

        Args:
            request: Executor-specific request object.
            context: Runtime context (app_id, user_id, correlation_id, etc.)

        Returns:
            Executor-specific response.
        """
        ...

    async def health(self) -> dict[str, Any]:
        """Return health status dict. Used by /health endpoint."""
        ...


class ExecutorRegistry:
    """Registry mapping ExecutorType → Executor instance.

    Phase 1: only WorkflowExecutor is registered by the runtime host.
    Phase 2: ModuleExecutor is registered when app declares modules.

    Example (Phase 2 usage in mozaiksai.hosts.platform):
        registry = ExecutorRegistry()
        registry.register(workflow_executor)
        registry.register(module_executor)

        # Dispatcher resolves type from request and calls:
        executor = registry.get(ExecutorType.WORKFLOW)
        result = await executor.execute(request, ctx)
    """

    def __init__(self) -> None:
        self._executors: dict[ExecutorType, Executor] = {}

    def register(self, executor: Executor) -> None:
        """Register an executor. Overwrites any existing entry for the same type."""
        self._executors[executor.executor_type] = executor
        logger.debug("EXECUTOR_REGISTERED: type=%s", executor.executor_type.value)

    def get(self, executor_type: ExecutorType) -> Executor | None:
        """Return executor for the given type, or None if not registered."""
        return self._executors.get(executor_type)

    def has(self, executor_type: ExecutorType) -> bool:
        return executor_type in self._executors

    @property
    def workflow_executor(self) -> Executor | None:
        return self._executors.get(ExecutorType.WORKFLOW)

    @property
    def module_executor(self) -> Executor | None:
        return self._executors.get(ExecutorType.MODULE)

    def registered_types(self) -> list[ExecutorType]:
        return list(self._executors.keys())

    def summary(self) -> dict[str, Any]:
        return {t.value: type(e).__name__ for t, e in self._executors.items()}
