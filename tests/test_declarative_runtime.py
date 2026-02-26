"""Tests for the declarative runtime system — dispatchers, YAML loader, event router.

Covers:
  - BusinessEventDispatcher (emit/register/reset)
  - ModularYAMLLoader (monolithic and modular layouts)
  - NotificationDispatcher (trigger matching, templating, condition eval, dispatch)
  - SubscriptionDispatcher (check_and_consume, warnings, blocking, feature check)
  - SettingsDispatcher (schema, get/set, validation)
  - EventRouter (wiring business events → notifications)
  - Port interfaces importability
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Contracts ports
# ---------------------------------------------------------------------------


def test_import_business_ports():
    from mozaiks.contracts.ports.business import (  # noqa: F401
        ConsumeResult,
        NotificationBackend,
        SettingsBackend,
        ThrottleBackend,
        UpdateResult,
        UsageBackend,
    )


def test_consume_result_defaults():
    from mozaiks.contracts.ports.business import ConsumeResult

    r = ConsumeResult(allowed=True)
    assert r.remaining == -1
    assert r.error is None


def test_update_result_defaults():
    from mozaiks.contracts.ports.business import UpdateResult

    r = UpdateResult(success=True)
    assert r.errors == {}


# ---------------------------------------------------------------------------
# BusinessEventDispatcher
# ---------------------------------------------------------------------------


class TestBusinessEventDispatcher:
    def setup_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()
        self.dispatcher = BusinessEventDispatcher.get_instance()

    def teardown_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    def test_singleton(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        assert BusinessEventDispatcher.get_instance() is self.dispatcher

    def test_reset(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        d1 = BusinessEventDispatcher.get_instance()
        BusinessEventDispatcher.reset()
        d2 = BusinessEventDispatcher.get_instance()
        assert d1 is not d2

    @pytest.mark.asyncio
    async def test_emit_calls_handler(self):
        received: list[dict] = []

        async def handler(event: dict[str, Any]) -> None:
            received.append(event)

        self.dispatcher.register("test.event", handler)
        await self.dispatcher.emit("test.event", {"key": "value"})

        assert len(received) == 1
        assert received[0]["event_type"] == "test.event"
        assert received[0]["key"] == "value"

    @pytest.mark.asyncio
    async def test_emit_no_handlers(self):
        # Should not raise
        await self.dispatcher.emit("unknown.event", {})

    @pytest.mark.asyncio
    async def test_handler_exception_logged_not_raised(self):
        async def bad_handler(event: dict[str, Any]) -> None:
            raise ValueError("boom")

        received: list[dict] = []

        async def good_handler(event: dict[str, Any]) -> None:
            received.append(event)

        self.dispatcher.register("test.event", bad_handler)
        self.dispatcher.register("test.event", good_handler)

        # Should not raise despite bad_handler
        await self.dispatcher.emit("test.event", {"x": 1})
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_unregister(self):
        received: list[dict] = []

        async def handler(event: dict[str, Any]) -> None:
            received.append(event)

        self.dispatcher.register("test.event", handler)
        self.dispatcher.unregister("test.event", handler)

        await self.dispatcher.emit("test.event", {})
        assert len(received) == 0


# ---------------------------------------------------------------------------
# ModularYAMLLoader
# ---------------------------------------------------------------------------


class TestModularYAMLLoader:
    def test_monolithic_load(self, tmp_path: Path):
        from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

        (tmp_path / "notifications.yaml").write_text(
            textwrap.dedent("""\
                notifications:
                  welcome:
                    trigger: lifecycle.workflow_first_run
                    channels: [in_app]
                    title: "Hello!"
                    message: "Welcome"
            """),
            encoding="utf-8",
        )

        loader = ModularYAMLLoader(tmp_path)
        data = loader.load_section("notifications")
        assert "notifications" in data
        assert "welcome" in data["notifications"]

    def test_modular_load(self, tmp_path: Path):
        from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

        (tmp_path / "notifications").mkdir()
        (tmp_path / "notifications" / "lifecycle.yaml").write_text(
            textwrap.dedent("""\
                notifications:
                  welcome:
                    trigger: lifecycle.workflow_first_run
                    channels: [in_app]
                    title: "Hello"
                    message: "Hi"
            """),
            encoding="utf-8",
        )
        (tmp_path / "notifications" / "custom.yaml").write_text(
            textwrap.dedent("""\
                notifications:
                  artifact_ready:
                    trigger: hook
                    hook_id: artifact_generated
                    channels: [in_app]
                    title: "Ready"
                    message: "Done"
            """),
            encoding="utf-8",
        )

        loader = ModularYAMLLoader(tmp_path)
        data = loader.load_section("notifications")
        assert "welcome" in data["notifications"]
        assert "artifact_ready" in data["notifications"]

    def test_load_all(self, tmp_path: Path):
        from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

        (tmp_path / "notifications.yaml").write_text("notifications: {}", encoding="utf-8")
        (tmp_path / "settings.yaml").write_text("settings: {}", encoding="utf-8")

        loader = ModularYAMLLoader(tmp_path)
        result = loader.load_all()
        assert "notifications" in result
        assert "settings" in result
        assert "subscription" not in result  # not present

    def test_missing_section_returns_empty(self, tmp_path: Path):
        from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

        loader = ModularYAMLLoader(tmp_path)
        assert loader.load_section("notifications") == {}

    def test_bad_yaml_returns_empty(self, tmp_path: Path):
        from mozaiks.core.workflows.yaml_loader import ModularYAMLLoader

        (tmp_path / "notifications.yaml").write_text("::not::valid", encoding="utf-8")
        loader = ModularYAMLLoader(tmp_path)
        data = loader.load_section("notifications")
        # Should not crash — returns whatever yaml parsed or {}
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# NotificationDispatcher
# ---------------------------------------------------------------------------


class _FakeNotificationBackend:
    """In-memory notification backend for testing."""

    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def send_in_app(self, user_id, title, message, priority, data):
        self.sent.append({"channel": "in_app", "user_id": user_id, "title": title, "message": message})
        return "id-1"

    async def send_email(self, user_id, title, message, priority, data):
        self.sent.append({"channel": "email", "user_id": user_id, "title": title, "message": message})
        return "id-2"

    async def send_push(self, user_id, title, message, priority, data):
        self.sent.append({"channel": "push", "user_id": user_id, "title": title})
        return "id-3"

    async def send_webhook(self, user_id, title, message, priority, data):
        self.sent.append({"channel": "webhook", "user_id": user_id, "title": title})
        return "id-4"


class TestNotificationDispatcher:
    def _make_workflow_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "notifications.yaml").write_text(
            textwrap.dedent("""\
                notifications:
                  welcome:
                    trigger: lifecycle.workflow_first_run
                    channels: [in_app]
                    title: "Welcome {{user_name}}!"
                    message: "Get started."
                    priority: normal

                  limit_warning:
                    trigger: entitlement.limit_warning
                    condition: "percent >= 80"
                    channels: [in_app, email]
                    title: "Approaching {{resource}} Limit"
                    message: "Used {{percent}}%"
                    priority: high
                    data_fields: [resource, percent]

                  hook_test:
                    trigger: hook
                    hook_id: my_hook
                    channels: [in_app]
                    title: "Hook fired"
                    message: "Via hook"
            """),
            encoding="utf-8",
        )
        return tmp_path

    @pytest.mark.asyncio
    async def test_trigger_simple(self, tmp_path: Path):
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher

        backend = _FakeNotificationBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = NotificationDispatcher("test", backend, workflow_dir=wdir)

        result = await d.trigger("lifecycle.workflow_first_run", "u1", {"user_name": "Alice"})
        assert "welcome" in result
        assert len(backend.sent) == 1
        assert backend.sent[0]["title"] == "Welcome Alice!"

    @pytest.mark.asyncio
    async def test_trigger_condition_pass(self, tmp_path: Path):
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher

        backend = _FakeNotificationBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = NotificationDispatcher("test", backend, workflow_dir=wdir)

        result = await d.trigger("entitlement.limit_warning", "u1", {"percent": 85, "resource": "tokens"})
        assert "limit_warning" in result
        assert len(backend.sent) == 2  # in_app + email

    @pytest.mark.asyncio
    async def test_trigger_condition_fail(self, tmp_path: Path):
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher

        backend = _FakeNotificationBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = NotificationDispatcher("test", backend, workflow_dir=wdir)

        result = await d.trigger("entitlement.limit_warning", "u1", {"percent": 50, "resource": "tokens"})
        assert result == []

    @pytest.mark.asyncio
    async def test_hook_trigger(self, tmp_path: Path):
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher

        backend = _FakeNotificationBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = NotificationDispatcher("test", backend, workflow_dir=wdir)

        result = await d.trigger("hook.my_hook", "u1", {})
        assert "hook_test" in result

    @pytest.mark.asyncio
    async def test_no_match(self, tmp_path: Path):
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher

        backend = _FakeNotificationBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = NotificationDispatcher("test", backend, workflow_dir=wdir)

        result = await d.trigger("unknown.trigger", "u1", {})
        assert result == []


# ---------------------------------------------------------------------------
# SubscriptionDispatcher
# ---------------------------------------------------------------------------


class _FakeUsageBackend:
    """In-memory usage backend for testing."""

    def __init__(self):
        self.usage: dict[str, dict[str, int]] = {}
        self.plans: dict[str, str] = {}

    async def get_usage(self, user_id: str, resource: str) -> int:
        return self.usage.get(user_id, {}).get(resource, 0)

    async def increment_usage(self, user_id: str, resource: str, amount: int) -> int:
        self.usage.setdefault(user_id, {})
        self.usage[user_id][resource] = self.usage[user_id].get(resource, 0) + amount
        return self.usage[user_id][resource]

    async def get_user_plan(self, user_id: str) -> str:
        return self.plans.get(user_id, "free")


class TestSubscriptionDispatcher:
    def _make_workflow_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "subscription.yaml").write_text(
            textwrap.dedent("""\
                resources:
                  tokens:
                    name: "AI Tokens"
                    unit: tokens
                  messages:
                    name: "Messages"
                    unit: count

                plans:
                  free:
                    name: "Free"
                    limits:
                      tokens: 100
                      messages: 10
                    features:
                      - basic_chat
                  pro:
                    name: "Pro"
                    limits:
                      tokens: -1
                      messages: 1000
                    features:
                      - basic_chat
                      - advanced_tools

                enforcement:
                  on_limit_reached:
                    tokens: block
                    messages: warn
                  warning_thresholds: [80, 90]
            """),
            encoding="utf-8",
        )
        return tmp_path

    def setup_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    def teardown_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    @pytest.mark.asyncio
    async def test_consume_within_limit(self, tmp_path: Path):
        from mozaiks.core.events.subscription_dispatcher import SubscriptionDispatcher

        backend = _FakeUsageBackend()
        backend.plans["u1"] = "free"
        wdir = self._make_workflow_dir(tmp_path)
        d = SubscriptionDispatcher("test", backend, workflow_dir=wdir)

        result = await d.check_and_consume("u1", "tokens", 50)
        assert result.allowed is True
        assert result.remaining == 50

    @pytest.mark.asyncio
    async def test_consume_blocked_at_limit(self, tmp_path: Path):
        from mozaiks.core.events.subscription_dispatcher import SubscriptionDispatcher

        backend = _FakeUsageBackend()
        backend.plans["u1"] = "free"
        backend.usage["u1"] = {"tokens": 100}
        wdir = self._make_workflow_dir(tmp_path)
        d = SubscriptionDispatcher("test", backend, workflow_dir=wdir)

        result = await d.check_and_consume("u1", "tokens", 1)
        assert result.allowed is False
        assert "limit reached" in (result.error or "")

    @pytest.mark.asyncio
    async def test_consume_warn_not_block(self, tmp_path: Path):
        from mozaiks.core.events.subscription_dispatcher import SubscriptionDispatcher

        backend = _FakeUsageBackend()
        backend.plans["u1"] = "free"
        backend.usage["u1"] = {"messages": 10}
        wdir = self._make_workflow_dir(tmp_path)
        d = SubscriptionDispatcher("test", backend, workflow_dir=wdir)

        result = await d.check_and_consume("u1", "messages", 1)
        # "warn" behaviour — allowed but limit was emitted
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_unlimited_plan(self, tmp_path: Path):
        from mozaiks.core.events.subscription_dispatcher import SubscriptionDispatcher

        backend = _FakeUsageBackend()
        backend.plans["u1"] = "pro"
        wdir = self._make_workflow_dir(tmp_path)
        d = SubscriptionDispatcher("test", backend, workflow_dir=wdir)

        result = await d.check_and_consume("u1", "tokens", 999999)
        assert result.allowed is True
        assert result.remaining == -1

    @pytest.mark.asyncio
    async def test_warning_threshold_emitted(self, tmp_path: Path):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher
        from mozaiks.core.events.subscription_dispatcher import SubscriptionDispatcher

        events: list[dict] = []

        async def capture(event: dict[str, Any]) -> None:
            events.append(event)

        bd = BusinessEventDispatcher.get_instance()
        bd.register("subscription.limit_warning", capture)

        backend = _FakeUsageBackend()
        backend.plans["u1"] = "free"
        backend.usage["u1"] = {"tokens": 79}
        wdir = self._make_workflow_dir(tmp_path)
        d = SubscriptionDispatcher("test", backend, workflow_dir=wdir)

        await d.check_and_consume("u1", "tokens", 2)
        # 79 → 81 crosses the 80 threshold
        assert len(events) == 1
        assert events[0]["threshold"] == 80

    def test_has_feature(self, tmp_path: Path):
        from mozaiks.core.events.subscription_dispatcher import SubscriptionDispatcher

        backend = _FakeUsageBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SubscriptionDispatcher("test", backend, workflow_dir=wdir)

        assert d.has_feature("free", "basic_chat") is True
        assert d.has_feature("free", "advanced_tools") is False
        assert d.has_feature("pro", "advanced_tools") is True
        assert d.has_feature("nonexistent", "basic_chat") is False


# ---------------------------------------------------------------------------
# SettingsDispatcher
# ---------------------------------------------------------------------------


class _FakeSettingsBackend:
    """In-memory settings backend for testing."""

    def __init__(self):
        self.data: dict[str, dict[str, dict[str, Any]]] = {}

    async def get_settings(self, user_id: str, namespace: str) -> dict[str, Any]:
        return dict(self.data.get(user_id, {}).get(namespace, {}))

    async def update_settings(self, user_id: str, namespace: str, updates: dict[str, Any]) -> None:
        self.data.setdefault(user_id, {}).setdefault(namespace, {}).update(updates)


class TestSettingsDispatcher:
    def _make_workflow_dir(self, tmp_path: Path) -> Path:
        (tmp_path / "settings.yaml").write_text(
            textwrap.dedent("""\
                settings:
                  preferences:
                    label: "Preferences"
                    description: "General preferences"
                    fields:
                      theme:
                        type: select
                        label: "Theme"
                        default: "system"
                        options:
                          - value: "light"
                            label: "Light"
                          - value: "dark"
                            label: "Dark"
                          - value: "system"
                            label: "System Default"
                      notifications_enabled:
                        type: boolean
                        label: "Enable Notifications"
                        default: true

                  ai_behavior:
                    label: "AI Behavior"
                    requires_feature: advanced_settings
                    fields:
                      creativity:
                        type: slider
                        label: "Creativity"
                        default: 50
                        min_value: 0
                        max_value: 100
                        step: 10
            """),
            encoding="utf-8",
        )
        return tmp_path

    def setup_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    def teardown_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    @pytest.mark.asyncio
    async def test_get_schema_all_groups(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        schema = await d.get_settings_schema("u1", "pro")
        group_ids = [g["id"] for g in schema["groups"]]
        assert "preferences" in group_ids
        assert "ai_behavior" in group_ids

    @pytest.mark.asyncio
    async def test_get_schema_feature_gated(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)

        # Feature checker that denies advanced_settings
        d = SettingsDispatcher(
            "test",
            backend,
            workflow_dir=wdir,
            feature_checker=lambda plan, feat: False,
        )

        schema = await d.get_settings_schema("u1", "free")
        group_ids = [g["id"] for g in schema["groups"]]
        assert "preferences" in group_ids
        assert "ai_behavior" not in group_ids

    @pytest.mark.asyncio
    async def test_get_defaults(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        values = await d.get_settings_values("u1")
        assert values["theme"] == "system"
        assert values["notifications_enabled"] is True
        assert values["creativity"] == 50

    @pytest.mark.asyncio
    async def test_update_valid(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        result = await d.update_settings("u1", {"theme": "dark"})
        assert result.success is True

        values = await d.get_settings_values("u1")
        assert values["theme"] == "dark"

    @pytest.mark.asyncio
    async def test_update_invalid_select(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        result = await d.update_settings("u1", {"theme": "neon"})
        assert result.success is False
        assert "theme" in result.errors

    @pytest.mark.asyncio
    async def test_update_invalid_slider_range(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        result = await d.update_settings("u1", {"creativity": 150})
        assert result.success is False
        assert "creativity" in result.errors

    @pytest.mark.asyncio
    async def test_update_unknown_field(self, tmp_path: Path):
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        result = await d.update_settings("u1", {"nonexistent": "x"})
        assert result.success is False
        assert "nonexistent" in result.errors

    @pytest.mark.asyncio
    async def test_update_emits_event(self, tmp_path: Path):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher
        from mozaiks.core.events.settings_dispatcher import SettingsDispatcher

        events: list[dict] = []

        async def capture(event: dict[str, Any]) -> None:
            events.append(event)

        bd = BusinessEventDispatcher.get_instance()
        bd.register("settings.updated", capture)

        backend = _FakeSettingsBackend()
        wdir = self._make_workflow_dir(tmp_path)
        d = SettingsDispatcher("test", backend, workflow_dir=wdir)

        await d.update_settings("u1", {"theme": "dark"})
        assert len(events) == 1
        assert events[0]["fields"] == ["theme"]


# ---------------------------------------------------------------------------
# EventRouter
# ---------------------------------------------------------------------------


class _FakeThrottleBackend:
    """In-memory throttle backend for testing."""

    def __init__(self):
        self.throttled: set[str] = set()

    async def is_throttled(self, key: str) -> bool:
        return key in self.throttled

    async def set_throttle(self, key: str, ttl_seconds: int) -> None:
        self.throttled.add(key)


class TestEventRouter:
    def setup_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    def teardown_method(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher

        BusinessEventDispatcher.reset()

    @pytest.mark.asyncio
    async def test_routes_subscription_to_notification(self, tmp_path: Path):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher
        from mozaiks.core.events.router import EventRouter

        # Set up notification config
        (tmp_path / "notifications.yaml").write_text(
            textwrap.dedent("""\
                notifications:
                  limit_warn:
                    trigger: entitlement.limit_warning
                    channels: [in_app]
                    title: "Limit warning"
                    message: "Watch out"
            """),
            encoding="utf-8",
        )

        backend = _FakeNotificationBackend()
        notif = NotificationDispatcher("test_wf", backend, workflow_dir=tmp_path)

        router = EventRouter(business_dispatcher=BusinessEventDispatcher.get_instance())
        router.register_notification_dispatcher("test_wf", notif)
        router.start()

        # Emit a business event that should route to the notification
        await BusinessEventDispatcher.get_instance().emit(
            "subscription.limit_warning",
            {"user_id": "u1", "workflow_name": "test_wf", "resource": "tokens", "percent": 85},
        )

        assert len(backend.sent) == 1
        assert backend.sent[0]["title"] == "Limit warning"

    @pytest.mark.asyncio
    async def test_throttled_event_suppressed(self, tmp_path: Path):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher
        from mozaiks.core.events.notification_dispatcher import NotificationDispatcher
        from mozaiks.core.events.router import EventRouter

        (tmp_path / "notifications.yaml").write_text(
            textwrap.dedent("""\
                notifications:
                  limit_warn:
                    trigger: entitlement.limit_warning
                    channels: [in_app]
                    title: "Limit"
                    message: "msg"
            """),
            encoding="utf-8",
        )

        nbackend = _FakeNotificationBackend()
        notif = NotificationDispatcher("test_wf", nbackend, workflow_dir=tmp_path)

        throttle = _FakeThrottleBackend()
        throttle.throttled.add("u1:entitlement.limit_warning")

        router = EventRouter(
            throttle_backend=throttle,
            business_dispatcher=BusinessEventDispatcher.get_instance(),
        )
        router.register_notification_dispatcher("test_wf", notif)
        router.start()

        await BusinessEventDispatcher.get_instance().emit(
            "subscription.limit_warning",
            {"user_id": "u1", "workflow_name": "test_wf"},
        )

        # Should be suppressed
        assert len(nbackend.sent) == 0

    @pytest.mark.asyncio
    async def test_start_stop(self):
        from mozaiks.core.events.dispatcher import BusinessEventDispatcher
        from mozaiks.core.events.router import EventRouter

        router = EventRouter(business_dispatcher=BusinessEventDispatcher.get_instance())
        router.start()
        assert router._started is True
        router.stop()
        assert router._started is False


# ---------------------------------------------------------------------------
# Import smoke tests for new public API surface
# ---------------------------------------------------------------------------


def test_import_core_events_package():
    from mozaiks.core.events import (  # noqa: F401
        BusinessEventDispatcher,
        EventRouter,
        NotificationConfig,
        NotificationDispatcher,
        SettingsDispatcher,
        SubscriptionConfig,
        SubscriptionDispatcher,
    )


def test_import_yaml_loader():
    from mozaiks.core.workflows import ModularYAMLLoader  # noqa: F401


def test_import_via_contracts_ports():
    from mozaiks.contracts.ports import (  # noqa: F401
        ConsumeResult,
        NotificationBackend,
        SettingsBackend,
        ThrottleBackend,
        UpdateResult,
        UsageBackend,
    )
