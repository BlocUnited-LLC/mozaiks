"""Unit tests for new/refactored modules added during API convergence."""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock

import pytest


# ── Auth middleware unit tests ────────────────────────────────────────────────

class TestAuthDataClasses:
    def test_auth_config_defaults(self) -> None:
        from mozaiks.core.auth.middleware import AuthConfig
        cfg = AuthConfig()
        assert cfg.authority == ""
        assert cfg.audience == ""
        assert cfg.issuer == ""

    def test_web_socket_user(self) -> None:
        from mozaiks.core.auth.middleware import WebSocketUser
        user = WebSocketUser(user_id="u1", display_name="Alice", roles=["admin"])
        assert user.user_id == "u1"
        assert user.display_name == "Alice"
        assert user.roles == ["admin"]
        assert user.app_id is None

    def test_user_principal(self) -> None:
        from mozaiks.core.auth.middleware import UserPrincipal
        p = UserPrincipal(user_id="u2", display_name="Bob")
        assert p.user_id == "u2"
        assert p.roles == []

    def test_service_principal(self) -> None:
        from mozaiks.core.auth.middleware import ServicePrincipal
        sp = ServicePrincipal(service_id="svc-1", roles=["internal"])
        assert sp.service_id == "svc-1"


class TestResourceOwnership:
    @pytest.mark.asyncio
    async def test_verify_user_owns_resource_match(self) -> None:
        from mozaiks.core.auth.middleware import verify_user_owns_resource
        assert await verify_user_owns_resource(user_id="u1", resource_owner_id="u1") is True

    @pytest.mark.asyncio
    async def test_verify_user_owns_resource_mismatch(self) -> None:
        from mozaiks.core.auth.middleware import verify_user_owns_resource
        assert await verify_user_owns_resource(user_id="u1", resource_owner_id="u2") is False

    @pytest.mark.asyncio
    async def test_require_resource_ownership_raises(self) -> None:
        from mozaiks.core.auth.middleware import require_resource_ownership
        with pytest.raises(PermissionError):
            await require_resource_ownership(user_id="u1", resource_owner_id="u2")

    @pytest.mark.asyncio
    async def test_require_resource_ownership_ok(self) -> None:
        from mozaiks.core.auth.middleware import require_resource_ownership
        await require_resource_ownership(user_id="u1", resource_owner_id="u1")

    @pytest.mark.asyncio
    async def test_require_user_scope_ok(self) -> None:
        from mozaiks.core.auth.middleware import UserPrincipal, require_user_scope
        user = UserPrincipal(user_id="u1", roles=["admin", "reader"])
        await require_user_scope(user=user, required_scope="admin")

    @pytest.mark.asyncio
    async def test_require_user_scope_raises(self) -> None:
        from mozaiks.core.auth.middleware import UserPrincipal, require_user_scope
        user = UserPrincipal(user_id="u1", roles=["reader"])
        with pytest.raises(PermissionError):
            await require_user_scope(user=user, required_scope="admin")


# ── Artifact helpers unit tests ───────────────────────────────────────────────

class TestArtifactAttachments:
    @pytest.mark.asyncio
    async def test_inject_empty_dir(self) -> None:
        from mozaiks.core.artifacts import inject_bundle_attachments_into_payload
        payload: dict = {"foo": 1}
        result = await inject_bundle_attachments_into_payload(payload, bundle_dir=None)
        assert result == {"foo": 1}
        assert "attachments" not in result

    @pytest.mark.asyncio
    async def test_inject_with_files(self) -> None:
        from mozaiks.core.artifacts import inject_bundle_attachments_into_payload
        with tempfile.TemporaryDirectory() as td:
            # Create a test file
            test_file = os.path.join(td, "hello.txt")
            with open(test_file, "wb") as f:
                f.write(b"hello world")

            payload: dict = {}
            result = await inject_bundle_attachments_into_payload(payload, bundle_dir=td)
            assert "attachments" in result
            assert "hello.txt" in result["attachments"]

    def test_iter_bundle_attachment_files(self) -> None:
        from mozaiks.core.artifacts import iter_bundle_attachment_files
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "a.bin"), "wb") as f:
                f.write(b"\x01\x02")
            with open(os.path.join(td, "b.txt"), "wb") as f:
                f.write(b"text")

            items = list(iter_bundle_attachment_files(td))
            assert len(items) == 2
            assert items[0][0] == "a.bin"
            assert items[0][1] == b"\x01\x02"
            assert items[1][0] == "b.txt"

    def test_iter_bundle_nonexistent(self) -> None:
        from mozaiks.core.artifacts import iter_bundle_attachment_files
        items = list(iter_bundle_attachment_files("/nonexistent/path"))
        assert items == []

    @pytest.mark.asyncio
    async def test_handle_chat_upload(self) -> None:
        from mozaiks.core.artifacts import handle_chat_upload
        result = await handle_chat_upload(
            file_bytes=b"test content",
            filename="test.txt",
            chat_id="chat-1",
            user_id="user-1",
        )
        assert "artifact_id" in result
        assert result["filename"] == "test.txt"
        assert result["size"] == 12
        assert result["chat_id"] == "chat-1"


# ── Persistence managers unit tests ──────────────────────────────────────────

class TestPersistenceManager:
    def test_construction(self) -> None:
        from mozaiks.core.persistence import PersistenceManager
        pm = PersistenceManager()
        assert pm._ready is False

    def test_event_store_raises_when_not_configured(self) -> None:
        from mozaiks.core.persistence import PersistenceManager
        pm = PersistenceManager()
        with pytest.raises(RuntimeError, match="event_store not configured"):
            _ = pm.event_store

    def test_checkpoint_store_raises_when_not_configured(self) -> None:
        from mozaiks.core.persistence import PersistenceManager
        pm = PersistenceManager()
        with pytest.raises(RuntimeError, match="checkpoint_store not configured"):
            _ = pm.checkpoint_store

    def test_construction_with_stores(self) -> None:
        from mozaiks.core.persistence import PersistenceManager
        mock_event = object()
        mock_cp = object()
        pm = PersistenceManager(event_store=mock_event, checkpoint_store=mock_cp)  # type: ignore[arg-type]
        assert pm.event_store is mock_event
        assert pm.checkpoint_store is mock_cp

    @pytest.mark.asyncio
    async def test_ensure_client(self) -> None:
        from mozaiks.core.persistence import PersistenceManager
        pm = PersistenceManager()
        await pm._ensure_client()
        assert pm._ready is True


class TestAG2PersistenceManager:
    def test_construction(self) -> None:
        from mozaiks.core.persistence import AG2PersistenceManager
        mgr = AG2PersistenceManager()
        assert mgr.persistence is not None

    @pytest.mark.asyncio
    async def test_get_or_assign_cache_seed(self) -> None:
        from mozaiks.core.persistence import AG2PersistenceManager
        mgr = AG2PersistenceManager()
        seed = await mgr.get_or_assign_cache_seed(chat_id="c1")
        assert seed == 42

    @pytest.mark.asyncio
    async def test_get_chat_usage_totals(self) -> None:
        from mozaiks.core.persistence import AG2PersistenceManager
        mgr = AG2PersistenceManager()
        totals = await mgr.get_chat_usage_totals(chat_id="c1")
        assert totals["total_tokens"] == 0


# ── SimpleTransport unit tests ───────────────────────────────────────────────

class TestSimpleTransport:
    @pytest.mark.asyncio
    async def test_singleton(self) -> None:
        from mozaiks.core.streaming.transport import SimpleTransport
        # Reset singleton for test isolation
        SimpleTransport._instance = None
        t1 = await SimpleTransport.get_instance()
        t2 = await SimpleTransport.get_instance()
        assert t1 is t2
        SimpleTransport._instance = None  # cleanup

    def test_register_unregister(self) -> None:
        from mozaiks.core.streaming.transport import SimpleTransport
        t = SimpleTransport()
        mock_ws = object()
        t.register_connection("chat-1", mock_ws)
        assert "chat-1" in t.connections
        t.unregister_connection("chat-1")
        assert "chat-1" not in t.connections

    @pytest.mark.asyncio
    async def test_resolve_ui_tool_response(self) -> None:
        from mozaiks.core.streaming.transport import SimpleTransport
        t = SimpleTransport()

        # Create a mock websocket that captures sends
        mock_ws = AsyncMock()
        t.register_connection("chat-1", mock_ws)

        # Send a UI tool event (creates the pending future)
        event_id = await t.send_ui_tool_event(
            tool_name="test_tool",
            payload={"key": "val"},
            chat_id="chat-1",
        )
        assert event_id in t._pending_responses

        # Resolve it
        t.resolve_ui_tool_response(event_id, {"answer": "yes"})

        # Wait should return immediately
        result = await t.wait_for_ui_tool_response(event_id, timeout=1.0)
        assert result == {"answer": "yes"}


# ── UI tool helpers unit tests ────────────────────────────────────────────────

class TestUIToolHelpers:
    def test_importable(self) -> None:
        from mozaiks.core.tools.ui import emit_tool_progress_event, use_ui_tool
        assert callable(use_ui_tool)
        assert callable(emit_tool_progress_event)
