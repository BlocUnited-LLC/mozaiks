"""
Phase 5 — Real-time Event Flow: validation tests.

Tests verify:
1. WebSocket endpoint wired in core_app.py
2. WebSocket event bridge (event_bus → WS push)
3. useCoreWebSocket frontend hook
4. useCoreNotifications upgraded to WS-first
5. Cross-substrate event bridge (mozaikscore ↔ mozaiksai)
6. CoreServiceClient.relay_event method
7. CoreServicePort.relay_event in protocol
8. Root-level core_app.py wrapper
9. Dual-substrate start script
"""

import os
import re
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def read_file(relpath):
    """Read a file relative to project root."""
    full = os.path.join(ROOT, relpath.replace("/", os.sep))
    assert os.path.isfile(full), f"File not found: {relpath}"
    with open(full, "r", encoding="utf-8") as f:
        return f.read()


# ── 1. WebSocket Endpoint in core_app.py ────────────────────────────────

class TestWebSocketEndpoint:
    @pytest.fixture
    def source(self):
        return read_file("mozaikscore/core_app.py")

    def test_imports_websocket(self, source):
        assert "WebSocket" in source
        assert "WebSocketDisconnect" in source

    def test_ws_route_defined(self, source):
        assert '/ws/{user_id}' in source or "@app.websocket" in source

    def test_uses_websocket_manager(self, source):
        assert "websocket_manager.connect" in source
        assert "websocket_manager.disconnect" in source

    def test_event_bus_starts_on_startup(self, source):
        assert "event_bus.start_background_processing" in source

    def test_event_bus_stops_on_shutdown(self, source):
        assert "event_bus.stop_background_processing" in source

    def test_websocket_event_bridge_registered(self, source):
        assert "register_websocket_events" in source

    def test_cross_substrate_bridge_registered(self, source):
        assert "register_outbound_relay" in source

    def test_relay_router_mounted(self, source):
        assert "relay_router" in source


# ── 2. WebSocket Event Bridge ───────────────────────────────────────────

class TestWebSocketEventBridge:
    @pytest.fixture
    def source(self):
        return read_file("mozaikscore/core/websocket_event_bridge.py")

    def test_file_exists(self, source):
        assert len(source) > 500

    def test_defines_user_targeted_events(self, source):
        assert "_USER_TARGETED_EVENTS" in source
        assert "notification_created" in source
        assert "module_executed" in source

    def test_defines_broadcast_events(self, source):
        assert "_BROADCAST_EVENTS" in source
        assert "system_announcement" in source

    def test_register_function(self, source):
        assert "def register_websocket_events" in source

    def test_subscribes_to_event_bus(self, source):
        assert "event_bus.subscribe" in source

    def test_sends_to_user(self, source):
        assert "websocket_manager.send_to_user" in source

    def test_broadcasts(self, source):
        assert "websocket_manager.broadcast" in source

    def test_sanitizes_payload(self, source):
        assert "_sanitize_for_json" in source

    def test_strips_internal_fields(self, source):
        assert 'k.startswith("_")' in source


# ── 3. useCoreWebSocket Frontend Hook ───────────────────────────────────

class TestUseCoreWebSocket:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/hooks/useCoreWebSocket.js")

    def test_file_exists(self, source):
        assert len(source) > 500

    def test_exports_hook(self, source):
        assert "export function useCoreWebSocket" in source

    def test_connects_via_websocket(self, source):
        assert "new WebSocket" in source

    def test_auto_reconnect(self, source):
        assert "RECONNECT_BASE_DELAY" in source or "reconnect" in source.lower()
        assert "scheduleReconnect" in source

    def test_exponential_backoff(self, source):
        assert "Math.pow" in source or "**" in source

    def test_ping_keepalive(self, source):
        assert "PING_INTERVAL" in source or "ping" in source

    def test_event_listener_api(self, source):
        # on(eventName, callback) pattern
        assert "const on = useCallback" in source
        assert "const off = useCallback" in source

    def test_returns_connected_state(self, source):
        assert "connected" in source
        assert "setConnected" in source

    def test_dispatches_events(self, source):
        assert "dispatch" in source
        assert "lastEvent" in source

    def test_wildcard_listener(self, source):
        assert "'*'" in source

    def test_cleanup_on_unmount(self, source):
        assert "unmountedRef" in source
        assert ".close()" in source

    def test_ws_url_construction(self, source):
        assert "getCoreWsUrl" in source
        assert "/ws/" in source

    def test_converts_protocol(self, source):
        # http → ws
        assert "replace" in source and "ws" in source


# ── 4. useCoreNotifications — WebSocket-first ───────────────────────────

class TestNotificationsWSUpgrade:
    @pytest.fixture
    def source(self):
        return read_file("chat-ui/src/hooks/useCoreNotifications.js")

    def test_accepts_ws_connection(self, source):
        assert "wsConnection" in source

    def test_subscribes_to_notification_created(self, source):
        assert "notification_created" in source

    def test_subscribes_to_notification_read(self, source):
        assert "notification_read" in source

    def test_subscribes_to_all_read(self, source):
        assert "all_notifications_read" in source

    def test_returns_source_indicator(self, source):
        assert "source" in source
        assert "'ws'" in source or '"ws"' in source
        assert "'poll'" in source or '"poll"' in source

    def test_falls_back_to_polling(self, source):
        # When wsConnection is not connected, polling continues
        assert "setInterval" in source
        assert "wsConnection?.connected" in source or "wsConnection.connected" in source

    def test_increments_on_new_notification(self, source):
        assert "c + 1" in source or "c+1" in source

    def test_decrements_on_read(self, source):
        assert "c - 1" in source or "c-1" in source


# ── 5. Cross-substrate Event Bridge ─────────────────────────────────────

class TestCrossSubstrateBridge:
    @pytest.fixture
    def source(self):
        return read_file("mozaikscore/core/cross_substrate_bridge.py")

    def test_file_exists(self, source):
        assert len(source) > 500

    # Outbound: mozaikscore → mozaiksai
    def test_outbound_events_defined(self, source):
        assert "_OUTBOUND_EVENTS" in source
        assert "get_automation_event_catalog" in source
        assert "source_event" in source

    def test_relay_function(self, source):
        assert "_relay_to_mozaiksai" in source

    def test_posts_to_mozaiksai(self, source):
        assert "/api/substrate-events" in source
        assert "httpx" in source

    def test_register_outbound(self, source):
        assert "def register_outbound_relay" in source

    def test_supports_nats_transport(self, source):
        assert "use_nats_transport" in source
        assert "get_substrate_event_nats_publisher" in source

    def test_includes_internal_api_key(self, source):
        assert "X-Internal-API-Key" in source

    def test_non_fatal_relay(self, source):
        # Must not crash if mozaiksai is down
        assert "except" in source

    # Inbound: mozaiksai → mozaikscore
    def test_inbound_route(self, source):
        assert "/__mozaiks/internal" in source
        assert "/relay-event" in source

    def test_validates_api_key(self, source):
        assert "hmac_compare" in source or "validate_internal_key" in source

    def test_publishes_to_event_bus(self, source):
        assert "event_bus.publish" in source

    def test_tags_relay_source(self, source):
        assert "_relay_source" in source

    def test_constant_time_comparison(self, source):
        assert "hmac.compare_digest" in source


# ── 6. CoreServiceClient.relay_event ────────────────────────────────────

class TestCoreClientRelayEvent:
    @pytest.fixture
    def source(self):
        return read_file("mozaiksai/core/adapters/core_client.py")

    def test_relay_event_method(self, source):
        assert "async def relay_event" in source

    def test_posts_to_internal_relay(self, source):
        assert "/__mozaiks/internal/relay-event" in source

    def test_includes_source_tag(self, source):
        assert '"mozaiksai"' in source or "'mozaiksai'" in source

    def test_returns_bool(self, source):
        # Method returns True/False
        assert "return True" in source
        assert "return False" in source


# ── 7. CoreServicePort Protocol ─────────────────────────────────────────

class TestCoreServicePortRelay:
    @pytest.fixture
    def source(self):
        return read_file("mozaiksai/core/ports/core_service.py")

    def test_relay_event_in_protocol(self, source):
        assert "relay_event" in source


# ── 8. Root-level core_app.py ───────────────────────────────────────────

class TestRootCoreApp:
    @pytest.fixture
    def source(self):
        return read_file("core_app.py")

    def test_file_exists(self, source):
        assert len(source) > 50

    def test_imports_from_mozaikscore(self, source):
        assert "from mozaikscore.core_app import app" in source


# ── 9. Dual-substrate Start Script ──────────────────────────────────────

class TestStartDualScript:
    @pytest.fixture
    def source(self):
        return read_file("start-dual.ps1")

    def test_file_exists(self, source):
        assert len(source) > 200

    def test_starts_mozaiksai(self, source):
        assert "run_server.py" in source or "mozaiksai" in source.lower()

    def test_starts_mozaikscore(self, source):
        assert "run_core.py" in source or "mozaikscore" in source.lower()

    def test_supports_frontend(self, source):
        assert "StartFrontend" in source
        assert "npm run dev" in source or "vite" in source.lower()

    def test_port_config(self, source):
        assert "8000" in source
        assert "8001" in source

    def test_graceful_shutdown(self, source):
        assert "Stop-Job" in source or "stop" in source.lower()
