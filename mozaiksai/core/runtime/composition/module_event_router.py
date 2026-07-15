from __future__ import annotations

"""Platform-owned routing for canonical module events.

The runtime dispatcher transports events. This router interprets app/module
manifests loaded by the platform host:

- reactions.yaml maps events to module-owned reactions
- notifications.yaml maps events to notification intents

This keeps module event meaning above the runtime kernel.
"""

import inspect
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from logs.logging_config import get_workflow_logger
from mozaiksai.core.runtime.app.module_loader import LoadedModule

logger = get_workflow_logger("module_event_router")

EventEmitter = Callable[[str, dict[str, Any]], Awaitable[Any] | Any]
NotificationStore = Callable[[dict[str, Any]], Awaitable[Any] | Any]
CapabilityInvoker = Callable[[str, dict[str, Any], dict[str, Any]], Awaitable[Any] | Any]


class _ReactionCtx:
    """Minimal context passed to handler methods during reaction dispatch."""

    def __init__(self, *, app_id: str, tenant_id: str, user_id: str, event_emitter: EventEmitter | None) -> None:
        self.app_id = app_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        self._event_emitter = event_emitter

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._event_emitter is not None:
            await ModuleEventRouter._maybe_await(self._event_emitter(event_type, payload))


class ModuleEventRouter:
    """Routes loaded module events to platform-level reactions."""

    def __init__(
        self,
        modules: Iterable[LoadedModule],
        *,
        event_emitter: EventEmitter | None = None,
        notification_store: NotificationStore | None = None,
        capability_invoker: CapabilityInvoker | None = None,
    ) -> None:
        self._event_emitter = event_emitter
        self._notification_store = notification_store
        self._capability_invoker = capability_invoker
        self._reactions_by_event: dict[str, list[dict]] = defaultdict(list)
        self._notifications_by_event: dict[str, list[dict]] = defaultdict(list)
        self._notifications_by_key: dict[tuple[str, str], dict] = {}
        self._handlers_by_module: dict[str, Any] = {}
        self._index_modules(modules)

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

        for reaction in self._reactions_by_event.get(event_type, []):
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
                        continue
                    key = (str(rule.get("module_id") or ""), str(rule.get("id") or ""))
                    await self._create_notification(rule, event_type, envelope)
                    emitted_notifications.add(key)
            elif target_kind == "handler":
                await self._dispatch_handler(reaction, event_type, envelope)
            elif target_kind:
                await self._emit_platform_reaction(reaction, event_type, envelope)

        for rule in self._notifications_by_event.get(event_type, []):
            key = (str(rule.get("module_id") or ""), str(rule.get("id") or ""))
            if key not in emitted_notifications and _notification_rule_matches(rule, envelope):
                await self._create_notification(rule, event_type, envelope)

    def _index_modules(self, modules: Iterable[LoadedModule]) -> None:
        for module in modules:
            module_id = module.name
            self._handlers_by_module[module_id] = module.handler
            if module.manifests.reactions is not None:
                for reaction_model in module.manifests.reactions.reactions:
                    event_type = str(reaction_model.event_type or "").strip()
                    if not event_type:
                        continue
                    reaction = reaction_model.model_dump(mode="python", exclude_none=True)
                    reaction["module_id"] = module_id
                    self._reactions_by_event[event_type].append(reaction)

            if module.manifests.notifications is not None:
                for raw_rule in module.manifests.notifications.notifications:
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
    ) -> None:
        if self._event_emitter is None:
            return
        target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
        target_kind = str(target.get("kind") or "unknown").strip() or "unknown"
        capability_result = None
        if target_kind == "capability":
            capability_id = str(
                target.get("capability_id")
                or target.get("id")
                or target.get("target")
                or ""
            ).strip()
            if capability_id and self._capability_invoker is not None:
                capability_result = await self._maybe_await(
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
        if capability_result is not None:
            reaction_payload["payload"]["result"] = capability_result
        await self._maybe_await(self._event_emitter(reaction_event_type, reaction_payload))

    async def _dispatch_handler(
        self,
        reaction: dict,
        event_type: str,
        envelope: dict[str, Any],
    ) -> None:
        module_id = str(reaction.get("module_id") or "").strip()
        target = reaction.get("target") if isinstance(reaction.get("target"), dict) else {}
        handler_method = str(target.get("handler_method") or "").strip()

        if not module_id or not handler_method:
            logger.warning(
                "HANDLER_TARGET_SKIPPED: reaction %r missing module_id or handler_method",
                reaction.get("id"),
            )
            return

        handler = self._handlers_by_module.get(module_id)
        if handler is None:
            logger.warning("HANDLER_TARGET_SKIPPED: no handler registered for module %r", module_id)
            return

        method = getattr(handler, handler_method, None)
        if not callable(method):
            logger.warning(
                "HANDLER_TARGET_SKIPPED: %r.%r not found or not callable",
                module_id,
                handler_method,
            )
            return

        tenant = envelope.get("tenant") if isinstance(envelope.get("tenant"), dict) else {}
        actor = envelope.get("actor") if isinstance(envelope.get("actor"), dict) else {}
        ctx = _ReactionCtx(
            app_id=str(tenant.get("app_id") or ""),
            tenant_id=str(tenant.get("tenant_id") or ""),
            user_id=str(actor.get("id") or ""),
            event_emitter=self._event_emitter,
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
