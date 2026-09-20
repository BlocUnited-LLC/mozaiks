# ==============================================================================
# FILE: mozaiksai/core/runtime/persistence/distributed_lock.py
# DESCRIPTION: MongoDB-backed distributed chat execution lease.
#              Guarantees at most one runtime instance executes a mutable
#              start/resume/restart for a given (app_id, chat_id) at a time,
#              so two instances cannot interleave session writes and WAL
#              sequence allocation for the same chat.
#
# Lease semantics (same idiom as core/workflow/queue.py):
#   Acquire: atomic insert / steal-expired findOneAndUpdate on a unique
#            (resource) index. Exactly one holder wins.
#   Renew:   a background task extends expires_at while the protected
#            operation runs; renewal failure marks the lease LOST.
#   Release: delete by (resource, holder_id) — a stale holder can never
#            delete a successor's lease.
#   Expire:  TTL index on expires_at cleans up leases from crashed holders.
#
# Operating modes (explicit, resolved once at host startup):
#   required — the Mongo lock authority must be reachable; acquisition fails
#              closed (ChatLockAuthorityUnavailableError) before any session
#              or WAL mutation when it is not. This is the only mode that
#              provides cross-instance exclusion.
#   local    — process-local asyncio locks. Single-process boundary only;
#              never distributed safety. Used when database persistence is
#              disabled (no Mongo configured) and in unconfigured embedded /
#              test processes.
#
# Hosts call configure_chat_lock() during startup; it resolves `required`
# whenever database persistence is enabled for the process. An unconfigured
# process (unit tests, embedded transports that never run host startup)
# stays in local mode unless MOZAIKS_CHAT_LOCK_MODE overrides it.
#
# Known residual window (documented, not fenced in this slice): a holder that
# stalls past its TTL without a failed renewal can have at most one in-flight
# write land after a successor acquires. Confirmed lease loss cancels the
# protected execution and stops all further guarded durable writes via
# assert_chat_mutable(). Storage-level fencing tokens are a later sub-slice of
# issue #426.
#
# Configuration (env vars):
#   MOZAIKS_CHAT_LOCK_MODE         — "required" | "local" explicit override
#   DISTRIBUTED_LOCK_TTL_SECONDS   — lease TTL in seconds (default 60)
#   DISTRIBUTED_LOCK_RETRY_DELAY   — retry interval on contention (default 0.2)
#   DISTRIBUTED_LOCK_MAX_RETRIES   — max acquisition retries (default 15 = 3s)
# ==============================================================================
from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pymongo.errors import DuplicateKeyError

from logs.logging_config import get_core_logger
from mozaiksai.core.multitenant.app_ids import normalize_app_id

logger = get_core_logger("distributed_lock")

_LOCK_COLLECTION = "distributed_locks"

CHAT_LOCK_MODE_ENV = "MOZAIKS_CHAT_LOCK_MODE"

_RELEASE_TIMEOUT_SECONDS = 5.0


class ChatLockMode(StrEnum):
    REQUIRED = "required"
    LOCAL = "local"


def _ttl() -> int:
    try:
        return max(2, int(os.getenv("DISTRIBUTED_LOCK_TTL_SECONDS", "60").strip()))
    except (ValueError, AttributeError):
        return 60


def _retry_delay() -> float:
    try:
        return float(os.getenv("DISTRIBUTED_LOCK_RETRY_DELAY", "0.2").strip())
    except (ValueError, AttributeError):
        return 0.2


def _max_retries() -> int:
    try:
        return int(os.getenv("DISTRIBUTED_LOCK_MAX_RETRIES", "15").strip())
    except (ValueError, AttributeError):
        return 15


class ChatLockError(Exception):
    """Base class for chat execution lease failures."""


class LockAcquisitionError(ChatLockError):
    """The resource is held by another owner and stayed held for the retry budget."""

    def __init__(self, resource: str) -> None:
        super().__init__(f"Could not acquire lock for resource '{resource}' within retry budget")
        self.resource = resource


class ChatLockAuthorityUnavailableError(ChatLockError):
    """The Mongo lock authority is unreachable while mode is `required`.

    Raised before any session/WAL mutation — callers must fail closed.
    """

    def __init__(self, resource: str, cause: str = "") -> None:
        detail = f": {cause}" if cause else ""
        super().__init__(f"Chat lock authority unavailable for resource '{resource}'{detail}")
        self.resource = resource


class ChatLeaseLostError(ChatLockError):
    """This process's lease for the chat was lost; durable writes are refused."""

    def __init__(self, resource: str) -> None:
        super().__init__(
            f"Chat execution lease lost for resource '{resource}'; refusing further durable writes"
        )
        self.resource = resource


def chat_lock_resource(app_id: Any, chat_id: str) -> str:
    """Canonical tenant-scoped lock identity for a chat.

    Scope comes from the same normalization authority as Mongo app-scope
    filters, so identical chat ids under different tenants never collide.
    """
    scope = normalize_app_id(app_id) or "__invalid__"
    return f"chat:{scope}:{chat_id}"


# ------------------------------------------------------------------------------
# Mode configuration
# ------------------------------------------------------------------------------

_configured_mode: ChatLockMode | None = None


def _env_mode_override() -> ChatLockMode | None:
    raw = (os.getenv(CHAT_LOCK_MODE_ENV) or "").strip().lower()
    if not raw:
        return None
    if raw in (ChatLockMode.REQUIRED, ChatLockMode.LOCAL):
        return ChatLockMode(raw)
    logger.warning("Invalid %s=%r; ignoring override", CHAT_LOCK_MODE_ENV, raw)
    return None


def configure_chat_lock(mode: ChatLockMode | str | None = None) -> ChatLockMode:
    """Resolve and pin the chat lock operating mode. Called at host startup.

    Resolution order: explicit ``mode`` argument, ``MOZAIKS_CHAT_LOCK_MODE``,
    then ``required`` when database persistence is enabled for this process
    (the multi-instance posture), else ``local``.
    """
    global _configured_mode
    resolved: ChatLockMode | None = ChatLockMode(mode) if mode else _env_mode_override()
    if resolved is None:
        from mozaiksai.core.runtime.persistence.startup_policy import (
            database_persistence_is_enabled,
            get_database_startup_policy,
        )

        policy = get_database_startup_policy()
        resolved = (
            ChatLockMode.REQUIRED
            if database_persistence_is_enabled(policy)
            else ChatLockMode.LOCAL
        )
    _configured_mode = resolved
    logger.info("Chat lock mode configured: %s", resolved)
    return resolved


def get_chat_lock_mode() -> ChatLockMode:
    """Current operating mode.

    Unconfigured processes (no host startup ran) default to ``local`` unless
    the env override says otherwise — local mode is a truthful description of
    a process that never entered the host lifecycle, not distributed safety.
    """
    if _configured_mode is not None:
        return _configured_mode
    return _env_mode_override() or ChatLockMode.LOCAL


def reset_chat_lock_state() -> None:
    """Test hook: clear configured mode, lease registry, and local locks."""
    global _configured_mode, _acquisition_indexes_verified
    _configured_mode = None
    _acquisition_indexes_verified = False
    _process_leases.clear()
    _local_locks.clear()


# ------------------------------------------------------------------------------
# Mongo authority
# ------------------------------------------------------------------------------

def _get_lock_collection() -> Any | None:
    try:
        from mozaiksai.core.core_config import get_mongo_client

        # Lazy import: persistence_manager imports this module at load time,
        # so a top-level namespaces import would be circular.
        from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE

        client = get_mongo_client()
        if client is None:
            return None
        return client[SYSTEM_DATABASE][_LOCK_COLLECTION]
    except Exception as exc:
        logger.debug("Lock collection unavailable: %s", exc)
        return None


async def ensure_lock_indexes() -> None:
    """Create TTL + unique resource indexes — call once at startup.

    The TTL index auto-cleans leases from crashed holders after expiry,
    preventing lock starvation.
    """
    collection = _get_lock_collection()
    if collection is None:
        return
    try:
        await collection.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="lock_ttl_idx",
            background=True,
        )
        await collection.create_index(
            [("resource", 1)],
            unique=True,
            name="lock_resource_unique_idx",
            background=True,
        )
        logger.debug("Distributed lock indexes ensured")
    except Exception as exc:
        logger.warning("Could not ensure lock indexes: %s", exc)


_acquisition_indexes_verified = False


def _is_unique_resource_index(spec: dict[str, Any]) -> bool:
    if not spec.get("unique"):
        return False
    key = spec.get("key") or []
    pairs = list(key.items()) if hasattr(key, "items") else list(key)
    return [(str(field), int(direction)) for field, direction in pairs] == [("resource", 1)]


async def _verify_acquisition_indexes(collection: Any) -> None:
    """Ensure the unique resource index exists before the first acquisition.

    The unique index is the atomicity backstop of the insert-based acquire:
    without it two holders can insert duplicate lease documents and both
    believe they own the chat. Index creation failures are therefore not a
    warning here — required mode fails closed.
    """
    global _acquisition_indexes_verified
    if _acquisition_indexes_verified:
        return
    try:
        await collection.create_index(
            [("resource", 1)],
            unique=True,
            name="lock_resource_unique_idx",
            background=True,
        )
        index_info = await collection.index_information()
    except Exception as exc:
        raise ChatLockAuthorityUnavailableError(
            "*", f"could not ensure unique lock index: {exc}"
        ) from exc
    if not any(_is_unique_resource_index(spec) for spec in index_info.values()):
        raise ChatLockAuthorityUnavailableError(
            "*", "unique lock index missing after creation attempt"
        )
    _acquisition_indexes_verified = True


async def _try_acquire(collection: Any, resource: str, holder_id: str, ttl_seconds: int) -> bool:
    """Attempt a single atomic lock acquisition. Returns True on success."""
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    # Steal an expired lease, or re-assert one this holder already owns.
    result = await collection.find_one_and_update(
        {
            "resource": resource,
            "$or": [
                {"expires_at": {"$lt": now}},
                {"holder_id": holder_id},
            ],
        },
        {
            "$set": {
                "resource": resource,
                "holder_id": holder_id,
                "expires_at": expires_at,
                "acquired_at": now.isoformat(),
            }
        },
        upsert=False,
        return_document=True,
    )
    if result is not None:
        return True

    # No expired lease exists — try a clean insert; the unique index on
    # `resource` makes exactly one concurrent inserter win. Only a proven
    # uniqueness race is contention; authority failures must use the distinct
    # unavailable diagnostic.
    try:
        await collection.insert_one({
            "resource": resource,
            "holder_id": holder_id,
            "expires_at": expires_at,
            "acquired_at": now.isoformat(),
        })
        return True
    except DuplicateKeyError:
        return False


# ------------------------------------------------------------------------------
# Lease handle + process registry
# ------------------------------------------------------------------------------

class ChatLease:
    """Handle for one held chat execution lease.

    ``lost`` flips to True on confirmed lease loss (renewal found the lease
    gone, owned by a successor, or unprovable past its TTL). Guarded durable
    writes consult it through assert_chat_mutable().
    """

    def __init__(
        self,
        *,
        resource: str,
        holder_id: str,
        mode: ChatLockMode,
        collection: Any | None,
        ttl_seconds: int,
    ) -> None:
        self.resource = resource
        self.holder_id = holder_id
        self.mode = mode
        self._collection = collection
        self._ttl_seconds = ttl_seconds
        self._expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        self._lost = False
        self._owner_task: asyncio.Task[Any] | None = None
        self._renew_task: asyncio.Task[None] | None = None

    @property
    def lost(self) -> bool:
        return self._lost

    def _mark_lost(self, reason: str) -> None:
        if not self._lost:
            self._lost = True
            logger.error(
                "CHAT_LOCK_RENEWAL_LOST resource=%s holder=%s: %s — cancelling protected execution and refusing durable writes",
                self.resource,
                self.holder_id,
                reason,
            )
            owner = self._owner_task
            if owner is not None and not owner.done():
                owner.cancel(f"chat execution lease lost: {self.resource}")

    async def _renew_once(self) -> None:
        """Extend the lease; mark lost when ownership can no longer be proven."""
        if self._collection is None or self._lost:
            return
        now = datetime.now(UTC)
        new_expiry = now + timedelta(seconds=self._ttl_seconds)
        try:
            result = await self._collection.find_one_and_update(
                {"resource": self.resource, "holder_id": self.holder_id},
                {"$set": {"expires_at": new_expiry, "renewed_at": now.isoformat()}},
                upsert=False,
                return_document=True,
            )
        except Exception as exc:
            # Transient authority failure: ownership is unprovable. Only a
            # lease that has actually passed its TTL may have been stolen.
            if datetime.now(UTC) >= self._expires_at:
                self._mark_lost(f"renewal unreachable past TTL ({exc})")
            else:
                logger.warning(
                    "CHAT_LOCK_RENEWAL_RETRY resource=%s holder=%s: %s",
                    self.resource,
                    self.holder_id,
                    exc,
                )
            return
        if result is None:
            self._mark_lost("lease document missing or owned by a successor")
            return
        self._expires_at = new_expiry

    async def _renew_loop(self) -> None:
        interval = max(self._ttl_seconds / 3.0, 1.0)
        while not self._lost:
            await asyncio.sleep(interval)
            await self._renew_once()

    def start_renewal(self) -> None:
        if self._collection is not None and self._renew_task is None:
            self._owner_task = asyncio.current_task()
            self._renew_task = asyncio.create_task(
                self._renew_loop(), name=f"chat-lease-renew:{self.resource}"
            )

    async def stop_renewal(self) -> None:
        task = self._renew_task
        self._renew_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    async def release(self) -> None:
        """Delete this holder's lease document. Never touches a successor's.

        The (resource, holder_id) filter means a stale holder's release
        matches nothing once a successor owns the resource.
        """
        if self._collection is None:
            return
        try:
            await self._collection.delete_one(
                {"resource": self.resource, "holder_id": self.holder_id}
            )
        except Exception as exc:
            logger.warning(
                "CHAT_LOCK_RELEASE_FAILED resource=%s holder=%s: %s — the TTL index reclaims the lease at expiry",
                self.resource,
                self.holder_id,
                exc,
            )


# Leases currently held by this process, keyed by resource. Consulted by
# assert_chat_mutable() at durable chat write seams.
_process_leases: dict[str, ChatLease] = {}


def assert_chat_mutable(*, app_id: Any, chat_id: str) -> None:
    """Refuse durable chat writes after confirmed lease loss.

    No-op when this process holds no lease for the chat (read paths, local
    mode, and processes outside the host lifecycle keep current behavior).
    """
    lease = _process_leases.get(chat_lock_resource(app_id, chat_id))
    if lease is not None and lease.lost:
        raise ChatLeaseLostError(lease.resource)


# ------------------------------------------------------------------------------
# Local (single-process) mode
# ------------------------------------------------------------------------------

class _LocalLockEntry:
    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.refs = 0


_local_locks: dict[str, _LocalLockEntry] = {}


def _default_holder_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid4().hex}"


# ------------------------------------------------------------------------------
# Acquisition context manager
# ------------------------------------------------------------------------------

@asynccontextmanager
async def chat_execution_lease(
    *,
    app_id: Any,
    chat_id: str,
    holder_id: str | None = None,
):
    """Hold the exclusive execution lease for one chat across the protected op.

    Usage:
        async with chat_execution_lease(app_id=app_id, chat_id=chat_id):
            # only one instance may run a mutable start/resume for this chat
            ...

    Raises:
        LockAcquisitionError                — another owner holds the lease (busy).
        ChatLockAuthorityUnavailableError   — mode is `required` and the Mongo
                                              lock authority is unreachable;
                                              callers must fail closed before
                                              any session/WAL mutation.
    """
    resource = chat_lock_resource(app_id, chat_id)
    mode = get_chat_lock_mode()
    delay = _retry_delay()
    max_retries = _max_retries()

    if mode is ChatLockMode.LOCAL:
        entry = _local_locks.setdefault(resource, _LocalLockEntry())
        entry.refs += 1
        try:
            budget = delay * max_retries if delay * max_retries > 0 else 0.001
            try:
                await asyncio.wait_for(entry.lock.acquire(), timeout=budget)
            except TimeoutError:
                logger.warning("CHAT_LOCK_BUSY resource=%s mode=local", resource)
                raise LockAcquisitionError(resource) from None
            lease = ChatLease(
                resource=resource,
                holder_id=holder_id or _default_holder_id(),
                mode=mode,
                collection=None,
                ttl_seconds=_ttl(),
            )
            _process_leases[resource] = lease
            try:
                yield lease
            finally:
                if _process_leases.get(resource) is lease:
                    _process_leases.pop(resource, None)
                entry.lock.release()
        finally:
            entry.refs -= 1
            if entry.refs <= 0 and not entry.lock.locked():
                _local_locks.pop(resource, None)
        return

    collection = _get_lock_collection()
    if collection is None:
        logger.error(
            "CHAT_LOCK_AUTHORITY_UNAVAILABLE resource=%s — refusing execution before session/WAL mutation",
            resource,
        )
        raise ChatLockAuthorityUnavailableError(resource, "MongoDB lock collection unavailable")

    await _verify_acquisition_indexes(collection)

    # Never replace a still-running holder in the process registry. Mongo
    # remains the cross-process authority, while this check closes the local
    # expiry/takeover window in which a successor could otherwise hide the
    # stale holder's lost state from assert_chat_mutable().
    if resource in _process_leases:
        logger.warning("CHAT_LOCK_BUSY resource=%s mode=required local_holder=true", resource)
        raise LockAcquisitionError(resource)

    effective_holder = holder_id or _default_holder_id()
    ttl = _ttl()

    acquired = False
    for attempt in range(max_retries + 1):
        try:
            acquired = await _try_acquire(collection, resource, effective_holder, ttl)
        except Exception as exc:
            logger.error(
                "CHAT_LOCK_AUTHORITY_UNAVAILABLE resource=%s during acquisition: %s",
                resource,
                exc,
            )
            raise ChatLockAuthorityUnavailableError(resource, str(exc)) from exc
        if acquired:
            break
        if attempt < max_retries:
            logger.debug(
                "LOCK_CONTENTION resource=%s attempt=%d/%d — retrying in %.2fs",
                resource, attempt + 1, max_retries, delay,
            )
            await asyncio.sleep(delay)

    if not acquired:
        logger.warning("CHAT_LOCK_BUSY resource=%s holder=%s", resource, effective_holder)
        raise LockAcquisitionError(resource)

    lease = ChatLease(
        resource=resource,
        holder_id=effective_holder,
        mode=mode,
        collection=collection,
        ttl_seconds=ttl,
    )
    _process_leases[resource] = lease
    lease.start_renewal()
    logger.debug("LOCK_ACQUIRED resource=%s holder=%s", resource, effective_holder)
    try:
        try:
            yield lease
        except asyncio.CancelledError as exc:
            if lease.lost:
                raise ChatLeaseLostError(resource) from exc
            raise
        if lease.lost:
            # The protected operation may have consumed cancellation as part
            # of its own shutdown protocol. Never let that turn confirmed
            # lease loss into an apparent successful run.
            raise ChatLeaseLostError(resource)
    finally:
        if _process_leases.get(resource) is lease:
            _process_leases.pop(resource, None)
        await lease.stop_renewal()
        # Bounded, cancellation-safe release: the delete keeps running in its
        # own task even if the surrounding task is being cancelled.
        release_task = asyncio.create_task(lease.release())
        try:
            await asyncio.wait_for(asyncio.shield(release_task), timeout=_RELEASE_TIMEOUT_SECONDS)
        except (TimeoutError, asyncio.CancelledError):
            # TTL expiry reclaims the lease if the detached delete also fails.
            logger.warning(
                "CHAT_LOCK_RELEASE_FAILED resource=%s holder=%s: release interrupted; TTL will reclaim",
                resource,
                effective_holder,
            )
        logger.debug("LOCK_RELEASED resource=%s holder=%s", resource, effective_holder)


__all__ = [
    "CHAT_LOCK_MODE_ENV",
    "ChatLease",
    "ChatLeaseLostError",
    "ChatLockAuthorityUnavailableError",
    "ChatLockError",
    "ChatLockMode",
    "LockAcquisitionError",
    "assert_chat_mutable",
    "chat_execution_lease",
    "chat_lock_resource",
    "configure_chat_lock",
    "ensure_lock_indexes",
    "get_chat_lock_mode",
    "reset_chat_lock_state",
]
