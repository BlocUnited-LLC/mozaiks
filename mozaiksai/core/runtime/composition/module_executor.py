from __future__ import annotations

"""ModuleExecutor — dispatches module action requests to loaded module handlers.

Implements the Executor protocol defined in executor_registry.py.

Request flow:
    1. Caller builds a ModuleRequest(module="contacts", action="list", params={...}, ctx=...)
    2. ModuleExecutor.execute() resolves the module by name from the registry
    3. ModuleExecutor looks up the action method on the handler instance
    4. ModuleExecutor calls handler.{action}(ctx, **params)
    5. Returns ModuleResult(success=True, data=result)

Module handlers are plain Python classes. Actions are methods named after
the action string. The class is registered via ModuleExecutor.register().

Example handler:
    class ContactsModule:
        async def list(self, ctx: ModuleContext, *, limit: int = 20) -> list:
            ...
        async def create(self, ctx: ModuleContext, *, name: str, email: str) -> dict:
            ...

Module handlers must NOT import from mozaiksai.core.workflow or any AI layer.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mozaiksai.core.runtime.composition.executor_registry import Executor, ExecutorType
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("module_executor")


# ---------------------------------------------------------------------------
# Request / Result types
# ---------------------------------------------------------------------------

@dataclass
class ModuleRequest:
    """A request to execute a module action.

    Fields:
        module:         Name of the module to invoke, e.g. "contacts"
        action:         Action method name on the handler, e.g. "list", "create"
        params:         Keyword arguments forwarded to the action method
        app_id:         Required — scope for multi-tenancy
        user_id:        Optional authenticated user
        tenant_id:      Optional tenant override
        auth_token:     Optional JWT forwarded to external API calls
        correlation_id: Optional tracing ID
    """
    module: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)

    # Identity (injected by runtime before dispatch)
    app_id: str = ""
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    auth_token: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class ModuleResult:
    """Result of a module action execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# ModuleExecutor
# ---------------------------------------------------------------------------

class ModuleExecutor:
    """Dispatches ModuleRequests to registered module handler instances.

    Implements the Executor protocol so it can be registered in ExecutorRegistry.

    Usage (in platform_app.py / AppLoader wiring):
        executor = ModuleExecutor()
        executor.register("contacts", ContactsModule())
        executor.register("tasks", TasksModule())

        registry = ExecutorRegistry()
        registry.register(executor)
    """

    executor_type: ExecutorType = ExecutorType.MODULE

    def __init__(self) -> None:
        self._modules: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, handler: Any) -> None:
        """Register a module handler instance under a name.

        Args:
            name:    Module name as declared in module.yaml and ModuleRequest.module
            handler: Instantiated module handler object
        """
        self._modules[name] = handler
        logger.info(f"MODULE_REGISTERED: {name} ({type(handler).__name__})")

    def registered_modules(self) -> List[str]:
        return list(self._modules.keys())

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    async def execute(self, request: ModuleRequest, context: Optional[ModuleContext] = None) -> ModuleResult:
        """Dispatch a ModuleRequest to the appropriate handler action.

        Builds a ModuleContext from the request if one is not supplied.
        """
        handler = self._modules.get(request.module)
        if handler is None:
            return ModuleResult(
                success=False,
                error=f"Module not found: {request.module!r}",
                error_code="MODULE_NOT_FOUND",
            )

        action_fn = getattr(handler, request.action, None)
        if action_fn is None:
            return ModuleResult(
                success=False,
                error=f"Action {request.action!r} not found on module {request.module!r}",
                error_code="ACTION_NOT_FOUND",
            )

        if context is None:
            context = ModuleContext(
                app_id=request.app_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                auth_token=request.auth_token,
                correlation_id=request.correlation_id,
            )

        try:
            if inspect.iscoroutinefunction(action_fn):
                result = await action_fn(context, **request.params)
            else:
                result = action_fn(context, **request.params)

            logger.debug(
                f"MODULE_ACTION_OK: module={request.module} action={request.action} "
                f"app_id={request.app_id}"
            )
            return ModuleResult(success=True, data=result)

        except TypeError as exc:
            logger.warning(
                f"MODULE_ACTION_BAD_PARAMS: module={request.module} action={request.action} error={exc}"
            )
            return ModuleResult(
                success=False,
                error=f"Invalid parameters for {request.module}.{request.action}: {exc}",
                error_code="INVALID_PARAMS",
            )
        except Exception as exc:
            logger.error(
                f"MODULE_ACTION_ERROR: module={request.module} action={request.action} error={exc}",
                exc_info=True,
            )
            return ModuleResult(
                success=False,
                error=str(exc),
                error_code="EXECUTION_ERROR",
            )

    async def health(self) -> Dict[str, Any]:
        return {
            "executor": "module",
            "modules": self.registered_modules(),
            "count": len(self._modules),
        }

    def can_handle(self, target: str) -> bool:
        return target in self._modules
