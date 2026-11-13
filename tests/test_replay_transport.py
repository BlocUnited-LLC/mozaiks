"""Replay protocol proofs, including owned loopback TCP/WebSocket handshakes.

The journal and issuer are synthetic. No shared host, database or real token is
used. Fault injection at ASGI send deterministically proves slow-peer cleanup.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import socket
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi import FastAPI
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from logs.logging_config import PrettyConsoleFormatter, ProductionJSONFormatter
from mozaiksai.core.auth import WS_BEARER_SUBPROTOCOL
from mozaiksai.core.auth.adapters import AuthError, UserClaims
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
from mozaiksai.core.auth.adapters.registry import register_adapter, reset_auth_adapter
from mozaiksai.core.transport import (
    ReplayEvent,
    ReplayGrant,
    ReplayLimits,
    ReplayPage,
    create_replay_router,
)

ORIGIN = "https://replay.example.test"
TOKEN = "synthetic-replay-credential"


@pytest.fixture
def formatted_logs(caplog, capsys):
    """Capture the real handshake at both configured sink formats, at DEBUG."""
    outputs = [io.StringIO(), io.StringIO()]
    handlers = []
    records = []
    root = logging.getLogger()
    for output, formatter in zip(outputs, [PrettyConsoleFormatter(no_color=True), ProductionJSONFormatter()], strict=True):
        handler = logging.StreamHandler(output)
        handler.setFormatter(formatter)
        root.addHandler(handler)
        handlers.append(handler)
    handlers[0].addFilter(lambda record: records.append(record) or True)
    try:
        with caplog.at_level(logging.DEBUG):
            yield outputs
        fallback = capsys.readouterr().err
        assert "--- Logging error ---" not in fallback
        assert TOKEN not in fallback and protocols()[1] not in fallback and "synthetic-url-secret" not in fallback
        # A formatter failure must not silently drop the sensitive record while
        # the remaining successful JSON lines make a redaction check pass.
        json_records = [json.loads(line) for line in outputs[1].getvalue().splitlines()]
        assert len(json_records) == len(records) > 0
        assert [entry["logger"] for entry in json_records] == [record.name for record in records]
    finally:
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()


class Issuer(BaseAuthAdapter):
    name = "replay-test"
    expires: object = None
    app_id: str | None = "app-1"
    chat_id: str | None = None
    calls: list[str] = []

    async def validate_token(self, token):
        self.calls.append(token)
        if token != TOKEN:
            raise AuthError("Invalid synthetic credential", provider=self.name)
        return UserClaims(
            user_id="viewer-1", provider=self.name, app_id=self.app_id, chat_id=self.chat_id,
            raw_claims={"exp": self.expires}, scopes=["journal.read"],
        )

    def is_enabled(self):
        return True


@pytest.fixture(autouse=True)
def synthetic_auth(monkeypatch):
    Issuer.expires = time.time() + 60
    Issuer.app_id = "app-1"
    Issuer.chat_id = None
    Issuer.calls = []
    register_adapter(Issuer.name, Issuer, config_identity="replay-tests-v1")
    monkeypatch.setenv("AUTH_PROVIDER", Issuer.name)
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MOZAIKS_WS_ALLOW_QUERY_TOKEN", "true")
    reset_auth_adapter()
    yield
    reset_auth_adapter()


class Journal:
    """Synthetic committed journal shared across independent transport instances."""

    def __init__(self):
        self.grant = ReplayGrant("app-1", "stream-1", "epoch-1", "membership-1")
        self.events = []
        self.floor = 0
        self.denied = False
        self.authorizations = 0
        self.reads = []
        self.active = 0
        self.cancelled = asyncio.Event()
        self.entered = asyncio.Event()
        self.block = False
        self.after_read = None
        self.page_override = None

    def append(self, count=1):
        for _ in range(count):
            sequence = len(self.events) + 1
            self.events.append(ReplayEvent(sequence, f"event-{sequence}", {"revision": sequence}))

    async def authorize(self, principal, *, app_id, stream_id):
        assert principal.is_authenticated and principal.user_id == "viewer-1"
        self.authorizations += 1
        if self.denied or (app_id, stream_id) != (self.grant.app_id, self.grant.stream_id):
            raise PermissionError("private policy detail")
        return self.grant

    async def read_page(self, principal, *, grant, after, limit):
        assert principal.user_id == "viewer-1" and grant == self.grant
        self.reads.append((after, limit))
        self.active += 1
        self.entered.set()
        try:
            if self.block:
                await asyncio.Event().wait()
            page = ReplayPage(
                epoch=grant.epoch, floor=self.floor, head=len(self.events),
                events=tuple(self.events[after:after + limit])
                if after is not None and self.floor <= after <= len(self.events) else (),
            )
            if self.after_read:
                self.after_read()
            if self.page_override:
                return self.page_override(page)
            return page
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active -= 1


def protocols(token=TOKEN):
    return [WS_BEARER_SUBPROTOCOL, base64.urlsafe_b64encode(token.encode()).decode().rstrip("=")]


def cursor(sequence, *, app="app-1", stream="stream-1", epoch="epoch-1"):
    raw = json.dumps([1, app, stream, epoch, sequence], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def app_for(journal, **overrides):
    app = FastAPI()
    settings = dict(page_size=2, poll_interval=0.01, heartbeat_interval=0.05, source_timeout=0.3, send_timeout=0.15)
    settings.update(overrides)
    app.include_router(create_replay_router(journal, allowed_origins=(ORIGIN,), limits=ReplayLimits(**settings)), prefix="/replay")
    return app


@asynccontextmanager
async def server_for(app):
    # Bind an owned ephemeral listener once; no port scanning or shared process.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False, lifespan="off", ws="auto"))
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    task.result()
                await asyncio.sleep(0.01)
        yield f"ws://127.0.0.1:{port}/replay/app-1/stream-1"
    finally:
        server.should_exit = True
        try:
            async with asyncio.timeout(5):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            listener.close()


async def frame(client, kind=None):
    async with asyncio.timeout(2):
        while True:
            result = json.loads(await client.recv())
            assert result["schema_version"] == "mozaiks.replay.v1"
            if kind is None or result["type"] == kind:
                return result


async def closed(client, code):
    async with asyncio.timeout(2):
        with pytest.raises(ConnectionClosed) as error:
            while True:
                received = json.loads(await client.recv())
                assert received["type"] == "heartbeat", received
    assert error.value.rcvd.code == code
    return error.value.rcvd.reason


@pytest.mark.parametrize("case", ["missing", "invalid", "malformed", "url_token", "wrong_app", "chat_bound", "missing_exp", "nan_exp", "infinite_exp", "bool_exp", "expired", "bad_origin", "duplicate_cursor"])
async def test_real_socket_refuses_invalid_auth_and_scope_before_source(case, caplog, formatted_logs):
    journal = Journal()
    offered, suffix, origin = protocols(), "", ORIGIN
    if case == "missing":
        offered = None
    elif case == "invalid":
        offered = protocols("invalid-synthetic-token")
    elif case == "malformed":
        offered = [WS_BEARER_SUBPROTOCOL, "!invalid!"]
    elif case == "url_token":
        suffix = "?access_token=synthetic-url-secret"
    elif case == "wrong_app":
        Issuer.app_id = "other-app"
    elif case == "chat_bound":
        Issuer.chat_id = "workflow-session"
    elif case == "missing_exp":
        Issuer.expires = None
    elif case == "nan_exp":
        Issuer.expires = float("nan")
    elif case == "infinite_exp":
        Issuer.expires = float("inf")
    elif case == "bool_exp":
        Issuer.expires = True
    elif case == "expired":
        Issuer.expires = time.time() - 1
    elif case == "bad_origin":
        origin = "https://attacker.example.test"
    elif case == "duplicate_cursor":
        suffix = f"?cursor={cursor(0)}&cursor={cursor(0)}"
    async with server_for(app_for(journal)) as url:
        with pytest.raises(InvalidStatus) as error:
            async with connect(url + suffix, origin=origin, subprotocols=offered):
                pytest.fail("Unauthorized handshake accepted")
        assert error.value.response.status_code == 403
    assert journal.authorizations == 0 and journal.reads == []
    # Third-party LogRecords contain raw request parameters before formatting.
    # Check actual output from both canonical sinks, including encoded headers.
    secrets = [TOKEN, "synthetic-url-secret", "invalid-synthetic-token", protocols()[1], protocols("invalid-synthetic-token")[1]]
    for output in formatted_logs:
        rendered = output.getvalue()
        assert "uvicorn.error" in rendered and "403" in rendered
        assert all(secret not in rendered for secret in secrets)
    owned_records = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith(("core.", "mozaiks.")))
    assert all(secret not in owned_records for secret in secrets)


async def test_split_protocol_headers_authenticate_without_exposing_credentials(formatted_logs):
    journal = Journal()
    encoded = protocols()[1]
    async with server_for(app_for(journal)) as url:
        async with connect(url, origin=ORIGIN, subprotocols=[WS_BEARER_SUBPROTOCOL],
                           additional_headers={"Sec-WebSocket-Protocol": encoded}) as client:
            assert (await frame(client, "ready"))["type"] == "ready"
    assert journal.authorizations > 0
    for output in formatted_logs:
        rendered = output.getvalue()
        assert "sec-websocket-protocol" in rendered.lower()
        assert TOKEN not in rendered and encoded not in rendered
        assert "***REDACTED***" in rendered


async def test_real_socket_rejects_development_identity(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("AUTH_PROVIDER", "none")
    monkeypatch.setenv("AUTH_ANON_ACCESS", "open")
    reset_auth_adapter()
    journal = Journal()
    async with server_for(app_for(journal)) as url:
        with pytest.raises(InvalidStatus):
            async with connect(url):
                pytest.fail("Development identity accepted")
    assert journal.authorizations == 0


@pytest.mark.parametrize("bad", ["!bad", "A", "x" * 1025, cursor(0, app="other"), cursor(0, stream="other"), cursor(True), cursor(-1), cursor(2**53)])
async def test_real_socket_rejects_invalid_or_cross_stream_cursor(bad):
    journal = Journal()
    async with server_for(app_for(journal)) as url:
        with pytest.raises(InvalidStatus):
            async with connect(url + "?cursor=" + bad, subprotocols=protocols()):
                pytest.fail("Invalid cursor accepted")
    assert journal.authorizations == 0


async def test_fresh_connection_is_bounded_and_heartbeats_then_delivers_current_state():
    journal = Journal()
    journal.append(30)
    async with server_for(app_for(journal)) as url:
        async with connect(url, origin=ORIGIN, subprotocols=protocols()) as client:
            assert client.subprotocol == WS_BEARER_SUBPROTOCOL
            ready = await frame(client, "ready")
            assert ready["cursor"] == cursor(30)
            assert journal.reads[0] == (None, 2)
            heartbeat = await frame(client, "heartbeat")
            assert heartbeat["cursor"] == ready["cursor"]
            journal.append()
            batch = await frame(client, "batch")
            assert [event["sequence"] for event in batch["events"]] == [31]
            assert batch["after"] == cursor(30) and batch["cursor"] == cursor(31)
    assert journal.active == 0


async def test_reconnect_across_new_router_replays_only_after_applied_batch():
    journal = Journal()
    journal.append(5)
    async with server_for(app_for(journal)) as url:
        async with connect(url + "?cursor=" + cursor(0), subprotocols=protocols()) as client:
            first = await frame(client, "batch")
            assert [event["sequence"] for event in first["events"]] == [1, 2]
    # A new server/router has no connection state. Durable source + applied
    # cursor recover even if the previous server sent additional unread batches.
    journal.append()
    async with server_for(app_for(journal)) as url:
        async with connect(url + "?cursor=" + first["cursor"], subprotocols=protocols()) as client:
            second, third = await frame(client, "batch"), await frame(client, "batch")
            assert [event["sequence"] for event in second["events"] + third["events"]] == [3, 4, 5, 6]
            assert third["cursor"] == cursor(6)
    assert journal.active == 0


@pytest.mark.parametrize("case,reason", [("retention", "retention_gap"), ("ahead", "cursor_ahead"), ("epoch", "epoch_changed")])
async def test_resync_is_explicit_and_sends_no_events(case, reason):
    journal = Journal()
    journal.append(3)
    position = 0
    if case == "retention":
        journal.floor = 1
    elif case == "ahead":
        position = 4
    else:
        journal.grant = replace(journal.grant, epoch="epoch-2")
    async with server_for(app_for(journal)) as url:
        async with connect(url + "?cursor=" + cursor(position), subprotocols=protocols()) as client:
            assert await frame(client) == {"schema_version": "mozaiks.replay.v1", "type": "resync_required", "reason": reason}
            await closed(client, 1000)


@pytest.mark.parametrize("change", ["deny", "generation", "epoch"])
async def test_authorization_changes_between_read_and_send_discard_payload(change):
    journal = Journal()
    journal.append()

    def changed():
        if change == "deny":
            journal.denied = True
        elif change == "generation":
            journal.grant = replace(journal.grant, authorization_version="membership-2")
        else:
            journal.grant = replace(journal.grant, epoch="epoch-2")

    journal.after_read = changed
    async with server_for(app_for(journal)) as url:
        async with connect(url + "?cursor=" + cursor(0), subprotocols=protocols()) as client:
            if change == "epoch":
                assert (await frame(client))["reason"] == "epoch_changed"
                await closed(client, 1000)
            else:
                reason = await closed(client, 1008)
                assert "private" not in reason


async def test_idle_revocation_and_idle_expiry_close_without_new_events():
    for expire in (False, True):
        journal = Journal()
        Issuer.expires = time.time() + (0.2 if expire else 60)
        async with server_for(app_for(journal)) as url:
            async with connect(url, subprotocols=protocols()) as client:
                await frame(client, "ready")
                if not expire:
                    journal.denied = True
                await closed(client, 1008)
        assert journal.active == 0


async def test_initial_denial_never_reads_journal():
    journal = Journal()
    journal.denied = True
    async with server_for(app_for(journal)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            assert await closed(client, 1008) == "Replay access denied"
    assert journal.reads == []


@pytest.mark.parametrize("binding", [None, ""])
async def test_unbound_human_requires_current_source_app_authorization(binding):
    # Human access tokens need not carry an app claim. The source must still
    # authorize this principal for the exact requested app and stream.
    Issuer.app_id = binding
    journal = Journal()
    async with server_for(app_for(journal)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            assert (await frame(client, "ready"))["cursor"] == cursor(0)
        prior_reads = len(journal.reads)
        prior_authorizations = journal.authorizations
        async with connect(url.replace("/app-1/", "/other-app/"), subprotocols=protocols()) as client:
            assert await closed(client, 1008) == "Replay access denied"
        assert journal.authorizations == prior_authorizations + 1
        assert len(journal.reads) == prior_reads


async def test_authorization_generation_change_while_idle_closes_bound_connection():
    journal = Journal()
    async with server_for(app_for(journal)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            await frame(client, "ready")
            journal.grant = replace(journal.grant, authorization_version="membership-2")
            assert await closed(client, 1008) == "Replay authorization changed"


@pytest.mark.parametrize("field,value", [("app_id", "other-app"), ("stream_id", "other-stream"), ("epoch", ""), ("authorization_version", "")])
async def test_wrong_or_malformed_source_grant_never_reaches_read(field, value):
    class InvalidGrantJournal(Journal):
        async def authorize(self, *args, **kwargs):
            grant = await super().authorize(*args, **kwargs)
            return replace(grant, **{field: value})

    journal = InvalidGrantJournal()
    async with server_for(app_for(journal)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            await closed(client, 1011)
    assert journal.reads == []


async def test_commit_after_initial_metadata_read_is_not_lost():
    journal = Journal()

    def commit_after_snapshot():
        journal.append()
        journal.after_read = None

    journal.after_read = commit_after_snapshot
    async with server_for(app_for(journal)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            assert (await frame(client, "ready"))["cursor"] == cursor(0)
            assert (await frame(client, "batch"))["cursor"] == cursor(1)


async def test_two_readers_have_independent_resume_positions():
    journal = Journal()
    journal.append(3)
    async with server_for(app_for(journal)) as url:
        async with (
            connect(url + "?cursor=" + cursor(0), subprotocols=protocols()) as first,
            connect(url + "?cursor=" + cursor(2), subprotocols=protocols()) as second,
        ):
            assert (await frame(first, "batch"))["cursor"] == cursor(2)
            assert (await frame(second, "batch"))["cursor"] == cursor(3)
            await first.close()
            journal.append()
            assert (await frame(second, "batch"))["cursor"] == cursor(4)


@pytest.mark.parametrize("case", ["disconnect", "expiry", "timeout", "client_write"])
async def test_pending_source_work_is_cancelled(case):
    journal = Journal()
    journal.block = True
    if case == "expiry":
        Issuer.expires = time.time() + 0.15
    async with server_for(app_for(journal, source_timeout=0.25)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            await asyncio.wait_for(journal.entered.wait(), 1)
            if case == "disconnect":
                await client.close()
            elif case == "client_write":
                await client.send('{"action":"write"}')
                await closed(client, 1008)
            else:
                await closed(client, 1008 if case == "expiry" else 1011)
            await asyncio.wait_for(journal.cancelled.wait(), 1)
    assert journal.active == 0


async def test_expiry_budget_is_not_misclassified_by_wall_clock_resolution(monkeypatch):
    import mozaiksai.core.transport.replay as replay_module

    # Windows event-loop deadlines and wall-clock readings can differ slightly.
    # Only the candidate module's clock reference is replaced; no OS/global clock.
    fixed = time.time()
    monkeypatch.setattr(replay_module, "time", SimpleNamespace(time=lambda: fixed, monotonic=time.monotonic))
    Issuer.expires = fixed + 0.05
    journal = Journal()
    journal.block = True
    async with server_for(app_for(journal, source_timeout=1)) as url:
        async with connect(url, subprotocols=protocols()) as client:
            assert await closed(client, 1008) == "Token expired"
    assert journal.cancelled.is_set() and journal.active == 0


@pytest.mark.parametrize("boundary,authorizations,reads", [(1, 0, 0), (2, 1, 0), (3, 1, 1), (4, 2, 1)])
async def test_already_expired_immediate_operations_never_start(boundary, authorizations, reads, monkeypatch, recwarn):
    import mozaiksai.core.transport.replay as replay_module

    # Expire exactly before first authorization, read, reauthorization or send.
    # The synthetic source completes immediately without an asyncio suspension;
    # a timeout(0) alone cannot prevent its body (or ASGI send) from running.
    now = time.time()
    clock = SimpleNamespace(value=now)
    Issuer.expires = now + 30
    monkeypatch.setattr(replay_module, "time", SimpleNamespace(time=lambda: clock.value, monotonic=time.monotonic))
    original_bounded = replay_module._bounded
    calls = 0

    async def expire_at_boundary(operation, timeout, expiry):
        nonlocal calls
        calls += 1
        if calls == boundary:
            clock.value = expiry
        return await original_bounded(operation, timeout, expiry)

    monkeypatch.setattr(replay_module, "_bounded", expire_at_boundary)
    journal = Journal()
    journal.append()
    async with server_for(app_for(journal)) as url:
        async with connect(url + "?cursor=" + cursor(0), subprotocols=protocols()) as client:
            assert await closed(client, 1008) == "Token expired"
    assert journal.authorizations == authorizations
    assert len(journal.reads) == reads
    assert not any("never awaited" in str(warning.message) for warning in recwarn)


async def test_expiry_during_serialization_refuses_before_asgi_send(monkeypatch):
    import mozaiksai.core.transport.replay as replay_module

    now = time.time()
    clock = SimpleNamespace(value=now)
    Issuer.expires = now + 30
    monkeypatch.setattr(replay_module, "time", SimpleNamespace(time=lambda: clock.value, monotonic=time.monotonic))

    class ExpiringEncoder(json.JSONEncoder):
        def iterencode(self, value, *args, **kwargs):
            yield from super().iterencode(value, *args, **kwargs)
            if value.get("type") == "batch":
                clock.value = Issuer.expires + 1

    monkeypatch.setattr(
        replay_module, "json", SimpleNamespace(JSONEncoder=ExpiringEncoder, dumps=json.dumps, loads=json.loads),
    )
    journal = Journal()
    journal.append()
    async with server_for(app_for(journal)) as url:
        async with connect(url + "?cursor=" + cursor(0), subprotocols=protocols()) as client:
            assert await closed(client, 1008) == "Token expired"


@pytest.mark.parametrize("fault", ["wrong_epoch", "bad_floor", "bad_head", "missing", "out_of_order", "duplicates", "extra", "bad_data", "nan", "oversize", "exception"])
async def test_malformed_source_pages_fail_closed_without_payload_or_error_details(fault, caplog):
    journal = Journal()
    journal.append(2)

    def broken(page):
        event = page.events[0]
        if fault == "wrong_epoch":
            return replace(page, epoch="other")
        if fault == "bad_floor":
            return replace(page, floor=True)
        if fault == "bad_head":
            return replace(page, head=2**53)
        if fault == "missing":
            return replace(page, events=page.events[:1])
        if fault == "out_of_order":
            return replace(page, events=tuple(reversed(page.events)))
        if fault == "duplicates":
            return replace(page, events=(event, replace(page.events[1], event_id=event.event_id)))
        if fault == "extra":
            return replace(page, events=page.events + (event,))
        if fault == "bad_data":
            return replace(page, events=(replace(event, data=[]), page.events[1]))
        if fault == "nan":
            return replace(page, events=(replace(event, data={"secret": float("nan")}), page.events[1]))
        if fault == "oversize":
            return replace(page, events=(replace(event, data={"secret": "private-body" * 200}), page.events[1]))
        raise RuntimeError("private-source-internals")

    journal.page_override = broken
    async with server_for(app_for(journal, max_frame_bytes=1024)) as url:
        async with connect(url + "?cursor=" + cursor(0), subprotocols=protocols()) as client:
            assert await closed(client, 1011) == "Replay source unavailable"
    assert "private-body" not in caplog.text and "private-source-internals" not in caplog.text


class BlockSend:
    """Keep real TCP auth/lifecycle; block ASGI data send as a slow socket would."""

    def __init__(self, app):
        self.app = app
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def __call__(self, scope, receive, send):
        async def stalled(message):
            if message["type"] == "websocket.send":
                self.entered.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
            await send(message)
        await self.app(scope, receive, stalled)


@pytest.mark.parametrize("case", ["slow", "disconnect", "expiry"])
async def test_slow_send_is_bounded_and_cancelled(case):
    journal = Journal()
    if case == "expiry":
        Issuer.expires = time.time() + 0.15
    blocked = BlockSend(app_for(journal, send_timeout=0.3 if case == "expiry" else 0.2))
    async with server_for(blocked) as url:
        async with connect(url, subprotocols=protocols()) as client:
            await asyncio.wait_for(blocked.entered.wait(), 1)
            if case == "disconnect":
                await client.close()
            else:
                await closed(client, 1008 if case == "expiry" else 1011)
            await asyncio.wait_for(blocked.cancelled.wait(), 1)
    assert journal.active == 0


@pytest.mark.parametrize("phase", ["source", "send"])
async def test_handler_cancellation_awaits_child_cleanup(phase):
    journal = Journal()
    journal.block = phase == "source"
    entered, cancelled = asyncio.Event(), asyncio.Event()
    queue = asyncio.Queue()
    queue.put_nowait({"type": "websocket.connect"})

    async def send(message):
        if message["type"] == "websocket.send" and phase == "send":
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    scope = {
        "type": "websocket", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "scheme": "ws", "path": "/replay/app-1/stream-1",
        "raw_path": b"/replay/app-1/stream-1", "query_string": b"", "root_path": "",
        "headers": [], "subprotocols": protocols(), "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 2),
    }
    task = asyncio.create_task(app_for(journal)(scope, queue.get, send))
    await asyncio.wait_for((journal.entered if phase == "source" else entered).wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (journal.cancelled if phase == "source" else cancelled).is_set()
    assert journal.active == 0


@pytest.mark.parametrize("values", [{"page_size": 0}, {"page_size": True}, {"max_frame_bytes": 1023}, {"poll_interval": float("nan")}, {"send_timeout": 0}, {"source_timeout": 61}])
def test_limits_are_bounded(values):
    with pytest.raises(ValueError):
        ReplayLimits(**values)


@pytest.mark.parametrize("origin", ["*", "https://good.test/", "https://good.test/path", "https://user:secret@good.test", "file://local"])
def test_origin_configuration_is_exact(origin):
    with pytest.raises(ValueError):
        create_replay_router(Journal(), allowed_origins=(origin,))
