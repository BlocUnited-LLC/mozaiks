from __future__ import annotations

"""Provider-neutral token wallet accounting.

This module owns runtime token balances and append-only wallet entries. It does
not know about payment providers, checkout sessions, invoices, or hosted-product
pricing. Payment systems and generated app modules call this primitive after
their own business checks succeed.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

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
    ) -> TokenWalletEntryResult:
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
            "source": _text(source) or "runtime",
            "reason": _text(reason) or None,
            "metadata": _safe_metadata(metadata),
            "usage_event_id": _text(usage_event_id) or None,
            "created_at": now,
            "updated_at": now,
        }

        balances, entries = await self._collections()
        existing = await entries.find_one({"_id": entry_id}, {"_id": 0})
        if existing and _idempotency_conflict(existing, entry):
            raise ValueError("idempotency_key was reused with different token wallet entry data")
        if existing and existing.get("status") in {"applied", "rejected"}:
            balance_doc = await balances.find_one({"_id": scope.balance_id})
            return TokenWalletEntryResult(
                status=str(existing.get("status") or "applied"),  # type: ignore[arg-type]
                entry=public_entry(existing),
                balance=public_balance(balance_doc, scope),
            )
        if existing is None:
            try:
                await entries.insert_one(entry)
            except DuplicateKeyError:
                existing = await entries.find_one({"_id": entry_id})
                if existing and _idempotency_conflict(existing, entry):
                    raise ValueError("idempotency_key was reused with different token wallet entry data") from None
                if existing and existing.get("status") in {"applied", "rejected"}:
                    balance_doc = await balances.find_one({"_id": scope.balance_id})
                    return TokenWalletEntryResult(
                        status=str(existing.get("status") or "applied"),  # type: ignore[arg-type]
                        entry=public_entry(existing),
                        balance=public_balance(balance_doc, scope),
                    )

        update_filter: dict[str, Any] = {
            "_id": scope.balance_id,
            "applied_entry_ids": {"$ne": entry_id},
        }
        if direction == "debit" and not allow_negative_balance:
            update_filter["balance"] = {"$gte": resolved_amount}

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

        balance_doc = await balances.find_one_and_update(
            update_filter,
            {
                "$setOnInsert": balance_seed,
                "$set": {
                    "updated_at": now,
                },
                "$inc": inc,
                "$addToSet": {"applied_entry_ids": entry_id},
            },
            upsert=direction == "credit",
            return_document=ReturnDocument.AFTER,
        )

        if balance_doc is None:
            current_balance = await balances.find_one({"_id": scope.balance_id})
            if current_balance and entry_id in set(current_balance.get("applied_entry_ids") or []):
                applied_entry = await self._mark_entry_applied(entries, entry_id, current_balance)
                return TokenWalletEntryResult(
                    status="applied",
                    entry=public_entry(applied_entry),
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

        applied_entry = await self._mark_entry_applied(entries, entry_id, balance_doc)
        return TokenWalletEntryResult(
            status="applied",
            entry=public_entry(applied_entry),
            balance=public_balance(balance_doc, scope),
        )

    async def _mark_entry_applied(self, entries: Any, entry_id: str, balance: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        await entries.update_one(
            {"_id": entry_id},
            {
                "$set": {
                    "status": "applied",
                    "balance_after": int(balance.get("balance") or 0),
                    "applied_at": now,
                    "updated_at": now,
                }
            },
        )
        return await entries.find_one({"_id": entry_id}, {"_id": 0}) or {}

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
