from __future__ import annotations

"""Platform-owned routing for canonical module events.

The runtime dispatcher transports events. This router interprets app/module
manifests loaded by the platform host:

- subscriptions.yaml maps events to reactions
- notifications.yaml maps events to notification intents

This keeps module event meaning above the runtime kernel.
"""

import inspect
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

from logs.logging_config import get_workflow_logger
from mozaiksai.core.runtime.app.module_loader import LoadedModule

logger = get_workflow_logger("module_event_router")

EventEmitter = Callable[[str, Dict[str, Any]], Awaitable[Any] | Any]
NotificationStore = Callable[[Dict[str, Any]], Awaitable[Any] | Any]
CapabilityInvoker = Callable[[str, Dict[str, Any], Dict[str, Any]], Awaitable[Any] | Any]


class ModuleEventRouter:
    """Routes loaded module events to platform-level reactions."""

    def __init__(
        self,
        modules: Iterable[LoadedModule],
        *,
        event_emitter: Optional[EventEmitter] = None,
        notification_store: Optional[NotificationStore] = None,
        capability_invoker: Optional[CapabilityInvoker] = None,
    ) -> None:
        self._event_emitter = event_emitter
        self._notification_store = notification_store
        self._capability_invoker = capability_invoker
        self._subscriptions_by_event: Dict[str, List[dict]] = defaultdict(list)
        self._notifications_by_event: Dict[str, List[dict]] = defaultdict(list)
        self._notifications_by_key: Dict[tuple[str, str], dict] = {}
        self._index_modules(modules)

    @property
    def event_types(self) -> List[str]:
        return sorted(set(self._subscriptions_by_event) | set(self._notifications_by_event))

    def register(self, dispatcher: Any) -> int:
        """Register this router with the runtime dispatcher."""
        count = 0
        for event_type in self.event_types:
            dispatcher.register_handler(event_type, self._handler_for(event_type))
            count += 1
        if count:
            logger.info("MODULE_EVENT_ROUTER_READY: %s event type(s)", count)
        return count

    async def handle_event(self, event_type: str, envelope: Dict[str, Any]) -> None:
        """Handle one canonical module event envelope."""
        emitted_notifications: set[tuple[str, str]] = set()

        for subscription in self._subscriptions_by_event.get(event_type, []):
            target = subscription.get("target") if isinstance(subscription.get("target"), dict) else {}
            target_kind = str(target.get("kind") or "").strip()
            if target_kind == "notification":
                notification_id = str(target.get("notification_id") or "").strip()
                rule = self._find_notification_rule(
                    module_id=str(subscription.get("module_id") or ""),
                    notification_id=notification_id,
                    event_type=event_type,
                )
                if rule is not None:
                    key = (str(rule.get("module_id") or ""), str(rule.get("id") or ""))
                    await self._create_notification(rule, event_type, envelope)
                    emitted_notifications.add(key)
            elif target_kind:
                await self._emit_platform_reaction(subscription, event_type, envelope)

        for rule in self._notifications_by_event.get(event_type, []):
            key = (str(rule.get("module_id") or ""), str(rule.get("id") or ""))
            if key not in emitted_notifications:
                await self._create_notification(rule, event_type, envelope)

    def _index_modules(self, modules: Iterable[LoadedModule]) -> None:
        for module in modules:
            module_id = module.name
            for raw_subscription in module.manifests.subscriptions.subscriptions:
                if not isinstance(raw_subscription, dict):
                    continue
                event_type = str(raw_subscription.get("event_type") or "").strip()
                if not event_type:
                    continue
                subscription = dict(raw_subscription)
                subscription["module_id"] = module_id
                self._subscriptions_by_event[event_type].append(subscription)

            for raw_rule in module.manifests.notifications.notifications:
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

    def _handler_for(self, event_type: str) -> Callable[[Dict[str, Any]], Awaitable[None]]:
        async def handle(envelope: Dict[str, Any]) -> None:
            await self.handle_event(event_type, envelope)

        return handle

    def _find_notification_rule(
        self,
        *,
        module_id: str,
        notification_id: str,
        event_type: str,
    ) -> Optional[dict]:
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
        subscription: dict,
        event_type: str,
        envelope: Dict[str, Any],
    ) -> None:
        if self._event_emitter is None:
            return
        target = subscription.get("target") if isinstance(subscription.get("target"), dict) else {}
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
                    self._capability_invoker(capability_id, envelope, subscription)
                )

        reaction_event_type = f"platform.subscription.{target_kind}_requested"
        reaction_payload = {
            "id": f"evt_{uuid4().hex}",
            "type": reaction_event_type,
            "version": 1,
            "occurred_at": _utc_now(),
            "source": {
                "layer": "platform",
                "module_id": subscription.get("module_id"),
                "subscription_id": subscription.get("id"),
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

    async def _create_notification(
        self,
        rule: dict,
        event_type: str,
        envelope: Dict[str, Any],
    ) -> None:
        payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else {}
        tenant = envelope.get("tenant") if isinstance(envelope.get("tenant"), dict) else {}
        template = rule.get("template") if isinstance(rule.get("template"), dict) else {}
        record = {
            "notification_id": f"ntf_{uuid4().hex}",
            "rule_id": rule.get("id"),
            "module_id": rule.get("module_id"),
            "event_type": event_type,
            "source_event_id": envelope.get("id"),
            "app_id": tenant.get("app_id"),
            "tenant_id": tenant.get("tenant_id"),
            "actor": envelope.get("actor") if isinstance(envelope.get("actor"), dict) else None,
            "audience": rule.get("audience") if isinstance(rule.get("audience"), dict) else {},
            "channels": rule.get("channels") if isinstance(rule.get("channels"), list) else ["in_app"],
            "title": _render_template(str(template.get("title") or rule.get("id") or "Notification"), payload),
            "body": _render_template(str(template.get("body") or ""), payload),
            "status": "unread",
            "created_at": _utc_now(),
            "source_event": envelope,
        }

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

    async def _store_notification(self, record: Dict[str, Any]) -> None:
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


def _render_template(template: str, payload: Dict[str, Any]) -> str:
    rendered = template
    for key, value in payload.items():
        rendered = rendered.replace("{payload." + str(key) + "}", str(value))
    return rendered


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
