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

from __future__ import annotations

import asyncio
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from logs.logging_config import get_workflow_logger
from mozaiksai.core.audit.audit_logger import get_audit_logger
from mozaiksai.core.ports.entitlement import EntitlementPort, NoOpEntitlementAdapter
from mozaiksai.core.runtime.app.module_loader import SettingDef
from mozaiksai.core.runtime.composition.bson_safe import json_safe_bson
from mozaiksai.core.runtime.composition.executor_registry import ExecutorType
from mozaiksai.core.runtime.composition.module_authority import (
    ModuleDispatchAudit,
    ModuleDispatchAuthority,
    ModuleDispatchProvenance,
    ModuleEntitlementCheck,
    ModuleExecutionPolicyInput,
    ModulePermissionCheck,
)
from mozaiksai.core.runtime.composition.module_context import ModuleContext
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.runtime.composition.schema_validation import (
    SchemaValidationDiagnostic,
    normalize_nullable_schema,
    validate_json_schema,
)
from mozaiksai.core.runtime.composition.workflow_trigger_guard import (
    WORKFLOW_TRIGGER_TRACE_KEY,
)
from mozaiksai.core.runtime.persistence import MongoPersistenceContext

logger = get_workflow_logger("module_executor")

# Timeout for async module action dispatch (default 30 s, 0 = disabled).
# Prevents a misbehaving module from blocking platform request handling indefinitely.
# Sync actions are not timed out here (they block the event loop anyway and should
# use threading if they perform I/O).
def _action_timeout() -> float | None:
    raw = os.getenv("MODULE_ACTION_TIMEOUT_SECONDS", "30").strip()
    try:
        v = float(raw)
        return v if v > 0 else None
    except (ValueError, AttributeError):
        return 30.0

# Maximum serialized byte size for module action params (default 512 KB).
# Prevents memory exhaustion from unexpectedly large payloads sent through
# module action dispatch. Override via MODULE_PARAMS_MAX_BYTES env var.
def _params_max_bytes() -> int:
    raw = os.getenv("MODULE_PARAMS_MAX_BYTES", "")
    try:
        v = int(raw.strip())
        return v if v > 0 else 512 * 1024
    except (ValueError, AttributeError):
        return 512 * 1024

# Maximum serialized byte size for a module action response (default 2 MB).
# Oversized responses are logged and replaced with an error result so callers
# don't buffer unbounded data in platform routes or WebSocket payloads.
def _response_max_bytes() -> int:
    raw = os.getenv("MODULE_RESPONSE_MAX_BYTES", "")
    try:
        v = int(raw.strip())
        return v if v > 0 else 2 * 1024 * 1024
    except (ValueError, AttributeError):
        return 2 * 1024 * 1024


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
        workspace_id:   Optional workspace/team scope under the tenant
        auth_token:     Optional JWT forwarded to external API calls
        correlation_id: Optional tracing ID
    """
    module: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)

    # Identity (injected by runtime before dispatch)
    app_id: str = ""
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    auth_token: str | None = None
    correlation_id: str | None = None

    # Explicit framework dispatch authority. Required: every caller must state
    # who is dispatching and whether the action's declared permissions and
    # entitlement gate are enforced. Trusted bypass is a property of the
    # authority's construction, never of a missing principal or empty list.
    authority: ModuleDispatchAuthority = field(kw_only=True)
    provenance: ModuleDispatchProvenance | None = None


@dataclass
class ModuleResult:
    """Result of a module action execution."""
    success: bool
    data: Any = None
    error: str | None = None
    error_code: str | None = None


# ---------------------------------------------------------------------------
# Schema validation helper
# ---------------------------------------------------------------------------

def _normalize_nullable_schema(schema: Any) -> Any:
    """Translate OpenAPI-style nullable fields into JSON Schema."""
    return normalize_nullable_schema(schema)


def _validate_schema(value: Any, schema: dict[str, Any]) -> str | None:
    """Validate *value* against a JSON Schema dict.

    Returns None on success or a short error string on failure.
    Empty or non-dict schemas are skipped (returns None).
    """
    if not schema or not isinstance(schema, dict):
        return None
    diagnostic = validate_json_schema(value, schema)
    return diagnostic.message if diagnostic is not None else None


class ModuleEventPayloadValidationError(ValueError):
    """Raised when a module emits an event payload that violates its contract."""

    def __init__(
        self,
        *,
        event_type: str,
        source_module: str | None,
        source_action: str | None,
        diagnostic: SchemaValidationDiagnostic,
    ) -> None:
        self.event_type = event_type
        self.source_module = source_module
        self.source_action = source_action
        self.diagnostic = diagnostic
        super().__init__(
            "MODULE_EVENT_PAYLOAD_INVALID: "
            f"event={event_type} module={source_module or ''} action={source_action or ''} "
            f"path={diagnostic.path} error={diagnostic.message}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": "MODULE_EVENT_PAYLOAD_INVALID",
            "event_type": self.event_type,
            "source_module": self.source_module,
            "source_action": self.source_action,
            "schema_error": self.diagnostic.to_dict(),
        }


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
        event_emitter: Callable[[str, dict[str, Any]], Awaitable[Any] | Any] | None = None,
        entitlement_checker: EntitlementPort | None = None,
    ) -> None:
        self._modules: dict[str, Any] = {}
        self._action_methods: dict[str, dict[str, str]] = {}
        self._settings: dict[str, list[SettingDef]] = {}
        self._action_permissions: dict[str, dict[str, list[str]]] = {}
        self._action_schemas: dict[str, dict[str, dict[str, Any]]] = {}
        self._action_entitlements: dict[str, dict[str, str | None]] = {}
        self._action_emits: dict[str, dict[str, list[str]]] = {}
        self._event_payload_schemas: dict[str, dict[str, dict[str, Any]]] = {}
        self._event_emitter = event_emitter
        # When None, use the no-op adapter — grants everything without a DB check.
        self._entitlement_checker: EntitlementPort = entitlement_checker or NoOpEntitlementAdapter()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        handler: Any,
        *,
        action_method_map: dict[str, str] | None = None,
        settings: list[SettingDef] | None = None,
        action_permissions: dict[str, list[str]] | None = None,
        action_schemas: dict[str, dict[str, Any]] | None = None,
        action_entitlements: dict[str, str | None] | None = None,
        action_emits: dict[str, list[str]] | None = None,
        event_payload_schemas: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Register a module handler instance under a name.

        Args:
            name:                Module name as declared in module.yaml and ModuleRequest.module
            handler:             Instantiated module handler object
            action_method_map:   Maps public action id -> handler method name
            settings:            Setting definitions from settings.yaml (list of setting dicts).
                                 Injected into ModuleContext.settings on every action call.
            action_permissions:  Maps action id -> list of required permission ids.
                                 Enforced against ModuleRequest.authority.permissions
                                 for every enforce-mode dispatch.
            action_schemas:      Maps action id -> {input: JSON Schema, output: JSON Schema}.
                                 Input validated before dispatch; output validated after (warn only).
            action_entitlements: Maps action id -> capability_id (or None).
                                 When a capability_id is set, the executor calls
                                 EntitlementPort.check() before dispatch and returns
                                 ENTITLEMENT_REQUIRED on denial.
            action_emits:        Maps action id -> event types declared in module.yaml.
            event_payload_schemas: Maps event type -> payload_schema from contracts/events.yaml.
        """
        self._modules[name] = handler
        self._action_methods[name] = dict(action_method_map or {})
        self._settings[name] = list(settings or [])
        self._action_permissions[name] = dict(action_permissions or {})
        self._action_schemas[name] = dict(action_schemas or {})
        self._action_entitlements[name] = dict(action_entitlements or {})
        self._action_emits[name] = {k: list(v) for k, v in (action_emits or {}).items()}
        self._event_payload_schemas[name] = dict(event_payload_schemas or {})
        logger.info("MODULE_REGISTERED: %s (%s)", name, type(handler).__name__)

    def registered_modules(self) -> list[str]:
        return list(self._modules.keys())

    def setting_defs(self, module: str) -> list[SettingDef]:
        """Return the declared setting definitions for a module (empty list if none)."""
        return self._settings.get(module) or []

    def resolve_settings(self, module: str) -> dict[str, Any]:
        """Resolve settings to a concrete value dict for use in ModuleContext.

        Resolution order (each layer overrides the previous):
          1. Declared defaults from settings.yaml
          (future) 2. App-scoped operator overrides from AppSettings collection
          (future) 3. User-scoped overrides from UserSettings collection

        Returns an empty dict when the module declares no settings.
        """
        defs = self._settings.get(module) or []
        return {d.id: d.default for d in defs}

    # ------------------------------------------------------------------
    # Executor protocol
    # ------------------------------------------------------------------

    async def execute(self, request: ModuleRequest, context: ModuleContext | None = None) -> ModuleResult:
        """Dispatch a ModuleRequest to the appropriate handler action.

        Builds a ModuleContext from the request if one is not supplied.
        """
        dispatch_authority = request.authority
        dispatch_provenance = request.provenance or ModuleDispatchProvenance(
            correlation_id=request.correlation_id,
        )
        permission_check = self._build_permission_check(request, dispatch_authority)
        entitlement_check = ModuleEntitlementCheck(checked=False, status="skipped")
        dispatch_audit = self._build_dispatch_audit(
            request,
            dispatch_authority,
            dispatch_provenance,
            permission_check,
            entitlement_check,
            outcome="allowed",
        )
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

        if not permission_check.allowed:
            logger.warning(
                "MODULE_PERMISSION_DENIED: module=%s action=%s missing=%s user=%s",
                request.module,
                request.action,
                list(permission_check.missing_permissions),
                request.user_id,
            )
            denied_audit = replace(
                dispatch_audit,
                outcome="denied",
                reason="permission denied",
                permission_check=permission_check,
            )
            asyncio.create_task(self._emit_dispatch_audit(denied_audit, error="PERMISSION_DENIED"))
            return ModuleResult(
                success=False,
                error=(
                    f"Permission denied for {request.module}.{request.action}: "
                    f"missing {list(permission_check.missing_permissions)}"
                ),
                error_code="PERMISSION_DENIED",
            )

        # Entitlement check — only when the action declares an entitlement_gate.
        # Enforce-mode dispatch always runs it; trusted_bypass authorities
        # (closed server-owned kinds only) skip both checks.
        if dispatch_authority.permission_mode == "enforce":
            capability_id = self._action_entitlements.get(request.module, {}).get(request.action)
            if capability_id:
                ent_result = await self._entitlement_checker.check(
                    capability_id,
                    app_id=request.app_id,
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                )
                entitlement_check = ModuleEntitlementCheck(
                    checked=True,
                    status="granted" if ent_result.granted else "denied",
                    capability_id=capability_id,
                    reason=ent_result.reason,
                )
                if not ent_result.granted:
                    logger.warning(
                        "MODULE_ENTITLEMENT_DENIED: module=%s action=%s capability=%s reason=%s user=%s",
                        request.module, request.action, capability_id, ent_result.reason, request.user_id)
                    denied_audit = replace(
                        dispatch_audit,
                        outcome="denied",
                        reason="entitlement required",
                        entitlement_check=entitlement_check,
                    )
                    asyncio.create_task(self._emit_dispatch_audit(denied_audit, error="ENTITLEMENT_REQUIRED"))
                    return ModuleResult(
                        success=False,
                        error=f"Entitlement required for {request.module}.{request.action}: {capability_id}",
                        error_code="ENTITLEMENT_REQUIRED",
                    )
            else:
                entitlement_check = ModuleEntitlementCheck(checked=False, status="not_applicable")
        else:
            entitlement_check = ModuleEntitlementCheck(checked=False, status="skipped")

        dispatch_audit = replace(
            dispatch_audit,
            permission_check=permission_check,
            entitlement_check=entitlement_check,
        )
        policy_input = ModuleExecutionPolicyInput(
            request=request,
            authority=dispatch_authority,
            provenance=dispatch_provenance,
            permission_check=permission_check,
            entitlement_check=entitlement_check,
        )
        policy_decision = await get_platform_hooks().call_before_module_execution(policy_input)
        if not policy_decision.allowed:
            reason = policy_decision.reason or "module execution denied by application policy"
            denied_audit = replace(
                dispatch_audit,
                outcome="denied",
                reason=reason,
                audit_tags=policy_decision.audit_tags,
            )
            asyncio.create_task(self._emit_dispatch_audit(denied_audit, error="MODULE_POLICY_DENIED"))
            return ModuleResult(
                success=False,
                error=reason,
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
                    "MODULE_INPUT_INVALID: module=%s action=%s error=%s",
                    request.module, request.action, input_error)
                return ModuleResult(
                    success=False,
                    error=f"Input validation failed for {request.module}.{request.action}: {input_error}",
                    error_code="INVALID_PARAMS",
                )

        # Input size gate — reject payloads that would exhaust memory on dispatch.
        if request.params:
            try:
                params_size = len(json.dumps(request.params, default=str))
            except Exception:
                params_size = 0
            max_params = _params_max_bytes()
            if params_size > max_params:
                logger.warning(
                    "MODULE_PARAMS_TOO_LARGE: module=%s action=%s size=%d limit=%d",
                    request.module, request.action, params_size, max_params,
                )
                return ModuleResult(
                    success=False,
                    error=f"Request payload too large for action '{request.action}'",
                    error_code="PAYLOAD_TOO_LARGE",
                )

        if context is None:
            context = ModuleContext(
                app_id=request.app_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
                module_id=request.module,
                action_id=request.action,
                auth_token=request.auth_token,
                permissions=(
                    list(dispatch_authority.permissions)
                    if dispatch_authority.permission_mode == "enforce"
                    else None
                ),
                correlation_id=request.correlation_id,
                settings=self.resolve_settings(request.module) or None,
                persistence=self._build_persistence_context(request),
                _emit=self._build_context_emitter(request),  # type: ignore[arg-type]
                dispatch_authority=dispatch_authority,
                dispatch_provenance=dispatch_provenance,
                dispatch_audit=dispatch_audit,
            )
        else:
            # A caller-supplied context still carries this dispatch's authority
            # facts so ctx.dispatch_authority is present on every execution path.
            context.dispatch_authority = dispatch_authority
            context.dispatch_provenance = dispatch_provenance
            context.dispatch_audit = dispatch_audit

        timeout = _action_timeout()
        try:
            if inspect.iscoroutinefunction(action_fn):
                coro = action_fn(context, **request.params)
                result = (
                    await asyncio.wait_for(coro, timeout=timeout)
                    if timeout is not None
                    else await coro
                )
            else:
                result = action_fn(context, **request.params)
        except TimeoutError:
            logger.error(
                "MODULE_ACTION_TIMEOUT: module=%s action=%s timeout=%.1fs user=%s",
                request.module, request.action, timeout, request.user_id,
            )
            return ModuleResult(
                success=False,
                error=f"Action '{request.action}' timed out",
                error_code="ACTION_TIMEOUT",
            )
        except TypeError as exc:
            logger.warning(
                "MODULE_ACTION_BAD_PARAMS: module=%s action=%s error=%s", request.module, request.action, exc)
            return ModuleResult(
                success=False,
                error=f"Invalid parameters for action '{request.action}'",
                error_code="INVALID_PARAMS",
            )
        except PermissionError as exc:
            logger.warning(
                "MODULE_ACTION_PERMISSION_DENIED: module=%s action=%s user=%s error=%s",
                request.module, request.action, request.user_id, exc,
            )
            return ModuleResult(
                success=False,
                error="Permission denied.",
                error_code="PERMISSION_DENIED",
            )
        except ModuleEventPayloadValidationError as exc:
            logger.warning(
                "MODULE_EVENT_PAYLOAD_INVALID: module=%s action=%s event=%s path=%s error=%s",
                request.module,
                request.action,
                exc.event_type,
                exc.diagnostic.path,
                exc.diagnostic.message,
                extra={"module_event_payload_validation": exc.to_dict()},
            )
            asyncio.create_task(
                self._emit_dispatch_audit(
                    replace(dispatch_audit, outcome="failed", reason="MODULE_EVENT_PAYLOAD_INVALID"),
                    error="MODULE_EVENT_PAYLOAD_INVALID",
                )
            )
            return ModuleResult(
                success=False,
                error=str(exc),
                error_code="INVALID_EVENT_PAYLOAD",
            )
        except Exception as exc:
            logger.error(
                "MODULE_ACTION_ERROR: module=%s action=%s error=%s", request.module, request.action, exc,
                exc_info=True,
            )
            asyncio.create_task(
                self._emit_dispatch_audit(
                    replace(dispatch_audit, outcome="failed", reason=type(exc).__name__),
                    error=type(exc).__name__,
                )
            )
            return ModuleResult(
                success=False,
                error=f"Action {request.action!r} failed",
                error_code="EXECUTION_ERROR",
            )

        # Module results routinely carry raw Mongo documents; normalize BSON
        # identifier types here — the one choke point every action result
        # crosses — so ObjectId/Decimal128 never reach a JSON serializer.
        result = json_safe_bson(result)

        # Output schema validation — warn only; don't fail the caller on a module contract bug.
        if output_schema and result is not None:
            out_error = _validate_schema(result, output_schema)
            if out_error:
                logger.warning(
                    "MODULE_OUTPUT_INVALID: module=%s action=%s error=%s",
                    request.module, request.action, out_error)

        # Response size gate — prevent unbounded responses from being buffered
        # in platform routes or sent over WebSocket payloads.
        if result is not None:
            try:
                result_size = len(json.dumps(result, default=str))
            except Exception:
                result_size = 0
            max_response = _response_max_bytes()
            if result_size > max_response:
                logger.error(
                    "MODULE_RESPONSE_TOO_LARGE: module=%s action=%s size=%d limit=%d",
                    request.module, request.action, result_size, max_response,
                )
                return ModuleResult(
                    success=False,
                    error=f"Response too large for action '{request.action}'",
                    error_code="RESPONSE_TOO_LARGE",
                )

        logger.debug(
            "MODULE_ACTION_OK: module=%s action=%s app_id=%s",
            request.module, request.action, request.app_id)
        # Audit trail — fire-and-forget; never blocks the action response.
        asyncio.create_task(
            self._emit_dispatch_audit(replace(dispatch_audit, outcome="ok"))
        )
        return ModuleResult(success=True, data=result)

    async def health(self) -> dict[str, Any]:
        return {
            "executor": "module",
            "modules": self.registered_modules(),
            "count": len(self._modules),
        }

    def can_handle(self, target: str) -> bool:
        return target in self._modules

    def _build_permission_check(
        self,
        request: ModuleRequest,
        authority: ModuleDispatchAuthority,
    ) -> ModulePermissionCheck:
        if authority.permission_mode == "trusted_bypass":
            return ModulePermissionCheck(checked=False)
        required = tuple(self._action_permissions.get(request.module, {}).get(request.action, []))
        granted = tuple(authority.permissions)
        granted_set = set(granted)
        missing = tuple(permission for permission in required if permission not in granted_set)
        return ModulePermissionCheck(
            checked=True,
            granted=granted,
            required_permissions=required,
            missing_permissions=missing,
        )

    def _build_dispatch_audit(
        self,
        request: ModuleRequest,
        authority: ModuleDispatchAuthority,
        provenance: ModuleDispatchProvenance,
        permission_check: ModulePermissionCheck,
        entitlement_check: ModuleEntitlementCheck,
        *,
        outcome: str,
        reason: str | None = None,
    ) -> ModuleDispatchAudit:
        return ModuleDispatchAudit(
            app_id=request.app_id or None,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            actor_id=request.user_id,
            module=request.module,
            action=request.action,
            authority_kind=authority.kind,
            permission_mode=authority.permission_mode,
            permission_check=permission_check,
            entitlement_check=entitlement_check,
            correlation_id=provenance.correlation_id or request.correlation_id,
            causation_id=provenance.causation_id,
            outcome=outcome,  # type: ignore[arg-type]
            reason=reason,
        )

    async def _emit_dispatch_audit(
        self,
        audit: ModuleDispatchAudit,
        *,
        error: str | None = None,
    ) -> None:
        await get_audit_logger().log_module_action(
            actor_id=audit.actor_id or "system",
            app_id=audit.app_id,
            module_id=audit.module,
            action_id=audit.action,
            params=None,
            outcome="ok" if audit.outcome == "ok" else audit.outcome,
            error=error or audit.reason,
            tenant_id=audit.tenant_id,
            workspace_id=audit.workspace_id,
            extra={"dispatch": audit.to_dict()},
        )
        await get_platform_hooks().call_module_dispatch_audit(audit)

    def _build_context_emitter(
        self,
        request: ModuleRequest,
    ) -> Callable[[str, dict[str, Any]], Awaitable[Any]] | None:
        if self._event_emitter is None:
            return None

        async def emit_module_event(event_type: str, payload: dict[str, Any]) -> None:
            event_type_text = str(event_type or "").strip()
            declared_emits = self._action_emits.get(request.module, {}).get(request.action)
            if declared_emits is not None and event_type_text not in declared_emits:
                diagnostic = SchemaValidationDiagnostic(
                    message=f"action {request.module}.{request.action} did not declare emitted event",
                    path="$",
                    schema_path="$",
                    validator="emits",
                )
                raise ModuleEventPayloadValidationError(
                    event_type=event_type_text,
                    source_module=request.module,
                    source_action=request.action,
                    diagnostic=diagnostic,
                )
            payload_schema = self._event_payload_schemas.get(request.module, {}).get(event_type_text)
            if payload_schema:
                validation_diagnostic = validate_json_schema(payload, payload_schema)
                if validation_diagnostic is not None:
                    raise ModuleEventPayloadValidationError(
                        event_type=event_type_text,
                        source_module=request.module,
                        source_action=request.action,
                        diagnostic=validation_diagnostic,
                    )

            tenant_scope = {
                "app_id": request.app_id,
                "tenant_id": request.tenant_id,
            }
            if request.workspace_id:
                tenant_scope["workspace_id"] = request.workspace_id

            envelope: dict[str, Any] = {
                "id": f"evt_{uuid4().hex}",
                "type": event_type_text,
                "version": 1,
                "occurred_at": datetime.now(UTC).isoformat(),
                "source": {
                    "layer": "module",
                    "app_id": request.app_id,
                    "module_id": request.module,
                    "action_id": request.action,
                    "capability_id": f"{request.module}.{request.action}",
                },
                "tenant": tenant_scope,
                "correlation": {
                    "correlation_id": request.correlation_id,
                },
                "payload": payload,
                "visibility": "internal",
            }
            envelope["authority"] = {
                "kind": request.authority.kind,
                "permission_mode": request.authority.permission_mode,
                "permissions": list(request.authority.permissions),
            }
            if request.user_id:
                envelope["actor"] = {"type": "user", "id": request.user_id}
            trigger_trace = (
                request.provenance.metadata.get(WORKFLOW_TRIGGER_TRACE_KEY)
                if request.provenance is not None
                else None
            )
            if isinstance(trigger_trace, dict):
                envelope[WORKFLOW_TRIGGER_TRACE_KEY] = dict(trigger_trace)

            result = self._event_emitter(event_type_text, envelope)  # type: ignore[misc]
            if inspect.isawaitable(result):
                await result

        return emit_module_event

    def _build_persistence_context(
        self,
        request: ModuleRequest,
    ) -> MongoPersistenceContext | None:
        app_id = str(request.app_id or "").strip()
        if not app_id:
            return None
        return MongoPersistenceContext(
            app_id=app_id,
            tenant_id=request.tenant_id,
            workspace_id=request.workspace_id,
            user_id=request.user_id,
        )
