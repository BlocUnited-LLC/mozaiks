"""Host-owned usage attribution for an isolated, untrusted ACP turn."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from mozaiksai.control_plane.contracts import CodingWorkerRequest
from mozaiksai.control_plane.repository_acp_usage import (
    IsolatedACPUsage,
    parse_isolated_acp_usage,
    record_isolated_acp_usage,
)
from mozaiksai.core.session.build_binding import RunBuildBinding
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.usage.ledger import RuntimeUsageLedger


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def update_one(self, query: dict, update: dict, *, upsert: bool) -> None:
        assert upsert is True
        self.docs.setdefault(query["_id"], update["$setOnInsert"])


def _context(*, user_id: str = "operator") -> AuxiliaryUsageContext:
    return AuxiliaryUsageContext(
        app_id="factory", user_id=user_id, tenant_id="tenant-a", workspace_id="workspace-a",
        chat_id="real-chat", workflow_name="AppGenerator",
        run_build_binding=RunBuildBinding(
            target_app_id="target", build_registry_id="registry", build_id="revision", phase="refinement",
        ),
    )


def _request(*, user_id: str = "operator", context: AuxiliaryUsageContext | None = None) -> CodingWorkerRequest:
    binding = RunBuildBinding(
        target_app_id="target", build_registry_id="registry", build_id="revision", phase="refinement",
    )
    return CodingWorkerRequest(
        app_id="factory", user_id=user_id, target_app_id="target", run_build_binding=binding,
        usage_context=context if context is not None else _context(user_id=user_id),
        build_family="app_bundle", change_class="patch",
    )


def test_container_usage_is_strict_bounded_and_only_numeric() -> None:
    assert parse_isolated_acp_usage({
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    }) == IsolatedACPUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120)
    for unsafe in (
        None, {},
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        {"prompt_tokens": True, "completion_tokens": 20, "total_tokens": 21},
        {"prompt_tokens": -1, "completion_tokens": 20, "total_tokens": 20},
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 119},
        {"prompt_tokens": 10_000_001, "completion_tokens": 0, "total_tokens": 10_000_001},
        {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120, "secret": "source text"},
    ):
        assert parse_isolated_acp_usage(unsafe) is None


@pytest.mark.asyncio
async def test_receipt_uses_host_identity_and_is_idempotent_in_canonical_ledger(monkeypatch) -> None:
    collection = _Collection()
    ledger = RuntimeUsageLedger()
    monkeypatch.setattr(ledger, "_coll", AsyncMock(return_value=collection))
    usage = parse_isolated_acp_usage({
        "prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
    })
    assert usage is not None
    request = _request()

    for _ in range(2):
        assert await record_isolated_acp_usage(
            usage, request=request, attempt_id="job-123:attempt-1",
            model_name="codex/model-1", ledger=ledger,
        ) is True

    assert len(collection.docs) == 1
    event_id, doc = next(iter(collection.docs.items()))
    assert event_id.startswith("acp:")
    assert doc["source"] == "isolated_acp"
    assert doc["execution_kind"] == "auxiliary"
    assert (doc["app_id"], doc["user_id"], doc["tenant_id"], doc["workspace_id"]) == (
        "factory", "operator", "tenant-a", "workspace-a",
    )
    assert (doc["chat_id"], doc["workflow_name"], doc["build_id"]) == (
        "real-chat", "AppGenerator", "revision",
    )
    assert (doc["prompt_tokens"], doc["completion_tokens"], doc["total_tokens"]) == (100, 20, 120)
    assert doc["model_name"] == "codex/model-1"
    assert "attempt_id" not in doc
    assert "source text" not in str(doc)

    await record_isolated_acp_usage(
        usage, request=request, attempt_id="job-123:attempt-2", ledger=ledger,
    )
    await record_isolated_acp_usage(
        usage, request=_request(user_id="second-operator"),
        attempt_id="job-123:attempt-1", ledger=ledger,
    )
    assert len(collection.docs) == 3


@pytest.mark.asyncio
async def test_missing_usage_never_records_a_receipt() -> None:
    ledger = AsyncMock(spec=RuntimeUsageLedger)
    assert await record_isolated_acp_usage(
        None, request=_request(), attempt_id="job-123:attempt-1", ledger=ledger,
    ) is False
    ledger.record_usage_delta.assert_not_awaited()


@pytest.mark.asyncio
async def test_ledger_outage_does_not_invalidate_the_coding_turn() -> None:
    ledger = AsyncMock(spec=RuntimeUsageLedger)
    ledger.record_usage_delta.side_effect = RuntimeError("private database error")
    usage = IsolatedACPUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    assert await record_isolated_acp_usage(
        usage, request=_request(), attempt_id="job-123:attempt-1", ledger=ledger,
    ) is False
    ledger.record_usage_delta.assert_awaited_once()


@pytest.mark.asyncio
async def test_receipt_rejects_untrusted_identity_and_model() -> None:
    usage = IsolatedACPUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    ledger = AsyncMock(spec=RuntimeUsageLedger)
    with pytest.raises(TypeError, match="host-owned"):
        await record_isolated_acp_usage(usage, request={"app_id": "fake"}, attempt_id="attempt-123", ledger=ledger)
    with pytest.raises(ValueError, match="owner does not match"):
        await record_isolated_acp_usage(
            usage, request=_request(context=_context(user_id="wrong-owner")),
            attempt_id="attempt-123", ledger=ledger,
        )
    with pytest.raises(ValueError, match="attempt_id"):
        await record_isolated_acp_usage(usage, request=_request(), attempt_id="agent\nclaim", ledger=ledger)
    with pytest.raises(ValueError, match="model_name"):
        await record_isolated_acp_usage(
            usage, request=_request(), attempt_id="attempt-123", model_name="secret\nline", ledger=ledger,
        )
    ledger.record_usage_delta.assert_not_awaited()
