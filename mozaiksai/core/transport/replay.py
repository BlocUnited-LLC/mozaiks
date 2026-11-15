"""Authenticated, bounded delivery from a host-selected durable journal.

This module owns connection mechanics, not journal storage or product authority.
It neither uses workflow chat sessions nor treats an event bus as durable storage.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import functools
import json
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, cast
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from mozaiksai.core.auth import WebSocketUser, accept_websocket, authenticate_websocket

_VERSION = "mozaiks.replay.v1"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MAX_SEQUENCE = 2**53 - 1
_T = TypeVar("_T")


@dataclass(frozen=True)
class ReplayGrant:
    """Current app/stream audience grant, including its separate policy version."""

    app_id: str
    stream_id: str
    epoch: str
    authorization_version: str


@dataclass(frozen=True)
class ReplayEvent:
    sequence: int
    event_id: str
    data: dict[str, Any]


@dataclass(frozen=True)
class ReplayPage:
    """A consistent journal view; ``floor`` is the earliest resumable position.

    All sequences in (floor, head] exist. Reads return every consecutive event
    after the requested position, up to the limit. Initial ``after=None`` reads
    return metadata only. Data must be a current, authorized projection, including
    tombstones/redaction; historical payloads must not resurrect erased content.
    """

    epoch: str
    floor: int
    head: int
    events: tuple[ReplayEvent, ...] = ()


class ReplaySource(Protocol):
    """Trusted host implementation; no client-selected adapter or raw DB handle.

    Implementations must cooperate with cancellation. ``authorize`` must consult
    current membership/consent and token scope, returning a version that changes
    whenever those permissions change. Denial raises PermissionError. Journal
    ordering, retention and transactional producer intent belong to this source.
    """

    async def authorize(self, principal: WebSocketUser, *, app_id: str, stream_id: str) -> ReplayGrant: ...

    async def read_page(
        self, principal: WebSocketUser, *, grant: ReplayGrant, after: int | None, limit: int,
    ) -> ReplayPage: ...


@dataclass(frozen=True)
class ReplayLimits:
    page_size: int = 50
    max_frame_bytes: int = 131_072
    poll_interval: float = 1.0
    heartbeat_interval: float = 15.0
    source_timeout: float = 5.0
    send_timeout: float = 5.0

    def __post_init__(self) -> None:
        if type(self.page_size) is not int or not 1 <= self.page_size <= 100:
            raise ValueError("Replay page_size must be between 1 and 100")
        if type(self.max_frame_bytes) is not int or not 1024 <= self.max_frame_bytes <= 1_048_576:
            raise ValueError("Replay max_frame_bytes must be between 1024 and 1048576")
        for value in (self.poll_interval, self.heartbeat_interval, self.source_timeout, self.send_timeout):
            if type(value) not in (int, float) or not 0 < value <= 60 or not math.isfinite(value):
                raise ValueError("Replay intervals and timeouts must be finite, positive and at most 60 seconds")


class _Refusal(Exception):
    def __init__(self, code: int, reason: str) -> None:
        self.code, self.reason = code, reason


def _identifier(value: Any) -> bool:
    return type(value) is str and _ID.fullmatch(value) is not None


def _position(value: Any) -> bool:
    return type(value) is int and 0 <= value <= _MAX_SEQUENCE


def _cursor(grant: ReplayGrant, sequence: int) -> str:
    raw = json.dumps([1, grant.app_id, grant.stream_id, grant.epoch, sequence], separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _parse_cursor(value: str, app_id: str, stream_id: str) -> tuple[str, int]:
    if not value or len(value) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise _Refusal(1008, "Invalid replay cursor")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if base64.urlsafe_b64encode(raw).decode().rstrip("=") != value:
            raise ValueError
        parts = json.loads(raw)
        if (
            type(parts) is not list or len(parts) != 5 or type(parts[0]) is not int or parts[0] != 1
            or parts[1:3] != [app_id, stream_id] or not _identifier(parts[3]) or not _position(parts[4])
        ):
            raise ValueError
        return parts[3], parts[4]
    except (ValueError, UnicodeError, binascii.Error, RecursionError):
        raise _Refusal(1008, "Invalid replay cursor") from None


def _expiry(principal: WebSocketUser) -> float:
    raw = principal.raw_claims.get("exp")
    if type(raw) not in (int, float):
        raise _Refusal(1008, "Valid token expiry required")
    value = cast("int | float", raw)
    if not 0 < value <= _MAX_SEQUENCE or not math.isfinite(value) or value <= time.time():
        raise _Refusal(1008, "Valid token expiry required")
    return float(value)


async def _bounded(operation: Callable[[], Awaitable[_T]], timeout: float, expiry: float) -> _T:
    # Reject before creating the awaitable: timeout(0) still permits an immediate
    # operation to run. The factory also avoids abandoned, unawaited coroutines.
    remaining = expiry - time.time()
    if remaining <= 0:
        raise _Refusal(1008, "Token expired")
    expiry_bound = remaining <= timeout
    deadline = asyncio.timeout(min(timeout, remaining))
    try:
        async with deadline:
            return await operation()
    except TimeoutError:
        if (deadline.expired() and expiry_bound) or time.time() >= expiry:
            raise _Refusal(1008, "Token expired") from None
        raise _Refusal(1011, "Replay operation timed out") from None


async def _close(websocket: WebSocket, code: int, reason: str, timeout: float) -> None:
    try:
        async with asyncio.timeout(timeout):
            await websocket.close(code=code, reason=reason)
    except (TimeoutError, OSError, RuntimeError, WebSocketDisconnect):
        pass


def _validate_page(page: ReplayPage, grant: ReplayGrant, after: int | None, limit: int) -> None:
    if (
        type(page) is not ReplayPage or page.epoch != grant.epoch
        or not _position(page.floor) or not _position(page.head) or page.floor > page.head
        or type(page.events) is not tuple or len(page.events) > limit
    ):
        raise ValueError("Invalid replay source page")
    expected = 0 if after is None or not page.floor <= after <= page.head else min(limit, page.head - after)
    if len(page.events) != expected:
        raise ValueError("Incomplete replay source page")
    seen: set[str] = set()
    for index, event in enumerate(page.events):
        if (
            type(event) is not ReplayEvent or not _position(event.sequence)
            or event.sequence != (after or 0) + index + 1 or not _identifier(event.event_id)
            or event.event_id in seen or type(event.data) is not dict
        ):
            raise ValueError("Invalid replay source event")
        seen.add(event.event_id)


async def _deliver(
    websocket: WebSocket, source: ReplaySource, principal: WebSocketUser,
    app_id: str, stream_id: str, resume: tuple[str, int] | None, limits: ReplayLimits, expiry: float,
) -> None:
    async def authorize() -> ReplayGrant:
        grant = await _bounded(
            lambda: source.authorize(principal, app_id=app_id, stream_id=stream_id), limits.source_timeout, expiry,
        )
        if (
            type(grant) is not ReplayGrant or grant.app_id != app_id or grant.stream_id != stream_id
            or not _identifier(grant.epoch) or not _identifier(grant.authorization_version)
        ):
            raise ValueError("Invalid replay source grant")
        return grant

    async def send(frame: dict[str, Any]) -> None:
        if time.time() >= expiry:
            raise _Refusal(1008, "Token expired")
        pieces, size = [], 0
        encoder = json.JSONEncoder(separators=(",", ":"), allow_nan=False)
        for piece in encoder.iterencode({"schema_version": _VERSION, **frame}):
            size += len(piece.encode("utf-8"))
            if size > limits.max_frame_bytes:
                raise ValueError("Replay source frame exceeds limit")
            pieces.append(piece)
        payload = "".join(pieces)
        await _bounded(lambda: websocket.send_text(payload), limits.send_timeout, expiry)

    async def resync(reason: str) -> None:
        await send({"type": "resync_required", "reason": reason})

    epoch, after = resume if resume is not None else (None, None)
    authorization_version: str | None = None
    observed_head = 0
    next_heartbeat = time.monotonic()
    while True:
        if time.time() >= expiry:
            raise _Refusal(1008, "Token expired")
        grant = await authorize()
        if epoch is not None and grant.epoch != epoch:
            await resync("epoch_changed")
            return
        if authorization_version is not None and grant.authorization_version != authorization_version:
            raise _Refusal(1008, "Replay authorization changed")
        authorization_version = grant.authorization_version
        page = await _bounded(
            functools.partial(source.read_page, principal, grant=grant, after=after, limit=limits.page_size),
            limits.source_timeout, expiry,
        )
        _validate_page(page, grant, after, limits.page_size)
        if page.head < observed_head:
            raise ValueError("Replay source head decreased")
        # A successful old read is not current authorization. Invalidate its
        # payload if membership/consent OR the stream epoch changed while waiting.
        current = await authorize()
        if current.epoch != grant.epoch:
            await resync("epoch_changed")
            return
        if current != grant:
            raise _Refusal(1008, "Replay authorization changed")
        epoch, observed_head = grant.epoch, page.head
        sent = False
        if after is None:
            # Never replay an unbounded backlog on a fresh connection. The client
            # establishes its HTTP snapshot and merges later batches from here.
            after = page.head
            await send({"type": "ready", "cursor": _cursor(grant, after)})
            sent = True
        elif after < page.floor or after > page.head:
            await resync("retention_gap" if after < page.floor else "cursor_ahead")
            return
        elif page.events:
            last = page.events[-1].sequence
            await send({
                "type": "batch", "after": _cursor(grant, after), "cursor": _cursor(grant, last),
                "events": [
                    {"sequence": event.sequence, "event_id": event.event_id, "data": event.data}
                    for event in page.events
                ],
            })
            after = last
            sent = True
        elif time.monotonic() >= next_heartbeat:
            await send({"type": "heartbeat", "cursor": _cursor(grant, after)})
            sent = True
        if sent:
            next_heartbeat = time.monotonic() + limits.heartbeat_interval
        # No lossy live queue/handoff: the next page always reads durable state.
        # Poll even when no bus notification arrives; disconnect cancels sleep.
        if after == page.head:
            delay = min(limits.poll_interval, limits.heartbeat_interval, max(0, expiry - time.time()))
            await asyncio.sleep(delay)
        else:
            await asyncio.sleep(0)


def create_replay_router(
    source: ReplaySource, *, allowed_origins: tuple[str, ...], limits: ReplayLimits | None = None,
) -> APIRouter:
    """Create ``/{app_id}/{stream_id}``; the host chooses its route prefix.

    Browser Origins must match an explicit origin; non-browser clients may omit
    Origin, but still require validated bearer authentication with finite expiry.
    Only the optional ``cursor`` query is accepted, even in query-token dev mode.
    No client application messages are supported. Every connection owns and
    cancels its read/send task; no global worker, persistence or queue is created.
    """
    limits = limits or ReplayLimits()
    origins = frozenset(allowed_origins)
    for origin in origins:
        parsed = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password
            or origin != f"{parsed.scheme}://{parsed.netloc}"
        ):
            raise ValueError("Replay allowed_origins must contain exact HTTP(S) origins")
    router = APIRouter()

    @router.websocket("/{app_id}/{stream_id}")
    async def replay(websocket: WebSocket, app_id: str, stream_id: str) -> None:
        tasks: list[asyncio.Task] = []
        try:
            if not _identifier(app_id) or not _identifier(stream_id):
                raise _Refusal(1008, "Invalid replay scope")
            query = websocket.query_params.multi_items()
            if any(key != "cursor" for key, _ in query) or len(query) > 1:
                raise _Refusal(1008, "Only a replay cursor is permitted in the URL")
            origin_headers = websocket.headers.getlist("origin")
            if len(origin_headers) > 1 or (origin_headers and origin_headers[0] not in origins):
                raise _Refusal(1008, "Replay origin denied")
            resume = _parse_cursor(query[0][1], app_id, stream_id) if query else None
            principal = await authenticate_websocket(websocket)
            if principal is None:
                return
            if (
                not principal.is_authenticated or not principal.user_id or not principal.validate_app_id(app_id)
                or principal.chat_id is not None
            ):
                raise _Refusal(1008, "Replay identity denied")
            expiry = _expiry(principal)
            await accept_websocket(websocket)

            async def receive() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message["type"] == "websocket.receive":
                        raise _Refusal(1008, "Replay is delivery only")

            tasks = [
                asyncio.create_task(receive()),
                asyncio.create_task(_deliver(websocket, source, principal, app_id, stream_id, resume, limits, expiry)),
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()
        except _Refusal as refusal:
            await _close(websocket, refusal.code, refusal.reason, limits.send_timeout)
        except PermissionError:
            await _close(websocket, 1008, "Replay access denied", limits.send_timeout)
        except (WebSocketDisconnect, OSError):
            pass
        except Exception:
            # Do not disclose/log source exceptions, credentials or projections.
            await _close(websocket, 1011, "Replay source unavailable", limits.send_timeout)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await _close(websocket, 1000, "Replay ended", limits.send_timeout)

    return router


__all__ = ["ReplayEvent", "ReplayGrant", "ReplayLimits", "ReplayPage", "ReplaySource", "create_replay_router"]
