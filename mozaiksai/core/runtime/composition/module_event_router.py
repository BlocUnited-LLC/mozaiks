from __future__ import annotations

"""Platform-owned routing for canonical module events.

The runtime dispatcher transports events. This router interprets app/module
manifests loaded by the platform host:

- reactions.yaml maps events to module-owned reactions
- notifications.yaml maps events to notification intents

This keeps module event meaning above the runtime kernel.
"""

import importlib
import inspect
import json
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any
from uuid import uuid4

from logs.logging_config import get_workflow_logger
from mozaiksai.core.runtime.app.module_loader import LoadedModule
from mozaiksai.core.runtime.composition.module_event_provenance import (
    ModuleEventProvenance,
    ModuleReactionAudit,
    ModuleReactionProvenance,
    build_module_reaction_audit,
    normalize_module_event_provenance,
    normalize_module_reaction_provenance,
)
from mozaiksai.core.runtime.composition.platform_hooks import get_platform_hooks
from mozaiksai.core.runtime.composition.reaction_idempotency_store import (
    ReactionIdempotencyStore,
)
from mozaiksai.core.runtime.composition.schema_validation import (
    SchemaValidationDiagnostic,
    validate_json_schema,
)

logger = get_workflow_logger("module_event_router")

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]
NotificationStore = Callable[[dict[str, Any]], Awaitable[Any] | Any]
CapabilityInvoker = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[Any] | Any]


class _ReactionCtx:
    """Minimal context passed to handler methods during reaction dispatch."""

    def __init__(
        self,
        *,
        app_id: str,
        tenant_id: str,
        user_id: str,
        event_emitter: EventEmitter | None,
        event_provenance: ModuleEventProvenance | None = None,
        reaction_provenance: ModuleReactionProvenance | None = None,
        permissions: Iterable[str] | None = None,
    ) -> None:
        self.app_id = app_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.event_provenance = event_provenance
        self.reaction_provenance = reaction_provenance
        self.permissions = list(permissions or [])
        self.correlation_id = event_provenance.correlation_id if event_provenance is not None else None
        self.causation_id = event_provenance.causation_id if event_provenance is not None else None
        self._event_emitter = event_emitter

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_emitter is not None:
            await ModuleEventRouter._maybe_await(self._event_emitter(event_type, payload))


class ModuleEventPayloadValidationError(ValueError):
    """Raised when a routed event violates its declared payload schema."""

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


class ModuleEventRouter:
    """Routes loaded module events to platform-level reactions."""

    def __init__(
        self,
        modules: Iterable[LoadedModule],
        *,
        event_emitter: EventEmitter | None = None,
        notification_store: NotificationStore | None = None,
        capability_invoker: CapabilityInvoker | None = None,
        idempotency_store: ReactionIdempotencyStore | None = None,
    ) -> None:
        self._event_emitter = event_emitter
        self._notification_store = notification_store
        self._capability_invoker = capability_invoker
        self._idempotency_store = idempotency_store
        self._reactions_by_event: dict[str, list[dict]] = defaultdict(list)
        self._notifications_by_event: dict[str, list[dict]] = defaultdict(list)
        self._notifications_by_key: dict[tuple[str, str], dict] = {}
        self._handlers_by_module: dict[str, Any] = {}
        self._event_schemas_by_producer: dict[tuple[str, str], dict[str, Any]] = {}
        self._event_schemas_by_type: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        self._capability_ids: set[str] = set()
        self._capability_index_available = False
        self._handler_emits_by_module_method: dict[tuple[str, str], list[str]] = {}
        self._processed_reaction_keys: set[tuple[str, str, str, str, str]] = set()
        self._index_modules(modules)
        self._validate_reaction_targets()
        self._validate_static_reaction_cycles()

    @property
    def event_types(self) -> list[str]:
        return sorted(set(self._reactions_by_event) | set(self._notifications_by_event))

    def register(self, dispatcher: Any) -> int:
        """Register this router with the runtime dispatcher."""
        count = 0
        for event_type in self.event_types:
            dispatcher.register_handler(event_type, self._handler_for(event_type))
            count += 1
        if count:
            logger.info("MODULE_EVENT_ROUTER_READY: %s event type(s)", count)
        return count

    async def handle_event(self, event_type: str, envelope: dict[str, Any]) -> None:
        """Handle one canonical module event envelope."""
        emitted_notifications: set[tuple[str, str]] = set()
        event_provenance = normalize_module_event_provenance(event_type, envelope)
        validation_error = self._validate_event_payload(event_type, envelope, event_provenance)
        if validation_error is not None:
            logger.warning(
                "MODULE_EVENT_PAYLOAD_INVALID: event=%s module=%s action=%s path=%s error=%s",
                event_type,
                validation_error.source_module,
                validation_error.source_action,
                validation_error.diagnostic.path,
                validation_error.diagnostic.message,
                extra={"module_event_payload_validation": validation_error.to_dict()},
            )
            for reaction in self._reactions_by_event.get(event_type, []):
                await self._emit_reaction_audit(
                    build_module_reaction_audit(
                        event=event_provenance,
                        reaction=self._reaction_provenance(reaction),
                        outcome="failed",
                        reason=str(validation_error),
                    )
                )
            raise validation_error

        for reaction in self._reactions_by_event.get(event_type, []):
            permission_result = self._evaluate_reaction_permissions(reaction, envelope)
            reaction_provenance = self._reaction_provenance(
                reaction,
                permissions_enforced=permission_result["required"],
                idempotency_enforced=bool(reaction.get("idempotency_key")),
            )
            if not permission_result["allowed"]:
                await self._emit_reaction_audit(
                    build_module_reaction_audit(
                        event=event_provenance,
                        reaction=reaction_provenance,
                        outcome="skipped",
                        reason=permission_result["reason"],
                    )
                )
                continue
            idempotency_key = self._idempotency_key(reaction, event_type, envelope, event_provenance)
            # durable_key_str is set when the durable store is active and the
            # slot was successfully claimed; used to mark complete/fail after dispatch.
            durable_key_str: str | None = None
            if idempotency_key is not None:
                # Fast path: in-memory check (same process, established by PR #256).
                if idempotency_key in self._processed_reaction_keys:
                    await self._emit_reaction_audit(
                        build_module_reaction_audit(
                            event=event_provenance,
                            reaction=reaction_provenance,
                            outcome="skipped",
                            reason="idempotent reaction already processed",
                        )
                    )
                    continue
                # Durable path: restart-safe check via persistent ledger.
                if self._idempotency_store is not None:
                    durable_key_str = "|".join(idempotency_key)
                    claimed = await self._idempotency_store.claim(
                        app_id=event_provenance.app_id or "",
                        tenant_id=event_provenance.tenant_id,
                        workspace_id=event_provenance.workspace_id,
                        idempotency_key_str=durable_key_str,
                    )
                    if not claimed:
                        durable_key_str = None  # not our claim; do not mark complete/fail
                        await self._emit_reaction_audit(
                            build_module_reaction_audit(
                                event=event_provenance,
                                reaction=reaction_provenance,
                                outcome="skipped",
                                reason="idempotent reaction suppressed by durable ledger",
                            )
                        )
                        continue
                self._processed_reaction_keys.add(idempotency_key)
            target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
            target_kind = str(target.get("kind") or "").strip()
            if target_kind == "notification":
                notification_id = str(target.get("notification_id") or "").strip()
                rule = self._find_notification_rule(
                    module_id=str(reaction.get("module_id") or ""),
                    notification_id=notification_id,
                    event_type=event_type,
                )
                if rule is not None:
                    if not _notification_rule_matches(rule, envelope):
                        await self._emit_reaction_audit(
                            build_module_reaction_audit(
                                event=event_provenance,
                                reaction=reaction_provenance,
                                outcome="skipped",
                                reason="notification condition did not match",
                            )
                        )
                        continue
                    key = (str(rule.get("module_id") or ""), str(rule.get("id") or ""))
                    await self._create_notification(rule, event_type, envelope)
                    emitted_notifications.add(key)
                    await self._emit_reaction_audit(
                        build_module_reaction_audit(
                            event=event_provenance,
                            reaction=reaction_provenance,
                            outcome="ok",
                        )
                    )
                    await self._durable_complete(durable_key_str, event_provenance)
                else:
                    await self._emit_reaction_audit(
                        build_module_reaction_audit(
                            event=event_provenance,
                            reaction=reaction_provenance,
                            outcome="skipped",
                            reason="notification rule not found",
                        )
                    )
            elif target_kind == "handler":
                outcome, reason = await self._dispatch_handler(
                    reaction,
                    event_type,
                    envelope,
                    event_provenance=event_provenance,
                    reaction_provenance=reaction_provenance,
                    permissions=permission_result["granted"],
                )
                await self._emit_reaction_audit(
                    build_module_reaction_audit(
                        event=event_provenance,
                        reaction=reaction_provenance,
                        outcome=outcome,
                        reason=reason,
                    )
                )
                if outcome == "ok":
                    await self._durable_complete(durable_key_str, event_provenance)
                else:
                    await self._durable_mark_failed(durable_key_str, event_provenance)
            elif target_kind == "service_adapter":
                adapter_result = await self._dispatch_service_adapter(
                    reaction,
                    event_type,
                    envelope,
                    event_provenance=event_provenance,
                    reaction_provenance=reaction_provenance,
                )
                await self._emit_platform_reaction(
                    reaction,
                    event_type,
                    envelope,
                    reaction_result=adapter_result,
                )
                adapter_failed = isinstance(adapter_result, dict) and adapter_result.get("success") is False
                await self._emit_reaction_audit(
                    build_module_reaction_audit(
                        event=event_provenance,
                        reaction=reaction_provenance,
                        outcome="failed" if adapter_failed else "ok",
                        reason=(
                            str(adapter_result.get("error_code") or "service adapter failed")
                            if adapter_failed
                            else None
                        ),
                    )
                )
                if adapter_failed:
                    await self._durable_mark_failed(durable_key_str, event_provenance)
                else:
                    await self._durable_complete(durable_key_str, event_provenance)
            elif target_kind:
                await self._emit_platform_reaction(reaction, event_type, envelope)
                await self._emit_reaction_audit(
                    build_module_reaction_audit(
                        event=event_provenance,
                        reaction=reaction_provenance,
                        outcome="ok",
                    )
                )
                await self._durable_complete(durable_key_str, event_provenance)

        for rule in self._notifications_by_event.get(event_type, []):
            key = (str(rule.get("module_id") or ""), str(rule.get("id") or ""))
            if key not in emitted_notifications and _notification_rule_matches(rule, envelope):
                await self._create_notification(rule, event_type, envelope)

    def _index_modules(self, modules: Iterable[LoadedModule]) -> None:
        for module in modules:
            module_id = module.name
            self._handlers_by_module[module_id] = module.handler
            manifests = getattr(module, "manifests", None)
            definition = getattr(module, "definition", None)
            if definition is not None:
                capabilities = getattr(definition, "capabilities", None)
                actions = getattr(definition, "actions", None)
                if isinstance(capabilities, list):
                    self._capability_index_available = True
                for capability in capabilities or []:
                    capability_id = str(getattr(capability, "capability_id", "") or "").strip()
                    if capability_id:
                        self._capability_ids.add(capability_id)
                for action in actions or []:
                    handler_method = str(getattr(action, "handler_method", "") or "").strip()
                    emits = [str(event).strip() for event in getattr(action, "emits", []) or [] if str(event).strip()]
                    if handler_method:
                        self._handler_emits_by_module_method[(module_id, handler_method)] = emits
            events_manifest = getattr(manifests, "events", None)
            if events_manifest is not None:
                events = getattr(events_manifest, "events", None)
                if not isinstance(events, list):
                    events = []
                for event in events:
                    event_type = str(getattr(event, "type", "") or "").strip()
                    payload_schema = getattr(event, "payload_schema", None)
                    if event_type and isinstance(payload_schema, dict) and payload_schema:
                        self._event_schemas_by_producer[(module_id, event_type)] = dict(payload_schema)
                        self._event_schemas_by_type[event_type].append((module_id, dict(payload_schema)))
            reactions_manifest = getattr(manifests, "reactions", None)
            if reactions_manifest is not None:
                for reaction_model in reactions_manifest.reactions:
                    event_type = str(reaction_model.event_type or "").strip()
                    if not event_type:
                        continue
                    reaction = reaction_model.model_dump(mode="python", exclude_none=True)
                    reaction["module_id"] = module_id
                    self._reactions_by_event[event_type].append(reaction)

            notifications_manifest = getattr(manifests, "notifications", None)
            if notifications_manifest is not None:
                for raw_rule in notifications_manifest.notifications:
                    if hasattr(raw_rule, "as_rule") and callable(raw_rule.as_rule):
                        raw_rule = raw_rule.as_rule()
                    elif hasattr(raw_rule, "model_dump") and callable(raw_rule.model_dump):
                        raw_rule = raw_rule.model_dump(mode="python", exclude_none=True)
                    if not isinstance(raw_rule, dict):
                        continue
                    event_type = str(raw_rule.get("event_type") or "").strip()
                    rule_id = str(raw_rule.get("id") or "").strip()
                    if not event_type or not rule_id:
                        continue
                    rule = dict(raw_rule)
                    rule["module_id"] = module_id
                    self._notifications_by_event[event_type].append(rule)
                    self._notifications_by_key[(module_id, rule_id)] = rule

    def _validate_reaction_targets(self) -> None:
        for reactions in self._reactions_by_event.values():
            for reaction in reactions:
                module_id = str(reaction.get("module_id") or "").strip()
                target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
                target_kind = str(target.get("kind") or "").strip()
                if target_kind == "handler":
                    handler_method = str(target.get("handler_method") or "").strip()
                    handler = self._handlers_by_module.get(module_id)
                    if handler is not None and handler_method and not callable(getattr(handler, handler_method, None)):
                        raise ValueError(
                            "MODULE_REACTION_TARGET_INVALID: "
                            f"module={module_id} reaction={reaction.get('id')} "
                            f"handler_method={handler_method} not found"
                        )
                elif target_kind == "capability":
                    capability_id = str(target.get("capability_id") or "").strip()
                    if self._capability_index_available and capability_id not in self._capability_ids:
                        raise ValueError(
                            "MODULE_REACTION_TARGET_INVALID: "
                            f"module={module_id} reaction={reaction.get('id')} "
                            f"capability_id={capability_id} not declared"
                        )

    def _validate_static_reaction_cycles(self) -> None:
        edges: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
        for event_type, reactions in self._reactions_by_event.items():
            for reaction in reactions:
                target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
                if target.get("kind") != "handler":
                    continue
                module_id = str(reaction.get("module_id") or "").strip()
                handler_method = str(target.get("handler_method") or "").strip()
                for emitted_event in self._handler_emits_by_module_method.get((module_id, handler_method), []):
                    edges[event_type].append((emitted_event, reaction))

        visiting: list[str] = []
        visited: set[str] = set()

        def visit(event_type: str) -> None:
            if event_type in visiting:
                cycle = [*visiting[visiting.index(event_type):], event_type]
                raise ValueError(
                    "MODULE_REACTION_CYCLE: "
                    + " -> ".join(cycle)
                )
            if event_type in visited:
                return
            visiting.append(event_type)
            for next_event, _reaction in edges.get(event_type, []):
                visit(next_event)
            visiting.pop()
            visited.add(event_type)

        for event_type in list(edges):
            visit(event_type)

    def _validate_event_payload(
        self,
        event_type: str,
        envelope: dict[str, Any],
        event_provenance: ModuleEventProvenance,
    ) -> ModuleEventPayloadValidationError | None:
        schema = self._payload_schema_for_event(event_type, event_provenance)
        if not schema:
            return None
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else envelope
        diagnostic = validate_json_schema(payload, schema)
        if diagnostic is None:
            return None
        return ModuleEventPayloadValidationError(
            event_type=event_type,
            source_module=event_provenance.producer_module_id,
            source_action=event_provenance.producer_action_id,
            diagnostic=diagnostic,
        )

    def _payload_schema_for_event(
        self,
        event_type: str,
        event_provenance: ModuleEventProvenance,
    ) -> dict[str, Any] | None:
        producer_module = str(event_provenance.producer_module_id or "").strip()
        if producer_module:
            schema = self._event_schemas_by_producer.get((producer_module, event_type))
            if schema:
                return schema
        schemas = self._event_schemas_by_type.get(event_type) or []
        if len(schemas) == 1:
            return schemas[0][1]
        return None

    def _reaction_provenance(
        self,
        reaction: dict[str, Any],
        *,
        permissions_enforced: bool = False,
        idempotency_enforced: bool = False,
    ) -> ModuleReactionProvenance:
        provenance = normalize_module_reaction_provenance(reaction)
        return replace(
            provenance,
            permissions_enforced=permissions_enforced,
            idempotency_enforced=idempotency_enforced,
        )

    def _evaluate_reaction_permissions(
        self,
        reaction: dict[str, Any],
        envelope: dict[str, Any],
    ) -> dict[str, Any]:
        required = [
            str(permission).strip()
            for permission in (reaction.get("permissions") or [])
            if str(permission).strip()
        ]
        if not required:
            return {"allowed": True, "required": False, "granted": [], "reason": None}
        authority = envelope.get("authority") if isinstance(envelope.get("authority"), dict) else {}
        granted = [
            str(permission).strip()
            for permission in (authority.get("granted_permissions") if isinstance(authority, dict) else []) or []
            if str(permission).strip()
        ]
        missing = sorted(set(required) - set(granted))
        if missing:
            return {
                "allowed": False,
                "required": True,
                "granted": granted,
                "reason": f"reaction permission denied: missing {missing}",
            }
        return {"allowed": True, "required": True, "granted": granted, "reason": None}

    def _idempotency_key(
        self,
        reaction: dict[str, Any],
        event_type: str,
        envelope: dict[str, Any],
        event_provenance: ModuleEventProvenance,
    ) -> tuple[str, str, str, str, str] | None:
        declared = str(reaction.get("idempotency_key") or "").strip()
        if not declared:
            return None
        reaction_id = str(reaction.get("id") or "").strip()
        module_id = str(reaction.get("module_id") or "").strip()
        event_identity = str(event_provenance.event_id or "").strip()
        if not event_identity:
            event_identity = sha256(
                json.dumps(
                    {
                        "event_type": event_type,
                        "tenant_id": event_provenance.tenant_id,
                        "app_id": event_provenance.app_id,
                        "actor_id": event_provenance.actor_id,
                        "payload": envelope.get("payload", envelope),
                    },
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        return (module_id, reaction_id, event_type, declared, event_identity)

    async def _durable_complete(
        self,
        durable_key_str: str | None,
        event_provenance: ModuleEventProvenance,
    ) -> None:
        """Mark the durable ledger slot as completed if active."""
        if durable_key_str is None or self._idempotency_store is None:
            return
        try:
            await self._idempotency_store.complete(
                app_id=event_provenance.app_id or "",
                tenant_id=event_provenance.tenant_id,
                workspace_id=event_provenance.workspace_id,
                idempotency_key_str=durable_key_str,
            )
        except Exception:
            logger.warning(
                "REACTION_IDEMPOTENCY_COMPLETE_FAILED: key=%s app=%s",
                durable_key_str,
                event_provenance.app_id,
                exc_info=True,
            )

    async def _durable_mark_failed(
        self,
        durable_key_str: str | None,
        event_provenance: ModuleEventProvenance,
    ) -> None:
        """Release the durable ledger slot on failure (retry-eligible)."""
        if durable_key_str is None or self._idempotency_store is None:
            return
        try:
            await self._idempotency_store.mark_failed(
                app_id=event_provenance.app_id or "",
                tenant_id=event_provenance.tenant_id,
                workspace_id=event_provenance.workspace_id,
                idempotency_key_str=durable_key_str,
            )
        except Exception:
            logger.warning(
                "REACTION_IDEMPOTENCY_FAIL_RELEASE_FAILED: key=%s app=%s",
                durable_key_str,
                event_provenance.app_id,
                exc_info=True,
            )

    def _handler_for(self, event_type: str) -> Callable[[dict[str, Any]], Awaitable[None]]:
        async def handle(envelope: dict[str, Any]) -> None:
            await self.handle_event(event_type, envelope)

        return handle

    def _find_notification_rule(
        self,
        *,
        module_id: str,
        notification_id: str,
        event_type: str,
    ) -> dict | None:
        if module_id and notification_id:
            rule = self._notifications_by_key.get((module_id, notification_id))
            if rule is not None:
                return rule
        for rule in self._notifications_by_event.get(event_type, []):
            if notification_id and rule.get("id") != notification_id:
                continue
            if module_id and rule.get("module_id") != module_id:
                continue
            return rule
        return None

    async def _emit_platform_reaction(
        self,
        reaction: dict,
        event_type: str,
        envelope: dict[str, Any],
        reaction_result: Any = None,
    ) -> None:
        if self._event_emitter is None:
            return
        target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
        target_kind = str(target.get("kind") or "unknown").strip() or "unknown"
        dispatch_result = reaction_result
        if target_kind == "capability":
            capability_id = str(
                target.get("capability_id")
                or target.get("id")
                or target.get("target")
                or ""
            ).strip()
            if capability_id and self._capability_invoker is not None:
                dispatch_result = await self._maybe_await(
                    self._capability_invoker(capability_id, envelope, reaction)
                )

        reaction_event_type = f"platform.reaction.{target_kind}_dispatched"
        reaction_payload = {
            "id": f"evt_{uuid4().hex}",
            "type": reaction_event_type,
            "version": 1,
            "occurred_at": _utc_now(),
            "source": {
                "layer": "platform",
                "module_id": reaction.get("module_id"),
                "reaction_id": reaction.get("id"),
            },
            "tenant": envelope.get("tenant") if isinstance(envelope.get("tenant"), dict) else {},
            "correlation": envelope.get("correlation") if isinstance(envelope.get("correlation"), dict) else {},
            "payload": {
                "event_type": event_type,
                "source_event": envelope,
                "target": target,
            },
            "visibility": "internal",
        }
        if dispatch_result is not None:
            reaction_payload["payload"]["result"] = dispatch_result
        await self._maybe_await(self._event_emitter(reaction_event_type, reaction_payload))

    async def _dispatch_service_adapter(
        self,
        reaction: dict,
        event_type: str,
        envelope: dict[str, Any],
        *,
        event_provenance: ModuleEventProvenance,
        reaction_provenance: ModuleReactionProvenance,
    ) -> Any:
        target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
        adapter_ref = str(target.get("adapter") or "").strip()
        adapter_method = str(target.get("adapter_method") or "").strip()
        if not adapter_ref or not adapter_method:
            logger.warning(
                "SERVICE_ADAPTER_TARGET_SKIPPED: reaction %r missing adapter or adapter_method",
                reaction.get("id"),
            )
            return None

        module_path, separator, attr_name = adapter_ref.partition(":")
        if not separator or not module_path or not attr_name:
            logger.warning(
                "SERVICE_ADAPTER_TARGET_SKIPPED: reaction %r has invalid adapter ref %r",
                reaction.get("id"),
                adapter_ref,
            )
            return None

        try:
            adapter_module = importlib.import_module(module_path)
            adapter_type = getattr(adapter_module, attr_name)
            adapter = adapter_type()
            method = getattr(adapter, adapter_method)
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
            return await self._maybe_await(
                _call_service_adapter(
                    method,
                    payload,
                    event_type,
                    envelope,
                    reaction,
                    event_provenance=event_provenance,
                    reaction_provenance=reaction_provenance,
                )
            )
        except Exception as exc:
            logger.error(
                "SERVICE_ADAPTER_DISPATCH_ERROR: %r.%r raised %s",
                adapter_ref,
                adapter_method,
                exc,
                exc_info=True,
            )
            return {"success": False, "error_code": "SERVICE_ADAPTER_DISPATCH_ERROR"}

    async def _dispatch_handler(
        self,
        reaction: dict,
        event_type: str,
        envelope: dict[str, Any],
        *,
        event_provenance: ModuleEventProvenance,
        reaction_provenance: ModuleReactionProvenance,
        permissions: Iterable[str] | None = None,
    ) -> tuple[str, str | None]:
        module_id = str(reaction.get("module_id") or "").strip()
        target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
        handler_method = str(target.get("handler_method") or "").strip()

        if not module_id or not handler_method:
            logger.warning(
                "HANDLER_TARGET_SKIPPED: reaction %r missing module_id or handler_method",
                reaction.get("id"),
            )
            return "skipped", "missing module_id or handler_method"

        handler = self._handlers_by_module.get(module_id)
        if handler is None:
            logger.warning("HANDLER_TARGET_SKIPPED: no handler registered for module %r", module_id)
            return "skipped", "handler not registered"

        method = getattr(handler, handler_method, None)
        if not callable(method):
            logger.warning(
                "HANDLER_TARGET_SKIPPED: %r.%r not found or not callable",
                module_id,
                handler_method,
            )
            return "skipped", "handler method not callable"

        tenant = envelope.get("tenant") if isinstance(envelope.get("tenant"), dict) else {}
        actor = envelope.get("actor") if isinstance(envelope.get("actor"), dict) else {}
        ctx = _ReactionCtx(
            app_id=str(tenant.get("app_id") or event_provenance.app_id or ""),
            tenant_id=str(tenant.get("tenant_id") or event_provenance.tenant_id or ""),
            user_id=str(actor.get("id") or event_provenance.actor_id or ""),
            event_emitter=self._event_emitter,
            event_provenance=event_provenance,
            reaction_provenance=reaction_provenance,
            permissions=permissions,
        )
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
        try:
            await self._maybe_await(method(ctx, **payload))
        except Exception as exc:
            logger.error(
                "HANDLER_DISPATCH_ERROR: %r.%r raised %s",
                module_id,
                handler_method,
                exc,
                exc_info=True,
            )
            return "failed", type(exc).__name__
        return "ok", None

    async def _emit_reaction_audit(self, audit: ModuleReactionAudit) -> None:
        """Emit payload-free reaction audit metadata without affecting fan-out."""

        record = audit.to_dict()
        logger.info(
            "MODULE_REACTION_AUDIT event=%s reaction=%s target=%s outcome=%s",
            audit.event.event_type,
            audit.reaction.reaction_id,
            audit.reaction.target_kind,
            audit.outcome,
            extra={"module_reaction_audit": record},
        )
        await get_platform_hooks().call_module_reaction_audit(audit)

    async def _create_notification(
        self,
        rule: dict,
        event_type: str,
        envelope: dict[str, Any],
    ) -> None:
        # Structured envelope: {tenant: {...}, payload: {...}, ...}
        # Flat envelope (module events): {session_id: ..., app_id: ..., amount: ..., ...}
        raw_payload = envelope.get("payload")
        raw_tenant = envelope.get("tenant")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        tenant = raw_tenant if isinstance(raw_tenant, dict) else {}

        # For flat event dicts emitted without tenant/payload nesting, fall back to
        # using the envelope scalars as the template rendering context and scope source.
        if not payload:
            payload = {
                k: v for k, v in envelope.items()
                if not isinstance(v, (dict, list))
                and k not in ("id", "version", "occurred_at", "visibility")
            }
        if not tenant and envelope.get("app_id"):
            tenant = {
                "app_id": str(envelope.get("app_id") or ""),
                "tenant_id": str(envelope.get("tenant_id") or ""),
            }

        template = rule.get("template") if isinstance(rule.get("template"), dict) else {}
        if not template and (rule.get("title") or rule.get("body")):
            template = {
                "title": rule.get("title"),
                "body": rule.get("body"),
            }

        # Build structured context from context_fields declared by the rule.
        # Only keys explicitly listed in context_fields are copied; secret-shaped
        # keys are stripped as a defence-in-depth guard even if listed.
        context_fields = rule.get("context_fields")
        if isinstance(context_fields, list) and context_fields:
            context: dict[str, Any] | None = {
                k: payload[k]
                for k in context_fields
                if isinstance(k, str) and k in payload and not _is_secret_context_key(k)
            } or None
        else:
            context = None

        audience = dict(rule.get("audience") if isinstance(rule.get("audience"), dict) else {})
        user_id_field = str(audience.get("user_id_field") or "").strip()
        if user_id_field and payload.get(user_id_field):
            raw_user_ids = payload.get(user_id_field)
            if isinstance(raw_user_ids, list):
                target_user_ids = [str(user_id).strip() for user_id in raw_user_ids if str(user_id).strip()]
            else:
                target_user_id = str(raw_user_ids).strip()
                target_user_ids = [target_user_id] if target_user_id else []
            if target_user_ids:
                existing_user_ids = audience.get("user_ids")
                if not isinstance(existing_user_ids, list):
                    existing_user_ids = []
                audience["user_ids"] = list(dict.fromkeys([*existing_user_ids, *target_user_ids]))

        record = {
            "notification_id": f"ntf_{uuid4().hex}",
            "rule_id": rule.get("id"),
            "module_id": rule.get("module_id"),
            "event_type": event_type,
            "source_event_id": envelope.get("id"),
            "app_id": tenant.get("app_id"),
            "tenant_id": tenant.get("tenant_id"),
            "actor": envelope.get("actor") if isinstance(envelope.get("actor"), dict) else None,
            "audience": audience,
            "channels": rule.get("channels") if isinstance(rule.get("channels"), list) else ["in_app"],
            "title": _render_template(str(template.get("title") or rule.get("id") or "Notification"), payload),
            "body": _render_template(str(template.get("body") or ""), payload),
            "status": "unread",
            "created_at": _utc_now(),
            "source_event": envelope,
        }
        if context is not None:
            record["context"] = context

        await self._store_notification(record)
        if self._event_emitter is not None:
            notification_event = {
                "id": f"evt_{uuid4().hex}",
                "type": "notification.created",
                "version": 1,
                "occurred_at": _utc_now(),
                "source": {
                    "layer": "platform",
                    "module_id": rule.get("module_id"),
                    "notification_id": record["notification_id"],
                },
                "tenant": tenant,
                "correlation": envelope.get("correlation") if isinstance(envelope.get("correlation"), dict) else {},
                "payload": record,
                "visibility": "internal",
            }
            await self._maybe_await(self._event_emitter("notification.created", notification_event))
            count_changed_event = {
                "id": f"evt_{uuid4().hex}",
                "type": "notification.count_changed",
                "version": 1,
                "occurred_at": _utc_now(),
                "source": {
                    "layer": "platform",
                    "module_id": rule.get("module_id"),
                },
                "tenant": tenant,
                "correlation": envelope.get("correlation") if isinstance(envelope.get("correlation"), dict) else {},
                "payload": {
                    "app_id": tenant.get("app_id"),
                    "module_id": rule.get("module_id"),
                },
                "visibility": "internal",
            }
            await self._maybe_await(self._event_emitter("notification.count_changed", count_changed_event))

    async def _store_notification(self, record: dict[str, Any]) -> None:
        try:
            if self._notification_store is not None:
                await self._maybe_await(self._notification_store(record))
                return

            from mozaiksai.core.core_config import get_mongo_client

            await get_mongo_client()["mozaiks"]["platform_notifications"].insert_one(dict(record))
        except Exception as exc:
            logger.debug("NOTIFICATION_STORE_SKIPPED: %s", exc)

    @staticmethod
    async def _maybe_await(result: Any) -> Any:
        if inspect.isawaitable(result):
            return await result
        return result


# Substrings that mark a payload key as potentially sensitive.
# context_fields that match any pattern are silently excluded from context.
_CONTEXT_SECRET_PATTERNS: tuple[str, ...] = (
    "_key",
    "_secret",
    "_token",
    "password",
    "credential",
    "payment_provider_",
    "idempotency_",
)


def _is_secret_context_key(key: str) -> bool:
    """Return True if *key* looks like a secret and must not be copied into context."""
    k = key.lower()
    return any(pat in k for pat in _CONTEXT_SECRET_PATTERNS)


def _notification_rule_matches(rule: dict[str, Any], envelope: dict[str, Any]) -> bool:
    """Evaluate the small deterministic condition shape supported by notifications.yaml."""
    condition = rule.get("condition")
    if not isinstance(condition, dict) or not condition:
        return True
    field = str(condition.get("field") or "").strip()
    if not field:
        return True
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else envelope
    value = payload.get(field) if isinstance(payload, dict) else None
    if "equals" in condition:
        return value == condition.get("equals")
    if "not_equals" in condition:
        return value != condition.get("not_equals")
    if "in" in condition and isinstance(condition.get("in"), list):
        return value in condition["in"]
    return True


def _call_service_adapter(
    method: Callable[..., Any],
    payload: dict[str, Any],
    event_type: str,
    envelope: dict[str, Any],
    reaction: dict[str, Any],
    *,
    event_provenance: ModuleEventProvenance | None = None,
    reaction_provenance: ModuleReactionProvenance | None = None,
) -> Any:
    """Call a contract-declared service adapter with the narrowest useful args."""
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return method(payload)

    params = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return method(
            payload=payload,
            event_type=event_type,
            envelope=envelope,
            reaction=reaction,
        )

    kwargs: dict[str, Any] = {}
    if "payload" in params:
        kwargs["payload"] = payload
    if "event_type" in params:
        kwargs["event_type"] = event_type
    if "envelope" in params:
        kwargs["envelope"] = envelope
    if "reaction" in params:
        kwargs["reaction"] = reaction
    if "event_provenance" in params:
        kwargs["event_provenance"] = event_provenance
    if "reaction_provenance" in params:
        kwargs["reaction_provenance"] = reaction_provenance
    if kwargs:
        return method(**kwargs)
    return method(payload)


def _render_template(template: str, payload: dict[str, Any]) -> str:
    """
    Render a notification template with payload substitution.

    Supports three syntaxes:
    - ``{{field}}``           — plain substitution
    - ``{{field | upper}}``   — field value uppercased
    - ``{% if field %}...{% endif %}`` — conditional block (rendered when field is truthy)

    Unknown fields are left as-is.
    """
    rendered = template

    # {% if field %}...{% endif %} conditionals (non-nested)
    def _replace_conditional(m: re.Match) -> str:
        field = m.group(1).strip()
        content = m.group(2)
        value = payload.get(field)
        return _render_template(content, payload) if value else ""

    rendered = re.sub(
        r"\{%\s*if\s+(\w+)\s*%\}(.*?)\{%\s*endif\s*%\}",
        _replace_conditional,
        rendered,
        flags=re.DOTALL,
    )

    # {{field | upper}} — apply upper filter
    def _replace_with_upper(m: re.Match) -> str:
        key = m.group(1).strip()
        return str(payload.get(key, "")).upper()

    rendered = re.sub(r"\{\{\s*(\w+)\s*\|\s*upper\s*\}\}", _replace_with_upper, rendered)

    # {{field}} — plain substitution (leave intact if key not found)
    def _replace_plain(m: re.Match) -> str:
        key = m.group(1).strip()
        val = payload.get(key)
        return str(val) if val is not None else m.group(0)

    rendered = re.sub(r"\{\{\s*(\w+)\s*\}\}", _replace_plain, rendered)

    return rendered


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
