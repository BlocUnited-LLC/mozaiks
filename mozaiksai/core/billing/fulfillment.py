"""Provider-neutral cash-to-token fulfillment.

Payment providers and hosted products own checkout, webhook verification,
taxes, refunds, and settlement. This module owns the normalized post-settlement
runtime mutation: assign a plan and apply token wallet effects.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.runtime.app.subscriptions_loader import (
    SubscriptionsConfig,
    TokenAllowanceDef,
    UsageLimitDef,
    load_subscriptions_config,
)
from mozaiksai.core.runtime.persistence.app_data import collection_name_for_alias
from mozaiksai.core.runtime.persistence.mongo import DEFAULT_APP_DATABASE_NAME
from mozaiksai.core.tokens.wallet import TokenWalletLedger, get_token_wallet_ledger

BillingFulfillmentEventType = Literal[
    "subscription_activated",
    "subscription_updated",
    "subscription_cancelled",
    "token_top_up_paid",
    "token_credit_granted",
    "refund_applied",
    "chargeback_applied",
]
BillingFulfillmentSource = Literal["mozaikspay", "stripe", "manual", "custom", "test"]
BillingFulfillmentEffect = Literal[
    "assignment_upsert",
    "assignment_cancel",
    "plan_allowances",
    "wallet_credit",
    "wallet_debit",
]
BillingFulfillmentEffectStatus = Literal["applied", "skipped", "rejected"]
BillingFulfillmentStatus = Literal["applied", "replayed", "rejected", "partial"]
BillingFulfillmentStartState = Literal["started", "replay", "conflict", "pending"]

CollectionResolver = Callable[[str], Any]
FulfillmentEventSink = Callable[[dict[str, Any]], Awaitable[None] | None]
COMMANDS_COLLECTION = RuntimeCollections.RUNTIME_BILLING_FULFILLMENT_COMMANDS

_SUBSCRIPTION_PLAN_EVENTS = {"subscription_activated", "subscription_updated"}
_SUBSCRIPTION_EVENTS = _SUBSCRIPTION_PLAN_EVENTS | {"subscription_cancelled"}
_TOKEN_CREDIT_EVENTS = {"token_top_up_paid", "token_credit_granted"}
_TOKEN_DEBIT_EVENTS = {"refund_applied", "chargeback_applied"}
_TOKEN_EVENTS = _TOKEN_CREDIT_EVENTS | _TOKEN_DEBIT_EVENTS
_CAPABILITY_ID_RE = re.compile(r"^[a-z0-9_.]+$")
_STATUS_RE = re.compile(r"^[a-z0-9_-]+$")
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
    "signature",
)
_FORBIDDEN_VALUE_MARKERS = (
    "-----BEGIN",
    "ghp_",
    "github_pat_",
    "sk-",
    "xoxb-",
    "xoxp-",
)


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
        return value.astimezone(UTC).isoformat()
    text = _text(value)
    return text or None


def _default_database_name() -> str:
    return (
        os.getenv("MOZAIKS_APP_DATA_DATABASE_NAME")
        or os.getenv("MOZAIKS_APP_DATABASE_NAME")
        or os.getenv("MOZAIKS_APPS_DATABASE")
        or DEFAULT_APP_DATABASE_NAME
    ).strip() or DEFAULT_APP_DATABASE_NAME


def _forbidden_metadata_path(payload: Any, *, path: str = "") -> str | None:
    if isinstance(payload, Mapping):
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


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    value = dict(metadata or {})
    forbidden = _forbidden_metadata_path(value)
    if forbidden:
        raise ValueError(f"billing fulfillment metadata must not contain secret-shaped data: {forbidden}")
    return value


def _json_hash(value: Any) -> str:
    import json

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


def _command_document(command: BillingFulfillmentCommand) -> dict[str, Any]:
    return command.model_dump(mode="json")


def _command_hash(command: BillingFulfillmentCommand) -> str:
    return _json_hash(_command_document(command))


def _status_from_effects(effects: list[BillingFulfillmentEffectResult]) -> BillingFulfillmentStatus:
    rejected = sum(1 for effect in effects if effect.status == "rejected")
    applied_or_skipped = sum(1 for effect in effects if effect.status in {"applied", "skipped"})
    if rejected and applied_or_skipped:
        return "partial"
    if rejected:
        return "rejected"
    return "applied"


def _assignment_id(app_id: str, query: Mapping[str, Any]) -> str:
    parts = [app_id]
    for key in sorted(query):
        parts.append(f"{key}={query[key]}")
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"subscription_assignment:{digest}"


def _capability_entries(capability_ids: list[str]) -> list[dict[str, str]]:
    return [{"capability_id": capability_id} for capability_id in capability_ids]


def _plan_snapshot(
    *,
    plan_id: str,
    label: str | None,
    capability_ids: list[str],
    token_allowances: list[TokenAllowanceDef],
    usage_limits: list[UsageLimitDef],
    source: str,
    captured_at: datetime,
) -> dict[str, Any]:
    return {
        "plan_id": plan_id,
        "label": label or plan_id,
        "granted_capabilities": _capability_entries(capability_ids),
        "token_allowances": [allowance.model_dump() for allowance in token_allowances],
        "usage_limits": [limit.model_dump() for limit in usage_limits],
        "source": source,
        "captured_at": _iso(captured_at),
    }


class BillingFulfillmentCommand(BaseModel):
    """Normalized, already-authorized post-settlement fulfillment command."""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    event_type: BillingFulfillmentEventType
    source: BillingFulfillmentSource = "custom"
    app_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    plan_id: str | None = None
    plan_label: str | None = None
    status: str | None = None
    wallet_id: str = "ai_tokens"
    token_amount: int | None = Field(default=None, ge=0)
    granted_capabilities: list[str] = Field(default_factory=list)
    token_allowances: list[TokenAllowanceDef] = Field(default_factory=list)
    usage_limits: list[UsageLimitDef] = Field(default_factory=list)
    occurred_at: datetime | None = None
    starts_at: datetime | None = None
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "command_id",
        "app_id",
        "user_id",
        "tenant_id",
        "workspace_id",
        "plan_id",
        "plan_label",
        "status",
        "wallet_id",
        mode="before",
    )
    @classmethod
    def _normalize_text_field(cls, value: Any) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("status")
    @classmethod
    def _validate_status(cls, value: str | None) -> str | None:
        if value is None:
            return None
        status = value.lower()
        if not _STATUS_RE.match(status):
            raise ValueError("status must match [a-z0-9_-]+")
        return status

    @field_validator("granted_capabilities", mode="before")
    @classmethod
    def _validate_capabilities(cls, value: object) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("granted_capabilities must be a list")
        capability_ids: list[str] = []
        for raw in value:
            capability_id = str(raw or "").strip()
            if not capability_id:
                continue
            if not _CAPABILITY_ID_RE.match(capability_id):
                raise ValueError(f"capability_id must match [a-z0-9_.]+, got {capability_id!r}")
            capability_ids.append(capability_id)
        return capability_ids

    @field_validator("metadata", mode="before")
    @classmethod
    def _validate_metadata(cls, value: object) -> dict[str, Any]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be an object")
        return _safe_metadata(value)

    @model_validator(mode="after")
    def _validate_event_requirements(self) -> BillingFulfillmentCommand:
        if not self.command_id:
            raise ValueError("command_id is required")
        if not self.app_id:
            raise ValueError("app_id is required")
        if not self.wallet_id:
            raise ValueError("wallet_id is required")
        if self.event_type in _SUBSCRIPTION_PLAN_EVENTS and not self.plan_id:
            raise ValueError(f"plan_id is required for {self.event_type}")
        if self.event_type in _TOKEN_EVENTS and _positive_int(self.token_amount) <= 0:
            raise ValueError(f"token_amount greater than zero is required for {self.event_type}")
        return self


class BillingFulfillmentEffectResult(BaseModel):
    """One deterministic mutation attempted by a fulfillment command."""

    model_config = ConfigDict(extra="forbid")

    effect: BillingFulfillmentEffect
    status: BillingFulfillmentEffectStatus
    reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class BillingFulfillmentResult(BaseModel):
    """Outcome of a normalized fulfillment command."""

    model_config = ConfigDict(extra="forbid")

    command_id: str
    event_type: BillingFulfillmentEventType
    source: BillingFulfillmentSource
    success: bool
    status: BillingFulfillmentStatus = "applied"
    replayed: bool = False
    replay_count: int = 0
    command_log_id: str | None = None
    effects: list[BillingFulfillmentEffectResult]
    applied: int = 0
    skipped: int = 0
    rejected: int = 0

    @classmethod
    def from_effects(
        cls,
        command: BillingFulfillmentCommand,
        effects: list[BillingFulfillmentEffectResult],
    ) -> BillingFulfillmentResult:
        return cls(
            command_id=command.command_id,
            event_type=command.event_type,
            source=command.source,
            success=not any(effect.status == "rejected" for effect in effects),
            status=_status_from_effects(effects),
            effects=effects,
            applied=sum(1 for effect in effects if effect.status == "applied"),
            skipped=sum(1 for effect in effects if effect.status == "skipped"),
            rejected=sum(1 for effect in effects if effect.status == "rejected"),
        )


class _ResolvedPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    label: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    token_allowances: list[TokenAllowanceDef] = Field(default_factory=list)
    usage_limits: list[UsageLimitDef] = Field(default_factory=list)


class BillingFulfillmentConflictError(ValueError):
    """Raised when a command_id is reused with different fulfillment data."""

    def __init__(self, *, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(f"billing fulfillment command_id was reused with different data: {command_id}")


class BillingFulfillmentPendingError(RuntimeError):
    """Raised when the same fulfillment command is already being processed."""

    def __init__(self, *, command_id: str) -> None:
        self.command_id = command_id
        super().__init__(f"billing fulfillment command is already pending: {command_id}")


@dataclass(frozen=True)
class BillingFulfillmentStartResult:
    state: BillingFulfillmentStartState
    record: dict[str, Any] | None = None


class BillingFulfillmentCommandStore:
    """Durable command log for replay-safe billing fulfillment."""

    def __init__(self, *, database: Any | None = None) -> None:
        self._database = database
        self._indexes_ready = False

    async def _db(self) -> Any:
        if self._database is not None:
            return self._database
        client = get_mongo_client()
        return client[SYSTEM_DATABASE]

    async def _collection(self) -> Any:
        db = await self._db()
        collection = db[COMMANDS_COLLECTION]
        if not self._indexes_ready:
            try:
                await collection.create_index("command_id", name="billing_fulfillment_command_id", unique=True)
                await collection.create_index(
                    [("app_id", 1), ("created_at", -1)],
                    name="billing_fulfillment_app_time",
                )
                await collection.create_index(
                    [("source", 1), ("event_type", 1), ("created_at", -1)],
                    name="billing_fulfillment_source_event_time",
                )
            except Exception:
                pass
            self._indexes_ready = True
        return collection

    async def start(self, command: BillingFulfillmentCommand) -> BillingFulfillmentStartResult:
        collection = await self._collection()
        now = _now()
        command_doc = _command_document(command)
        command_hash = _command_hash(command)
        record = {
            "_id": command.command_id,
            "command_id": command.command_id,
            "command_hash": command_hash,
            "command": command_doc,
            "status": "pending",
            "source": command.source,
            "event_type": command.event_type,
            "app_id": command.app_id,
            "user_id": command.user_id,
            "tenant_id": command.tenant_id,
            "workspace_id": command.workspace_id,
            "plan_id": command.plan_id,
            "wallet_id": command.wallet_id,
            "token_amount": command.token_amount,
            "replay_count": 0,
            "conflict_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await collection.insert_one(record)
            return BillingFulfillmentStartResult(state="started", record=record)
        except DuplicateKeyError:
            existing = await collection.find_one({"_id": command.command_id})
            if not existing:
                raise

        if existing.get("command_hash") != command_hash:
            await collection.update_one(
                {"_id": command.command_id},
                {
                    "$inc": {"conflict_count": 1},
                    "$set": {
                        "last_conflict_at": now,
                        "last_conflict_hash": command_hash,
                        "updated_at": now,
                    },
                },
            )
            return BillingFulfillmentStartResult(state="conflict", record=existing)

        if existing.get("status") == "pending":
            return BillingFulfillmentStartResult(state="pending", record=existing)

        updated = await collection.find_one_and_update(
            {"_id": command.command_id},
            {
                "$inc": {"replay_count": 1},
                "$set": {
                    "last_replayed_at": now,
                    "updated_at": now,
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        return BillingFulfillmentStartResult(state="replay", record=updated or existing)

    async def finish(
        self,
        command: BillingFulfillmentCommand,
        result: BillingFulfillmentResult,
    ) -> BillingFulfillmentResult:
        collection = await self._collection()
        now = _now()
        completed_result = result.model_copy(update={"command_log_id": command.command_id})
        await collection.update_one(
            {"_id": command.command_id},
            {
                "$set": {
                    "status": completed_result.status,
                    "result": completed_result.model_dump(mode="json"),
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        return completed_result

    async def replay_result(
        self,
        record: Mapping[str, Any],
    ) -> BillingFulfillmentResult:
        raw_result = record.get("result")
        if not isinstance(raw_result, Mapping):
            raise BillingFulfillmentPendingError(command_id=str(record.get("command_id") or ""))
        replay_count = int(record.get("replay_count") or 0)
        return BillingFulfillmentResult.model_validate(raw_result).model_copy(
            update={
                "status": "replayed",
                "replayed": True,
                "replay_count": replay_count,
                "command_log_id": str(record.get("command_id") or ""),
            }
        )

    async def list_records(
        self,
        *,
        app_id: str | None = None,
        command_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        collection = await self._collection()
        query: dict[str, Any] = {}
        if command_id:
            query["_id"] = command_id
        if app_id:
            query["app_id"] = app_id
        bounded_limit = max(1, min(int(limit or 1), 500))
        cursor = collection.find(query, {"_id": 0}).sort("created_at", -1).limit(bounded_limit)
        docs = await cursor.to_list(length=bounded_limit)
        return [dict(doc) for doc in docs]


class BillingFulfillmentService:
    """Apply normalized billing fulfillment commands to OSS runtime state."""

    def __init__(
        self,
        *,
        config: SubscriptionsConfig | None = None,
        app_root: str | Path | None = None,
        ledger: TokenWalletLedger | None = None,
        collection_resolver: CollectionResolver | None = None,
        event_sink: FulfillmentEventSink | None = None,
        command_store: BillingFulfillmentCommandStore | None = None,
    ) -> None:
        if config is None and app_root is not None:
            config = load_subscriptions_config(Path(app_root))
        self._config = config
        self._ledger = ledger or get_token_wallet_ledger()
        self._collection_resolver = collection_resolver
        self._event_sink = event_sink
        self._command_store = command_store

    async def apply(self, command: BillingFulfillmentCommand | Mapping[str, Any]) -> BillingFulfillmentResult:
        resolved = (
            command
            if isinstance(command, BillingFulfillmentCommand)
            else BillingFulfillmentCommand.model_validate(dict(command))
        )
        result = await self._apply_effects(resolved)
        await self._emit_result(result)
        return result

    async def apply_durable(
        self,
        command: BillingFulfillmentCommand | Mapping[str, Any],
    ) -> BillingFulfillmentResult:
        resolved = (
            command
            if isinstance(command, BillingFulfillmentCommand)
            else BillingFulfillmentCommand.model_validate(dict(command))
        )
        store = self._command_store or BillingFulfillmentCommandStore()
        started = await store.start(resolved)
        if started.state == "conflict":
            raise BillingFulfillmentConflictError(command_id=resolved.command_id)
        if started.state == "pending":
            raise BillingFulfillmentPendingError(command_id=resolved.command_id)
        if started.state == "replay":
            replayed = await store.replay_result(started.record or {})
            await self._emit_result(replayed)
            return replayed

        result = await self._apply_effects(resolved)
        result = await store.finish(resolved, result)
        await self._emit_result(result)
        return result

    async def _apply_effects(self, resolved: BillingFulfillmentCommand) -> BillingFulfillmentResult:
        effects: list[BillingFulfillmentEffectResult] = []

        if resolved.event_type in _SUBSCRIPTION_EVENTS:
            effects.extend(await self._apply_subscription_effects(resolved))

        if resolved.event_type in _TOKEN_CREDIT_EVENTS:
            effects.append(await self._apply_wallet_credit(resolved))
        elif resolved.event_type in _TOKEN_DEBIT_EVENTS:
            effects.append(await self._apply_wallet_debit(resolved))

        return BillingFulfillmentResult.from_effects(resolved, effects)

    async def _apply_subscription_effects(
        self,
        command: BillingFulfillmentCommand,
    ) -> list[BillingFulfillmentEffectResult]:
        if self._config is None:
            return [
                BillingFulfillmentEffectResult(
                    effect="assignment_upsert"
                    if command.event_type != "subscription_cancelled"
                    else "assignment_cancel",
                    status="skipped",
                    reason="subscriptions_config_missing",
                ),
                BillingFulfillmentEffectResult(
                    effect="plan_allowances",
                    status="skipped",
                    reason="subscriptions_config_missing",
                ),
            ]

        effects: list[BillingFulfillmentEffectResult] = []
        plan = self._resolve_plan(command)
        if command.event_type in _SUBSCRIPTION_PLAN_EVENTS and plan is None:
            effects.append(
                BillingFulfillmentEffectResult(
                    effect="assignment_upsert",
                    status="rejected",
                    reason="unknown_plan",
                    details={"plan_id": command.plan_id},
                )
            )
            effects.append(
                BillingFulfillmentEffectResult(
                    effect="plan_allowances",
                    status="skipped",
                    reason="unknown_plan",
                )
            )
            return effects

        effects.append(await self._apply_assignment(command, plan=plan))
        if command.event_type == "subscription_cancelled":
            effects.append(
                BillingFulfillmentEffectResult(
                    effect="plan_allowances",
                    status="skipped",
                    reason="subscription_cancelled",
                )
            )
            return effects

        effects.append(await self._apply_plan_allowances(command, plan=plan))
        return effects

    def _resolve_plan(self, command: BillingFulfillmentCommand) -> _ResolvedPlan | None:
        if self._config is None or not command.plan_id:
            return None
        for plan in self._config.plans:
            if plan.plan_id == command.plan_id:
                return _ResolvedPlan(
                    plan_id=plan.plan_id,
                    label=plan.label,
                    capabilities=list(plan.capabilities),
                    token_allowances=list(plan.token_allowances),
                    usage_limits=list(plan.usage_limits),
                )
        has_command_snapshot = bool(
            command.plan_label
            or command.granted_capabilities
            or command.token_allowances
            or command.usage_limits
        )
        if not has_command_snapshot:
            return None
        return _ResolvedPlan(
            plan_id=command.plan_id,
            label=command.plan_label or command.plan_id,
            capabilities=list(command.granted_capabilities),
            token_allowances=list(command.token_allowances),
            usage_limits=list(command.usage_limits),
        )

    async def _collection(self) -> Any | None:
        if self._config is None or self._config.assignment_store is None:
            return None
        store = self._config.assignment_store
        if self._collection_resolver is not None:
            return self._collection_resolver(store.data_alias)
        collection_name = collection_name_for_alias(store.data_alias)
        client = get_mongo_client()
        return client[_default_database_name()][collection_name]

    def _assignment_query(self, command: BillingFulfillmentCommand) -> dict[str, Any]:
        if self._config is None or self._config.assignment_store is None:
            return {}
        store = self._config.assignment_store
        query: dict[str, Any] = {store.app_id_field: command.app_id}
        if store.tenant_id_field:
            query[store.tenant_id_field] = command.tenant_id
        if store.workspace_id_field:
            query[store.workspace_id_field] = command.workspace_id
        if store.user_id_field:
            query[store.user_id_field] = command.user_id
        return query

    async def _apply_assignment(
        self,
        command: BillingFulfillmentCommand,
        *,
        plan: _ResolvedPlan | None,
    ) -> BillingFulfillmentEffectResult:
        if self._config is None or self._config.assignment_store is None:
            return BillingFulfillmentEffectResult(
                effect="assignment_cancel" if command.event_type == "subscription_cancelled" else "assignment_upsert",
                status="skipped",
                reason="assignment_store_missing",
            )

        store = self._config.assignment_store
        collection = await self._collection()
        if collection is None:
            return BillingFulfillmentEffectResult(
                effect="assignment_cancel" if command.event_type == "subscription_cancelled" else "assignment_upsert",
                status="skipped",
                reason="assignment_store_missing",
            )

        now = _now()
        query = self._assignment_query(command)
        assignment_id = _assignment_id(command.app_id, query)
        status = command.status or ("cancelled" if command.event_type == "subscription_cancelled" else "active")
        plan_id = command.plan_id or self._config.default_plan_id
        plan_label = command.plan_label or plan_id
        capabilities = list(plan.capabilities if plan is not None else command.granted_capabilities)
        token_allowances = list(plan.token_allowances if plan is not None else command.token_allowances)
        usage_limits = list(plan.usage_limits if plan is not None else command.usage_limits)

        updates: dict[str, Any] = {
            **query,
            store.plan_id_field: plan_id,
            store.status_field: status,
            "billing_fulfillment": {
                "command_id": command.command_id,
                "event_type": command.event_type,
                "source": command.source,
                "processed_at": _iso(now),
                "metadata": command.metadata,
            },
            "updated_at": now,
        }
        if store.starts_at_field and command.event_type != "subscription_cancelled":
            updates[store.starts_at_field] = _iso(command.starts_at or command.occurred_at or now)
        if store.expires_at_field:
            if command.expires_at is not None:
                updates[store.expires_at_field] = _iso(command.expires_at)
            elif command.event_type == "subscription_cancelled":
                updates[store.expires_at_field] = _iso(command.occurred_at or now)
        if store.capabilities_field:
            updates[store.capabilities_field] = _capability_entries(capabilities)
        if store.plan_snapshot_field:
            updates[store.plan_snapshot_field] = _plan_snapshot(
                plan_id=plan_id,
                label=plan_label,
                capability_ids=capabilities,
                token_allowances=token_allowances,
                usage_limits=usage_limits,
                source=command.source,
                captured_at=now,
            )

        await collection.update_one(
            query,
            {
                "$set": updates,
                "$setOnInsert": {
                    "_id": assignment_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        return BillingFulfillmentEffectResult(
            effect="assignment_cancel" if command.event_type == "subscription_cancelled" else "assignment_upsert",
            status="applied",
            details={
                "assignment_id": assignment_id,
                "data_alias": store.data_alias,
                "plan_id": plan_id,
                "status": status,
            },
        )

    async def _apply_plan_allowances(
        self,
        command: BillingFulfillmentCommand,
        *,
        plan: _ResolvedPlan | None,
    ) -> BillingFulfillmentEffectResult:
        if self._config is None:
            return BillingFulfillmentEffectResult(
                effect="plan_allowances",
                status="skipped",
                reason="subscriptions_config_missing",
            )
        if plan is None:
            return BillingFulfillmentEffectResult(
                effect="plan_allowances",
                status="skipped",
                reason="unknown_plan",
            )
        if not plan.token_allowances:
            return BillingFulfillmentEffectResult(
                effect="plan_allowances",
                status="skipped",
                reason="no_token_allowances",
            )

        try:
            results = await self._ledger.ensure_plan_allowances(
                config=self._config,
                app_id=command.app_id,
                plan_id=plan.plan_id,
                plan_label=plan.label,
                token_allowances=[
                    allowance.model_dump()
                    for allowance in plan.token_allowances
                ],
                user_id=command.user_id,
                tenant_id=command.tenant_id,
                period_start=command.starts_at or command.occurred_at or _now(),
            )
        except Exception as exc:
            return BillingFulfillmentEffectResult(
                effect="plan_allowances",
                status="rejected",
                reason=str(exc),
            )

        statuses = [result.status for result in results]
        return BillingFulfillmentEffectResult(
            effect="plan_allowances",
            status="rejected" if any(status == "rejected" for status in statuses) else "applied",
            details={
                "entries": [
                    {
                        "status": getattr(result, "status", ""),
                        "wallet_id": getattr(result, "entry", {}).get("wallet_id"),
                        "amount": getattr(result, "entry", {}).get("amount"),
                        "balance": getattr(result, "balance", {}).get("balance"),
                    }
                    for result in results
                ],
            },
        )

    def _wallet_scope(self, wallet_id: str) -> Literal["user", "tenant"] | None:
        if self._config is None:
            return None
        wallet = self._config.token_wallet_by_id(wallet_id)
        return wallet.scope if wallet is not None else None

    def _wallet_allows_negative_balance(self, wallet_id: str) -> bool:
        if self._config is None:
            return False
        wallet = self._config.token_wallet_by_id(wallet_id)
        return bool(wallet.allow_negative_balance) if wallet is not None else False

    def _wallet_metadata(self, command: BillingFulfillmentCommand) -> dict[str, Any]:
        return _safe_metadata(
            {
                "command_id": command.command_id,
                "event_type": command.event_type,
                "source": command.source,
                "plan_id": command.plan_id,
                "metadata": command.metadata,
            }
        )

    async def _apply_wallet_credit(
        self,
        command: BillingFulfillmentCommand,
    ) -> BillingFulfillmentEffectResult:
        try:
            result = await self._ledger.credit(
                app_id=command.app_id,
                wallet_id=command.wallet_id,
                amount=_positive_int(command.token_amount),
                idempotency_key=f"billing_fulfillment:{command.command_id}:wallet_credit:{command.wallet_id}",
                user_id=command.user_id,
                tenant_id=command.tenant_id,
                preferred_scope=self._wallet_scope(command.wallet_id),
                source="billing_fulfillment",
                reason="Paid token credit" if command.event_type == "token_top_up_paid" else "Granted token credit",
                metadata=self._wallet_metadata(command),
            )
        except Exception as exc:
            return BillingFulfillmentEffectResult(
                effect="wallet_credit",
                status="rejected",
                reason=str(exc),
            )

        return BillingFulfillmentEffectResult(
            effect="wallet_credit",
            status=result.status,
            reason=result.entry.get("rejection_reason"),
            details={
                "wallet_id": command.wallet_id,
                "amount": _positive_int(command.token_amount),
                "balance": result.balance.get("balance"),
                "entry_id": result.entry.get("entry_id"),
            },
        )

    async def _apply_wallet_debit(
        self,
        command: BillingFulfillmentCommand,
    ) -> BillingFulfillmentEffectResult:
        try:
            result = await self._ledger.debit(
                app_id=command.app_id,
                wallet_id=command.wallet_id,
                amount=_positive_int(command.token_amount),
                idempotency_key=f"billing_fulfillment:{command.command_id}:wallet_debit:{command.wallet_id}",
                user_id=command.user_id,
                tenant_id=command.tenant_id,
                preferred_scope=self._wallet_scope(command.wallet_id),
                source="billing_fulfillment",
                reason="Refund or chargeback token reversal",
                metadata=self._wallet_metadata(command),
                allow_negative_balance=self._wallet_allows_negative_balance(command.wallet_id),
            )
        except Exception as exc:
            return BillingFulfillmentEffectResult(
                effect="wallet_debit",
                status="rejected",
                reason=str(exc),
            )

        return BillingFulfillmentEffectResult(
            effect="wallet_debit",
            status=result.status,
            reason=result.entry.get("rejection_reason"),
            details={
                "wallet_id": command.wallet_id,
                "amount": _positive_int(command.token_amount),
                "balance": result.balance.get("balance"),
                "entry_id": result.entry.get("entry_id"),
            },
        )

    async def _emit_result(self, result: BillingFulfillmentResult) -> None:
        if self._event_sink is None:
            return
        emitted = self._event_sink(
            {
                "event_type": f"billing.fulfillment.{result.status}",
                "command_id": result.command_id,
                "source": result.source,
                "fulfillment_event_type": result.event_type,
                "status": result.status,
                "replayed": result.replayed,
                "command_log_id": result.command_log_id,
                "applied": result.applied,
                "skipped": result.skipped,
                "rejected": result.rejected,
            }
        )
        if inspect.isawaitable(emitted):
            await emitted


_global_billing_fulfillment_service: BillingFulfillmentService | None = None


def get_billing_fulfillment_service() -> BillingFulfillmentService:
    global _global_billing_fulfillment_service
    if _global_billing_fulfillment_service is None:
        _global_billing_fulfillment_service = BillingFulfillmentService()
    return _global_billing_fulfillment_service


__all__ = [
    "BillingFulfillmentCommand",
    "BillingFulfillmentCommandStore",
    "BillingFulfillmentConflictError",
    "BillingFulfillmentEffectResult",
    "BillingFulfillmentPendingError",
    "BillingFulfillmentResult",
    "BillingFulfillmentService",
    "get_billing_fulfillment_service",
]
