from __future__ import annotations

"""OperationExecutor — dispatches operation action requests to loaded operation handlers.

Implements the Executor protocol defined in executor_registry.py.

Request flow:
    1. Caller builds an OperationRequest(operation="contacts", action="list", params={...}, ctx=...)
    2. OperationExecutor.execute() resolves the operation by name from the registry
    3. OperationExecutor looks up the action method on the handler instance
    4. OperationExecutor calls handler.{action}(ctx, **params)
    5. Returns OperationResult(success=True, data=result)

Operation handlers are plain Python classes. Actions are methods named after
the action string. The class is registered via OperationExecutor.register().

Example handler:
    class ContactsOperation:
        async def list(self, ctx: OperationContext, *, limit: int = 20) -> list:
            ...
        async def create(self, ctx: OperationContext, *, name: str, email: str) -> dict:
            ...

Operation handlers must NOT import from mozaiksai.core.workflow or any AI layer.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from mozaiksai.core.runtime.composition.executor_registry import Executor, ExecutorType
from mozaiksai.core.runtime.composition.module_context import OperationContext
from logs.logging_config import get_workflow_logger

logger = get_workflow_logger("operation_executor")


# ---------------------------------------------------------------------------
# Request / Result types
# ---------------------------------------------------------------------------

@dataclass
class OperationRequest:
    """A request to execute an operation action.

    Fields:
        operation:  Name of the operation to invoke, e.g. "contacts"
        action:     Action method name on the handler, e.g. "list", "create"
        params:     Keyword arguments forwarded to the action method
        app_id:     Required — scope for multi-tenancy
        user_id:    Optional authenticated user
        tenant_id:  Optional tenant override
        auth_token: Optional JWT forwarded to external API calls
        correlation_id: Optional tracing ID
    """
    operation: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)

    # Identity (injected by runtime before dispatch)
    app_id: str = ""
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    auth_token: Optional[str] = None
    correlation_id: Optional[str] = None


@dataclass
class OperationResult:
    """Result of an operation action execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# OperationExecutor
# ---------------------------------------------------------------------------

class OperationExecutor:
    """Dispatches OperationRequests to registered operation handler instances.

    Implements the Executor protocol so it can be registered in ExecutorRegistry.

    Usage (in shared_app.py / AppLoader wiring):
        executor = OperationExecutor()
        executor.register("contacts", ContactsOperation())
        executor.register("tasks", TasksOperation())

        registry = ExecutorRegistry()
        registry.register(executor)
    """

    executor_type: ExecutorType = ExecutorType.OPERATION

    def __init__(self) -> None:
        self._operations: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, name: str, handler: Any) -> None:
        """Register an operation handler instance under a name.

        Args:
            name:    Operation name as declared in operation.yaml and OperationRequest.operation
            handler: Instantiated operation handler object
        """
        self._operations[name] = handler
        logger.info(f"OPERATION_REGISTERED: {name} ({type(handler).__name__})")

    def registered_operations(self) -> List[str]:
        return list(self._operations.keys())

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    async def execute(self, request: OperationRequest, context: Optional[OperationContext] = None) -> OperationResult:
        """Dispatch an OperationRequest to the appropriate handler action.

        Builds an OperationContext from the request if one is not supplied.
        """
        handler = self._operations.get(request.operation)
        if handler is None:
            return OperationResult(
                success=False,
                error=f"Operation not found: {request.operation!r}",
                error_code="OPERATION_NOT_FOUND",
            )

        action_fn = getattr(handler, request.action, None)
        if action_fn is None:
            return OperationResult(
                success=False,
                error=f"Action {request.action!r} not found on operation {request.operation!r}",
                error_code="ACTION_NOT_FOUND",
            )

        if context is None:
            context = OperationContext(
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
                f"OPERATION_ACTION_OK: operation={request.operation} action={request.action} "
                f"app_id={request.app_id}"
            )
            return OperationResult(success=True, data=result)

        except TypeError as exc:
            logger.warning(
                f"OPERATION_ACTION_BAD_PARAMS: operation={request.operation} action={request.action} error={exc}"
            )
            return OperationResult(
                success=False,
                error=f"Invalid parameters for {request.operation}.{request.action}: {exc}",
                error_code="INVALID_PARAMS",
            )
        except Exception as exc:
            logger.error(
                f"OPERATION_ACTION_ERROR: operation={request.operation} action={request.action} error={exc}",
                exc_info=True,
            )
            return OperationResult(
                success=False,
                error=str(exc),
                error_code="EXECUTION_ERROR",
            )

    async def health(self) -> Dict[str, Any]:
        return {
            "executor": "operation",
            "operations": self.registered_operations(),
            "count": len(self._operations),
        }

    def can_handle(self, target: str) -> bool:
        return target in self._operations


# Backward-compatible aliases — remove once all call sites are updated
ModuleRequest = OperationRequest
ModuleResult = OperationResult
ModuleExecutor = OperationExecutor
