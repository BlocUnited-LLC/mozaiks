from __future__ import annotations

"""Provider-neutral token wallet accounting.

This module owns runtime token balances and append-only wallet entries. It does
not know about payment providers, checkout sessions, invoices, or hosted-product
pricing. Payment systems and generated app modules call this primitive after
their own business checks succeed.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from logs.logging_config import get_core_logger
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.multitenant import coalesce_app_id
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    TokenAllowanceDef,
    TokenWalletDef,
)

logger = get_core_logger("token_wallet")

BALANCES_COLLECTION = RuntimeCollections.RUNTIME_TOKEN_WALLET_BALANCES
ENTRIES_COLLECTION = RuntimeCollections.RUNTIME_TOKEN_WALLET_ENTRIES

TokenWalletOperation = Literal[
    "allocation",
    "credit",
    "refund",
    "adjustment_credit",
    "usage_debit",
    "debit",
    "adjustment_debit",
]
TokenWalletStatus = Literal["pending", "applied", "rejected"]

_CREDIT_OPERATIONS = {"allocation", "credit", "refund", "adjustment_credit"}
_DEBIT_OPERATIONS = {"usage_debit", "debit", "adjustment_debit"}

# Bounded attempts to take ownership of a movement's reservation. Every losing
# attempt observes a strictly changed reservation — inserted, adopted, or
# finalized — so contention resolves in a couple of rounds or is not
# contention at all.
_RESERVATION_ATTEMPTS = 5


class TokenWalletReservationUnavailable(RuntimeError):
    """The durable reservation for a movement could not be acquired.

    Reported instead of moving the balance. A credit recorded without the
    entry that explains it is worse than a credit that has to be retried.
    """

    def __init__(self, *, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(
            f"token wallet reservation could not be acquired for entry {entry_id!r}"
        )


# Rejection reason for a movement refused because a newer revision of the same
# entitlement subject has already committed.
STALE_SUBJECT_REVISION_REASON = "stale_subject_revision"

# Sub-document on the balance holding the highest revision committed per
# entitlement subject. Nested under one key so the balance keeps exactly one
# ordering surface no matter how many subjects share a wallet scope.
_SUBJECT_REVISIONS_FIELD = "subject_revisions"


def _subject_revision_path(subject_key: str | None) -> str | None:
    """Dotted path holding one subject's committed revision, or None.

    The key is sanitized to hex-safe characters because it becomes a Mongo
    field name: a dot would silently create nesting and a `$` would be
    rejected, so an opaque caller identity must never reach the path raw.
    """
    key = str(subject_key or "").strip()
    if not key:
        return None
    safe = "".join(char for char in key if char.isalnum() or char in "_-")
    if not safe:
        return None
    return f"{_SUBJECT_REVISIONS_FIELD}.{safe}"


def _subject_revision_is_stale(
    balance: dict[str, Any] | None,
    revision_path: str | None,
    subject_revision: int | None,
) -> bool:
    """Did this balance already commit a strictly newer revision?"""
    if balance is None or revision_path is None or subject_revision is None:
        return False
    committed = (balance.get(_SUBJECT_REVISIONS_FIELD) or {}).get(
        revision_path.split(".", 1)[-1]
    )
    return isinstance(committed, int) and committed > subject_revision


_FORBIDDEN_METADATA_KEY_FRAGMENTS = (
    "secret",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "private_key",
)
_FORBIDDEN_VALUE_MARKERS = (
    "-----BEGIN",
    "ghp_",
    "github_pat_",
    "sk-",
    "xoxb-",
    "xoxp-",
)


@dataclass(frozen=True)
class TokenWalletScope:
    app_id: str
    wallet_id: str
    scope_type: Literal["user", "tenant", "app"]
    scope_id: str
    user_id: str | None = None
    tenant_id: str | None = None

    @property
    def balance_id(self) -> str:
        return f"{self.app_id}:{self.wallet_id}:{self.scope_type}:{self.scope_id}"


@dataclass(frozen=True)
class TokenWalletEntryResult:
    status: TokenWalletStatus
    entry: dict[str, Any]
    balance: dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()  # type: ignore[no-any-return]
    text = _text(value)
    return text or None


def _forbidden_metadata_path(payload: Any, *, path: str = "") -> str | None:
    if isinstance(payload, dict):
        for raw_key, value in payload.items():
            key = str(raw_key)
            normalized = key.strip().lower()
            key_path = f"{path}.{key}" if path else key
            if any(fragment in normalized for fragment in _FORBIDDEN_METADATA_KEY_FRAGMENTS):
                return key_path
            nested = _forbidden_metadata_path(value, path=key_path)
            if nested:
                return nested
    elif isinstance(payload, list):
        for idx, value in enumerate(payload):
            item_path = f"{path}[{idx}]" if path else f"[{idx}]"
            nested = _forbidden_metadata_path(value, path=item_path)
            if nested:
                return nested
    else:
        text = str(payload or "")
        if any(marker in text for marker in _FORBIDDEN_VALUE_MARKERS):
            return path or "value"
    return None


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(metadata or {})
    forbidden = _forbidden_metadata_path(value)
    if forbidden:
        raise ValueError(f"token wallet metadata must not contain secret-shaped data: {forbidden}")
    return value


def _scope_for(
    *,
    app_id: str,
    wallet_id: str,
    user_id: str | None = None,
    tenant_id: str | None = None,
    preferred_scope: Literal["user", "tenant"] | None = None,
) -> TokenWalletScope:
    resolved_app_id = _text(coalesce_app_id(app_id=app_id))
    resolved_wallet_id = _text(wallet_id) or "ai_tokens"
    resolved_user_id = _text(user_id) or None
    resolved_tenant_id = _text(tenant_id) or None
    if not resolved_app_id:
        raise ValueError("app_id is required")
    if not resolved_wallet_id:
        raise ValueError("wallet_id is required")

    if preferred_scope == "tenant" and resolved_tenant_id:
        return TokenWalletScope(
            app_id=resolved_app_id,
            wallet_id=resolved_wallet_id,
            scope_type="tenant",
            scope_id=resolved_tenant_id,
            user_id=resolved_user_id,
            tenant_id=resolved_tenant_id,
        )
    if preferred_scope == "user" and resolved_user_id:
        return TokenWalletScope(
            app_id=resolved_app_id,
            wallet_id=resolved_wallet_id,
            scope_type="user",
            scope_id=resolved_user_id,
            user_id=resolved_user_id,
            tenant_id=resolved_tenant_id,
        )
    if resolved_tenant_id:
        return TokenWalletScope(
            app_id=resolved_app_id,
            wallet_id=resolved_wallet_id,
            scope_type="tenant",
            scope_id=resolved_tenant_id,
            user_id=resolved_user_id,
            tenant_id=resolved_tenant_id,
        )
    if resolved_user_id:
        return TokenWalletScope(
            app_id=resolved_app_id,
            wallet_id=resolved_wallet_id,
            scope_type="user",
            scope_id=resolved_user_id,
            user_id=resolved_user_id,
            tenant_id=resolved_tenant_id,
        )
    return TokenWalletScope(
        app_id=resolved_app_id,
        wallet_id=resolved_wallet_id,
        scope_type="app",
        scope_id="app",
        user_id=None,
        tenant_id=None,
    )


def _entry_id(scope: TokenWalletScope, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{scope.balance_id}:{idempotency_key}".encode()).hexdigest()
    return f"token_wallet_entry:{digest}"


def _observed_reservation_predicate(existing: Mapping[str, Any]) -> dict[str, Any]:
    """Compare-and-swap against exactly what was observed, absence included.

    An entry written before reservations were tracked carries neither field.
    Reading a missing field as Python `None` or `0` and then querying for that
    value is not the same question: Mongo matches `{"field": 0}` only against a
    stored zero, so the swap would never match the very record it is trying to
    take over — and a movement whose balance already committed would become
    unrecoverable. Absence is a state, so it is compared as one.
    """
    predicate: dict[str, Any] = {}
    for field in ("reservation_owner", "reservation_generation"):
        predicate[field] = (
            existing[field] if field in existing else {"$exists": False}
        )
    return predicate


def _idempotency_conflict(existing: dict[str, Any], requested: dict[str, Any]) -> bool:
    for key in ("balance_id", "operation", "direction", "amount", "signed_amount"):
        if existing.get(key) != requested.get(key):
            return True
    return False


def _empty_balance(scope: TokenWalletScope) -> dict[str, Any]:
    return {
        "_id": scope.balance_id,
        "app_id": scope.app_id,
        "wallet_id": scope.wallet_id,
        "scope_type": scope.scope_type,
        "scope_id": scope.scope_id,
        "user_id": scope.user_id,
        "tenant_id": scope.tenant_id,
        "balance": 0,
        "total_allocated": 0,
        "total_credited": 0,
        "total_spent": 0,
        "total_refunded": 0,
        "entry_count": 0,
        "updated_at": None,
        "created_at": None,
    }


def public_balance(balance: dict[str, Any] | None, scope: TokenWalletScope | None = None) -> dict[str, Any]:
    data = dict(balance or (_empty_balance(scope) if scope else {}))
    data.pop("applied_entry_ids", None)
    # Internal ordering state, never part of the public balance surface.
    data.pop(_SUBJECT_REVISIONS_FIELD, None)
    for key in ("balance", "total_allocated", "total_credited", "total_spent", "total_refunded", "entry_count"):
        data[key] = _positive_int(data.get(key))
    if "_id" in data:
        data["balance_id"] = data.pop("_id")
    for key in ("created_at", "updated_at"):
        if key in data:
            data[key] = _iso(data.get(key))
    return data


def public_entry(entry: dict[str, Any]) -> dict[str, Any]:
    data = dict(entry or {})
    # Internal reservation bookkeeping, never part of the public entry surface.
    data.pop("reservation_owner", None)
    data.pop("reservation_generation", None)
    if "_id" in data:
        data["entry_id"] = data.pop("_id")
    for key in ("created_at", "updated_at", "applied_at", "rejected_at"):
        if key in data:
            data[key] = _iso(data.get(key))
    return data


class TokenWalletLedger:
    """Append-only token wallet ledger with balance projection."""

    def __init__(self, *, database: Any | None = None) -> None:
        self._database = database
        self._indexes_ready = False

    async def _db(self) -> Any:
        if self._database is not None:
            return self._database
        client = get_mongo_client()
        return client[SYSTEM_DATABASE]

    async def _collections(self) -> tuple[Any, Any]:
        db = await self._db()
        balances = db[BALANCES_COLLECTION]
        entries = db[ENTRIES_COLLECTION]
        if not self._indexes_ready:
            try:
                await entries.create_index("idempotency_key", name="token_wallet_idempotency")
                await entries.create_index(
                    [("app_id", 1), ("wallet_id", 1), ("scope_type", 1), ("scope_id", 1), ("created_at", -1)],
                    name="token_wallet_entries_scope_time",
                )
                await balances.create_index(
                    [("app_id", 1), ("wallet_id", 1), ("scope_type", 1), ("scope_id", 1)],
                    name="token_wallet_balance_scope",
                )
            except Exception as exc:
                logger.debug("token wallet index ensure skipped: %s", exc)
            self._indexes_ready = True
        return balances, entries

    async def _acquire_entry_reservation(
        self,
        entries: Any,
        *,
        entry_id: str,
        entry: dict[str, Any],
        reservation_owner: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        """Take positive ownership of this movement's durable reservation.

        Returns None once this invocation owns a pending reservation, or the
        existing terminal entry when the movement already settled. It never
        returns "nothing happened": the caller may only touch the balance after
        a None, so there is no path where a balance moves under a reservation
        somebody else holds — or under none at all.

        Each attempt re-reads before acting, because every observation here can
        be invalidated the instant after it is made. A pending reservation may
        be deleted by the attempt that made it, adopted by a newer entitled
        revision, or finalized. So adoption is a compare-and-swap on the exact
        observed owner AND generation, and a lost swap is not a failure — it
        means the world moved, so look again and answer the new state. The
        generation is what makes an owner that is handed back and forth still
        distinguishable.

        Attempts are bounded. Contention that will not settle is an operational
        outcome, not a licence to write anyway.
        """
        for _attempt in range(_RESERVATION_ATTEMPTS):
            existing = await entries.find_one({"_id": entry_id})
            if existing is None:
                try:
                    await entries.insert_one(entry)
                    return None
                except DuplicateKeyError:
                    # Someone reserved between the read and the insert.
                    continue

            if _idempotency_conflict(existing, entry):
                raise ValueError(
                    "idempotency_key was reused with different token wallet entry data"
                )

            status = existing.get("status")
            if status in {"applied", "rejected"}:
                return dict(existing)

            if status != "pending":
                continue

            adopted = await entries.find_one_and_update(
                {
                    "_id": entry_id,
                    "status": "pending",
                    **_observed_reservation_predicate(existing),
                },
                {
                    "$set": {
                        "reservation_owner": reservation_owner,
                        "updated_at": now,
                    },
                    "$inc": {"reservation_generation": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if adopted is not None:
                return None

        raise TokenWalletReservationUnavailable(entry_id=entry_id)

    async def record_entry(
        self,
        *,
        app_id: str,
        wallet_id: str = "ai_tokens",
        amount: int,
        operation: TokenWalletOperation,
        idempotency_key: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        preferred_scope: Literal["user", "tenant"] | None = None,
        source: str = "runtime",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_negative_balance: bool = False,
        usage_event_id: str | None = None,
        subject_key: str | None = None,
        subject_revision: int | None = None,
    ) -> TokenWalletEntryResult:
        """Record one idempotent wallet movement.

        ``subject_key`` / ``subject_revision`` are an optional ordering fence.
        When both are supplied, the movement commits only while this revision
        is the newest one seen for that subject, and the check happens inside
        the same single-document update that changes the balance — so a
        revision that lost the race cannot mint anything, even if it was
        already mid-flight when the newer one committed. The ledger never
        interprets ``subject_key``; it is an opaque identity owned by whatever
        ordering authority the caller uses. Omit both for unordered movements
        (the default), which behave exactly as before.
        """
        if operation not in _CREDIT_OPERATIONS and operation not in _DEBIT_OPERATIONS:
            raise ValueError(f"unsupported token wallet operation: {operation}")
        resolved_amount = _positive_int(amount)
        if resolved_amount <= 0:
            raise ValueError("amount must be greater than zero")
        idempotency_value = _text(idempotency_key)
        if not idempotency_value:
            raise ValueError("idempotency_key is required")

        direction = "credit" if operation in _CREDIT_OPERATIONS else "debit"
        signed_amount = resolved_amount if direction == "credit" else -resolved_amount
        scope = _scope_for(
            app_id=app_id,
            wallet_id=wallet_id,
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=preferred_scope,
        )
        entry_id = _entry_id(scope, idempotency_value)
        reservation_owner = f"rsv_{uuid4().hex}"
        now = _now()
        balance_seed = {
            "_id": scope.balance_id,
            "app_id": scope.app_id,
            "wallet_id": scope.wallet_id,
            "scope_type": scope.scope_type,
            "scope_id": scope.scope_id,
            "user_id": scope.user_id,
            "tenant_id": scope.tenant_id,
            "created_at": now,
        }
        entry = {
            "_id": entry_id,
            "entry_id": entry_id,
            "idempotency_key": idempotency_value,
            "balance_id": scope.balance_id,
            "app_id": scope.app_id,
            "wallet_id": scope.wallet_id,
            "scope_type": scope.scope_type,
            "scope_id": scope.scope_id,
            "user_id": scope.user_id,
            "tenant_id": scope.tenant_id,
            "operation": operation,
            "direction": direction,
            "amount": resolved_amount,
            "signed_amount": signed_amount,
            "status": "pending",
            # Who currently owns this reservation. A stale attempt may only
            # clean up a reservation it still owns; if a newer valid revision
            # has adopted it, ownership has moved and the stale cleanup must
            # not touch it.
            "reservation_owner": reservation_owner,
            "reservation_generation": 0,
            "source": _text(source) or "runtime",
            "reason": _text(reason) or None,
            "metadata": _safe_metadata(metadata),
            "usage_event_id": _text(usage_event_id) or None,
            "created_at": now,
            "updated_at": now,
        }

        balances, entries = await self._collections()
        # No balance may move without this invocation positively owning the
        # durable reservation that represents the movement. "I looked and
        # something was there" is not ownership: the reservation may be
        # deleted, adopted, or finalized between the read and the write.
        acquired = await self._acquire_entry_reservation(
            entries,
            entry_id=entry_id,
            entry=entry,
            reservation_owner=reservation_owner,
            now=now,
        )
        if acquired is not None:
            balance_doc = await balances.find_one({"_id": scope.balance_id})
            return TokenWalletEntryResult(
                status=str(acquired.get("status") or "applied"),  # type: ignore[arg-type]
                entry=public_entry(acquired),
                balance=public_balance(balance_doc, scope),
            )

        update_filter: dict[str, Any] = {
            "_id": scope.balance_id,
            "applied_entry_ids": {"$ne": entry_id},
        }
        if direction == "debit" and not allow_negative_balance:
            update_filter["balance"] = {"$gte": resolved_amount}

        revision_path = _subject_revision_path(subject_key)
        fenced = revision_path is not None and subject_revision is not None
        if revision_path is not None and subject_revision is not None:
            # The ordering predicate rides in the SAME filter as the balance
            # mutation, so "am I still current?" and "apply the movement" are
            # one atomic act. Checking the subject's revision separately and
            # then writing would leave a window for a newer revision to commit
            # in between — which is exactly the stale-allowance race.
            update_filter["$or"] = [
                {revision_path: {"$exists": False}},
                {revision_path: None},
                {revision_path: {"$lte": subject_revision}},
            ]

        inc: dict[str, int] = {
            "balance": signed_amount,
            "entry_count": 1,
        }
        if operation == "allocation":
            inc["total_allocated"] = resolved_amount
        elif operation == "refund":
            inc["total_refunded"] = resolved_amount
        elif direction == "credit":
            inc["total_credited"] = resolved_amount
        else:
            inc["total_spent"] = resolved_amount

        update_document: dict[str, Any] = {
            "$setOnInsert": balance_seed,
            "$set": {
                "updated_at": now,
            },
            "$inc": inc,
            "$addToSet": {"applied_entry_ids": entry_id},
        }
        if fenced:
            update_document["$max"] = {revision_path: subject_revision}

        try:
            balance_doc = await balances.find_one_and_update(
                update_filter,
                update_document,
                upsert=direction == "credit",
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # An upserting credit whose filter missed because the balance
            # already exists: the deterministic _id collides. Treat it as a
            # miss and classify below.
            balance_doc = None

        if balance_doc is None:
            current_balance = await balances.find_one({"_id": scope.balance_id})
            if current_balance and entry_id in set(current_balance.get("applied_entry_ids") or []):
                applied_entry = await self._mark_entry_applied(
                    entries, entry_id, current_balance, entry=entry
                )
                return TokenWalletEntryResult(
                    status="applied",
                    entry=public_entry(applied_entry),
                    balance=public_balance(current_balance, scope),
                )
            if fenced and _subject_revision_is_stale(
                current_balance, revision_path, subject_revision
            ):
                # A stale movement must leave no trace under the identity that
                # a SUCCESSFUL allocation owns. That identity is plan- and
                # period-scoped, so persisting a rejection here would block the
                # allocation a later valid revision of the same plan and period
                # is entitled to make — the stale attempt would permanently
                # deny a legitimate one. Roll back the reservation we made and
                # report the refusal without recording it.
                await entries.delete_one(
                    {
                        "_id": entry_id,
                        "status": "pending",
                        "reservation_owner": reservation_owner,
                    }
                )
                stale_entry = {
                    **entry,
                    "status": "rejected",
                    "rejection_reason": STALE_SUBJECT_REVISION_REASON,
                }
                return TokenWalletEntryResult(
                    status="rejected",
                    entry=public_entry(stale_entry),
                    balance=public_balance(current_balance, scope),
                )
            rejected_entry = await self._mark_entry_rejected(
                entries,
                entry_id,
                current_balance,
                reason="insufficient_balance",
            )
            return TokenWalletEntryResult(
                status="rejected",
                entry=public_entry(rejected_entry),
                balance=public_balance(current_balance, scope),
            )

        applied_entry = await self._mark_entry_applied(
            entries, entry_id, balance_doc, entry=entry
        )
        return TokenWalletEntryResult(
            status="applied",
            entry=public_entry(applied_entry),
            balance=public_balance(balance_doc, scope),
        )

    async def _mark_entry_applied(
        self,
        entries: Any,
        entry_id: str,
        balance: dict[str, Any],
        *,
        entry: dict[str, Any],
    ) -> dict[str, Any]:
        """Finalize the entry that explains a balance the wallet already moved.

        The write is an upsert on the movement's own facts. Reaching here means
        the balance counts this entry, so the entry must exist and must say so
        — an `applied` result whose entry document is absent would report a
        credit with no wallet or amount behind it. The reservation this
        invocation owns cannot ordinarily be removed underneath it, but the
        postcondition does not depend on that being true: if the document is
        gone, it is restored rather than papered over with an empty lookup.
        """
        now = _now()
        seed = {key: value for key, value in entry.items() if key != "_id"}
        for key in ("status", "updated_at"):
            seed.pop(key, None)
        await entries.update_one(
            {"_id": entry_id},
            {
                "$set": {
                    "status": "applied",
                    "balance_after": int(balance.get("balance") or 0),
                    "applied_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": seed,
            },
            upsert=True,
        )
        applied = await entries.find_one({"_id": entry_id}, {"_id": 0})
        if not applied:
            raise TokenWalletReservationUnavailable(entry_id=entry_id)
        return dict(applied)

    async def _mark_entry_rejected(
        self,
        entries: Any,
        entry_id: str,
        balance: dict[str, Any] | None,
        *,
        reason: str,
    ) -> dict[str, Any]:
        now = _now()
        await entries.update_one(
            {"_id": entry_id},
            {
                "$set": {
                    "status": "rejected",
                    "rejection_reason": reason,
                    "balance_after": int((balance or {}).get("balance") or 0),
                    "rejected_at": now,
                    "updated_at": now,
                }
            },
        )
        return await entries.find_one({"_id": entry_id}, {"_id": 0}) or {}

    async def credit(
        self,
        *,
        app_id: str,
        wallet_id: str = "ai_tokens",
        amount: int,
        idempotency_key: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        preferred_scope: Literal["user", "tenant"] | None = None,
        source: str = "credit",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> TokenWalletEntryResult:
        return await self.record_entry(
            app_id=app_id,
            wallet_id=wallet_id,
            amount=amount,
            operation="credit",
            idempotency_key=idempotency_key,
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=preferred_scope,
            source=source,
            reason=reason,
            metadata=metadata,
        )

    async def debit(
        self,
        *,
        app_id: str,
        wallet_id: str = "ai_tokens",
        amount: int,
        idempotency_key: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        preferred_scope: Literal["user", "tenant"] | None = None,
        source: str = "debit",
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        allow_negative_balance: bool = False,
        usage_event_id: str | None = None,
    ) -> TokenWalletEntryResult:
        return await self.record_entry(
            app_id=app_id,
            wallet_id=wallet_id,
            amount=amount,
            operation="usage_debit" if usage_event_id else "debit",
            idempotency_key=idempotency_key,
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=preferred_scope,
            source=source,
            reason=reason,
            metadata=metadata,
            allow_negative_balance=allow_negative_balance,
            usage_event_id=usage_event_id,
        )

    async def query_balance(
        self,
        *,
        app_id: str,
        wallet_id: str = "ai_tokens",
        user_id: str | None = None,
        tenant_id: str | None = None,
        preferred_scope: Literal["user", "tenant"] | None = None,
    ) -> dict[str, Any]:
        scope = _scope_for(
            app_id=app_id,
            wallet_id=wallet_id,
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=preferred_scope,
        )
        balances, _entries = await self._collections()
        return public_balance(await balances.find_one({"_id": scope.balance_id}), scope)

    async def list_entries(
        self,
        *,
        app_id: str,
        wallet_id: str = "ai_tokens",
        user_id: str | None = None,
        tenant_id: str | None = None,
        preferred_scope: Literal["user", "tenant"] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        scope = _scope_for(
            app_id=app_id,
            wallet_id=wallet_id,
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=preferred_scope,
        )
        _balances, entries = await self._collections()
        bounded_limit = max(1, min(int(limit or 1), 500))
        cursor = entries.find({"balance_id": scope.balance_id}, {"_id": 0}).sort("created_at", -1).limit(bounded_limit)
        docs = await cursor.to_list(length=bounded_limit)
        return [public_entry(doc) for doc in docs]

    async def advance_subject_revision(
        self,
        *,
        app_id: str,
        wallet_id: str = "ai_tokens",
        user_id: str | None = None,
        tenant_id: str | None = None,
        preferred_scope: Literal["user", "tenant"] | None = None,
        subject_key: str,
        subject_revision: int,
    ) -> bool:
        """Advance this subject's ordering head, moving no balance.

        A revision that legitimately credits nothing — a cancellation, a plan
        with no allowance, a return to a plan whose period allocation already
        exists — still supersedes everything older. If the head only moved when
        tokens were minted it would mean "last revision that minted", not
        "latest accepted revision", and a delayed older allowance would sail
        past it.

        Returns False when a strictly newer revision already holds the head, in
        which case nothing is written — a meaning-bearing decline, not a
        successful no-op. Equal revisions succeed so that a retry, or the
        allowance belonging to the very revision that advanced the head, is not
        fenced out by its own claim. Operational failures propagate: a caller
        must be able to tell "someone newer won" from "the write did not
        happen", because only the first means the command was overtaken.
        """
        revision_path = _subject_revision_path(subject_key)
        if revision_path is None:
            return False
        scope = _scope_for(
            app_id=app_id,
            wallet_id=wallet_id,
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=preferred_scope,
        )
        balances, _entries = await self._collections()
        now = _now()
        try:
            updated = await balances.find_one_and_update(
                {
                    "_id": scope.balance_id,
                    "$or": [
                        {revision_path: {"$exists": False}},
                        {revision_path: None},
                        {revision_path: {"$lte": subject_revision}},
                    ],
                },
                {
                    "$setOnInsert": {
                        "_id": scope.balance_id,
                        "app_id": scope.app_id,
                        "wallet_id": scope.wallet_id,
                        "scope_type": scope.scope_type,
                        "scope_id": scope.scope_id,
                        "user_id": scope.user_id,
                        "tenant_id": scope.tenant_id,
                        "created_at": now,
                    },
                    "$set": {"updated_at": now},
                    "$max": {revision_path: subject_revision},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            # The balance exists but a newer revision holds the head, so the
            # filtered upsert collided on the deterministic balance id.
            return False
        return updated is not None

    async def ensure_plan_allowances(
        self,
        *,
        config: SubscriptionsConfig,
        app_id: str,
        plan_id: str | None = None,
        plan_label: str | None = None,
        token_allowances: list[TokenAllowanceDef | dict[str, Any]] | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        period_start: datetime | None = None,
        subject_key: str | None = None,
        subject_revision: int | None = None,
    ) -> list[TokenWalletEntryResult]:
        plan = None
        resolved_plan_id = _text(plan_id)
        resolved_plan_label = _text(plan_label)
        if token_allowances is None:
            plan = config.plan_by_id(plan_id)
            resolved_plan_id = plan.plan_id
            resolved_plan_label = plan.label
            allowances = list(plan.token_allowances)
        else:
            allowances = [
                allowance
                if isinstance(allowance, TokenAllowanceDef)
                else TokenAllowanceDef.model_validate(allowance)
                for allowance in token_allowances
            ]
            if not resolved_plan_id:
                plan = config.plan_by_id(None)
                resolved_plan_id = plan.plan_id
                resolved_plan_label = resolved_plan_label or plan.label
            elif not resolved_plan_label:
                plan = next(
                    (
                        candidate
                        for candidate in config.plans
                        if candidate.plan_id == resolved_plan_id
                    ),
                    None,
                )
                resolved_plan_label = plan.label if plan is not None else resolved_plan_id

        period = period_start or _now()
        results: list[TokenWalletEntryResult] = []
        for allowance in allowances:
            if allowance.amount <= 0 or allowance.cadence == "manual":
                continue
            wallet = config.token_wallet_by_id(allowance.wallet_id)
            preferred_scope = wallet.scope if wallet is not None else None
            if preferred_scope == "tenant" and not tenant_id:
                continue
            if preferred_scope == "user" and not user_id:
                continue
            period_key = (
                period.strftime("%Y-%m")
                if allowance.cadence == "monthly"
                else "one_time"
            )
            results.append(
                await self.record_entry(
                    app_id=app_id,
                    wallet_id=allowance.wallet_id,
                    amount=allowance.amount,
                    operation="allocation",
                    idempotency_key=(
                        f"subscription_allowance:{resolved_plan_id}:{allowance.wallet_id}:"
                        f"{allowance.cadence}:{period_key}"
                    ),
                    user_id=user_id,
                    tenant_id=tenant_id,
                    preferred_scope=preferred_scope,
                    source="subscription_allowance",
                    reason=allowance.label or f"{resolved_plan_label} allowance",
                    metadata={
                        "plan_id": resolved_plan_id,
                        "cadence": allowance.cadence,
                        "period_key": period_key,
                    },
                    subject_key=subject_key,
                    subject_revision=subject_revision,
                )
            )
        return results

    async def wallet_summaries_for_config(
        self,
        *,
        config: SubscriptionsConfig | None,
        app_id: str,
        user_id: str | None = None,
        tenant_id: str | None = None,
        plan_id: str | None = None,
        ensure_allowances: bool = True,
    ) -> dict[str, Any]:
        if config is None or not config.token_wallets:
            return {"wallets": [], "source": "none"}
        if ensure_allowances:
            await self.ensure_plan_allowances(
                config=config,
                app_id=app_id,
                plan_id=plan_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        plan = config.plan_by_id(plan_id)
        wallets = []
        for wallet in config.token_wallets:
            balance = await self.query_balance(
                app_id=app_id,
                wallet_id=wallet.wallet_id,
                user_id=user_id,
                tenant_id=tenant_id,
                preferred_scope=wallet.scope,
            )
            allowances = [
                allowance.model_dump()
                for allowance in plan.token_allowances
                if allowance.wallet_id == wallet.wallet_id
            ]
            wallets.append(
                {
                    "wallet_id": wallet.wallet_id,
                    "label": wallet.label or wallet.wallet_id,
                    "unit": wallet.unit,
                    "scope": wallet.scope,
                    "usage_meter_id": wallet.usage_meter_id,
                    "auto_debit_usage": wallet.auto_debit_usage,
                    "allow_negative_balance": wallet.allow_negative_balance,
                    "balance": balance,
                    "plan_allowances": allowances,
                }
            )
        return {
            "wallets": wallets,
            "plan_id": plan.plan_id,
            "source": "token_wallet_ledger",
        }

    async def record_usage_debit(
        self,
        payload: dict[str, Any],
        *,
        wallet: TokenWalletDef,
        allow_negative_balance: bool | None = None,
    ) -> TokenWalletEntryResult | None:
        amount = _positive_int(payload.get("total_tokens"))
        if amount <= 0:
            return None
        app_id = _text(payload.get("app_id"))
        user_id = _text(payload.get("user_id")) or None
        tenant_id = _text(payload.get("tenant_id")) or None
        event_id = _text(payload.get("event_id")) or _text(payload.get("invocation_id"))
        if not app_id or not event_id:
            return None
        return await self.debit(
            app_id=app_id,
            wallet_id=wallet.wallet_id,
            amount=amount,
            idempotency_key=f"usage:{event_id}",
            user_id=user_id,
            tenant_id=tenant_id,
            preferred_scope=wallet.scope,
            source="runtime_usage",
            reason="LLM token usage",
            metadata={
                "event_id": event_id,
                "chat_id": _text(payload.get("chat_id")) or None,
                "workspace_id": _text(payload.get("workspace_id")) or None,
                "workflow_name": _text(payload.get("workflow_name")) or None,
                "agent_name": _text(payload.get("agent_name")) or None,
                "model_name": _text(payload.get("model_name")) or None,
                "prompt_tokens": _positive_int(payload.get("prompt_tokens")),
                "completion_tokens": _positive_int(payload.get("completion_tokens")),
                "total_tokens": amount,
            },
            allow_negative_balance=(
                wallet.allow_negative_balance
                if allow_negative_balance is None
                else bool(allow_negative_balance)
            ),
            usage_event_id=event_id,
        )


_global_wallet_ledger: TokenWalletLedger | None = None


def get_token_wallet_ledger() -> TokenWalletLedger:
    global _global_wallet_ledger
    if _global_wallet_ledger is None:
        _global_wallet_ledger = TokenWalletLedger()
    return _global_wallet_ledger


__all__ = [
    "TokenWalletEntryResult",
    "TokenWalletLedger",
    "TokenWalletOperation",
    "TokenWalletScope",
    "TokenWalletStatus",
    "get_token_wallet_ledger",
    "public_balance",
    "public_entry",
]
