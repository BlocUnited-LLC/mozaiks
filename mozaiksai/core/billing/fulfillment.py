"""Provider-neutral cash-to-token fulfillment.

Payment providers and hosted products own checkout, webhook verification,
taxes, refunds, and settlement. This module owns the normalized post-settlement
runtime mutation: assign a plan and apply token wallet effects.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

from logs.logging_config import get_core_logger
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
from mozaiksai.core.tokens.wallet import (
    STALE_SUBJECT_REVISION_REASON,
    TokenWalletLedger,
    get_token_wallet_ledger,
)

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
BillingFulfillmentStatus = Literal[
    "applied", "replayed", "rejected", "partial", "superseded"
]
BillingFulfillmentStartState = Literal["started", "replay", "conflict", "pending"]

CollectionResolver = Callable[[str], Any]
FulfillmentEventSink = Callable[[dict[str, Any]], Awaitable[None] | None]
COMMANDS_COLLECTION = RuntimeCollections.RUNTIME_BILLING_FULFILLMENT_COMMANDS

logger = get_core_logger(__name__)

# Effect reason emitted when a command is suppressed because the entitlement
# subject already carries a newer (or equal) revision.
STALE_REVISION_REASON = "stale_revision"

# Effect reason emitted when the deterministic assignment id collides with a
# row whose subject fields differ from the incoming command's. Historic
# assignment ids are built by string-formatting scope values, so `None` and
# the literal string "None" hash alike; treating that as the same subject
# would let one subject suppress another. Fail closed instead.
SUBJECT_IDENTITY_COLLISION_REASON = "subject_identity_collision"

# Largest value BSON can store in an int64 field.
MAX_SUBJECT_REVISION = 9223372036854775807

# Bounded retries of the fenced assignment upsert. Each duplicate-key retry
# observes a strictly newer persisted state, so two attempts suffice: one to
# lose an insert race, one to apply against (or be refused by) the winner.
_ASSIGNMENT_FENCE_ATTEMPTS = 2

# Deterministic name for the unique subject index revision fencing requires.
# Stable across processes so a restart verifies the existing index instead of
# creating a second one, and so an incompatible pre-existing index of the same
# name surfaces as a clear conflict.
SUBJECT_UNIQUE_INDEX_NAME = "mozaiks_billing_subject_unique"

# Mongo failures that mean "this collection cannot satisfy subject uniqueness":
# duplicate data (11000), and an index of this name already existing with
# different keys or options (85/86).
_SUBJECT_INDEX_BLOCKING_CODES = frozenset({11000, 85, 86})

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
        return str(value.astimezone(UTC).isoformat())
    text = _text(value)
    return text or None


def _effect_status(value: Any) -> Literal["applied", "skipped", "rejected"]:
    text = str(value or "").strip()
    if text == "applied":
        return "applied"
    if text == "skipped" or text == "pending":
        return "skipped"
    if text == "rejected":
        return "rejected"
    return "rejected"


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
    """Canonical durable document for a command.

    ``subject_revision`` is omitted entirely when absent so a command that
    does not opt into revision fencing keeps byte-identical canonical content
    — and therefore an identical command hash — across the upgrade that
    introduced the field. Serializing an explicit ``null`` would turn every
    pre-upgrade command into a content conflict on its first replay.
    """
    document = command.model_dump(mode="json")
    if command.subject_revision is None:
        document.pop("subject_revision", None)
    return document


def _command_hash(command: BillingFulfillmentCommand) -> str:
    return _json_hash(_command_document(command))


#: Terminal-status precedence for a fulfillment command, highest first:
#:
#: 1. ``partial``    — something was rejected AND something landed. A hard
#:                     failure is present, so it outranks ordering.
#: 2. ``rejected``   — everything was rejected.
#: 3. ``superseded`` — nothing was rejected, and at least one revision-scoped
#:                     effect stood down because a newer authoritative revision
#:                     won. This holds even when an earlier effect of the same
#:                     command already applied: the command as a whole was
#:                     overtaken, and its remaining effects were invalidated.
#: 4. ``applied``    — everything that ran, ran.
#:
#: Individual effect history stays truthful in every case: an effect that did
#: apply still reports ``applied`` and still counts toward ``applied``.
_STALE_EFFECT_REASONS = frozenset({STALE_REVISION_REASON, STALE_SUBJECT_REVISION_REASON})


def _status_from_effects(effects: list[BillingFulfillmentEffectResult]) -> BillingFulfillmentStatus:
    rejected = sum(1 for effect in effects if effect.status == "rejected")
    applied_or_skipped = sum(1 for effect in effects if effect.status in {"applied", "skipped"})
    if rejected and applied_or_skipped:
        return "partial"
    if rejected:
        return "rejected"
    if any(effect.reason in _STALE_EFFECT_REASONS for effect in effects):
        return "superseded"
    return "applied"


def _assignment_id(app_id: str, query: Mapping[str, Any]) -> str:
    parts = [app_id]
    for key in sorted(query):
        parts.append(f"{key}={query[key]}")
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"subscription_assignment:{digest}"


def _resolve_document_path(document: Mapping[str, Any], path: str) -> Any:
    """Read a possibly-dotted configured field path out of a document.

    Mirrors Mongo's own dotted-path semantics so query, write, and typed
    comparison all agree about what a configured mapping refers to.
    """
    current: Any = document
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(segment)
    return current


def _subject_identity(app_id: str, query: Mapping[str, Any]) -> str:
    """Type-preserving digest of an entitlement subject.

    The assignment `_id` string-formats scope values, so a null scope and the
    literal string "None" collapse into the same identity. That is tolerable
    there — a typed comparison of the persisted subject fields runs before any
    stale classification — but ordering authority is claimed on wallets that
    hold no subject fields to compare, so it must never conflate two subjects
    in the first place. This digest carries each value's type alongside it.
    """
    parts = [["app_id", type(app_id).__name__, app_id]]
    for key in sorted(query):
        value = query[key]
        parts.append([str(key), type(value).__name__, value])
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode()).hexdigest()


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
    # Provider-neutral ordering authority for this entitlement subject.
    # The upstream billing source allocates a monotonically increasing integer
    # per subject each time it commits a canonical entitlement revision; the
    # receiver applies subscription effects only when the incoming revision is
    # strictly greater than the last one committed for that subject. Omit
    # (None) for callers with no ordering authority — those commands are
    # applied unfenced, exactly as before.
    #
    # Strict: no coercion from bool, float, str, or Decimal, because a
    # silently coerced ordering value is worse than a rejected one. Bounded to
    # BSON int64 so a persisted revision can always round-trip through Mongo
    # instead of raising OverflowError at write time. Values loaded back as
    # bson.int64.Int64 (an int subclass) remain acceptable.
    subject_revision: int | None = Field(
        default=None, ge=0, le=MAX_SUBJECT_REVISION, strict=True
    )
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
    # Set only on a replay: the terminal disposition the original application
    # reached. `status` reports that THIS response is a replay; this reports
    # what is being replayed.
    original_status: BillingFulfillmentStatus | None = None
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


class BillingFulfillmentPreEffectError(RuntimeError):
    """A fenced command failed before any fulfillment effect could commit.

    The distinction is durable-retry safety, not severity. Every failure of
    this kind happens inside the ordering prerequisite that runs before the
    assignment mutation, so the caller knows with certainty that nothing was
    written: no assignment, no allowance, no wallet movement. That is exactly
    the condition under which the durable command reservation may be released
    so an identical retry can start, and it is the ONLY condition under which
    it may be — a failure after an effect has committed must stay pending.
    """


class BillingFulfillmentOrderingUnavailable(BillingFulfillmentPreEffectError):
    """A wallet ordering head could not be written.

    This is an operational failure, not a decline. A wallet reporting that a
    strictly newer revision already owns it is `superseded` and terminal; this
    is "the write did not happen", which says nothing about who won and must
    be retried rather than acknowledged.
    """

    def __init__(self, *, wallet_id: str, cause: BaseException) -> None:
        self.wallet_id = wallet_id
        super().__init__(
            "billing revision ordering could not be established for wallet "
            f"{wallet_id!r}: {cause}"
        )


class BillingFulfillmentSubjectIndexError(BillingFulfillmentPreEffectError):
    """Subject uniqueness could not be established for a fenced store.

    Either the collection already holds more than one assignment for the same
    entitlement subject, or an index of the reserved name exists with different
    keys. Both need an operator decision — which row survives, or which index
    is correct — so fencing refuses to run rather than guessing.
    """

    def __init__(self, data_alias: str, fields: list[str]) -> None:
        self.data_alias = data_alias
        self.fields = list(fields)
        super().__init__(
            "billing revision fencing requires one assignment per entitlement "
            f"subject in {data_alias!r} (unique over {', '.join(fields)}); "
            "resolve duplicate subjects or the conflicting "
            f"{SUBJECT_UNIQUE_INDEX_NAME!r} index before enabling it"
        )


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

    async def release_pre_effect_failure(
        self, command: BillingFulfillmentCommand
    ) -> bool:
        """Give up a reservation whose invocation committed no effect.

        Only the invocation that obtained `started` and then failed inside the
        fenced ordering prerequisite may call this, and only while unwinding —
        by then no further effect can run. The compare-and-delete pins the
        exact command id, the still-pending status, and the exact command hash,
        so it can never reclaim a record that some other invocation owns or
        that has since reached a terminal state.

        Deleting rather than marking is deliberate: the command produced no
        durable meaning, so there is nothing about this attempt worth keeping,
        and the retry then takes the ordinary `start` path with no second
        reacquisition rule to get wrong. A concurrent identical caller that
        arrived before the delete still sees `pending` and is still refused —
        exclusivity is unchanged. Only a later retry can start.
        """
        collection = await self._collection()
        result = await collection.delete_one(
            {
                "_id": command.command_id,
                "status": "pending",
                "command_hash": _command_hash(command),
            }
        )
        released = bool(getattr(result, "deleted_count", 0))
        if released:
            logger.info(
                "BILLING_FULFILLMENT_COMMAND_RELEASED: command_id=%s reason=pre_effect_failure",
                command.command_id,
            )
        return released

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
        original = BillingFulfillmentResult.model_validate(raw_result)
        # `status` stays "replayed" so existing callers are unaffected, but the
        # original disposition is preserved explicitly: a replayed command that
        # was superseded (or rejected, or partial) must stay recognizably so.
        # Inferring it from counters is not sound — an applied command and a
        # superseded one can both report success with zero rejected effects.
        return original.model_copy(
            update={
                "status": "replayed",
                "original_status": original.original_status or original.status,
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
        # Collections whose subject-uniqueness prerequisite has been verified.
        self._subject_index_ready: set[tuple[int, str]] = set()

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

        try:
            result = await self._apply_effects(resolved)
        except BillingFulfillmentPreEffectError:
            # The failure is positively before any effect committed, so the
            # durable reservation describes work that never happened. Leaving
            # it pending would refuse every identical retry forever; releasing
            # it here — and only here — lets the same command finish later.
            await store.release_pre_effect_failure(resolved)
            raise
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

        if self._fencing_enabled(command):
            # Ordering authority is established BEFORE the assignment commits.
            # Doing it afterwards leaves a window in which the assignment is
            # already the authoritative revision while some wallet still
            # permits an older revision to credit — and that window cannot be
            # repaired retroactively, because the older allowance may already
            # be in flight. Claiming first makes the transition atomic in the
            # only sense that matters: either this revision owns every
            # applicable wallet head and may commit, or it does not commit.
            await self._ensure_fenced_subject_prerequisites()
            if not await self._claim_wallet_subject_revision(command):
                # A newer revision already owns a wallet head, so this command
                # has been overtaken. Its assignment must not apply.
                return [
                    BillingFulfillmentEffectResult(
                        effect=(
                            "assignment_cancel"
                            if command.event_type == "subscription_cancelled"
                            else "assignment_upsert"
                        ),
                        status="skipped",
                        reason=STALE_REVISION_REASON,
                        details={"subject_revision": command.subject_revision},
                    ),
                    BillingFulfillmentEffectResult(
                        effect="plan_allowances",
                        status="skipped",
                        reason=STALE_REVISION_REASON,
                    ),
                ]

        assignment = await self._apply_assignment(command, plan=plan)
        effects.append(assignment)
        if assignment.reason == SUBJECT_IDENTITY_COLLISION_REASON:
            # The assignment could not prove which subject it belongs to, so
            # nothing downstream may act on it either.
            effects.append(
                BillingFulfillmentEffectResult(
                    effect="plan_allowances",
                    status="skipped",
                    reason=SUBJECT_IDENTITY_COLLISION_REASON,
                )
            )
            return effects
        if assignment.reason == STALE_REVISION_REASON:
            # A newer revision already governs this subject. Allowances must be
            # suppressed too: their idempotency key is plan-and-period scoped,
            # so a stale command naming a different plan would otherwise mint
            # real tokens behind a fenced assignment.
            effects.append(
                BillingFulfillmentEffectResult(
                    effect="plan_allowances",
                    status="skipped",
                    reason=STALE_REVISION_REASON,
                )
            )
            return effects
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

    async def _ensure_fenced_subject_prerequisites(self) -> None:
        """Validate subject uniqueness before any authority is claimed."""
        if self._config is None or self._config.assignment_store is None:
            return
        collection = await self._collection()
        if collection is None:
            return
        await self._ensure_subject_uniqueness(collection, self._config.assignment_store)

    async def _claim_wallet_subject_revision(
        self, command: BillingFulfillmentCommand
    ) -> bool:
        """Claim the ordering head on every wallet this subject could touch.

        Each wallet keeps its own head, so the claim covers all wallets the
        config declares for a compatible scope — otherwise a later allowance on
        an unclaimed wallet would accept a stale revision.

        Returns False when a wallet reports that a strictly newer revision
        already owns it: that is a meaning-bearing decline and the command has
        been overtaken. Operational failures are NOT swallowed — they propagate
        so the command can be retried, because acknowledging a revision whose
        ordering was never established is exactly how a stale credit slips
        through. Retry is safe: equal revisions succeed, so heads already
        claimed do not block a second attempt.
        """
        subject_key = self._subject_key(command)
        if subject_key is None or self._config is None or command.subject_revision is None:
            return True
        for wallet in self._config.token_wallets or []:
            scope = getattr(wallet, "scope", None)
            if scope == "tenant" and not command.tenant_id:
                continue
            if scope == "user" and not command.user_id:
                continue
            try:
                claimed = await self._ledger.advance_subject_revision(
                    app_id=command.app_id,
                    wallet_id=wallet.wallet_id,
                    user_id=command.user_id,
                    tenant_id=command.tenant_id,
                    preferred_scope=scope if scope in {"user", "tenant"} else None,
                    subject_key=subject_key,
                    subject_revision=command.subject_revision,
                )
            except Exception as exc:
                raise BillingFulfillmentOrderingUnavailable(
                    wallet_id=wallet.wallet_id, cause=exc
                ) from exc
            if not claimed:
                logger.info(
                    "BILLING_SUBJECT_REVISION_SUPERSEDED: app_id=%s wallet_id=%s revision=%s",
                    command.app_id,
                    wallet.wallet_id,
                    command.subject_revision,
                )
                return False
        return True

    def _fencing_enabled(self, command: BillingFulfillmentCommand) -> bool:
        """One decision governs the whole command.

        Fencing is on only when the caller supplies ordering authority AND the
        app's assignment store declares the revision field. Deciding per effect
        produced a half-fenced system: an app that opted out still had its
        wallet allowances refused as stale while its assignment happily took
        the older revision.
        """
        if command.subject_revision is None:
            return False
        store = self._config.assignment_store if self._config is not None else None
        return store is not None and store.revision_field is not None

    def _subject_key(self, command: BillingFulfillmentCommand) -> str | None:
        """Opaque entitlement-subject identity shared by every fenced effect.

        Derived from the same assignment query the assignment fence uses, so
        the two commit boundaries order against exactly the same subject even
        though a wallet balance is scoped more coarsely (it drops workspace).
        """
        if not self._fencing_enabled(command):
            return None
        query = self._assignment_query(command)
        if not query:
            return None
        return _subject_identity(command.app_id, query)

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
        if plan_id is None:
            return BillingFulfillmentEffectResult(
                effect="assignment_cancel" if command.event_type == "subscription_cancelled" else "assignment_upsert",
                status="skipped",
                reason="plan_id_missing",
            )
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

        effect: BillingFulfillmentEffect = (
            "assignment_cancel"
            if command.event_type == "subscription_cancelled"
            else "assignment_upsert"
        )
        revision = command.subject_revision
        revision_field = store.revision_field
        insert_only = {"_id": assignment_id, "created_at": now}

        if revision_field is None or revision is None:
            # Unfenced caller: unchanged pre-fence behavior.
            await collection.update_one(
                query, {"$set": updates, "$setOnInsert": insert_only}, upsert=True
            )
            return self._assignment_applied(
                effect,
                assignment_id=assignment_id,
                store=store,
                plan_id=plan_id,
                status=status,
                revision=None,
            )

        # Fencing is only sound when the SUBJECT has exactly one row. The
        # deterministic `_id` cannot supply that on its own: an assignment
        # created before this contract carries an ObjectId, so a stale
        # revision-filtered upsert would happily insert a second, deterministic
        # row for the same subject and report itself applied. A unique index
        # over the configured subject paths is what makes "one subject, one
        # row" true regardless of how a row was originally created.
        await self._ensure_subject_uniqueness(collection, store)

        updates[revision_field] = revision
        # ONE atomic operation covers creation and update alike. The filter
        # carries the revision predicate, so there is never an unfenced write:
        #
        #   - subject absent            -> insert, stamped with this revision
        #   - subject older             -> filter matches, update applies
        #   - subject newer or equal    -> filter misses, the upsert attempts an
        #                                  insert and the unique subject index
        #                                  refuses it
        #
        # A separate "does it exist yet?" read followed by an unfenced upsert
        # would lose the creation race outright: two concurrent new-subject
        # revisions both observe absence, and the loser's unfenced write then
        # overwrites the winner.
        fence_query = {
            **query,
            "$or": [
                {revision_field: {"$exists": False}},
                {revision_field: None},
                {revision_field: {"$lt": revision}},
            ],
        }
        write = {"$set": updates, "$setOnInsert": insert_only}

        # A DuplicateKeyError proves a row exists but did not match the fence.
        # That is either a genuinely newer revision (stale — terminal) or a row
        # inserted by a concurrent writer whose revision is still older than
        # ours (retry: the same fenced upsert now matches it). Each iteration
        # therefore either applies, proves staleness, or observes a strictly
        # newer state, so the loop cannot spin.
        for _ in range(_ASSIGNMENT_FENCE_ATTEMPTS):
            try:
                await collection.update_one(fence_query, write, upsert=True)
            except DuplicateKeyError:
                # Resolve the row through the canonical SUBJECT selector, not
                # through the deterministic id: the blocking row may predate
                # this contract and carry an ObjectId.
                existing = await collection.find_one(dict(query))
                if existing is None:
                    existing = await collection.find_one({"_id": assignment_id})
                collision = self._assignment_subject_mismatch(existing, query=query)
                if collision is not None:
                    return BillingFulfillmentEffectResult(
                        effect=effect,
                        status="rejected",
                        reason=SUBJECT_IDENTITY_COLLISION_REASON,
                        details={
                            "assignment_id": assignment_id,
                            "data_alias": store.data_alias,
                            "conflicting_field": collision,
                        },
                    )
                continue
            return self._assignment_applied(
                effect,
                assignment_id=assignment_id,
                store=store,
                plan_id=plan_id,
                status=status,
                revision=revision,
            )

        return BillingFulfillmentEffectResult(
            effect=effect,
            status="skipped",
            reason=STALE_REVISION_REASON,
            details={
                "assignment_id": assignment_id,
                "data_alias": store.data_alias,
                "subject_revision": revision,
            },
        )

    async def _ensure_subject_uniqueness(self, collection: Any, store: Any) -> None:
        """Guarantee one assignment row per entitlement subject, once.

        Revision fencing is a correctness prerequisite, not a convenience: with
        two rows for one subject, a stale command can create the second and
        report itself applied. The index is built over the exact configured
        subject paths, so it follows a store that maps them to dotted or
        renamed fields — unless the collection already carries an index that
        makes the same promise, in which case that one is accepted as is. What
        fencing needs is the guarantee, not ownership of a particular name.

        Pre-existing duplicate subjects make the index impossible. That is a
        data-migration problem the operator must resolve, so it fails closed
        with an actionable error rather than silently picking a winner or
        deleting rows.
        """
        alias = str(getattr(store, "data_alias", "") or "")
        cache_key = (id(collection), alias)
        if cache_key in self._subject_index_ready:
            return
        fields = self._subject_field_paths(store)
        if not fields:
            self._subject_index_ready.add(cache_key)
            return
        required = [(field, 1) for field in fields]
        if await self._subject_uniqueness_already_guaranteed(collection, required):
            # Fencing needs the guarantee, not ownership of a particular index
            # name. A collection that already carries an equivalent unique index
            # is fine, and demanding a second one would reject a valid
            # configuration outright.
            self._subject_index_ready.add(cache_key)
            return
        try:
            await collection.create_index(
                required,
                unique=True,
                name=SUBJECT_UNIQUE_INDEX_NAME,
            )
        except DuplicateKeyError as exc:
            raise BillingFulfillmentSubjectIndexError(alias, fields) from exc
        except OperationFailure as exc:
            code = getattr(exc, "code", None)
            if code in _SUBJECT_INDEX_BLOCKING_CODES:
                raise BillingFulfillmentSubjectIndexError(alias, fields) from exc
            raise
        self._subject_index_ready.add(cache_key)

    @staticmethod
    async def _subject_uniqueness_already_guaranteed(
        collection: Any, required: list[tuple[str, int]]
    ) -> bool:
        """Does an existing index already guarantee subject uniqueness?

        Equivalence is judged on what the index actually promises, not on its
        name: the same ordered keys and directions, and `unique: true`. Options
        that narrow which documents the constraint covers — `sparse`, a
        `partialFilterExpression` — disqualify it, because subjects outside the
        covered set would be free to duplicate. A non-simple `collation` is
        also refused: it changes which values count as equal, which is a
        different guarantee than the one fencing reasons about.
        """
        information = getattr(collection, "index_information", None)
        if information is None:
            return False
        try:
            existing = await information()
        except Exception:
            return False
        if not isinstance(existing, Mapping):
            return False
        for definition in existing.values():
            if not isinstance(definition, Mapping):
                continue
            if not definition.get("unique"):
                continue
            if definition.get("sparse") or definition.get("partialFilterExpression"):
                continue
            collation = definition.get("collation")
            if collation and str(collation.get("locale", "")) != "simple":
                continue
            keys = [
                (str(field), int(direction))
                for field, direction in (definition.get("key") or [])
            ]
            if keys == required:
                return True
        return False

    @staticmethod
    def _subject_field_paths(store: Any) -> list[str]:
        """Configured paths that together identify one entitlement subject."""
        names = ("app_id_field", "tenant_id_field", "workspace_id_field", "user_id_field")
        paths: list[str] = []
        for name in names:
            value = str(getattr(store, name, "") or "").strip()
            if value and value not in paths:
                paths.append(value)
        return paths

    @staticmethod
    def _assignment_subject_mismatch(
        existing: Mapping[str, Any] | None,
        *,
        query: Mapping[str, Any],
    ) -> str | None:
        """Name of a subject field whose persisted value differs, if any.

        A duplicate-key collision is not by itself proof that a row belongs to
        this entitlement subject — the deterministic assignment id is built by
        string-formatting scope values, so a null scope and the literal string
        "None" produce the same id — and treating it as proof would let one
        subject silently suppress another. Compare the persisted scope fields
        with typed equality first.

        Configured mappings may be dotted paths, so the comparison resolves
        them exactly as the Mongo query and the `$set` write do. Reading
        `document["scope.user"]` instead of walking into `scope.user` would
        report a mismatch for a row that genuinely is the same subject.
        """
        if existing is None:
            # The row vanished between the failed upsert and this read; the
            # caller retries the fenced upsert.
            return None
        for field, expected in query.items():
            actual = _resolve_document_path(existing, str(field))
            if actual != expected or type(actual) is not type(expected):
                return str(field)
        return None

    @staticmethod
    def _assignment_applied(
        effect: BillingFulfillmentEffect,
        *,
        assignment_id: str,
        store: Any,
        plan_id: str,
        status: str,
        revision: int | None,
    ) -> BillingFulfillmentEffectResult:
        details: dict[str, Any] = {
            "assignment_id": assignment_id,
            "data_alias": store.data_alias,
            "plan_id": plan_id,
            "status": status,
        }
        if revision is not None:
            details["subject_revision"] = revision
        return BillingFulfillmentEffectResult(
            effect=effect,
            status="applied",
            details=details,
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
                period_start=command.occurred_at or command.starts_at or _now(),
                # Same entitlement subject the assignment fence uses, so both
                # effects order identically. The ledger fences its own commit:
                # the assignment having been applied earlier in this call is
                # not authority any more once we have awaited.
                subject_key=self._subject_key(command),
                subject_revision=command.subject_revision,
            )
        except Exception as exc:
            return BillingFulfillmentEffectResult(
                effect="plan_allowances",
                status="rejected",
                reason=str(exc),
            )

        if results and all(
            result.entry.get("rejection_reason") == STALE_SUBJECT_REVISION_REASON
            for result in results
            if result.status == "rejected"
        ) and any(result.status == "rejected" for result in results):
            # The ledger refused at its own commit boundary because a newer
            # revision of this subject already landed. That is ordering, not
            # failure.
            return BillingFulfillmentEffectResult(
                effect="plan_allowances",
                status="skipped",
                reason=STALE_REVISION_REASON,
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
            status=_effect_status(result.status),
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
            status=_effect_status(result.status),
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
