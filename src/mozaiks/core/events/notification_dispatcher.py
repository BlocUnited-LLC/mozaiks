"""Notification dispatcher — YAML-driven notification delivery.

Reads ``notifications.yaml`` (or ``notifications/*.yaml``) from a workflow
directory, matches events to triggers, templates messages, and dispatches
to configured channels via the :class:`NotificationBackend` port.

Layer: ``core.events`` (shared runtime).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mozaiks.contracts.ports.business import NotificationBackend
from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

logger = logging.getLogger(__name__)

_TEMPLATE_VAR = re.compile(r"\{\{(\w+)\}\}")


@dataclass
class NotificationConfig:
    """Parsed representation of a single notification entry."""

    name: str
    trigger: str
    channels: list[str]
    title: str
    message: str
    priority: str = "normal"
    data_fields: list[str] = field(default_factory=list)
    condition: str | None = None
    hook_id: str | None = None


class NotificationDispatcher:
    """Dispatches notifications based on YAML declarations."""

    def __init__(
        self,
        workflow_name: str,
        backend: NotificationBackend,
        *,
        workflow_dir: Path | None = None,
    ) -> None:
        self.workflow_name = workflow_name
        self._backend = backend
        self._configs: dict[str, NotificationConfig] = {}

        if workflow_dir is not None:
            self._load(workflow_dir)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self, workflow_dir: Path) -> None:
        loader = ModularYAMLLoader(workflow_dir)
        raw = loader.load_section("notifications")
        entries = raw.get("notifications", raw)

        for name, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            try:
                self._configs[name] = NotificationConfig(
                    name=name,
                    trigger=entry["trigger"],
                    channels=entry.get("channels", ["in_app"]),
                    title=entry.get("title", ""),
                    message=entry.get("message", ""),
                    priority=entry.get("priority", "normal"),
                    data_fields=entry.get("data_fields", []),
                    condition=entry.get("condition"),
                    hook_id=entry.get("hook_id"),
                )
            except KeyError:
                logger.warning("Skipping invalid notification entry: %s", name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def trigger(
        self,
        trigger_type: str,
        user_id: str,
        context: dict[str, Any],
    ) -> list[str]:
        """Find matching notifications and dispatch them.

        Returns the list of notification names that were dispatched.
        """
        dispatched: list[str] = []

        for config in self._configs.values():
            if not self._matches_trigger(config, trigger_type):
                continue
            if not self._evaluate_condition(config, context):
                continue

            title = self._template(config.title, context)
            message = self._template(config.message, context)
            data = self._extract_data_fields(config, context)

            for channel in config.channels:
                await self._dispatch_channel(
                    channel=channel,
                    user_id=user_id,
                    title=title,
                    message=message,
                    priority=config.priority,
                    data=data,
                )

            dispatched.append(config.name)

        return dispatched

    # ------------------------------------------------------------------
    # Trigger matching
    # ------------------------------------------------------------------

    @staticmethod
    def _matches_trigger(config: NotificationConfig, trigger_type: str) -> bool:
        if config.trigger == "hook":
            return trigger_type == f"hook.{config.hook_id}"
        return config.trigger == trigger_type

    # ------------------------------------------------------------------
    # Condition evaluation (safe, simple comparisons)
    # ------------------------------------------------------------------

    @staticmethod
    def _evaluate_condition(config: NotificationConfig, context: dict[str, Any]) -> bool:
        if not config.condition:
            return True

        # Parse simple expressions like "percent >= 80" or "days_remaining == 3"
        for op, func in [
            (">=", lambda a, b: a >= b),
            ("<=", lambda a, b: a <= b),
            ("==", lambda a, b: a == b),
            ("!=", lambda a, b: a != b),
            (">", lambda a, b: a > b),
            ("<", lambda a, b: a < b),
        ]:
            if op in config.condition:
                parts = config.condition.split(op, 1)
                if len(parts) == 2:
                    var_name = parts[0].strip()
                    try:
                        threshold = float(parts[1].strip())
                    except ValueError:
                        return False
                    var_value = context.get(var_name)
                    if var_value is None:
                        return False
                    try:
                        return func(float(var_value), threshold)
                    except (ValueError, TypeError):
                        return False
                break

        return True

    # ------------------------------------------------------------------
    # Templating
    # ------------------------------------------------------------------

    @staticmethod
    def _template(template: str, context: dict[str, Any]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1)
            value = context.get(key, "")
            return str(value)

        return _TEMPLATE_VAR.sub(_replace, template)

    @staticmethod
    def _extract_data_fields(
        config: NotificationConfig,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        if not config.data_fields:
            return {}
        return {k: context.get(k) for k in config.data_fields}

    # ------------------------------------------------------------------
    # Channel dispatch
    # ------------------------------------------------------------------

    async def _dispatch_channel(
        self,
        *,
        channel: str,
        user_id: str,
        title: str,
        message: str,
        priority: str,
        data: dict[str, Any],
    ) -> None:
        try:
            if channel == "in_app":
                await self._backend.send_in_app(user_id, title, message, priority, data)
            elif channel == "email":
                await self._backend.send_email(user_id, title, message, priority, data)
            elif channel == "push":
                await self._backend.send_push(user_id, title, message, priority, data)
            elif channel == "webhook":
                await self._backend.send_webhook(user_id, title, message, priority, data)
            else:
                logger.warning("Unknown notification channel: %s", channel)
        except Exception:
            logger.exception(
                "Failed to send %s notification to %s via %s",
                title,
                user_id,
                channel,
            )
