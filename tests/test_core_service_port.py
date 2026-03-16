# ==============================================================================
# FILE: tests/test_core_service_port.py
# DESCRIPTION: Tests for Phase 2 Integration Bridge:
#              - CoreServicePort protocol compliance
#              - CoreServiceClient adapter structure
#              - core_bridge tool functions
#              - Auth middleware
#              - Route module mounting
#
# NOTE: mozaiksai.core.__init__.py eagerly imports workflow/AG2 modules.
#       Tests that only need ports/adapters use importlib to import the
#       specific submodule without triggering the AG2 dependency chain.
# ==============================================================================
from __future__ import annotations

import os
import sys
import pytest
import importlib
import importlib.util

# Ensure dev mode for auth stubs
os.environ.setdefault("ENV", "development")
os.environ.setdefault("MOZAIKS_APP_ID", "test_app")


def _import_module_directly(module_name: str):
    """Import a module by dotted name without triggering parent __init__ side-effects.
    
    This is needed because mozaiksai.core.__init__.py eagerly imports AG2-dependent
    modules. We bypass that by importing the leaf module directly from its file path.
    """
    if module_name in sys.modules:
        return sys.modules[module_name]

    parts = module_name.split(".")
    # Build file path from workspace root
    workspace = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(workspace, *parts) + ".py"
    
    if not os.path.exists(file_path):
        # Try as package (__init__.py)
        file_path = os.path.join(workspace, *parts, "__init__.py")
    
    if not os.path.exists(file_path):
        raise ImportError(f"Cannot find module file for {module_name}")
    
    # Ensure parent packages exist in sys.modules as namespace stubs
    for i in range(1, len(parts)):
        parent_name = ".".join(parts[:i])
        if parent_name not in sys.modules:
            parent_path = os.path.join(workspace, *parts[:i])
            if os.path.isdir(parent_path):
                import types
                pkg = types.ModuleType(parent_name)
                pkg.__path__ = [parent_path]
                pkg.__package__ = parent_name
                sys.modules[parent_name] = pkg

    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# 1. CoreServicePort protocol + data classes
# ===========================================================================

class TestCoreServicePort:
    """Verify the CoreServicePort protocol and its data classes."""

    def _get_port_module(self):
        return _import_module_directly("mozaiksai.core.ports.core_service")

    def test_port_importable(self):
        mod = self._get_port_module()
        assert hasattr(mod, "CoreServicePort")

    def test_port_is_protocol(self):
        from typing import Protocol
        mod = self._get_port_module()
        assert issubclass(mod.CoreServicePort, Protocol)

    def test_module_request_fields(self):
        mod = self._get_port_module()
        req = mod.ModuleRequest(
            module_name="admin_portal",
            action="get_dashboard",
            user_id="u1",
            app_id="app1",
            payload={"key": "value"},
        )
        assert req.module_name == "admin_portal"
        assert req.action == "get_dashboard"
        assert req.user_id == "u1"
        assert req.payload == {"key": "value"}

    def test_module_request_frozen(self):
        mod = self._get_port_module()
        req = mod.ModuleRequest(module_name="m", action="a", user_id="u", app_id="a")
        with pytest.raises(AttributeError):
            req.module_name = "other"

    def test_module_result_success(self):
        mod = self._get_port_module()
        r = mod.ModuleResult(success=True, data={"count": 5})
        assert r.success is True
        assert r.data == {"count": 5}
        assert r.error is None

    def test_module_result_failure(self):
        mod = self._get_port_module()
        r = mod.ModuleResult(success=False, error="timeout")
        assert r.success is False
        assert r.error == "timeout"

    def test_notification_request_defaults(self):
        mod = self._get_port_module()
        nr = mod.NotificationRequest(user_id="u1", title="Hello", message="World")
        assert nr.category == "system"
        assert nr.channels is None
        assert nr.metadata is None

    def test_substrate_health(self):
        mod = self._get_port_module()
        h = mod.SubstrateHealth(healthy=True, version="1.0.0", modules_loaded=3)
        assert h.healthy is True
        assert h.modules_loaded == 3

    def test_all_exports(self):
        mod = self._get_port_module()
        assert hasattr(mod, "__all__")
        expected = {"CoreServicePort", "ModuleRequest", "ModuleResult", "NotificationRequest", "SubstrateHealth"}
        assert set(mod.__all__) == expected


# ===========================================================================
# 2. CoreServiceClient adapter
# ===========================================================================

class TestCoreServiceClient:
    """Verify the CoreServiceClient adapter structure and protocol compliance."""

    def _get_client_module(self):
        # Ensure port module is loaded first (dependency)
        _import_module_directly("mozaiksai.core.ports.core_service")
        return _import_module_directly("mozaiksai.core.adapters.core_client")

    def _get_port_module(self):
        return _import_module_directly("mozaiksai.core.ports.core_service")

    def test_client_importable(self):
        mod = self._get_client_module()
        assert hasattr(mod, "CoreServiceClient")

    def test_client_implements_protocol(self):
        port = self._get_port_module()
        client_mod = self._get_client_module()
        assert isinstance(client_mod.CoreServiceClient(), port.CoreServicePort)

    def test_singleton_accessor(self):
        mod = self._get_client_module()
        c1 = mod.get_core_client()
        c2 = mod.get_core_client()
        assert c1 is c2

    def test_default_base_url(self):
        mod = self._get_client_module()
        c = mod.CoreServiceClient()
        assert "8001" in c._base_url

    def test_capabilities(self):
        mod = self._get_client_module()
        caps = mod.CoreServiceClient().capabilities()
        assert caps["substrate"] == "mozaikscore"
        assert caps["supports_modules"] is True
        assert caps["supports_notifications"] is True

    def test_internal_headers_with_key(self):
        os.environ["INTERNAL_API_KEY"] = "test-key-123"
        mod = self._get_client_module()
        c = mod.CoreServiceClient()
        headers = c._internal_headers()
        assert headers["X-Internal-API-Key"] == "test-key-123"
        os.environ.pop("INTERNAL_API_KEY", None)

    def test_auth_headers_with_token(self):
        mod = self._get_client_module()
        c = mod.CoreServiceClient()
        headers = c._auth_headers("jwt-abc")
        assert headers["Authorization"] == "Bearer jwt-abc"


# ===========================================================================
# 3. Core bridge tool functions
# ===========================================================================

class TestCoreBridge:
    """Verify core_bridge tool functions are importable and well-structured."""

    def _get_bridge_module(self):
        # Load dependencies first
        _import_module_directly("mozaiksai.core.ports.core_service")
        _import_module_directly("mozaiksai.core.adapters.core_client")
        return _import_module_directly("mozaiksai.core.workflow.core_bridge")

    def test_all_tools_importable(self):
        mod = self._get_bridge_module()
        assert all(hasattr(mod, name) for name in [
            "execute_core_module",
            "send_notification",
            "get_user_subscription",
            "get_user_profile",
            "check_core_health",
        ])

    def test_tools_are_async(self):
        import inspect
        mod = self._get_bridge_module()
        for name in mod.__all__:
            fn = getattr(mod, name)
            assert inspect.iscoroutinefunction(fn), f"{name} should be async"

    def test_exports(self):
        mod = self._get_bridge_module()
        assert hasattr(mod, "__all__")
        assert len(mod.__all__) == 5


# ===========================================================================
# 4. Auth middleware
# ===========================================================================

class TestAuthMiddleware:
    """Verify mozaikscore auth module."""

    def test_auth_importable(self):
        from mozaikscore.core.auth import (
            require_admin_key,
            require_internal_api_key,
            require_admin_or_internal,
            require_admin_user,
            get_current_user,
        )
        assert all([
            require_admin_key,
            require_internal_api_key,
            require_admin_or_internal,
            require_admin_user,
            get_current_user,
        ])

    def test_auth_functions_are_async(self):
        import inspect
        from mozaikscore.core.auth import (
            require_admin_key,
            require_internal_api_key,
            require_admin_or_internal,
        )
        for fn in [require_admin_key, require_internal_api_key, require_admin_or_internal]:
            assert inspect.iscoroutinefunction(fn), f"{fn.__name__} should be async"


# ===========================================================================
# 5. Route modules  
# ===========================================================================

class TestRouteModules:
    """Verify all route modules are importable and have routers."""

    ROUTE_MODULES = [
        "mozaikscore.core.routes.admin_users",
        "mozaikscore.core.routes.notifications",
        "mozaikscore.core.routes.notifications_admin",
        "mozaikscore.core.routes.analytics",
        "mozaikscore.core.routes.status",
        "mozaikscore.core.routes.app_metadata",
        "mozaikscore.core.routes.push_subscriptions",
        "mozaikscore.core.routes.events",
        "mozaikscore.core.routes.subscription_sync",
    ]

    @pytest.mark.parametrize("module_path", ROUTE_MODULES)
    def test_route_module_importable(self, module_path):
        mod = importlib.import_module(module_path)
        assert hasattr(mod, "router"), f"{module_path} missing 'router'"

    def test_routes_init_exports(self):
        from mozaikscore.core.routes import (
            admin_users_router,
            notifications_router,
            notifications_admin_router,
            analytics_router,
            status_router,
            app_metadata_router,
            push_subscriptions_router,
            events_router,
            subscription_sync_router,
        )
        routers = [
            admin_users_router,
            notifications_router,
            notifications_admin_router,
            analytics_router,
            status_router,
            app_metadata_router,
            push_subscriptions_router,
            events_router,
            subscription_sync_router,
        ]
        for r in routers:
            assert r is not None

    def test_admin_users_prefix(self):
        from mozaikscore.core.routes.admin_users import router
        assert any("/__mozaiks/admin/users" in str(r.path) for r in router.routes) or router.prefix == "/__mozaiks/admin/users"

    def test_notifications_prefix(self):
        from mozaikscore.core.routes.notifications import router
        assert router.prefix == "/api/notifications"

    def test_events_prefix(self):
        from mozaikscore.core.routes.events import router
        assert router.prefix == "/api/events"

    def test_subscription_sync_prefix(self):
        from mozaikscore.core.routes.subscription_sync import router
        assert router.prefix == "/api/internal/subscription"


# ===========================================================================
# 6. Core app mounts all routers
# ===========================================================================

class TestCoreAppMounting:
    """Verify core_app.py mounts all route modules."""

    def test_app_importable(self):
        from mozaikscore.core_app import app
        assert app is not None

    def test_app_has_routes(self):
        from mozaikscore.core_app import app
        paths = [r.path for r in app.routes]
        # Director routes
        assert "/" in paths or any("/" == p for p in paths)

    def test_admin_routes_mounted(self):
        from mozaikscore.core_app import app
        paths = [getattr(r, "path", "") for r in app.routes]
        path_str = " ".join(paths)
        assert "/__mozaiks/admin/users" in path_str or any("admin/users" in p for p in paths)

    def test_notifications_routes_mounted(self):
        from mozaikscore.core_app import app
        paths = [getattr(r, "path", "") for r in app.routes]
        path_str = " ".join(paths)
        assert "/api/notifications" in path_str

    def test_events_routes_mounted(self):
        from mozaikscore.core_app import app
        paths = [getattr(r, "path", "") for r in app.routes]
        path_str = " ".join(paths)
        assert "/api/events" in path_str


# ===========================================================================
# 7. Phase 1 service tests (event_bus, state_manager, module_manager)
# ===========================================================================

class TestEventBus:
    """Test the mozaikscore event bus."""

    def test_publish_subscribe(self):
        from mozaikscore.core.event_bus import EventBus
        bus = EventBus()
        received = []
        bus.subscribe("test_event", lambda data: received.append(data))
        bus.publish("test_event", {"key": "value"})
        assert len(received) == 1
        assert received[0]["key"] == "value"

    def test_subscribe_unsubscribe(self):
        from mozaikscore.core.event_bus import EventBus
        bus = EventBus()
        received = []

        def handler(data):
            received.append(data)

        bus.subscribe("decorated_event", handler)
        bus.publish("decorated_event", {"x": 1})
        assert len(received) == 1

        bus.unsubscribe("decorated_event", handler)
        bus.publish("decorated_event", {"x": 2})
        assert len(received) == 1  # handler removed, no new events

    def test_event_history(self):
        from mozaikscore.core.event_bus import EventBus
        bus = EventBus()
        bus.publish("hist_event", {"a": 1})
        bus.publish("hist_event", {"a": 2})
        history = bus.get_event_history("hist_event")
        # get_event_history returns {event_type: [entries]}
        assert "hist_event" in history
        assert len(history["hist_event"]) == 2

    def test_no_cross_event_leakage(self):
        from mozaikscore.core.event_bus import EventBus
        bus = EventBus()
        received_a = []
        received_b = []
        bus.subscribe("event_a", lambda d: received_a.append(d))
        bus.subscribe("event_b", lambda d: received_b.append(d))
        bus.publish("event_a", {"val": 1})
        assert len(received_a) == 1
        assert len(received_b) == 0


class TestStateManager:
    """Test the mozaikscore state manager."""

    def test_get_set(self):
        from mozaikscore.core.state_manager import StateManager
        sm = StateManager()
        sm.set("key1", "value1")
        assert sm.get("key1") == "value1"

    def test_get_missing_returns_none(self):
        from mozaikscore.core.state_manager import StateManager
        sm = StateManager()
        assert sm.get("nonexistent") is None

    def test_delete(self):
        from mozaikscore.core.state_manager import StateManager
        sm = StateManager()
        sm.set("del_key", "val")
        sm.delete("del_key")
        assert sm.get("del_key") is None

    def test_ttl_expiry(self):
        import time
        from mozaikscore.core.state_manager import StateManager
        sm = StateManager()
        sm.set("ttl_key", "val", expire_in=0.1)
        assert sm.get("ttl_key") == "val"
        time.sleep(0.15)
        assert sm.get("ttl_key") is None

    def test_clear(self):
        from mozaikscore.core.state_manager import StateManager
        sm = StateManager()
        sm.set("a", 1)
        sm.set("b", 2)
        sm.clear()
        assert sm.get("a") is None
        assert sm.get("b") is None


class TestConfigLoader:
    """Test the config loader."""

    def test_navigation_config(self):
        from mozaikscore.core.config_loader import get_navigation_config
        config = get_navigation_config()
        assert config is not None
        assert "pages" in config or "modules" in config or "header_controls" in config

    def test_theme_config(self):
        from mozaikscore.core.config_loader import get_theme_config
        config = get_theme_config()
        assert config is not None

    def test_module_registry(self):
        from mozaikscore.core.config_loader import get_module_registry
        config = get_module_registry()
        assert config is not None
        assert "modules" in config


class TestNotificationsManagerRead:
    """Test read-only notification_manager attributes (no DB required)."""

    def test_singleton_exists(self):
        from mozaikscore.core.notifications_manager import notifications_manager
        assert notifications_manager is not None

    def test_mark_read_accepts_bool(self):
        """Verify mark_notification_read signature accepts read kwarg."""
        import inspect
        from mozaikscore.core.notifications_manager import NotificationsManager
        sig = inspect.signature(NotificationsManager.mark_notification_read)
        params = list(sig.parameters.keys())
        assert "read" in params
