from __future__ import annotations

"""ModuleExecutor — dispatches module action requests to loaded module handlers.

Implements the Executor protocol defined in executor_registry.py.

Request flow:
    1. Caller builds a ModuleRequest(module="contacts", action="list", params={...}, ctx=...)
    2. ModuleExecutor.execute() resolves the module by name from the registry
    3. ModuleExecutor maps the public action id to module.yaml actions[].handler_method
    4. ModuleExecutor calls handler.{handler_method}(ctx, **params)
    5. Returns ModuleResult(success=True, data=result)

Module handlers are plain Python classes. Public action ids are declared in
module.yaml; handler method names are explicit so the public API can remain
stable while implementation method names stay Pythonic.

Example handler:
    class ContactsModule:
        async def list_contacts(self, ctx: ModuleContext, *, limit: int = 20) -> list:
            ...
        async def create_contact(self, ctx: ModuleContext, *, name: str, email: str) -> dict:
            ...

Module handlers must NOT import from mozaiksai.core.workflow or any AI layer.
"""

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional
from uuid import uuid4

import jsonschema

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

    # Permission ids held by the caller. When None, enforcement is skipped
    # (trusted internal / AI workflow call). When set (even to []), the executor
    # checks that all action-declared permissions are present.
    granted_permissions: Optional[List[str]] = None


@dataclass
class ModuleResult:
    """Result of a module action execution."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    error_code: Optional[str] = None


# ---------------------------------------------------------------------------
# Schema validation helper
# ---------------------------------------------------------------------------

def _validate_schema(value: Any, schema: Dict[str, Any]) -> Optional[str]:
    """Validate *value* against a JSON Schema dict.

    Returns None on success or a short error string on failure.
    Empty or non-dict schemas are skipped (returns None).
    """
    if not schema or not isinstance(schema, dict):
        return None
    try:
        jsonschema.validate(instance=value, schema=schema)
        return None
    except jsonschema.ValidationError as exc:
        return exc.message
    except Exception as exc:  # malformed schema — don't crash the executor
        logger.warning("MODULE_SCHEMA_ERROR: could not validate schema: %s", exc)
        return None


# ---------------------------------------------------------------------------
# ModuleExecutor
# ---------------------------------------------------------------------------

class ModuleExecutor:
    """Dispatches ModuleRequests to registered module handler instances.

    Implements the Executor protocol so it can be registered in ExecutorRegistry.

    Usage (in mozaiksai.hosts.platform / AppLoader wiring):
        executor = ModuleExecutor()
        executor.register("contacts", ContactsModule())
        executor.register("tasks", TasksModule())

        registry = ExecutorRegistry()
        registry.register(executor)
    """

    executor_type: ExecutorType = ExecutorType.MODULE

    def __init__(
        self,
        *,
        event_emitter: Optional[Callable[[str, Dict[str, Any]], Awaitable[Any] | Any]] = None,
    ) -> None:
        self._modules: Dict[str, Any] = {}
        self._action_methods: Dict[str, Dict[str, str]] = {}
        self._settings: Dict[str, Optional[List[Dict[str, Any]]]] = {}
        self._action_permissions: Dict[str, Dict[str, List[str]]] = {}
        self._action_schemas: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._event_emitter = event_emitter

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        handler: Any,
        *,
        action_method_map: Optional[Dict[str, str]] = None,
        settings: Optional[List[Dict[str, Any]]] = None,
        action_permissions: Optional[Dict[str, List[str]]] = None,
        action_schemas: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """Register a module handler instance under a name.

        Args:
            name:               Module name as declared in module.yaml and ModuleRequest.module
            handler:            Instantiated module handler object
            action_method_map:  Maps public action id -> handler method name
            settings:           Setting definitions from settings.yaml (list of setting dicts).
                                Injected into ModuleContext.settings on every action call.
            action_permissions: Maps action id -> list of required permission ids.
                                Used to enforce ModuleRequest.granted_permissions at dispatch time.
            action_schemas:     Maps action id -> {input: JSON Schema, output: JSON Schema}.
                                Input validated before dispatch; output validated after (warn only).
        """
        self._modules[name] = handler
        self._action_methods[name] = dict(action_method_map or {})
        self._settings[name] = settings
        self._action_permissions[name] = dict(action_permissions or {})
        self._action_schemas[name] = dict(action_schemas or {})
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

        handler_method = self._action_methods.get(request.module, {}).get(request.action, request.action)
        action_fn = getattr(handler, handler_method, None)
        if action_fn is None:
            return ModuleResult(
                success=False,
                error=f"Action {request.action!r} not found on module {request.module!r}",
                error_code="ACTION_NOT_FOUND",
            )

        # Permission enforcement — only when the caller supplies granted_permissions.
        # None means a trusted internal/AI-workflow call; bypass silently.
        if request.granted_permissions is not None:
            required = self._action_permissions.get(request.module, {}).get(request.action, [])
            granted = set(request.granted_permissions)
            missing = [p for p in required if p not in granted]
            if missing:
                logger.warning(
                    f"MODULE_PERMISSION_DENIED: module={request.module} action={request.action} "
                    f"missing={missing} user={request.user_id}"
                )
                return ModuleResult(
                    success=False,
                    error=f"Permission denied for {request.module}.{request.action}: missing {missing}",
                    error_code="PERMISSION_DENIED",
                )

        # Input schema validation — applied before dispatch when a schema is declared.
        schemas = self._action_schemas.get(request.module, {}).get(request.action, {})
        input_schema = schemas.get("input") if schemas else None
        output_schema = schemas.get("output") if schemas else None
        if input_schema:
            input_error = _validate_schema(request.params, input_schema)
            if input_error:
                logger.warning(
                    f"MODULE_INPUT_INVALID: module={request.module} action={request.action} "
                    f"error={input_error}"
                )
                return ModuleResult(
                    success=False,
                    error=f"Input validation failed for {request.module}.{request.action}: {input_error}",
                    error_code="INVALID_PARAMS",
                )

        if context is None:
            context = ModuleContext(
                app_id=request.app_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                auth_token=request.auth_token,
                correlation_id=request.correlation_id,
                settings=self._settings.get(request.module),
                _emit=self._build_context_emitter(request),
            )

        try:
            if inspect.iscoroutinefunction(action_fn):
                result = await action_fn(context, **request.params)
            else:
                result = action_fn(context, **request.params)

            # Output schema validation — warn only; don't fail the caller on a module contract bug.
            if output_schema and result is not None:
                out_error = _validate_schema(result, output_schema)
                if out_error:
                    logger.warning(
                        f"MODULE_OUTPUT_INVALID: module={request.module} action={request.action} "
                        f"error={out_error}"
                    )

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

    def _build_context_emitter(
        self,
        request: ModuleRequest,
    ) -> Optional[Callable[[str, Dict[str, Any]], Awaitable[Any]]]:
        if self._event_emitter is None:
            return None

        async def emit_module_event(event_type: str, payload: Dict[str, Any]) -> None:
            envelope: Dict[str, Any] = {
                "id": f"evt_{uuid4().hex}",
                "type": event_type,
                "version": 1,
                "occurred_at": datetime.now(UTC).isoformat(),
                "source": {
                    "layer": "module",
                    "app_id": request.app_id,
                    "module_id": request.module,
                    "capability_id": f"{request.module}.{request.action}",
                },
                "tenant": {
                    "app_id": request.app_id,
                    "tenant_id": request.tenant_id,
                },
                "correlation": {
                    "correlation_id": request.correlation_id,
                },
                "payload": payload,
                "visibility": "internal",
            }
            if request.user_id:
                envelope["actor"] = {"type": "user", "id": request.user_id}

            result = self._event_emitter(event_type, envelope)
            if inspect.isawaitable(result):
                await result

        return emit_module_event
