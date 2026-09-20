"""Real-MongoDB proofs for billing fulfillment revision fencing.

The in-memory double in ``test_billing_fulfillment.py`` models Mongo's
document-level atomicity, but only a real server can prove the things this
fence depends on: that a filtered upsert collides on the deterministic
assignment id rather than inserting a second row, that concurrent creations of
the same subject resolve to the highest revision, and that a stale allowance
loses its race inside the balance update itself.

These are gated on MongoDB *reachability*, matching
``test_chat_execution_lease_real_mongo.py``. The authoritative CI shard lane
exports ``MONGO_URI`` against a live ``mongo:7`` service, so these execute on
every PR instead of silently skipping the way an opt-in env flag would.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from mozaiksai.core.billing.fulfillment import (
    MAX_SUBJECT_REVISION,
    SUBJECT_UNIQUE_INDEX_NAME,
    BillingFulfillmentCommand,
    BillingFulfillmentCommandStore,
    BillingFulfillmentOrderingUnavailable,
    BillingFulfillmentPendingError,
    BillingFulfillmentService,
    BillingFulfillmentSubjectIndexError,
)
from mozaiksai.core.runtime.app.subscriptions_loader import SubscriptionsConfig
from mozaiksai.core.tokens.wallet import TokenWalletLedger, _entry_id, _scope_for


def _mongo_uri() -> str | None:
    for name in ("MONGO_URI", "MONGODB_URI", "MONGO_URL"):
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _mongo_reachable() -> bool:
    uri = _mongo_uri()
    if not uri:
        return False
    try:
        from pymongo import MongoClient

        MongoClient(uri, serverSelectionTimeoutMS=2000).admin.command("ping")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _mongo_reachable(),
    reason="requires a reachable MongoDB via MONGO_URI (provided by the CI mongo service)",
)


def _config(*, revision_field: str | None = "billing_revision") -> SubscriptionsConfig:
    return SubscriptionsConfig.model_validate(
        {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Fence proof",
            "default_plan_id": "free",
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "user_id_field": "user_id",
                "revision_field": revision_field,
            },
            "token_wallets": [
                {
                    "wallet_id": "ai_tokens",
                    "label": "AI tokens",
                    "unit": "tokens",
                    "usage_meter_id": "ai_tokens",
                    "scope": "user",
                }
            ],
            "plans": [
                {"plan_id": "free", "label": "Free", "capabilities": []},
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["reports.export"],
                    "token_allowances": [
                        {"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}
                    ],
                },
                {
                    "plan_id": "enterprise",
                    "label": "Enterprise",
                    "capabilities": ["reports.export", "reports.admin"],
                    "token_allowances": [
                        {"wallet_id": "ai_tokens", "amount": 200, "cadence": "monthly"}
                    ],
                },
            ],
        }
    )


class _Harness:
    def __init__(self, database: Any, suffix: str) -> None:
        self.database = database
        self.assignments = database[f"fence_assignments_{suffix}"]
        self.app_id = f"app_{suffix}"
        self.ledger = TokenWalletLedger(database=database)
        self.store = BillingFulfillmentCommandStore(database=database)

    def service(self, *, revision_field: str | None = "billing_revision", durable: bool = False):
        return BillingFulfillmentService(
            config=_config(revision_field=revision_field),
            ledger=self.ledger,
            collection_resolver=lambda _alias: self.assignments,
            command_store=self.store if durable else None,
        )

    def command(
        self,
        command_id: str,
        *,
        plan_id: str = "pro",
        subject_revision: int | None = None,
        event_type: str = "subscription_updated",
        user_id: str = "user_1",
    ) -> BillingFulfillmentCommand:
        return BillingFulfillmentCommand(
            command_id=f"{command_id}_{self.app_id}",
            event_type=event_type,
            source="test",
            app_id=self.app_id,
            user_id=user_id,
            plan_id=plan_id,
            subject_revision=subject_revision,
            occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
        )

    async def assignment(self, *, user_id: str = "user_1") -> dict[str, Any] | None:
        return await self.assignments.find_one(
            {"app_id": self.app_id, "tenant_id": None, "user_id": user_id}
        )

    async def balance(self, *, user_id: str = "user_1") -> int:
        result = await self.ledger.query_balance(app_id=self.app_id, user_id=user_id)
        return int(result["balance"])


@pytest.fixture()
async def harness():
    from mozaiksai.core.core_config import close_mongo_client, get_mongo_client

    close_mongo_client()
    client = get_mongo_client()
    database = client["mozaiks_billing_fence_proof"]
    suffix = uuid.uuid4().hex[:12]
    subject = _Harness(database, suffix)
    yield subject
    await subject.assignments.drop()
    close_mongo_client()


# ── Defect A: strict, BSON-safe revision ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [True, False, 1.0, "1", "1.0", -1, MAX_SUBJECT_REVISION + 1]
)
async def test_invalid_subject_revision_is_rejected(harness, value) -> None:
    """No coercion, no out-of-range value that Mongo could not store."""
    with pytest.raises(ValueError):
        harness.command("cmd_bad", subject_revision=value)


@pytest.mark.asyncio
async def test_max_int64_revision_round_trips_through_mongo(harness) -> None:
    """The upper bound must actually persist rather than raise OverflowError."""
    from bson.int64 import Int64

    service = harness.service()
    result = await service.apply(
        harness.command("cmd_max", subject_revision=MAX_SUBJECT_REVISION)
    )
    assert result.status == "applied"
    stored = await harness.assignment()
    assert int(stored["billing_revision"]) == MAX_SUBJECT_REVISION
    # And a value read back as BSON Int64 is still an acceptable command value.
    assert (
        harness.command("cmd_i64", subject_revision=Int64(5)).subject_revision == 5
    )


# ── Defects C/D: atomic creation and subject identity ────────────────────────


@pytest.mark.asyncio
async def test_first_fenced_write_creates_and_stamps(harness) -> None:
    service = harness.service()
    result = await service.apply(harness.command("cmd_first", subject_revision=3))
    assert result.status == "applied"
    assert (await harness.assignment())["billing_revision"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(("first", "second"), [(2, 9), (9, 2)])
async def test_two_concurrent_creations_keep_the_highest(harness, first, second) -> None:
    """Both revisions observe an absent subject at the same moment.

    A read-then-unfenced-upsert loses this outright: whichever writer inserts
    second overwrites the winner and the subject ends at the lower revision.
    """
    service = harness.service()
    results = await asyncio.gather(
        service.apply(
            harness.command(f"cmd_a_{first}", plan_id="pro", subject_revision=first)
        ),
        service.apply(
            harness.command(
                f"cmd_b_{second}", plan_id="enterprise", subject_revision=second
            )
        ),
    )
    stored = await harness.assignment()
    assert stored["billing_revision"] == max(first, second)
    assert stored["plan_id"] == ("pro" if first > second else "enterprise")
    # Whether the two interleave or serialize, the invariant is the same: the
    # subject ends at the highest revision and no command reports a stale
    # application. (Serialized execution legitimately applies both in order.)
    assert all(r.status in {"applied", "superseded"} for r in results)
    assert await harness.assignments.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["ascending", "descending"])
async def test_ten_concurrent_creations_settle_on_the_highest(harness, order) -> None:
    service = harness.service()
    revisions = list(range(1, 11))
    if order == "descending":
        revisions.reverse()
    results = await asyncio.gather(
        *[
            service.apply(harness.command(f"cmd_{r}", subject_revision=r))
            for r in revisions
        ]
    )
    stored = await harness.assignment()
    assert stored["billing_revision"] == 10, "the highest revision must govern"
    assert await harness.assignments.count_documents({}) == 1, "one subject, one row"
    # No stale command may claim it applied.
    for revision, result in zip(revisions, results, strict=True):
        if result.status == "applied":
            assert result.effects[0].details["subject_revision"] == revision


@pytest.mark.asyncio
async def test_stale_and_equal_revisions_are_superseded(harness) -> None:
    service = harness.service()
    await service.apply(harness.command("cmd_head", plan_id="pro", subject_revision=5))
    stale = await service.apply(
        harness.command("cmd_stale", plan_id="free", subject_revision=2)
    )
    equal = await service.apply(
        harness.command("cmd_equal", plan_id="free", subject_revision=5)
    )
    assert stale.status == "superseded" and equal.status == "superseded"
    assert stale.applied == 0 and equal.applied == 0
    assert (await harness.assignment())["plan_id"] == "pro"


@pytest.mark.asyncio
async def test_subject_identity_collision_fails_closed(harness) -> None:
    """A null scope and the literal string "None" hash to the same assignment
    id. That collision must never be read as evidence of the same subject."""
    service = harness.service()
    await service.apply(
        harness.command("cmd_null_user", subject_revision=4, user_id="user_x")
    )
    row = await harness.assignments.find_one({"user_id": "user_x"})
    # Force the historic ambiguity: same deterministic _id, different subject.
    await harness.assignments.update_one(
        {"_id": row["_id"]}, {"$set": {"user_id": None}}
    )

    collided = await service.apply(
        harness.command("cmd_string_none", subject_revision=99, user_id="user_x")
    )
    assert collided.success is False
    assignment_effect = collided.effects[0]
    assert assignment_effect.status == "rejected"
    assert assignment_effect.reason == "subject_identity_collision"
    assert assignment_effect.details["conflicting_field"] == "user_id"
    # Nothing downstream may act on an unprovable subject either.
    assert collided.effects[1].reason == "subject_identity_collision"
    # Only the first, legitimate command minted; the collided one added nothing.
    assert await harness.balance(user_id="user_x") == 100
    # Nothing was suppressed as if it were the same subject, and nothing was
    # written over the colliding row.
    assert (await harness.assignments.find_one({"_id": row["_id"]}))["user_id"] is None


@pytest.mark.asyncio
async def test_distinct_subjects_stay_isolated(harness) -> None:
    service = harness.service()
    await service.apply(
        harness.command("cmd_u1", subject_revision=9, user_id="user_1")
    )
    other = await service.apply(
        harness.command("cmd_u2", subject_revision=2, user_id="user_2")
    )
    assert other.status == "applied", "one subject's revision must not fence another"
    assert (await harness.assignment(user_id="user_2"))["billing_revision"] == 2


# ── Defect E: allowance fenced at its own commit ─────────────────────────────


@pytest.mark.asyncio
async def test_stale_allowance_cannot_mint_after_a_newer_revision(harness) -> None:
    """The motivating race: revision 1 pauses right before its allowance
    commit, revision 2 fully applies, then revision 1 resumes."""
    service = harness.service()

    # Revision 2 applies fully first (assignment + 200 tokens).
    newer = await service.apply(
        harness.command("cmd_rev2", plan_id="enterprise", subject_revision=2)
    )
    assert newer.status == "applied"
    assert await harness.balance() == 200

    # Revision 1 resumes and attempts ONLY its allowance commit, exactly as a
    # paused in-flight command would.
    stale_entries = await harness.ledger.ensure_plan_allowances(
        config=_config(),
        app_id=harness.app_id,
        plan_id="pro",
        token_allowances=[{"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}],
        user_id="user_1",
        subject_key=service._subject_key(
            harness.command("cmd_rev1", plan_id="pro", subject_revision=1)
        ),
        subject_revision=1,
    )

    assert [entry.status for entry in stale_entries] == ["rejected"]
    assert stale_entries[0].entry["rejection_reason"] == "stale_subject_revision"
    assert await harness.balance() == 200, "a stale revision must not mint tokens"


@pytest.mark.asyncio
async def test_allowance_fence_permits_the_newest_revision(harness) -> None:
    service = harness.service()
    first = await service.apply(
        harness.command("cmd_p1", plan_id="pro", subject_revision=1)
    )
    assert first.status == "applied"
    assert await harness.balance() == 100

    second = await service.apply(
        harness.command("cmd_p2", plan_id="enterprise", subject_revision=2)
    )
    assert second.status == "applied"
    assert await harness.balance() == 300, "the newer plan's allowance is additive"


@pytest.mark.asyncio
async def test_concurrent_allowances_leave_no_stale_credit(harness) -> None:
    service = harness.service()
    results = await asyncio.gather(
        *[
            service.apply(
                harness.command(
                    f"cmd_c{r}",
                    plan_id="pro" if r % 2 else "enterprise",
                    subject_revision=r,
                )
            )
            for r in range(1, 6)
        ]
    )
    stored = await harness.assignment()
    assert stored["billing_revision"] == 5
    applied = [r for r in results if r.status == "applied"]
    # Allowance idempotency is plan-and-period scoped, so repeated applications
    # of the same plan within the period credit once. Every applied revision
    # contributed its own plan's allowance and no superseded revision
    # contributed anything.
    plans = {
        "pro" if int(r.effects[0].details["subject_revision"]) % 2 else "enterprise"
        for r in applied
    }
    expected = sum(100 if plan == "pro" else 200 for plan in plans)
    assert await harness.balance() == expected


# ── Cancellation / reactivation ordering ─────────────────────────────────────


@pytest.mark.asyncio
async def test_delayed_activation_cannot_revive_a_newer_cancellation(harness) -> None:
    service = harness.service()
    await service.apply(
        harness.command(
            "cmd_act1", subject_revision=1, event_type="subscription_activated"
        )
    )
    await service.apply(
        harness.command(
            "cmd_cancel2", subject_revision=2, event_type="subscription_cancelled"
        )
    )
    late = await service.apply(
        harness.command(
            "cmd_act1_late", subject_revision=1, event_type="subscription_activated"
        )
    )
    assert late.status == "superseded"
    assert (await harness.assignment())["status"] == "cancelled"


@pytest.mark.asyncio
async def test_delayed_cancellation_cannot_revoke_a_newer_activation(harness) -> None:
    service = harness.service()
    await service.apply(
        harness.command(
            "cmd_cancel2", subject_revision=2, event_type="subscription_cancelled"
        )
    )
    await service.apply(
        harness.command(
            "cmd_act3", subject_revision=3, event_type="subscription_activated"
        )
    )
    late = await service.apply(
        harness.command(
            "cmd_cancel2_late", subject_revision=2, event_type="subscription_cancelled"
        )
    )
    assert late.status == "superseded"
    assert (await harness.assignment())["status"] == "active"


@pytest.mark.asyncio
async def test_a_b_a_sequence_ends_at_the_third_revision(harness) -> None:
    service = harness.service()
    for index, plan in enumerate(["pro", "enterprise", "pro"], start=1):
        result = await service.apply(
            harness.command(f"cmd_seq{index}", plan_id=plan, subject_revision=index)
        )
        assert result.status == "applied"
    stored = await harness.assignment()
    assert stored["plan_id"] == "pro"
    assert stored["billing_revision"] == 3


# ── Defects F/G: durable command identity and replay ─────────────────────────


@pytest.mark.asyncio
async def test_pre_fence_command_hash_survives_the_upgrade(harness) -> None:
    """A command completed before this change must replay, not conflict.

    The pre-change canonical document is reproduced by simply omitting
    `subject_revision`, which is exactly what an unfenced command serializes
    to now — so a hash computed then still matches one computed today.
    """
    service = harness.service(durable=True)
    unfenced = harness.command("cmd_unfenced", subject_revision=None)

    first = await service.apply_durable(unfenced)
    assert first.status == "applied"

    records = await harness.store.list_records(command_id=unfenced.command_id)
    assert len(records) == 1
    assert "subject_revision" not in records[0]["command"], (
        "an unfenced command must not persist the new field at all"
    )

    replay = await service.apply_durable(unfenced)
    assert replay.status == "replayed"
    assert replay.original_status == "applied"
    assert replay.success is True


@pytest.mark.asyncio
async def test_superseded_replay_keeps_its_disposition(harness) -> None:
    service = harness.service(durable=True)
    await service.apply_durable(harness.command("cmd_head", subject_revision=9))
    stale = harness.command("cmd_stale", plan_id="free", subject_revision=1)

    first = await service.apply_durable(stale)
    replay = await service.apply_durable(stale)

    assert first.status == "superseded"
    assert replay.status == "replayed"
    assert replay.original_status == "superseded", (
        "a replayed supersession must stay recognizably superseded"
    )
    assert replay.success is True and replay.applied == 0


@pytest.mark.asyncio
async def test_unknown_remote_outcome_acceptance_theorem(harness) -> None:
    """#497's acceptance theorem, end to end.

    An old revision's request is in flight when the sender loses the response;
    a newer revision applies; the old request finally reaches the receiver.
    Both the assignment AND the allowance authority must stay at the newer
    revision, and retrying the old command must replay a stable supersession.
    """
    service = harness.service(durable=True)
    old_command = harness.command("cmd_old", plan_id="pro", subject_revision=4)

    newer = await service.apply_durable(
        harness.command("cmd_new", plan_id="enterprise", subject_revision=8)
    )
    assert newer.status == "applied"
    assert await harness.balance() == 200

    late = await service.apply_durable(old_command)
    assert late.status == "superseded"
    assert (await harness.assignment())["billing_revision"] == 8
    assert (await harness.assignment())["plan_id"] == "enterprise"
    assert await harness.balance() == 200, "the allowance authority did not regress"

    retry = await service.apply_durable(old_command)
    assert retry.status == "replayed"
    assert retry.original_status == "superseded"


# ── commands that never opted into fencing ───────────────────────────────────


@pytest.mark.asyncio
async def test_commands_without_revisions_are_untouched(harness) -> None:
    """Merely upgrading must not stamp revisions on commands that never opted in."""
    service = harness.service()
    await service.apply(harness.command("cmd_unfenced1", plan_id="pro"))
    stored = await harness.assignment()
    assert "billing_revision" not in stored
    assert stored["plan_id"] == "pro"

    await service.apply(harness.command("cmd_unfenced2", plan_id="enterprise"))
    stored = await harness.assignment()
    assert stored["plan_id"] == "enterprise", "unfenced writes stay last-writer-wins"
    assert "billing_revision" not in stored
    assert await harness.balance() == 300


@pytest.mark.asyncio
async def test_revision_field_null_disables_fencing_end_to_end(harness) -> None:
    service = harness.service(revision_field=None)
    await service.apply(harness.command("cmd_n1", plan_id="pro", subject_revision=9))
    older = await service.apply(
        harness.command("cmd_n2", plan_id="free", subject_revision=1)
    )
    assert older.status == "applied", "opted-out stores keep prior behavior"
    stored = await harness.assignment()
    assert stored["plan_id"] == "free"
    assert "billing_revision" not in stored


# ══ correction round: subject uniqueness, wallet head, aggregation ══════════


@pytest.mark.asyncio
async def test_existing_objectid_row_cannot_be_duplicated_by_a_stale_revision(
    harness,
) -> None:
    """The defect this round closes.

    An assignment created before this contract carries an ObjectId. A stale
    revision-filtered upsert used to insert a SECOND, deterministic-id row for
    the same subject and report itself applied — leaving one subject with two
    assignments. Deterministic-id uniqueness cannot prevent that; subject
    uniqueness can.
    """
    service = harness.service()
    # A pre-contract row: ObjectId _id, no revision stamp.
    await harness.assignments.insert_one(
        {
            "app_id": harness.app_id,
            "tenant_id": None,
            "user_id": "user_1",
            "plan_id": "free",
            "status": "active",
        }
    )

    newer = await service.apply(
        harness.command("cmd_new", plan_id="enterprise", subject_revision=6)
    )
    assert newer.status == "applied"
    stale = await service.apply(
        harness.command("cmd_stale", plan_id="pro", subject_revision=2)
    )

    assert stale.status == "superseded"
    assert await harness.assignments.count_documents({}) == 1, (
        "one entitlement subject must never hold two assignment rows"
    )
    stored = await harness.assignment()
    assert stored["plan_id"] == "enterprise"
    assert stored["billing_revision"] == 6
    assert not isinstance(stored["_id"], str), "the historical row must be updated in place"


@pytest.mark.asyncio
async def test_fenced_mode_establishes_the_subject_unique_index(harness) -> None:
    service = harness.service()
    await service.apply(harness.command("cmd_idx", subject_revision=1))

    names = await harness.assignments.index_information()
    assert SUBJECT_UNIQUE_INDEX_NAME in names
    index = names[SUBJECT_UNIQUE_INDEX_NAME]
    assert index.get("unique") is True
    assert [key for key, _direction in index["key"]] == ["app_id", "tenant_id", "user_id"]


@pytest.mark.asyncio
async def test_pre_existing_duplicate_subjects_fail_initialization(harness) -> None:
    """A data-migration problem the operator must resolve — never a silent
    winner and never a deletion."""
    for plan in ("free", "pro"):
        await harness.assignments.insert_one(
            {
                "app_id": harness.app_id,
                "tenant_id": None,
                "user_id": "user_1",
                "plan_id": plan,
                "status": "active",
            }
        )

    service = harness.service()
    with pytest.raises(BillingFulfillmentSubjectIndexError) as excinfo:
        await service.apply(harness.command("cmd_dupes", subject_revision=1))

    assert "one assignment per entitlement subject" in str(excinfo.value)
    assert await harness.assignments.count_documents({}) == 2, "no row may be removed"


@pytest.mark.asyncio
async def test_dotted_subject_mapping_classifies_the_same_subject_correctly(
    harness,
) -> None:
    """A configured `scope.user` mapping must resolve nested, or a legitimate
    stale request for the SAME subject reads as an identity collision."""
    config = SubscriptionsConfig.model_validate(
        {
            **_config().model_dump(mode="json"),
            "assignment_store": {
                "data_alias": "billing.subscriptions",
                "user_id_field": "scope.user",
                "tenant_id_field": None,
                "revision_field": "billing_revision",
            },
        }
    )
    service = BillingFulfillmentService(
        config=config,
        ledger=harness.ledger,
        collection_resolver=lambda _alias: harness.assignments,
    )

    newer = await service.apply(harness.command("cmd_dot_new", subject_revision=5))
    assert newer.status == "applied"
    stored = await harness.assignments.find_one({"app_id": harness.app_id})
    assert stored["scope"]["user"] == "user_1", "the dotted path is written nested"

    stale = await service.apply(
        harness.command("cmd_dot_stale", plan_id="free", subject_revision=2)
    )
    assert stale.status == "superseded", "same subject, just older"
    assert stale.effects[0].reason == "stale_revision"
    assert await harness.assignments.count_documents({}) == 1


# ── wallet ordering authority advances for every accepted revision ───────────


@pytest.mark.asyncio
async def test_cancellation_advances_wallet_authority(harness) -> None:
    """A1's allowance is delayed; a cancellation carrying no allowance
    completes; A1 resumes and must not credit."""
    service = harness.service()
    accepted = await service.apply(
        harness.command("cmd_a1", plan_id="pro", subject_revision=1)
    )
    assert accepted.status == "applied"
    await harness.assignments.update_one({}, {"$set": {"billing_revision": 0}})
    await harness.ledger._collections()
    balances, _entries = await harness.ledger._collections()
    await balances.delete_many({})

    cancelled = await service.apply(
        harness.command(
            "cmd_cancel2", plan_id="pro", subject_revision=2,
            event_type="subscription_cancelled",
        )
    )
    assert cancelled.status == "applied"

    # A1's delayed allowance now arrives.
    stale = await harness.ledger.ensure_plan_allowances(
        config=_config(),
        app_id=harness.app_id,
        plan_id="pro",
        token_allowances=[{"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}],
        user_id="user_1",
        subject_key=service._subject_key(
            harness.command("cmd_a1", plan_id="pro", subject_revision=1)
        ),
        subject_revision=1,
    )
    assert [entry.status for entry in stale] == ["rejected"]
    assert stale[0].entry["rejection_reason"] == "stale_subject_revision"
    assert await harness.balance() == 0, "a cancellation must stop an older allowance"


@pytest.mark.asyncio
async def test_zero_allowance_plan_advances_wallet_authority(harness) -> None:
    service = harness.service()
    # Revision 2 selects a valid plan that credits nothing.
    accepted = await service.apply(
        harness.command("cmd_free2", plan_id="free", subject_revision=2)
    )
    assert accepted.status == "applied"
    assert await harness.balance() == 0

    stale = await harness.ledger.ensure_plan_allowances(
        config=_config(),
        app_id=harness.app_id,
        plan_id="pro",
        token_allowances=[{"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}],
        user_id="user_1",
        subject_key=service._subject_key(
            harness.command("cmd_pro1", plan_id="pro", subject_revision=1)
        ),
        subject_revision=1,
    )
    assert stale[0].status == "rejected"
    assert await harness.balance() == 0, "a zero-credit revision still supersedes"


@pytest.mark.asyncio
async def test_idempotent_allowance_reuse_still_advances_authority(harness) -> None:
    """A1 allocates plan A for period P. B2's allowance is delayed. A3 returns
    to plan A in the same period: it must NOT mint again, but it must still
    advance the head so the delayed B2 is refused."""
    service = harness.service()

    first = await service.apply(
        harness.command("cmd_a1", plan_id="pro", subject_revision=1)
    )
    assert first.status == "applied"
    assert await harness.balance() == 100

    third = await service.apply(
        harness.command("cmd_a3", plan_id="pro", subject_revision=3)
    )
    assert third.status == "applied"
    assert await harness.balance() == 100, "same plan and period must not double mint"

    delayed_b2 = await harness.ledger.ensure_plan_allowances(
        config=_config(),
        app_id=harness.app_id,
        plan_id="enterprise",
        token_allowances=[{"wallet_id": "ai_tokens", "amount": 200, "cadence": "monthly"}],
        user_id="user_1",
        subject_key=service._subject_key(
            harness.command("cmd_b2", plan_id="enterprise", subject_revision=2)
        ),
        subject_revision=2,
    )
    assert delayed_b2[0].status == "rejected"
    assert delayed_b2[0].entry["rejection_reason"] == "stale_subject_revision"
    assert await harness.balance() == 100


@pytest.mark.asyncio
async def test_stale_rejection_does_not_consume_the_allowance_identity(harness) -> None:
    """A stale attempt must leave no record under the identity a SUCCESSFUL
    allocation owns, or a later valid revision of the same plan and period is
    permanently denied the allocation it is entitled to."""
    service = harness.service()

    # B2 wins the subject first; plan A never allocated for this period.
    winner = await service.apply(
        harness.command("cmd_b2", plan_id="free", subject_revision=2)
    )
    assert winner.status == "applied"

    stale_a1 = await harness.ledger.ensure_plan_allowances(
        config=_config(),
        app_id=harness.app_id,
        plan_id="pro",
        token_allowances=[{"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}],
        user_id="user_1",
        subject_key=service._subject_key(
            harness.command("cmd_a1", plan_id="pro", subject_revision=1)
        ),
        subject_revision=1,
    )
    assert stale_a1[0].status == "rejected"
    assert await harness.balance() == 0

    # A3 legitimately returns to plan A in the same period.
    valid_a3 = await service.apply(
        harness.command("cmd_a3", plan_id="pro", subject_revision=3)
    )
    assert valid_a3.status == "applied"
    assert await harness.balance() == 100, (
        "the stale attempt must not have consumed plan A's period allocation"
    )


# ── a partially applied command overtaken mid-flight ─────────────────────────


@pytest.mark.asyncio
async def test_partially_applied_command_reports_superseded(harness) -> None:
    """The assignment landed, then a newer revision won, so the command's
    remaining effects were invalidated. Its terminal meaning is superseded even
    though one effect truthfully applied."""
    service = harness.service(durable=True)
    old_command = harness.command("cmd_old", plan_id="pro", subject_revision=5)

    # Interleave for real: the old command claims the head and commits its
    # assignment, and only then does a newer revision take the head — so the
    # allowance that is still to come is the effect that gets invalidated.
    original_apply = service._apply_assignment

    async def _apply_then_lose(command, *, plan):
        effect = await original_apply(command, plan=plan)
        if command.command_id == old_command.command_id:
            await harness.ledger.advance_subject_revision(
                app_id=harness.app_id,
                wallet_id="ai_tokens",
                user_id="user_1",
                preferred_scope="user",
                subject_key=service._subject_key(old_command),
                subject_revision=9,
            )
        return effect

    service._apply_assignment = _apply_then_lose

    result = await service.apply_durable(old_command)

    assignment_effect = next(e for e in result.effects if e.effect == "assignment_upsert")
    allowance_effect = next(e for e in result.effects if e.effect == "plan_allowances")
    assert assignment_effect.status == "applied", "effect history stays truthful"
    assert allowance_effect.reason in {"stale_revision", "stale_subject_revision"}
    assert result.applied >= 1, "the applied effect is still counted"
    assert result.status == "superseded", (
        "a command whose remaining effects were invalidated was overtaken"
    )
    assert result.success is True
    service._apply_assignment = original_apply

    replay = await service.apply_durable(old_command)
    assert replay.status == "replayed"
    assert replay.original_status == "superseded", (
        "the durable replay must preserve the real terminal disposition"
    )


# ── the opt-out disables BOTH fences ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_revision_field_null_disables_assignment_and_wallet_fences(
    harness,
) -> None:
    """Half-fenced is the bug: assignment took the older revision while the
    wallet refused its allowance as stale."""
    service = harness.service(revision_field=None)

    newer = await service.apply(
        harness.command("cmd_b9", plan_id="enterprise", subject_revision=9)
    )
    assert newer.status == "applied"
    assert await harness.balance() == 200

    older = await service.apply(
        harness.command("cmd_a1", plan_id="pro", subject_revision=1)
    )
    assert older.status == "applied", "opted-out stores keep last-writer-wins"
    index_names = await harness.assignments.index_information()
    assert SUBJECT_UNIQUE_INDEX_NAME not in index_names, (
        "an opted-out config must not acquire fenced-mode index requirements"
    )
    stored = await harness.assignment()
    assert stored["plan_id"] == "pro"
    assert "billing_revision" not in stored
    assert await harness.balance() == 300, (
        "the wallet must not fence when the store opted out"
    )
    balances, _entries = await harness.ledger._collections()
    balance_doc = await balances.find_one({"app_id": harness.app_id})
    assert balance_doc is not None
    assert "subject_revisions" not in balance_doc, "no ordering head when opted out"


# ── R3-A: the guarantee matters, not which index provides it ─────────────────


@pytest.mark.asyncio
async def test_an_equivalent_unique_index_under_another_name_is_accepted(
    harness,
) -> None:
    """A store that already enforces one row per subject is correctly
    configured. Requiring our own index name on top of it would reject a valid
    deployment outright, so equivalence is judged on the ordered key fields,
    their directions, and uniqueness."""
    await harness.assignments.create_index(
        [("app_id", 1), ("tenant_id", 1), ("user_id", 1)],
        unique=True,
        name="subscriptions_by_app_user",
    )

    service = harness.service()
    newer = await service.apply(
        harness.command("cmd_new", plan_id="enterprise", subject_revision=6)
    )
    assert newer.status == "applied"

    names = await harness.assignments.index_information()
    assert SUBJECT_UNIQUE_INDEX_NAME not in names, (
        "an equivalent guarantee must be accepted, not duplicated"
    )
    assert "subscriptions_by_app_user" in names

    stale = await service.apply(
        harness.command("cmd_stale", plan_id="pro", subject_revision=2)
    )
    assert stale.status == "superseded", "fencing still works through the existing index"
    assert await harness.assignments.count_documents({}) == 1


@pytest.mark.asyncio
async def test_a_narrowed_unique_index_does_not_satisfy_the_requirement(
    harness,
) -> None:
    """A partial index only constrains the documents it covers, so subjects
    outside that filter could still duplicate. It is not the same guarantee."""
    await harness.assignments.create_index(
        [("app_id", 1), ("tenant_id", 1), ("user_id", 1)],
        unique=True,
        name="only_active_subjects",
        partialFilterExpression={"status": "active"},
    )

    service = harness.service()
    assert (
        await service.apply(harness.command("cmd_partial", subject_revision=1))
    ).status == "applied"

    names = await harness.assignments.index_information()
    assert SUBJECT_UNIQUE_INDEX_NAME in names, (
        "a narrowed index must not stand in for the full constraint"
    )


# ── R3-B: ordering authority is established before the assignment commits ────


@pytest.mark.asyncio
async def test_a_failed_head_claim_aborts_before_the_assignment_commits(
    harness,
) -> None:
    """The window this closes: the assignment used to commit first and claim
    wallet ordering afterwards, so a failed head write left the subject at
    revision 5 while every wallet still admitted revision 1. Claiming first
    means the failure costs nothing — no assignment, no credit, and a retry
    that completes normally."""
    service = harness.service()
    command = harness.command("cmd_claim_fails", plan_id="pro", subject_revision=5)

    working = harness.ledger.advance_subject_revision

    async def _fail(**_kwargs):
        raise RuntimeError("wallet head write failed")

    harness.ledger.advance_subject_revision = _fail
    with pytest.raises(RuntimeError, match="wallet head write failed"):
        await service.apply(command)
    harness.ledger.advance_subject_revision = working

    assert await harness.assignment() is None, (
        "no assignment may exist without the ordering authority behind it"
    )
    assert await harness.balance() == 0
    balances, _entries = await harness.ledger._collections()
    assert await balances.count_documents({"app_id": harness.app_id}) == 0, (
        "no head was claimed either"
    )

    # The retry is the same command and completes normally.
    retried = await service.apply(command)
    assert retried.status == "applied"
    stored = await harness.assignment()
    assert stored["plan_id"] == "pro"
    assert int(stored["billing_revision"]) == 5
    assert await harness.balance() == 100


@pytest.mark.asyncio
async def test_null_and_the_string_none_are_different_subjects(harness) -> None:
    """Ordering authority is keyed per subject, so two subjects that differ
    only by type must never share a head — otherwise one silently fences the
    other out."""
    service = harness.service()
    typed = service._subject_key(harness.command("cmd_typed", subject_revision=1))
    forged = service._subject_key(
        harness.command("cmd_forged", subject_revision=1, user_id="None")
    )
    assert typed is not None and forged is not None
    assert typed != forged


# ── R3-C: a declined head claim means the command was overtaken ──────────────


@pytest.mark.asyncio
async def test_cancellation_resuming_behind_a_newer_revision_is_superseded(
    harness,
) -> None:
    """A revision-2 cancellation that resumes after revision 3 has already
    taken the subject must not cancel anything. Declining the head claim is
    meaning-bearing: the command lost, so its assignment never applies."""
    service = harness.service()
    assert (
        await service.apply(harness.command("cmd_r3", plan_id="pro", subject_revision=3))
    ).status == "applied"

    late_cancel = await service.apply(
        harness.command(
            "cmd_cancel_r2",
            plan_id="pro",
            subject_revision=2,
            event_type="subscription_cancelled",
        )
    )

    assert late_cancel.status == "superseded"
    assert {effect.reason for effect in late_cancel.effects} == {"stale_revision"}
    assert [effect.status for effect in late_cancel.effects] == ["skipped", "skipped"]
    stored = await harness.assignment()
    assert stored["status"] == "active", "the cancellation must not have applied"
    assert stored["plan_id"] == "pro"
    assert int(stored["billing_revision"]) == 3


@pytest.mark.asyncio
async def test_a_declined_head_blocks_a_subject_the_assignment_store_forgot(
    harness,
) -> None:
    """The case only the wallet head can catch. If the assignment row is gone —
    a restored store, a migration, an assignment that never landed — the
    assignment-side comparison has nothing to compare against and would happily
    create revision 2 as if it were the first. The wallet still remembers
    revision 3, so the claim declines and nothing is written."""
    service = harness.service()
    assert (
        await service.apply(harness.command("cmd_r3", plan_id="pro", subject_revision=3))
    ).status == "applied"
    await harness.assignments.delete_many({})

    late = await service.apply(
        harness.command("cmd_r2", plan_id="free", subject_revision=2)
    )

    assert late.status == "superseded"
    assert await harness.assignments.count_documents({}) == 0, (
        "an overtaken revision must not resurrect the subject at an older plan"
    )


# ── R3-D: reservations are owned, so a stale attempt cannot steal one ────────


@pytest.mark.asyncio
async def test_a_stale_attempt_cannot_delete_a_reservation_a_newer_revision_adopted(
    harness,
) -> None:
    """A1 reserves the allowance identity, then goes stale. A3 — legitimately
    entitled to that same plan-and-period identity — adopts the reservation.
    A1's rollback must not delete it out from under A3, or A3 commits a balance
    movement whose ledger entry no longer exists."""
    service = harness.service()
    subject_key = service._subject_key(harness.command("cmd_k", subject_revision=1))
    _balances, entries = await harness.ledger._collections()

    state: dict[str, Any] = {"armed": True, "adopted_owner": "rsv_a3_simulated"}

    async def _overtake() -> None:
        if state["armed"]:
            state["armed"] = False
            # A3 wins the subject and adopts A1's pending reservation, but has
            # not committed its balance movement yet.
            await harness.ledger.advance_subject_revision(
                app_id=harness.app_id,
                wallet_id="ai_tokens",
                user_id="user_1",
                preferred_scope="user",
                subject_key=subject_key,
                subject_revision=3,
            )
            pending = await entries.find_one(
                {"app_id": harness.app_id, "status": "pending"}
            )
            assert pending is not None, "A1 must have reserved before committing"
            state["entry_id"] = pending["_id"]
            await entries.update_one(
                {"_id": pending["_id"]},
                {
                    "$set": {"reservation_owner": state["adopted_owner"]},
                    "$inc": {"reservation_generation": 1},
                },
            )

    class _BalancesThatLoseTheRace:
        """Fires the overtake at the exact instant A1 commits its balance."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
            await _overtake()
            return await self._inner.find_one_and_update(*args, **kwargs)

    original_collections = harness.ledger._collections

    async def _hooked_collections() -> tuple[Any, Any]:
        inner_balances, inner_entries = await original_collections()
        return _BalancesThatLoseTheRace(inner_balances), inner_entries

    harness.ledger._collections = _hooked_collections
    try:
        stale = await harness.ledger.record_entry(
            app_id=harness.app_id,
            wallet_id="ai_tokens",
            amount=100,
            operation="allocation",
            idempotency_key="proof_alloc",
            user_id="user_1",
            preferred_scope="user",
            subject_key=subject_key,
            subject_revision=1,
        )
    finally:
        harness.ledger._collections = original_collections

    assert stale.status == "rejected"
    assert stale.entry["rejection_reason"] == "stale_subject_revision"
    survivor = await entries.find_one({"_id": state["entry_id"]})
    assert survivor is not None, (
        "the stale attempt deleted a reservation it no longer owned"
    )
    assert survivor["reservation_owner"] == state["adopted_owner"]

    # A3 now completes against the reservation it owns.
    committed = await harness.ledger.record_entry(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        amount=100,
        operation="allocation",
        idempotency_key="proof_alloc",
        user_id="user_1",
        preferred_scope="user",
        subject_key=subject_key,
        subject_revision=3,
    )
    assert committed.status == "applied"
    assert await harness.balance() == 100
    final = await entries.find_one({"_id": state["entry_id"]})
    assert final["status"] == "applied"


@pytest.mark.asyncio
async def test_a_pending_entry_already_counted_in_the_balance_is_recovered(
    harness,
) -> None:
    """Crash recovery: the balance moved but the entry never got marked. The
    retry must finish the entry, not treat the reservation as rollback-able."""
    service = harness.service()
    subject_key = service._subject_key(harness.command("cmd_k", subject_revision=1))
    _balances, entries = await harness.ledger._collections()

    first = await harness.ledger.record_entry(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        amount=100,
        operation="allocation",
        idempotency_key="recover_alloc",
        user_id="user_1",
        preferred_scope="user",
        subject_key=subject_key,
        subject_revision=2,
    )
    assert first.status == "applied"
    entry_id = first.entry["entry_id"]
    # Rewind only the entry, as a crash between the two writes would.
    await entries.update_one({"_id": entry_id}, {"$set": {"status": "pending"}})

    recovered = await harness.ledger.record_entry(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        amount=100,
        operation="allocation",
        idempotency_key="recover_alloc",
        user_id="user_1",
        preferred_scope="user",
        subject_key=subject_key,
        subject_revision=2,
    )
    assert recovered.status == "applied"
    assert await entries.count_documents({"_id": entry_id}) == 1
    assert await harness.balance() == 100, "recovery must not double credit"


# ── R4-A: a pre-effect ordering failure must not wedge the command ───────────


def _two_wallet_config() -> SubscriptionsConfig:
    """Same contract, two user-scoped wallets, so one head can fail alone."""
    base = _config().model_dump(mode="json")
    base["token_wallets"] = [
        {
            "wallet_id": "ai_tokens",
            "label": "AI tokens",
            "unit": "tokens",
            "usage_meter_id": "ai_tokens",
            "scope": "user",
        },
        {
            "wallet_id": "build_tokens",
            "label": "Build tokens",
            "unit": "tokens",
            "usage_meter_id": "build_tokens",
            "scope": "user",
        },
    ]
    return SubscriptionsConfig.model_validate(base)


async def _subject_heads(harness) -> dict[str, dict[str, int]]:
    balances, _entries = await harness.ledger._collections()
    heads: dict[str, dict[str, int]] = {}
    async for document in balances.find({"app_id": harness.app_id}):
        revisions = document.get("subject_revisions") or {}
        if revisions:
            heads[str(document.get("wallet_id"))] = {
                key: int(value) for key, value in revisions.items()
            }
    return heads


@pytest.mark.asyncio
async def test_a_durable_command_survives_a_pre_assignment_ordering_failure(
    harness,
) -> None:
    """The wedge this closes.

    `apply_durable` reserves the command as pending before running effects.
    Round 3 correctly made a failed wallet-head write propagate — but the
    reservation then stayed pending forever, so every identical retry was
    refused with `BillingFulfillmentPendingError` even though nothing had been
    written. A failure that provably precedes every effect must leave the exact
    command retryable.
    """
    service = harness.service(durable=True)
    activation = harness.command("cmd_r1", plan_id="pro", subject_revision=1)
    assert (await service.apply_durable(activation)).status == "applied"

    # Revision 1's allowance is delayed: nothing has been credited yet.
    balances, entries = await harness.ledger._collections()
    await balances.delete_many({"app_id": harness.app_id})
    await entries.delete_many({"app_id": harness.app_id})

    cancellation = harness.command(
        "cmd_r2_cancel",
        plan_id="pro",
        subject_revision=2,
        event_type="subscription_cancelled",
    )
    working = harness.ledger.advance_subject_revision

    async def _fail(**_kwargs):
        raise RuntimeError("wallet head write failed")

    harness.ledger.advance_subject_revision = _fail
    with pytest.raises(BillingFulfillmentOrderingUnavailable):
        await service.apply_durable(cancellation)
    harness.ledger.advance_subject_revision = working

    stored = await harness.assignment()
    assert stored["status"] == "active", "no effect may have committed"
    assert int(stored["billing_revision"]) == 1

    # The identical command retries and completes.
    retried = await service.apply_durable(cancellation)
    assert retried.status == "applied"
    stored = await harness.assignment()
    assert stored["status"] == "cancelled"
    assert int(stored["billing_revision"]) == 2

    # And the delayed revision-1 allowance still cannot credit behind it.
    delayed = await harness.ledger.ensure_plan_allowances(
        config=_config(),
        app_id=harness.app_id,
        plan_id="pro",
        token_allowances=[{"wallet_id": "ai_tokens", "amount": 100, "cadence": "monthly"}],
        user_id="user_1",
        subject_key=service._subject_key(activation),
        subject_revision=1,
    )
    assert delayed[0].status == "rejected"
    assert delayed[0].entry["rejection_reason"] == "stale_subject_revision"
    assert await harness.balance() == 0

    # The durable command is terminal, and replays as one.
    replay = await service.apply_durable(cancellation)
    assert replay.status == "replayed"
    assert replay.original_status == "applied"


@pytest.mark.asyncio
async def test_a_partial_multi_wallet_head_failure_converges_on_retry(
    harness,
) -> None:
    """Wallet A's head advances, wallet B's write fails, the assignment never
    commits. The retry finds A already equal, advances B, and completes. A is
    never reversed — conservative forward authority is safe, and
    reversing it would reopen the window an older revision could credit in."""
    service = BillingFulfillmentService(
        config=_two_wallet_config(),
        ledger=harness.ledger,
        collection_resolver=lambda _alias: harness.assignments,
        command_store=harness.store,
    )
    command = harness.command("cmd_multi", plan_id="pro", subject_revision=3)
    working = harness.ledger.advance_subject_revision
    state = {"fail_second_wallet": True}

    async def _fail_one_wallet(**kwargs):
        if state["fail_second_wallet"] and kwargs.get("wallet_id") == "build_tokens":
            raise RuntimeError("wallet head write failed")
        return await working(**kwargs)

    harness.ledger.advance_subject_revision = _fail_one_wallet
    with pytest.raises(BillingFulfillmentOrderingUnavailable):
        await service.apply_durable(command)

    assert await harness.assignment() is None, "the assignment never committed"
    heads = await _subject_heads(harness)
    assert "ai_tokens" in heads, "the wallet that succeeded keeps its claim"
    assert "build_tokens" not in heads

    state["fail_second_wallet"] = False
    retried = await service.apply_durable(command)
    harness.ledger.advance_subject_revision = working

    assert retried.status == "applied"
    stored = await harness.assignment()
    assert int(stored["billing_revision"]) == 3
    heads = await _subject_heads(harness)
    assert set(heads) == {"ai_tokens", "build_tokens"}
    assert {tuple(sorted(value.values())) for value in heads.values()} == {(3,)}


@pytest.mark.asyncio
async def test_a_concurrent_duplicate_stays_excluded_until_the_release(
    harness,
) -> None:
    """Releasing a pending reservation must not weaken exclusivity. While the
    failing invocation is still running, an identical caller is still refused;
    only a retry after the release can start."""
    service = harness.service(durable=True)
    command = harness.command("cmd_exclusive", plan_id="pro", subject_revision=4)
    working = harness.ledger.advance_subject_revision
    state = {"armed": True, "probed": False}

    async def _probe_then_fail(**_kwargs):
        if state["armed"]:
            state["armed"] = False
            with pytest.raises(BillingFulfillmentPendingError):
                await service.apply_durable(command)
            state["probed"] = True
        raise RuntimeError("wallet head write failed")

    harness.ledger.advance_subject_revision = _probe_then_fail
    with pytest.raises(BillingFulfillmentOrderingUnavailable):
        await service.apply_durable(command)
    harness.ledger.advance_subject_revision = working

    assert state["probed"], "the concurrent duplicate must have been refused"
    assert (await service.apply_durable(command)).status == "applied"


@pytest.mark.asyncio
async def test_only_a_pre_effect_failure_releases_the_command(harness) -> None:
    """A failure after an effect has committed must stay pending. The release
    is not general crash recovery — it is only sound because the ordering
    prerequisite provably precedes every mutation."""
    service = harness.service(durable=True)
    command = harness.command("cmd_post_effect", plan_id="pro", subject_revision=1)
    original_allowances = service._apply_plan_allowances

    async def _explode(_command, *, plan):
        raise RuntimeError("something failed after the assignment committed")

    service._apply_plan_allowances = _explode
    with pytest.raises(RuntimeError, match="after the assignment committed"):
        await service.apply_durable(command)
    service._apply_plan_allowances = original_allowances

    with pytest.raises(BillingFulfillmentPendingError):
        await service.apply_durable(command)


# ── R4-B: a balance may not move without an owned reservation ────────────────


@pytest.mark.asyncio
async def test_a_lost_adoption_reacquires_before_touching_the_balance(
    harness,
) -> None:
    """The exact interleave Codex found.

    A3 reads A1's pending reservation, A1 deletes its own reservation before
    A3's compare-and-swap runs, and the swap therefore matches nothing. The old
    code read that as "carry on", crediting the balance with no ledger entry
    behind it: +100, an applied_entry_id on the balance, and zero entries. A
    lost swap means the world moved, so A3 must look again and establish a
    reservation it actually owns before any balance write.
    """
    service = harness.service()
    subject_key = service._subject_key(harness.command("cmd_k", subject_revision=1))
    balances, entries = await harness.ledger._collections()

    scope = _scope_for(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        user_id="user_1",
        preferred_scope="user",
    )
    entry_id = _entry_id(scope, "proof_alloc")
    await entries.insert_one(
        {
            "_id": entry_id,
            "entry_id": entry_id,
            "idempotency_key": "proof_alloc",
            "balance_id": scope.balance_id,
            "app_id": harness.app_id,
            "wallet_id": "ai_tokens",
            "scope_type": "user",
            "scope_id": "user_1",
            "user_id": "user_1",
            "tenant_id": None,
            "operation": "allocation",
            "direction": "credit",
            "amount": 100,
            "signed_amount": 100,
            "status": "pending",
            "reservation_owner": "rsv_a1",
            "reservation_generation": 0,
        }
    )

    state: dict[str, Any] = {"armed": True, "reservation_at_balance_write": "unset"}

    class _EntriesThatLoseTheSwap:
        """A1 deletes its own reservation the instant before A3's swap."""

        def __init__(self, inner: Any) -> None:
            self._inner = inner

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
            if state["armed"]:
                state["armed"] = False
                await self._inner.delete_one(
                    {"_id": entry_id, "status": "pending", "reservation_owner": "rsv_a1"}
                )
            return await self._inner.find_one_and_update(*args, **kwargs)

    class _BalancesThatWitnessTheReservation:
        """Captures what the ledger owned at the instant the balance moved."""

        def __init__(self, inner: Any, inner_entries: Any) -> None:
            self._inner = inner
            self._entries = inner_entries

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        async def find_one_and_update(self, *args: Any, **kwargs: Any) -> Any:
            state["reservation_at_balance_write"] = await self._entries.find_one(
                {"_id": entry_id}
            )
            return await self._inner.find_one_and_update(*args, **kwargs)

    original_collections = harness.ledger._collections

    async def _hooked_collections() -> tuple[Any, Any]:
        inner_balances, inner_entries = await original_collections()
        return (
            _BalancesThatWitnessTheReservation(inner_balances, inner_entries),
            _EntriesThatLoseTheSwap(inner_entries),
        )

    harness.ledger._collections = _hooked_collections
    try:
        result = await harness.ledger.record_entry(
            app_id=harness.app_id,
            wallet_id="ai_tokens",
            amount=100,
            operation="allocation",
            idempotency_key="proof_alloc",
            user_id="user_1",
            preferred_scope="user",
            subject_key=subject_key,
            subject_revision=3,
        )
    finally:
        harness.ledger._collections = original_collections

    assert state["armed"] is False, "the swap must actually have been raced"

    # The invariant itself: at the instant the balance moved, this invocation
    # held a durable pending reservation that was no longer A1's.
    witnessed = state["reservation_at_balance_write"]
    assert witnessed is not None, (
        "the balance was mutated with no durable reservation behind it"
    )
    assert witnessed["status"] == "pending"
    assert witnessed["reservation_owner"] != "rsv_a1"

    assert result.status == "applied"
    assert result.entry["wallet_id"] == "ai_tokens"
    assert result.entry["amount"] == 100
    assert await harness.balance() == 100

    stored = await entries.find_one({"_id": entry_id})
    assert stored is not None, "a moved balance must have the entry that explains it"
    assert stored["status"] == "applied"
    assert stored["wallet_id"] == "ai_tokens"
    assert stored["amount"] == 100
    assert stored["reservation_owner"] != "rsv_a1", "ownership must have been retaken"
    assert await entries.count_documents({"app_id": harness.app_id}) == 1

    balance_doc = await balances.find_one({"_id": scope.balance_id})
    assert entry_id in set(balance_doc.get("applied_entry_ids") or [])

    # And the allocation is not mintable twice.
    again = await harness.ledger.record_entry(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        amount=100,
        operation="allocation",
        idempotency_key="proof_alloc",
        user_id="user_1",
        preferred_scope="user",
        subject_key=subject_key,
        subject_revision=3,
    )
    assert again.status == "applied"
    assert await harness.balance() == 100, "no duplicate mint"


@pytest.mark.asyncio
async def test_the_release_cannot_reclaim_a_record_it_does_not_own(harness) -> None:
    """The compare-and-delete pins command id, pending status, and command
    hash together. Anything else — a settled command, or the same id carrying
    different data — is somebody else's record and stays put."""
    service = harness.service(durable=True)
    settled = harness.command("cmd_settled", plan_id="pro", subject_revision=1)
    assert (await service.apply_durable(settled)).status == "applied"

    assert await harness.store.release_pre_effect_failure(settled) is False, (
        "a completed command must never be reclaimed"
    )
    record = await harness.store._collection()
    assert await record.count_documents({"_id": settled.command_id}) == 1

    # A pending record is only releasable by the exact same command.
    pending = harness.command("cmd_pending", plan_id="pro", subject_revision=2)
    assert (await harness.store.start(pending)).state == "started"
    impostor = harness.command("cmd_pending", plan_id="enterprise", subject_revision=2)
    assert await harness.store.release_pre_effect_failure(impostor) is False, (
        "a different command body must not release this reservation"
    )
    assert await harness.store.release_pre_effect_failure(pending) is True
    assert await record.count_documents({"_id": pending.command_id}) == 0


# ── a reservation written before reservations were tracked ───────────────────


def _pre_reservation_entry(
    scope: Any, entry_id: str, *, idempotency_key: str
) -> dict[str, Any]:
    """The exact document `record_entry` wrote before this PR.

    Copied field for field from `mozaiksai/core/tokens/wallet.py` as it stands
    on the base commit: no `reservation_owner`, no `reservation_generation`,
    because neither existed yet. Approximating this shape would prove nothing —
    the whole question is what a real historical row looks like.
    """
    return {
        "_id": entry_id,
        "entry_id": entry_id,
        "idempotency_key": idempotency_key,
        "balance_id": scope.balance_id,
        "app_id": scope.app_id,
        "wallet_id": scope.wallet_id,
        "scope_type": scope.scope_type,
        "scope_id": scope.scope_id,
        "user_id": scope.user_id,
        "tenant_id": scope.tenant_id,
        "operation": "allocation",
        "direction": "credit",
        "amount": 100,
        "signed_amount": 100,
        "status": "pending",
        "source": "runtime",
        "reason": None,
        "metadata": {},
        "usage_event_id": None,
        "created_at": datetime(2026, 6, 1, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 1, tzinfo=UTC),
    }


@pytest.mark.asyncio
async def test_a_pre_reservation_pending_entry_still_recovers(harness) -> None:
    """The regression this closes.

    A crash between the balance write and the entry finalization leaves a
    pending entry whose movement already committed. When that entry predates
    reservation tracking it carries neither reservation field, and asking Mongo
    for `reservation_generation: 0` never matches it — so acquisition exhausted
    its attempts and raised, permanently stranding a movement the wallet had
    already made.
    """
    balances, entries = await harness.ledger._collections()
    scope = _scope_for(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        user_id="user_1",
        preferred_scope="user",
    )
    entry_id = _entry_id(scope, "historical_alloc")
    historical = _pre_reservation_entry(
        scope, entry_id, idempotency_key="historical_alloc"
    )
    assert "reservation_owner" not in historical
    assert "reservation_generation" not in historical
    await entries.insert_one(historical)
    await balances.insert_one(
        {
            "_id": scope.balance_id,
            "app_id": harness.app_id,
            "wallet_id": "ai_tokens",
            "scope_type": "user",
            "scope_id": "user_1",
            "user_id": "user_1",
            "tenant_id": None,
            "balance": 100,
            "total_allocated": 100,
            "total_credited": 0,
            "total_spent": 0,
            "total_refunded": 0,
            "entry_count": 1,
            "applied_entry_ids": [entry_id],
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 6, 1, tzinfo=UTC),
        }
    )

    recovered = await harness.ledger.record_entry(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        amount=100,
        operation="allocation",
        idempotency_key="historical_alloc",
        user_id="user_1",
        preferred_scope="user",
    )

    assert recovered.status == "applied"
    assert recovered.entry["wallet_id"] == "ai_tokens"
    assert recovered.entry["amount"] == 100
    assert await harness.balance() == 100, "the movement must not happen twice"

    stored = await entries.find_one({"_id": entry_id})
    assert stored["status"] == "applied"
    assert stored["amount"] == 100
    assert stored["wallet_id"] == "ai_tokens"
    assert await entries.count_documents({"app_id": harness.app_id}) == 1

    # And the idempotent repeat is still idempotent.
    again = await harness.ledger.record_entry(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        amount=100,
        operation="allocation",
        idempotency_key="historical_alloc",
        user_id="user_1",
        preferred_scope="user",
    )
    assert again.status == "applied"
    assert await harness.balance() == 100, "no second mint"
    assert await entries.count_documents({"app_id": harness.app_id}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reservation_fields", "label"),
    [
        ({}, "absent"),
        ({"reservation_owner": "rsv_prior", "reservation_generation": 0}, "zero"),
        ({"reservation_owner": "rsv_prior", "reservation_generation": 7}, "nonzero"),
    ],
)
async def test_adoption_matches_every_persisted_reservation_state(
    harness, reservation_fields, label
) -> None:
    """Absent, zero, and non-zero are three different persisted states, and the
    compare-and-swap must take over all three. Collapsing absence into zero is
    what broke recovery."""
    _balances, entries = await harness.ledger._collections()
    scope = _scope_for(
        app_id=harness.app_id,
        wallet_id="ai_tokens",
        user_id="user_1",
        preferred_scope="user",
    )
    key = f"state_{label}"
    entry_id = _entry_id(scope, key)
    await entries.insert_one(
        {**_pre_reservation_entry(scope, entry_id, idempotency_key=key), **reservation_fields}
    )

    acquired = await harness.ledger._acquire_entry_reservation(
        entries,
        entry_id=entry_id,
        entry=_pre_reservation_entry(scope, entry_id, idempotency_key=key),
        reservation_owner="rsv_mine",
        now=datetime(2026, 7, 1, tzinfo=UTC),
    )

    assert acquired is None, "ownership must have been taken, not refused"
    owned = await entries.find_one({"_id": entry_id})
    assert owned["reservation_owner"] == "rsv_mine"
    expected = int(reservation_fields.get("reservation_generation", 0)) + 1
    assert owned["reservation_generation"] == expected, (
        "the generation advances from whatever was actually stored"
    )
